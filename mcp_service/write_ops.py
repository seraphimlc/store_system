# -*- coding: utf-8 -*-
"""写操作能力层：上传 / 出表 / 重算 / 改单价。

一律复用既有服务层（`importer` / `flow` / `perf`），不在适配层重写业务逻辑。
每个写函数入口先过闸门（guards），并把异常转成带错误码的 GuardError。
"""
import base64
import os
from pathlib import Path
from typing import Any

from mcp_service import guards


def allow_local_path() -> bool:
    """本地路径上传开关：**仅本机测试开**。

    远端 MCP 服务若接受任意路径参数 = 任意文件读取漏洞，故默认关闭，
    需显式 `VISIT_MCP_ALLOW_LOCAL_PATH=1`。
    """
    return os.environ.get("VISIT_MCP_ALLOW_LOCAL_PATH", "") == "1"


def _file_months(db, import_id: int) -> set[str]:
    """该文件涉及的结算月：raw 月份 ∪ 该文件正式表月份。"""
    from app.models import FormalRecord, RawRecord
    months = {(r.modified_raw or "")[:7] for r in
              db.query(RawRecord).filter(RawRecord.import_id == import_id).all()}
    months |= {(str(f.japan_date or ""))[:7] for f in
               db.query(FormalRecord).filter(FormalRecord.import_id == import_id).all()}
    return {m for m in months if len(m) == 7}


def _upload_visit(db, actor, *, filename: str | None = None,
                  content: bytes | None = None, path: str | None = None) -> dict[str, Any]:
    """上传巡店 Excel → 解析 → 判定 → 自动入正式表（+工资/找平/看板刷新）。

    与网页上传走**同一条链路**（importer.upload_and_store → parse_file →
    flow.process_import → flow.auto_finalize_pipeline）。
    """
    from app.services import flow, importer

    guards.require_write(actor)

    if content is None:
        if not path:
            raise guards.GuardError("BAD_PARAM", "必须提供 content_base64 或 path",
                                    "请把文件内容以 base64 传入，或在本机模式下给绝对路径")
        if not allow_local_path():
            raise guards.GuardError(
                "FORBIDDEN_TOOL", "本服务未开启本地路径上传",
                "远端部署下不接受本地路径（安全考虑）；请改传 content_base64")
        p = Path(path).expanduser()
        if not p.is_file():
            raise guards.GuardError("NOT_FOUND", f"文件不存在：{path}",
                                    "请确认路径正确，或改传 content_base64")
        content = p.read_bytes()
        filename = filename or p.name

    if not filename:
        raise guards.GuardError("BAD_PARAM", "缺少文件名", "请提供 filename")

    try:
        imp = importer.upload_and_store(filename, content, actor.uid, db)
    except importer.DuplicateUpload as exc:
        raise guards.GuardError("DUPLICATE_FILE", str(exc),
                                "该文件已上传过（内容 sha256 相同），无需重复上传") from exc
    except importer.UploadError as exc:
        raise guards.GuardError("BAD_PARAM", str(exc),
                                "仅支持 .xlsx，且不超过上传大小上限") from exc

    importer.parse_file(imp, db)
    if imp.status == "failed":
        from mcp_service import envelope
        return envelope.error(
            "PARSE_FAILED",
            "；".join((imp.errors or [])[:3]) or "解析失败",
            "文件格式无法识别；可在网页端「解析布局」页人工纠正表头后再试")

    months = sorted(_file_months(db, imp.id))
    hit = guards.sealed_months(db, months)
    if hit:
        raise guards.GuardError(
            "MONTH_SEALED",
            f"文件涉及封账月份 {', '.join(hit)}：已解析（文件#{imp.id}）但未入正式表",
            f"{', '.join(hit)} 已封账不可写入；如需修正请走对账找平流程")

    r = flow.process_import(db, imp.id)
    judge = r.get("judge", {})
    fr = flow.auto_finalize_pipeline(db, imp.id, actor.uid)

    return {"ok": True, "data": {
        "file_id": imp.id,
        "file_name": imp.file_name,
        "rows_parsed": imp.parsed_rows,
        "format": imp.format,
        "months": months,
        "judge": judge,
        "formal_added": fr.get("added", 0),
        "pipeline_ok": fr.get("ok", True),
        "is_overwrite": False,     # 巡店通道无"覆盖上一版"语义（sha256 去重防重复）
        "note": "已自动完成：判定 → 入正式表 → 工资/找平/看板/员工分析刷新",
    }}


# 让用户做选择时展示的选项（agent 据此询问，用户答完用 kind 重传）
KIND_OPTIONS = [
    {"kind": "daily_records",
     "label": "巡店记录（MarsNavi STORE VISIT RECORD）",
     "effect": "解析→判定→入正式表→工资/找平/看板刷新"},
    {"kind": "recon", "label": "对账明细（如支付宝/结算数据）",
     "effect": "建对账任务→与系统人日统计比对→差异行"},
]


