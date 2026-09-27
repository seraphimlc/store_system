# -*- coding: utf-8 -*-
"""对账写能力层：上传对账文件（支付宝结算数据等）。

能力函数 upload_recon(db, actor, ...) 供 scenario_ops 的 visit_upload（kind='recon'）调用。

对账通道与巡店上传通道**不同**：
- 巡店记录（MarsNavi STORE VISIT RECORD）→ visit_upload（判定/入正式表）
- 对账文件（Alipay 结算数据等，逐条明细）→ visit_upload（自动识别为对账通道）
  （解析 → 与系统人日统计比对 → 差异行 → 结果 Excel）

复用 `app.services.recon.submit_task`（与网页 /recon 上传同一条链路）。
"""
from typing import Any

from mcp_service import guards


def _read_content(content: bytes | None, path: str | None) -> tuple[bytes, str | None]:
    """解析文件内容：base64 直传优先；本地路径需显式开关（远端=任意文件读取风险）。"""
    from mcp_service import write_ops

    if content is not None and path is not None:
        raise guards.GuardError(
            "BAD_PARAM", "path 与 content_base64 不可同时提供",
            "二选一：跨机器上传用 content_base64，本机同机用 path")
    if content is not None:
        return content, None
    if not path:
        raise guards.GuardError(
            "BAD_PARAM", "必须提供 content_base64 或 path",
            "请把文件内容以 base64 传入，或在本机模式下给绝对路径")
    if not write_ops.allow_local_path():
        raise guards.GuardError(
            "FORBIDDEN_TOOL", "本服务未开启本地路径上传",
            "远端部署下不接受本地路径（安全考虑）；请改传 content_base64")
    from pathlib import Path
    p = Path(path).expanduser()
    if not p.is_file():
        raise guards.GuardError("NOT_FOUND", f"文件不存在：{path}",
                                "请确认路径正确，或改传 content_base64")
    return p.read_bytes(), p.name


def upload_recon(db, actor, *, month: str, filename: str | None = None,
                 content: bytes | None = None, path: str | None = None) -> dict[str, Any]:
    """上传对账文件并**同步**完成对账（解析 → 比对 → 差异行 → 结果产物）。

    - 需要写权限；封账月份允许（对账不写正式表，只写对账结果表）
    - 同月已有版本会被标记为「上一版」保留
    """
    from app.services import recon
    from mcp_service import recon_ops

    guards.require_write(actor)
    month = guards.validate_month(month)

    data, name_from_path = _read_content(content, path)
    filename = filename or name_from_path or "recon.xlsx"

    try:
        task, older = recon.submit_task(db, month, filename, data, actor.uid, sync=True)
    except guards.GuardError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise guards.GuardError(
            "INTERNAL", f"对账文件无法登记任务：{type(exc).__name__}: {exc}",
            "请确认这是对账明细文件（含日期/店/人员列）；巡店记录请用 visit_upload") from exc

    # 复用只读工具的汇总口径（同一真相）
    status = recon_ops._recon_status(db, month)
    task_row = next((x for x in status.get("tasks", []) if x["id"] == task.id), None)
    if task_row is not None:
        # P1-7：task.kind 反映实际识别结果（detected.kind = recon），
        # 不再暴露内部固定值（daily_records）
        from mcp_service.file_kind import KIND_RECON
        task_row["kind"] = KIND_RECON

    return {"ok": True, "data": {
        "task_id": task.id,
        "month": month,
        "status": task.status,
        "replaced_previous_ids": older,
        "is_overwrite": bool(older),     # P0-3：同月已有版本 → 覆盖（旧任务标记为上一版）
        "task": task_row,
        "currency": "JPY",
        "note": "对账已同步完成；差异明细用 visit_recon(view='diff', month=...) 查询，"
                "结果 Excel 用 visit_recon_export(kind='diff', task_id=...) 导出",
    }}
