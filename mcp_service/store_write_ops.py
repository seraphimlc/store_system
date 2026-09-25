# -*- coding: utf-8 -*-
"""店铺主档写操作：合并候选对 / 标记不同店 / 拆分实体 / 整批应用推荐。

与网页 /stores 工作台**同一链路**：一律复用 `app.services.store_master`
（merge_pair / skip_pair / split_entity / apply_all_recommended），不在适配层重写业务。

每个工具用 `mcp_service.tools._write_call` 做统一包装：
- 闸门在 guards.py（require_write / assert_confirm），不重复实现；
- 意外异常一律 `retryable=False` → `INTERNAL_WRITE`（合并/拆分/整批**非幂等**，
  可能已部分生效，**禁止自动重试**），描述里已注明；
- 每调用独立 `SessionLocal()`，`finally: db.close()`（由 _write_call 保证）。
"""
from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from mcp_service import guards
from mcp_service.tools import _write_call
from mcp_service.annotations import write as write_ann


def _entity_summary(e) -> dict[str, Any]:
    """实体摘要（与 read_ops.store_search 同口径字段）。"""
    return {
        "id": e.id,
        "store_id_raw": e.store_id_raw,
        "name_local": e.name_local or "",
        "name_norm": e.name_norm or "",
        "city": e.city or "",
        "address_local": e.address_local or "",
        "master_id": e.master_id,
        "is_master": e.master_id == e.id,
        "master_store_id": e.master_store_id,
    }


def _pair(db, pair_id: int):
    """取候选对；不存在 → NOT_FOUND（先于业务执行）。"""
    from app.models import StorePair

    p = db.get(StorePair, pair_id)
    if p is None:
        raise guards.GuardError(
            "NOT_FOUND", f"候选对不存在：{pair_id}",
            "先调用只读工具查看候选列表 / 用 visit_store_search 检索，确认 pair_id")
    return p


def _resolve_kind(kind: str | None, pair_kind: str) -> str:
    """kind 可选：未传时按候选对自身类型（exact/fuzzy）推导。"""
    k = (kind or "").strip().lower() or pair_kind
    if k not in ("exact", "fuzzy"):
        raise guards.GuardError(
            "BAD_PARAM", f"kind 只能是 exact/fuzzy，收到 {kind!r}",
            "候选对类型：exact=归一化全等；fuzzy=高相似（人工/AI 判定）")
    return k


# ---------------------------------------------------------------------------
# 能力函数（f(db, actor, ...) → 完整信封或抛 GuardError；可直接单测）
# ---------------------------------------------------------------------------

def merge_pair(db, actor, *, pair_id: int, keep: int, kind: str | None = None,
               note: str | None = None) -> dict[str, Any]:
    """visit_store_merge_pair：把候选对里另一实体并入 keep 主档。

    复用路由 /stores/pairs/{pid}/merge 的 `store_master.merge_pair`：
    `keep` 指定保留哪个规范名（候选对中一个实体的 id）；kind 未传时按候选对
    自身类型推导 basis（exact→program_exact，fuzzy→manual），与网页口径一致。
    """
    from app.models import StoreEntity
    from app.services import store_master

    guards.require_write(actor)
    p = _pair(db, pair_id)
    a = db.get(StoreEntity, p.entity_a)
    b = db.get(StoreEntity, p.entity_b)
    if a is None or b is None:
        raise guards.GuardError(
            "NOT_FOUND", f"候选对 {pair_id} 的实体缺失（{p.entity_a} / {p.entity_b}）",
            "数据异常，请先核对店铺实体是否被删除")
    if keep not in (a.id, b.id):
        raise guards.GuardError(
            "BAD_PARAM",
            f"keep 必须是候选对中的实体 id（{a.id} 或 {b.id}），收到 {keep}",
            "keep 用于指定保留哪个规范名：用 visit_store_search 查实体 id 后传入")
    k = _resolve_kind(kind, p.kind)

    try:
        store_master.merge_pair(db, pair_id, keep, actor.uid,
                                basis="program_exact" if k == "exact" else "manual",
                                note=note or "")
    except ValueError as exc:
        raise guards.GuardError(
            "NOT_FOUND", str(exc),
            "候选对不存在或已被处理，请用只读工具核对当前状态") from exc

    keeper = a if a.id == keep else b
    other = b if keeper is a else a
    return {"ok": True, "data": {
        "pair_id": pair_id,
        "kind": k,
        "keep_entity_id": keeper.id,
        "entity": _entity_summary(keeper),
        "merged_entity_id": other.id,
        "merged_store_id": other.store_id_raw,
        "affected_entities": 1,
        "note": "该对已合并，不再显示为候选；用 visit_store_search 可查主档归属",
    }}


