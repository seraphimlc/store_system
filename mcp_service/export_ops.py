# -*- coding: utf-8 -*-
"""P1 导出类 MCP 工具：把 4 个 Excel 导出能力暴露为只读工具（task p1-mcpify-export）。

设计（对齐 mcp_service/tools.py 的适配层职责边界）：
- 工具函数只做「取 Context → actor → 调构建层 → 包错误信封」，业务逻辑在
  app/services/report.py（与 Web 路由共用同一份构建函数，保证口径一致）；
- 成功返回 base64 + 服务端落盘路径（data/exports/），客户端解码保存即得 Excel；
- 只读工具：actor 取到即可（不要求 write scope）；无凭据 → UNAUTHORIZED 信封。

测试入口：模块级 `_run_export(db, kind, **params)`（错误信封映射与落盘全在内），
MCP 注册闭包只是薄封装；见 mcp_service/tests/test_export_ops.py。
"""
import base64
import os
import re
from pathlib import Path

from mcp_service.capability import MONTH_PATTERN

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXPORTS_DIR_ENV = "VISIT_MCP_EXPORT_DIR"   # 测试/部署可重定向落盘目录


class ExportError(RuntimeError):
    """业务错误 → 错误信封映射（BAD_MONTH / NOT_FOUND）。"""

    def __init__(self, code: str, message: str, hint: str) -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint


def _exports_dir() -> str:
    """落盘目录：env 覆盖优先，默认 <仓库根>/data/exports（不存在则建）。"""
    override = os.environ.get(_EXPORTS_DIR_ENV, "").strip()
    d = Path(override) if override else (_REPO_ROOT / "data" / "exports")
    os.makedirs(d, exist_ok=True)
    return str(d)


def _persist(filename: str, data: bytes) -> str:
    """落盘到 data/exports/<filename>，返回服务端绝对路径。"""
    path = os.path.join(_exports_dir(), filename)
    with open(path, "wb") as f:
        f.write(data)
    return os.path.abspath(path)


def _ok_data(filename: str, data: bytes) -> dict:
    return {
        "filename": filename,
        "size": len(data),
        "content_base64": base64.b64encode(data).decode("ascii"),
        "saved_path": _persist(filename, data),
        "hint": "把 content_base64 解码保存为 filename 即可得到 Excel",
    }


def _check_month(month: str) -> str:
    """月份唯一关口（同 capability.BadMonth 口径）→ 非法抛 ExportError(BAD_MONTH)。"""
    if not re.fullmatch(MONTH_PATTERN, month or ""):
        raise ExportError("BAD_MONTH", f"月份格式非法：{month!r}，应为 YYYY-MM",
                          "月份必须是 YYYY-MM，例如 2026-09")
    return month


def _salary_payload(db, month: str, period: str = "half1") -> dict:
    _check_month(month)
    from app.models import MonthPerfRecord
    if db.query(MonthPerfRecord).filter(
            MonthPerfRecord.month == month).count() == 0:
        raise ExportError("NOT_FOUND", f"该月无发薪数据：{month}",
                          "该月月绩效记录为空；请确认该月已入正式表并生成了月绩效")
    from app.services import report
    data, fname = report.build_payroll_workbook(db, month, period)
    return _ok_data(fname, data)


def _settle_payload(db, month: str) -> dict:
    _check_month(month)
    from app.models import PayrollPeriodRow
    if db.query(PayrollPeriodRow).filter(
            PayrollPeriodRow.month == month).count() == 0:
        raise ExportError("NOT_FOUND", f"该月无分期对账偏差数据：{month}",
                          "该月尚未生成分期对账偏差表（payroll_period_rows 无行）；"
                          "请先完成该月对账并生成偏差表")
    from app.services import report
    data, fname = report.build_payroll_settle_workbook(db, month)
    return _ok_data(fname, data)


def _recon_diff_payload(db, task_id: int) -> dict:
    from app.services import report
    res = report.build_recon_diff_workbook(db, task_id)
    if res is None:
        raise ExportError("NOT_FOUND", f"对账任务不存在：task_id={task_id}",
                          "请先用对账任务查询工具确认 task_id")
    data, fname = res
    return _ok_data(fname, data)


def _recon_report_payload(db, task_id: int) -> dict:
    from app.services import report
    res = report.build_recon_report_workbook(db, task_id)
    if res is None:
        raise ExportError("NOT_FOUND", f"对账任务不存在：task_id={task_id}",
                          "请先用对账任务查询工具确认 task_id")
    data, fname = res
    return _ok_data(fname, data)


_KINDS = {
    "salary": _salary_payload,
    "payroll_settle": _settle_payload,
    "recon_diff": _recon_diff_payload,
    "recon_report": _recon_report_payload,
}


def _run_export(db, kind: str, **params) -> dict:
    """统一执行入口：payload 构建（含落盘）→ 业务错误/意外异常 → 错误信封。

    供 MCP 注册闭包与测试共用：测试直接以 fixture session 调用，完整覆盖
    成功信封与 BAD_MONTH/NOT_FOUND/INTERNAL 映射。
    """
    from mcp_service import tools as _t
    try:
        data = _KINDS[kind](db, **params)
    except ExportError as exc:
        return _t._envelope_error(exc.code, exc.message, exc.hint)
    except Exception as exc:  # noqa: BLE001
        return _t._envelope_error("INTERNAL", repr(exc),
                                  "系统内部错误，已记录；可重试")
    return {"ok": True, "data": data}


def _authorize(ctx):
    """取 actor + **授权矩阵校验**；拒绝 → (None, 拒绝信封)。

    只读工具不要求 write scope，但**同样受角色约束**（员工不得导出公司级文件）。
    工具名取自调用者函数名（注册函数均为 visit_*）。
    """
    import inspect as _inspect
    from mcp_service import authz
    from mcp_service import tools as _t
    actor = _t.actor_from_ctx(ctx)
    if actor is None:
        return None, _t._envelope_error(
            "UNAUTHORIZED", "未认证",
            "请在 WorkBuddy 连接器设置中重新填写 Access Token")
    tool = next((f.function for f in _inspect.stack()
                 if f.function.startswith("visit_")), "unknown")
    denied = authz.enforce(tool, actor, {})
    if denied is not None:
        return None, denied
    return actor, None
