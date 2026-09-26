# -*- coding: utf-8 -*-
"""两阶段调用审计（规格：docs/specs-mcp-identity.md 第五节/第六节）。

- 每次工具调用：先插一行 `mcp_audit_log`（ok=NULL，含 token_id/user_id/tool/
  params_json 脱敏摘要/client_info），执行后回填 ok/error_code/detail（含耗时 ms）。
- 被拒绝的调用（FORBIDDEN_TOOL / UNAUTHORIZED 等）也记一行。
- 参数脱敏：content_base64 只记长度；路径只记 basename；Token/口令类键不落库。
- **审计写入失败绝不影响工具返回**（best-effort，全部 try/except）。
"""
import json
import os

from app.models import McpAuditLog

# 该工具在业务内部落一行审计拿 preview_id（闸门 6 契约），
# 审计层不重复插行，finish 时回填那一行。
_PREVIEW_TOOL = "visit_rebuild_preview"

# 敏感键（含这些子串的参数值不落库）
_SENSITIVE_KEYS = ("token", "secret", "password", "authorization",
                   "api_key", "apikey")
# 路径类键：只记 basename（不暴露服务器目录结构）
_PATH_KEYS = ("path", "file_path", "saved_path", "source_path",
              "stored_path", "result_path")


def redact_params(params: dict | None) -> dict | None:
    """参数摘要脱敏：返回可安全落库的 dict（绝不包含 Token 明文 / 文件内容）。"""
    if params is None:
        return None
    out = {}
    for k, v in params.items():
        lk = str(k).lower()
        if any(s in lk for s in _SENSITIVE_KEYS):
            out[k] = "<redacted>"
        elif lk == "content_base64":
            out[k] = f"<base64 len={len(v)}>" if isinstance(v, str) else "<base64>"
        elif any(lk == p or lk.endswith(p) for p in _PATH_KEYS) \
                and isinstance(v, str):
            out[k] = os.path.basename(v) or "<path>"
        elif isinstance(v, dict):
            out[k] = redact_params(v)
        elif isinstance(v, (list, tuple)):
            out[k] = [redact_params(x) if isinstance(x, dict) else x
                      for x in v]
        else:
            out[k] = v
    return out


def _json_params(params) -> str | None:
    try:
        return json.dumps(redact_params(params), ensure_ascii=False,
                          default=str)
    except Exception:            # noqa: BLE001  best-effort
        return None


def _actor_ids(actor) -> tuple:
    return ((actor.token_id, actor.uid) if actor is not None else (None, None))


def start(db, *, tool: str, actor, params, client_info) -> int | None:
    """执行前：插一行 ok=NULL。返回审计行 id（preview 工具返回 None）。"""
    try:
        if tool == _PREVIEW_TOOL:
            return None
        token_id, user_id = _actor_ids(actor)
        row = McpAuditLog(token_id=token_id, user_id=user_id, tool=tool,
                          params_json=_json_params(params),
                          client_info=client_info, ok=None)
        db.add(row)
        db.commit()
        return row.id
    except Exception:            # noqa: BLE001  best-effort：审计失败不影响调用
        try:
            db.rollback()
        except Exception:        # noqa: BLE001
            pass
        return None


def _fill(row, result, duration_ms) -> None:
    row.duration_ms = duration_ms
    if isinstance(result, dict):
        row.ok = bool(result.get("ok"))
        err = result.get("error")
        if isinstance(err, dict):
            row.error_code = err.get("code")
            row.detail = (err.get("hint") or err.get("message")) or None
        else:
            row.error_code = None
            row.detail = None
    else:
        row.ok = False
        row.error_code = "INTERNAL"
        row.detail = repr(result)


def finish(db, *, tool: str, actor, audit_id, result, client_info,
           duration_ms: int) -> None:
    """执行后：回填 ok/error_code/detail/耗时。审计失败不影响调用。"""
    try:
        row = None
        if audit_id is not None:
            row = db.get(McpAuditLog, audit_id)
        else:
            # preview 工具：回填工具函数内部创建的那一行（同 tool + 同身份 + ok=NULL）
            q = db.query(McpAuditLog).filter(
                McpAuditLog.tool == tool, McpAuditLog.ok.is_(None))
            if actor is not None and actor.token_id is not None:
                q = q.filter(McpAuditLog.token_id == actor.token_id)
            if actor is not None and actor.uid is not None:
                q = q.filter(McpAuditLog.user_id == actor.uid)
            row = q.order_by(McpAuditLog.id.desc()).first()
        if row is None:
            # 兜底：行不存在（如 preview 业务在落行前失败）→ 补一整行
            token_id, user_id = _actor_ids(actor)
            row = McpAuditLog(token_id=token_id, user_id=user_id, tool=tool,
                              params_json=_json_params({}),
                              client_info=client_info, ok=None)
            db.add(row)
        _fill(row, result, duration_ms)
        db.commit()
    except Exception:            # noqa: BLE001  best-effort
        try:
            db.rollback()
        except Exception:        # noqa: BLE001
            pass


def record_denial(db, *, tool: str, actor, params, denied, client_info) -> None:
    """被拒绝的调用：直接落一行完整审计（ok=False + error_code）。"""
    try:
        err = denied.get("error") or {}
        token_id, user_id = _actor_ids(actor)
        row = McpAuditLog(
            token_id=token_id, user_id=user_id, tool=tool,
            params_json=_json_params(params),
            ok=False,
            error_code=err.get("code") if isinstance(err, dict) else None,
            detail=(err.get("hint") or err.get("message"))
            if isinstance(err, dict) else None,
            client_info=client_info,
            duration_ms=None,
        )
        db.add(row)
        db.commit()
    except Exception:            # noqa: BLE001  best-effort
        try:
            db.rollback()
        except Exception:        # noqa: BLE001
            pass