def skip_pair(db, actor, *, pair_id: int, kind: str | None = None) -> dict[str, Any]:
    """visit_store_skip_pair：标记候选对为「不同店」（复用路由 skip_pair 口径）。"""
    from app.services import store_master

    guards.require_write(actor)
    p = _pair(db, pair_id)
    try:
        store_master.skip_pair(db, pair_id, actor.uid)
    except ValueError as exc:
        raise guards.GuardError(
            "NOT_FOUND", str(exc),
            "候选对不存在或已被处理，请用只读工具核对当前状态") from exc

    return {"ok": True, "data": {
        "pair_id": pair_id,
        "kind": p.kind,
        "status": "skip",
        "note": "已标记为不同店，该对不再显示为候选",
    }}


def split_entity(db, actor, *, entity_id: int, confirm_text: str) -> dict[str, Any]:
    """visit_store_split_entity：把被误合并的实体拆回自己为主档（撤销并入）。

    拆分影响历史统计口径 → 必须确认语 `确认拆分 {entity_id}`。
    """
    from app.models import StoreEntity
    from app.services import store_master

    guards.require_write(actor)
    guards.assert_confirm(confirm_text, f"确认拆分 {entity_id}")
    e = db.get(StoreEntity, entity_id)
    if e is None:
        raise guards.GuardError(
            "NOT_FOUND", f"店铺实体不存在：{entity_id}",
            "请用 visit_store_search 检索确认实体 id")
    old = e.master_id

    try:
        store_master.split_entity(db, entity_id, actor.uid)
    except ValueError as exc:
        raise guards.GuardError(
            "NOT_FOUND", str(exc),
            "实体不存在或已被处理，请用只读工具核对当前状态") from exc

    return {"ok": True, "data": {
        "entity_id": entity_id,
        "entity": _entity_summary(e),
        "from_master": old,
        "to_master": e.id,
        "affected_entities": 1,
        "note": "已拆回自己为主档；拆分影响历史统计口径，请人工核对相关月数据",
    }}


def apply_all(db, actor, *, confirm_text: str, kind: str | None = None) -> dict[str, Any]:
    """visit_store_apply_all：整批应用推荐合并（复用路由 apply_all 的
    `apply_all_recommended`：A 组每组并入最早建档实体，跨城市组自动留出）。

    批量、影响大 → 必须确认语 `确认批量应用合并`。
    """
    from app.services import store_master

    guards.require_write(actor)
    guards.assert_confirm(confirm_text, "确认批量应用合并")
    res = store_master.apply_all_recommended(db, actor.uid)

    return {"ok": True, "data": {
        "groups_applied": res.get("groups", 0),
        "entities_merged": res.get("merged_entities", 0),
        "groups_left_manual": res.get("left_groups", 0),
        "hint": "批量合并会影响店铺主档与历史统计口径，建议先在网页端查看推荐列表；"
                "误并可凭合并日志在实体检索里拆回",
    }}


# ---------------------------------------------------------------------------
# 工具函数（模块级，可直接单测；全部走 _write_call，retryable=False）
# ---------------------------------------------------------------------------

def visit_store_merge_pair(ctx: Context, pair_id: int, keep: int,
                           kind: str | None = None,
                           note: str | None = None) -> dict[str, Any]:
    """合并一对候选店名。"""
    def run(db, actor):
        return merge_pair(db, actor, pair_id=pair_id, keep=keep,
                          kind=kind, note=note)

    return _write_call(ctx, "visit_store_merge_pair",
                       {"pair_id": pair_id, "keep": keep, "kind": kind},
                       run, retryable=False)


def visit_store_skip_pair(ctx: Context, pair_id: int,
                          kind: str | None = None) -> dict[str, Any]:
    """标记一对候选为「不同店」。"""
    def run(db, actor):
        return skip_pair(db, actor, pair_id=pair_id, kind=kind)

    return _write_call(ctx, "visit_store_skip_pair",
                       {"pair_id": pair_id, "kind": kind},
                       run, retryable=False)


