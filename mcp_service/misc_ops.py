# -*- coding: utf-8 -*-
"""杂项能力层（task p1-mcpify-final-gaps；能力函数供 scenario_ops 调用）。

能力函数：
  file_layout(db, file_id)            只读：文件解析布局（表头行 / 列映射 /
                                      visible·deploy 值映射 / 点数规则）＋解析诊断
  product_doc(db)                     只读：系统产品说明原文（app/product_doc.md）
  staff_set_status(db, actor, ...)    写：员工账号状态（复用 accounts_r.staff_set_status
                                      口径，含 is_active 联动；确认语）
  store_ai_run(db, actor)             写：启动 B 组 AI 批处理（复用 stores_r.ai_run
                                      口径；后台线程执行，已有 running 拒绝）
  export_recon_result(db, task_id)    只读导出：对账任务原始产物（task.params.result_path
                                      落盘 xlsx；缺失时按 /recon/result 路由逻辑现算）

口径对齐：
- 只读/导出走 read_ops/export_ops 信封风格：成功 {"ok": True, "data": {...}}；
  失败 {"ok": False, "error": {"code","message","hint"}}；意外异常一律 INTERNAL。
- 写工具走 tools._write_call：require_write 闸门；本模块两个写工具均幂等
  （可安全重试）→ retryable=True → 意外异常 INTERNAL（可重试）。
- 错误码沿用 guards.GuardError.code 词表：UNAUTHORIZED / FORBIDDEN_TOOL /
  BAD_PARAM / CONFIRM_REQUIRED / NOT_FOUND / INTERNAL / INTERNAL_WRITE。

测试：mcp_service/tests/test_misc_ops.py（临时 SQLite fixture，绝不碰本地库）。
"""
import io
import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Context

from mcp_service import guards
from mcp_service.export_ops import _authorize, _ok_data

_DOC_PATH = Path(__file__).resolve().parent.parent / "app" / "product_doc.md"
_DOC_CHAR_LIMIT = 8000

_STATUS_ALLOW = {"active", "leave", "disabled", "resigned"}
_STATUS_LABELS = {"active": "在岗", "leave": "请假", "disabled": "停用",
                  "resigned": "离职"}


class MiscError(RuntimeError):
    """只读/导出路径业务错误 → 信封映射（NOT_FOUND / BAD_PARAM）。"""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint


def _authz_deny(actor):
    """按授权矩阵校验当前工具（工具名取自调用者函数名）；拒绝返回信封，放行返回 None。"""
    import inspect as _inspect
    from mcp_service import authz
    tool = next((f.function for f in _inspect.stack()
                 if f.function.startswith("visit_")), "unknown")
    return authz.enforce(tool, actor, {})


def _envelope_error(code: str, message: str, hint: str, **extra) -> dict[str, Any]:
    """统一失败信封（含 retryable；码表见 mcp_service/envelope.py）。"""
    from mcp_service import envelope
    return envelope.error(code, message, hint, **extra)


def _call(db, fn) -> dict[str, Any]:
    """执行只读/导出能力函数并包错误信封（只读约定：意外异常一律 INTERNAL）。"""
    try:
        return {"ok": True, "data": fn(db)}
    except MiscError as exc:
        return _envelope_error(exc.code, exc.message, exc.hint)
    except Exception as exc:  # noqa: BLE001
        return _envelope_error("INTERNAL", repr(exc),
                               "系统内部错误，已记录；可重试")


# ---------------------------------------------------------------------------
# 布局文本（与网页 /files/{id}/layout 同口径：_vm_text / _pr_text）
# ---------------------------------------------------------------------------

def _vm_text(vm) -> str:
    """value_map dict → "KEY=值,KEY2=值2" 文本（KEY 空字符串显示为 ~）。"""
    if not vm:
        return ""
    return ", ".join(f"{('~' if k == '' else k)}={v}" for k, v in vm.items())