def upload_file(db, actor, *, filename: str | None = None,
                content: bytes | None = None, path: str | None = None,
                month: str | None = None, kind: str | None = None,
                dry_run: bool = False) -> dict[str, Any]:
    """**统一上传入口**：自动识别文件类型并路由到对应通道。

    - 巡店记录（sheet 名 STORE_TASK_EXCEL_SHEET）→ 判定/入正式表全链路
    - 对账明细（Alipay 结算数据等）→ 对账任务（月份可从文件日期推断）
    - 手工结算对照件 / 无法识别的表头 → 明确报错并给指引（不抛晦涩异常）

    `dry_run=True`（P2-12）：只做 识别(detect_kind) + 推断月份(infer_month)
    + 查将替换的对账任务，**不写入任何数据**；返回影响预估供模型复述给用户。

    用户只需"把文件丢进来"，不必自己判断走哪条通道。
    """
    from mcp_service import file_kind, envelope

    guards.require_write(actor)

    # P0-1：path 与 content_base64 互斥且必须其一（描述已声明，此处强制）
    if content is not None and path is not None:
        raise guards.GuardError(
            "BAD_PARAM", "path 与 content_base64 不可同时提供",
            "二选一：跨机器上传用 content_base64，本机同机用 path")
    # 先解析内容（路径/base64），识别需要真实字节
    raw = content
    name = filename
    if raw is None:
        raw, name_from_path = _read_path(path)
        name = name or name_from_path
    if raw is None:
        raise guards.GuardError("BAD_PARAM", "缺少文件内容",
                                "请提供 content_base64 或 path")

    info = file_kind.detect_kind(raw)
    detected = info["kind"]

    # 用户/模型显式指定类型时优先（用于"识别不出来 → 问用户 → 重传"）
    forced = file_kind.normalize_kind(kind)
    if forced and forced not in ("daily_records", "recon"):
        raise guards.GuardError(
            "BAD_PARAM", f"kind 只能是 daily_records 或 recon，收到 {kind!r}",
            "巡店记录用 kind='daily_records'（旧值 'visit' 兼容）；"
            "对账明细用 kind='recon'")
    if forced:
        detected = forced
        info = {"kind": forced,
                "reason": f"由调用方指定（kind={forced}）",
                "sheets": info.get("sheets", []), "header": info.get("header", [])}
    kind = detected

    # ---------------- dry_run：只预估，不写入 ----------------
    if dry_run:
        est = file_kind.estimate(raw, kind)
        month_est = month or (
            file_kind.infer_month(raw, "recon") if kind == "recon" else None)
        will_replace = []
        if kind == "recon" and month_est:
            from app.models import ReconTask
            will_replace = sorted(
                t.id for t in db.query(ReconTask).filter(
                    ReconTask.kind == "monthly_v3").all()
                if (t.params or {}).get("month") == month_est
                and not (t.params or {}).get("replaced_by"))
        return {"ok": True, "data": {
            "dry_run": True,
            "detected": {"kind": kind, "reason": info.get("reason", "")},
            "month": month_est,
            "will_replace_task_ids": will_replace,
            "estimated_rows": est["estimated_rows"],
            "estimated_persons": est["estimated_persons"],
            "hint": "dry_run=true：未写入任何数据；以上为识别与影响预估，"
                    "确认后再用 dry_run=false 正式上传",
        }}

    # 打不开为 Excel（非 xlsx 等）→ 交回巡店通道，由它给出标准错误
    if kind == "unknown" and "无法打开为 Excel" in str(info.get("reason", "")):
        return _upload_visit(db, actor, filename=name, content=raw, path=None)

    if kind == "daily_records":
        res = _upload_visit(db, actor, filename=name, content=raw, path=None)
        if res.get("ok"):
            res["data"]["detected"] = {
                "kind": "daily_records", "reason": info["reason"]}
        return res

    if kind == "recon":
        from mcp_service import recon_write_ops

        m = month or file_kind.infer_month(raw, "recon")
        if not m:
            raise guards.GuardError(
                "BAD_PARAM", "无法从对账文件推断结算月",
                "请在调用时提供 month（YYYY-MM），例如 month='2026-08'")
        res = recon_write_ops.upload_recon(db, actor, month=m,
                                           filename=name, content=raw)
        if res.get("ok"):
            res["data"]["detected"] = {"kind": "recon", "reason": info["reason"],
                                       "month_inferred": month is None}
            # P1-7：task.kind 反映实际识别结果（recon），不暴露内部固定值
            task_row = res["data"].get("task")
            if isinstance(task_row, dict):
                task_row["kind"] = "recon"
        return res

    if kind == "manual":
        return envelope.error(
            "MANUAL_SETTLEMENT_FILE",
            "这是手工结算对照件（巡回最终结算），系统无对应入库通道",
            "它是核对用的「标准答案」，不是巡店记录也不是对账明细。"
            "如用户确认要按巡店记录或对账明细处理，请带 kind 参数重传"
            "（kind='daily_records' / kind='recon'）；否则作为参考文件人工比对。",
            options=KIND_OPTIONS)

    return envelope.error(
        "NEED_FILE_KIND",
        f"无法自动识别文件类型：{info.get('reason')}",
        "请**询问用户**这个文件属于下面哪一类，然后带上 kind 参数重新上传"
        "（kind='daily_records' 巡店记录 / kind='recon' 对账明细）。"
        "不要自行猜测类型。",
        options=KIND_OPTIONS,
        seen={"sheets": info.get("sheets", []),
              "header": [h for h in info.get("header", []) if h][:12]})


def _read_path(path: str | None) -> tuple[bytes | None, str | None]:
    """读本地路径（需显式开关）。"""
    if not path:
        return None, None
    if not allow_local_path():
        raise guards.GuardError(
            "FORBIDDEN_TOOL", "本服务未开启本地路径上传",
            "远端部署下不接受本地路径（安全考虑）；请改传 content_base64")
    p = Path(path).expanduser()
    if not p.is_file():
        raise guards.GuardError("NOT_FOUND", f"文件不存在：{path}",
                                "请确认路径正确，或改传 content_base64")
    return p.read_bytes(), p.name


def decode_base64(content_base64: str) -> bytes:
    try:
        return base64.b64decode(content_base64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise guards.GuardError("BAD_PARAM", f"base64 解码失败：{exc!r}",
                                "请确认 content_base64 是文件的 base64 编码") from exc