def visit_store_split_entity(ctx: Context, entity_id: int,
                             confirm_text: str) -> dict[str, Any]:
    """拆分被误合并的实体（需确认语）。"""
    def run(db, actor):
        return split_entity(db, actor, entity_id=entity_id,
                            confirm_text=confirm_text)

    return _write_call(ctx, "visit_store_split_entity",
                       {"entity_id": entity_id},
                       run, retryable=False)


def visit_store_apply_all(ctx: Context, confirm_text: str,
                          kind: str | None = None) -> dict[str, Any]:
    """整批应用推荐合并（需确认语）。"""
    def run(db, actor):
        return apply_all(db, actor, confirm_text=confirm_text, kind=kind)

    return _write_call(ctx, "visit_store_apply_all",
                       {"kind": kind},
                       run, retryable=False)


# ---------------------------------------------------------------------------
# 注册（父会话在 tools.py 里接线调用）
# ---------------------------------------------------------------------------

def register(mcp: MCPServer) -> None:
    """注册 4 个店铺主档写工具。"""

    mcp.tool(
        name="visit_store_merge_pair",
        title="合并候选店名",
        annotations=write_ann("合并候选店名", idempotent=False),
        description=(
            "合并一对候选店名：把候选对里另一实体并入 keep 指定的主档。"
            "参数：pair_id（候选对 id）；keep（**候选对中一个实体的 id**，"
            "表示保留哪个规范名，可用只读工具 visit_store_search 查实体 id）；"
            "kind 可选（exact/fuzzy，未传时按候选对自身类型）；note 可选。"
            "闸门：需要写权限 Token；候选对不存在返回 NOT_FOUND；"
            "keep 不是对内实体返回 BAD_PARAM。"
            "意外失败返回 INTERNAL_WRITE（合并非幂等，可能已部分生效），"
            "**不要自动重试**，先用只读工具核对当前状态。"
        ),
    )(visit_store_merge_pair)

    mcp.tool(
        name="visit_store_skip_pair",
        title="标记不同店",
        annotations=write_ann("标记不同店", idempotent=False),
        description=(
            "把一对候选店名标记为「不同店」（不再合并，也不再有提示）。"
            "参数：pair_id（候选对 id）；kind 可选（exact/fuzzy）。"
            "闸门：需要写权限 Token；候选对不存在返回 NOT_FOUND。"
            "意外失败返回 INTERNAL_WRITE（写操作可能已部分生效），"
            "**不要自动重试**，先用只读工具核对当前状态。"
        ),
    )(visit_store_skip_pair)

    mcp.tool(
        name="visit_store_split_entity",
        title="拆分店铺实体",
        annotations=write_ann("拆分店铺实体", idempotent=False),
        description=(
            "拆分被误合并的店铺实体：把该实体拆回自己为主档（撤销并入）。"
            "参数：entity_id（实体 id，可用 visit_store_search 检索）；"
            "confirm_text 必须原文：确认拆分 {entity_id}。"
            "**拆分影响历史统计口径**，执行前请先向用户确认影响。"
            "闸门：需要写权限 Token；确认语不匹配返回 CONFIRM_REQUIRED；"
            "实体不存在返回 NOT_FOUND。返回拆分结果概要（原主档 → 自己）。"
            "意外失败返回 INTERNAL_WRITE（拆分非幂等，可能已部分生效），"
            "**不要自动重试**，先用只读工具核对当前状态。"
        ),
    )(visit_store_split_entity)

    mcp.tool(
        name="visit_store_apply_all",
        title="整批应用合并",
        annotations=write_ann("整批应用合并", idempotent=False),
        description=(
            "整批应用推荐合并：A 组（同名归一化全等）每组并入最早建档实体，"
            "跨城市组自动留出待人工。参数：confirm_text 必须原文："
            "确认批量应用合并；kind 可选（保留参数）。"
            "闸门：需要写权限 Token；确认语不匹配返回 CONFIRM_REQUIRED。"
            "返回：应用了几组 / 并入几店 / 留出几组。"
            "**批量合并会影响店铺主档与历史统计口径，建议先在网页端查看推荐列表**；"
            "误并可凭合并日志在实体检索里拆回。"
            "意外失败返回 INTERNAL_WRITE（整批非幂等，可能已部分生效），"
            "**不要自动重试**，先用只读工具核对当前状态。"
        ),
    )(visit_store_apply_all)
