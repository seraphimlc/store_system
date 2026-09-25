# -*- coding: utf-8 -*-
"""对账写操作：上传对账文件（支付宝结算数据等）。

对账通道与巡店上传通道**不同**：
- 巡店记录（MarsNavi STORE VISIT RECORD）→ `visit_upload_file`（判定/入正式表）
- 对账文件（Alipay 结算数据等，逐条明细）→ 本模块 `visit_upload_recon`
  （解析 → 与系统人日统计比对 → 差异行 → 结果 Excel）

复用 `app.services.recon.submit_task`（与网页 /recon 上传同一条链路）。
"""
from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from mcp_service import guards


def _read_content(content: bytes | None, path: str | None) -> tuple[bytes, str | None]:
    """解析文件内容：base64 直传优先；本地路径需显式开关（远端=任意文件读取风险）。"""
    from mcp_service import write_ops

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
            "请确认这是对账明细文件（含日期/店/人员列）；巡店记录请用 visit_upload_file") from exc

    # 复用只读工具的汇总口径（同一真相）
    status = recon_ops._recon_status(db, month)
    task_row = next((x for x in status.get("tasks", []) if x["id"] == task.id), None)

    return {"ok": True, "data": {
        "task_id": task.id,
        "month": month,
        "status": task.status,
        "replaced_previous_ids": older,
        "task": task_row,
        "currency": "JPY",
        "note": "对账已同步完成；差异明细用 visit_recon_diff(month=...) 查询，"
                "结果 Excel 用 visit_export_recon_diff(task_id=...) 导出",
    }}


def register(mcp: MCPServer) -> None:
    from mcp_service.tools import _write_call

    @mcp.tool(
        name="visit_upload_recon",
        description=(
            "上传**对账文件**（如支付宝结算数据，逐条明细含日期/店/人员）并同步完成对账："
            "解析 → 与系统人日统计比对 → 生成差异行与结果。需要写权限 Token。"
            "参数：month（结算月 YYYY-MM）；filename；content_base64（文件内容 base64）；"
            "path（本机绝对路径，仅服务端开启本地路径模式时可用）。"
            "注意：巡店记录请改用 visit_upload_file，本工具只处理对账明细文件。"
        ),
    )
    def visit_upload_recon(
        ctx: Context,
        month: str,
        filename: str | None = None,
        content_base64: str | None = None,
        path: str | None = None,
    ) -> dict[str, Any]:
        from mcp_service import write_ops

        content = write_ops.decode_base64(content_base64) if content_base64 else None

        def run(db, actor):
            return upload_recon(db, actor, month=month, filename=filename,
                                content=content, path=path)

        return _write_call(ctx, "visit_upload_recon",
                           {"month": month, "filename": filename, "path": path,
                            "has_content": bool(content_base64)},
                           run, retryable=False)