def _pr_text(pr) -> str:
    """point_rules → 行式文本（每行：可见值|列表 & 投放值|列表 = 点数；~=空、*=任意）。"""
    if not pr:
        return ""
    lines = []
    for r in pr:
        if not isinstance(r, dict):
            continue

        def _fmt(k):
            v = r.get(k)
            if v is None:
                return "*"
            if isinstance(v, list):
                return "|".join(("~" if x == "" else str(x)) for x in v)
            return str(v)

        lines.append(f"{_fmt('visible')}&{_fmt('deploy')}={r.get('points', '')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 能力层：f(db, ...) -> data（只读/导出）；f(db, actor, ...) -> 完整信封（写）
# ---------------------------------------------------------------------------

def file_layout(db, file_id: int) -> dict[str, Any]:
    """查看某文件的解析布局（表头行/列映射/visible·deploy 映射/点数规则）＋解析诊断。

    数据来源：ImportFile.layout / parsed_sheets / ignored_sheets / warnings / errors。
    """
    from app.models import ImportFile

    f = db.get(ImportFile, file_id)
    if f is None:
        raise MiscError("NOT_FOUND", f"文件不存在：{file_id}",
                        "请先用 visit_files(view='list') 确认正确的 file_id")
    layout = f.layout or {}
    vm = layout.get("value_map") or {}
    data = {
        "file_id": f.id,
        "file_name": f.file_name,
        "status": f.status,
        "format": f.format,
        "header_row": layout.get("header_row") or f.header_row,
        "data_start_row": f.data_start_row,
        "layout": layout or None,
        "layout_source": layout.get("source"),
        "cols": layout.get("cols"),
        "value_map": vm,
        "value_map_text": {"visible": _vm_text(vm.get("visible")),
                           "deploy": _vm_text(vm.get("deploy"))},
        "point_rules": layout.get("point_rules"),
        "point_rules_text": _pr_text(layout.get("point_rules")),
        "parsed_sheets": f.parsed_sheets or [],
        "ignored_sheets": f.ignored_sheets or [],
        "warnings": f.warnings or [],
        "errors": f.errors or [],
        "parsed_rows": f.parsed_rows or 0,
        "total_rows": f.total_rows or 0,
    }
    if not layout:
        data["hint"] = ("该文件暂无解析布局（尚未解析或解析失败）；可先用 "
                        "visit_files(view='report') 看判定结果，或在网页 "
                        f"/files/{f.id}/layout 人工纠正列映射后重新解析")
    return data


def product_doc(db) -> dict[str, Any]:
    """返回系统产品说明原文（app/product_doc.md）；超长截断并在 data 注明 truncated。"""
    try:
        text = _DOC_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise MiscError("NOT_FOUND", f"产品说明文件不可读：{_DOC_PATH}",
                        f"文件缺失或读取失败（{exc}）；请检查仓库 app/product_doc.md") \
            from exc
    total = len(text)
    truncated = total > _DOC_CHAR_LIMIT
    data = {"doc": text[:_DOC_CHAR_LIMIT], "truncated": truncated,
            "total_chars": total, "char_limit": _DOC_CHAR_LIMIT,
            "source": str(_DOC_PATH)}
    if truncated:
        data["hint"] = (f"文档已截断至前 {_DOC_CHAR_LIMIT} 字符（原文 {total} 字符）；"
                        "如需要全文可查看网页 /product/raw（纯 Markdown）")
    return data


def staff_set_status(db, actor, *, user_id: int, new_status: str,
                     confirm_text: str | None = None) -> dict[str, Any]:
    """修改员工账号状态（复用网页 /staff-admin/{uid}/status 口径，含 is_active 联动）。"""
    from app.models import User

    guards.require_write(actor)
    target = db.get(User, user_id)
    if target is None or target.role != "staff":
        raise guards.GuardError("NOT_FOUND", f"员工不存在：user_id={user_id}",
                                "请先用 visit_staff(view='list') 确认正确的员工用户名")
    if new_status not in _STATUS_ALLOW:
        raise guards.GuardError("BAD_PARAM", f"未知状态：{new_status!r}",
                                "可选状态：" + "、".join(sorted(_STATUS_ALLOW)))
    expect = f"确认改状态 {target.username} {new_status}"
    guards.assert_confirm(confirm_text, expect)
    target.status = new_status
    # 停用/离职 → 禁登录；在岗/请假 → 可登录（与路由同口径；数据永不删除）
    target.is_active = new_status in ("active", "leave")
    db.commit()
    return {"ok": True, "data": {
        "user_id": target.id,
        "username": target.username,
        "display_name": target.display_name,
        "new_status": target.status,
        "status_label": _STATUS_LABELS[target.status],
        "is_active": target.is_active,
        "hint": "状态已更新（数据永不删除）；停用/离职后该账号不可登录",
    }}


def _start_bg(run_id: int) -> None:
    """后台线程执行 B 组 AI 批处理（与网页 BackgroundTasks 等价，独立进程内可运行）。"""
    import threading

    def _bg():
        from app.services import ai_batch
        ai_batch.run_batch_task(run_id)

    threading.Thread(target=_bg, daemon=True).start()


def store_ai_run(db, actor) -> dict[str, Any]:
    """启动 B 组 AI 批处理（复用网页 /stores/ai-run 的判定顺序与口径）。"""
    from app.models import AiRun, StorePair
    from app.services import ai_batch

    guards.require_write(actor)
    if not ai_batch.configured():
        return {"ok": True, "data": {
            "started": False, "reason": "ai_not_configured", "run_id": None,
            "hint": "未配置 AI_API_KEY（AI 未启用）；请先在服务端环境配置 AI key 后再启动",
        }}
    if db.query(AiRun).filter(AiRun.status == "running").first() is not None:
        raise guards.GuardError(
            "BAD_PARAM", "已有 AI 批处理在运行，拒绝重复启动",
            "请稍候刷新：等正在运行的批处理 status 变为 done 后再启动；"
            "可用 visit_store(view='search') 或网页 /stores?kind=fuzzy 观察进度")
    n = db.query(StorePair).filter(StorePair.kind == "fuzzy",
                                   StorePair.status == "pending").count()
    if n == 0:
        return {"ok": True, "data": {
            "started": False, "reason": "no_pending_pairs", "run_id": None,
            "hint": "当前没有待处理候选（fuzzy pending=0），无需启动批处理",
        }}
    run = AiRun(total_pairs=n, created_by=actor.uid)
    db.add(run)
    db.commit()
    return {"ok": True, "data": {
        "started": True, "run_id": run.id, "total_pairs": n,
        "status": "running",
        "hint": "AI 批处理已在后台启动（通常几分钟）；可用 visit_store(view='search') "
                "观察主档结果，网页 /stores?kind=fuzzy 可看进度",
    }}


def _recompute(db, task_id: int, author_name: str = "") -> bytes | None:
    """产物缺失时按路由逻辑现算：recon.build_report → xlsx bytes；None=无法现算。"""
    from app.services import recon

    wb = recon.build_report(db, task_id, author_name)
    if wb is None:
        return None
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def export_recon_result(db, task_id: int,
                        author_name: str = "") -> dict[str, Any]:
    """下载对账任务原始产物 xlsx（task.params.result_path 落盘；缺失按路由逻辑现算）。"""
    from app.models import ReconTask

    t = db.get(ReconTask, task_id)
    if t is None:
        raise MiscError("NOT_FOUND", f"对账任务不存在：task_id={task_id}",
                        "请先用对账查询工具（visit_recon(view='status') 等）确认 task_id")
    if t.status not in ("done", "parsed"):
        raise MiscError("NOT_FOUND",
                        f"任务尚未完成（status={t.status}），暂无可下载产物",
                        "请稍候任务完成后再试；可先用对账查询工具查看任务状态")
    fp = ((t.params or {}).get("result_path", "")
          if isinstance(t.params, dict) else "")
    source_path = None
    data = None
    if fp and os.path.exists(fp):
        with open(fp, "rb") as f:
            data = f.read()
        source_path = os.path.abspath(fp)
    else:
        data = _recompute(db, task_id, author_name)
        if data is None:
            raise MiscError("NOT_FOUND",
                            f"对账任务 {task_id} 产物缺失且无法现算",
                            "原始产物文件不存在且报告生成失败；"
                            "请联系维护人员核对任务数据")
    filename = f"对账结果_任务{task_id}.xlsx"
    d = _ok_data(filename, data)
    d["source_path"] = source_path
    d["hint"] = ("把 content_base64 解码保存为 filename 即可得到 Excel"
                 + ("" if source_path else "（原始产物缺失，已按路由逻辑现算生成）"))
    return d


# ---------------------------------------------------------------------------
# 工具函数（模块级，可直接单测；写工具走 _write_call）
# ---------------------------------------------------------------------------
