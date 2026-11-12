# -*- coding: utf-8 -*-
"""P1 四张表的模型与约束（spec §11）。"""
from app.models import ApiToken, McpAuditLog, RebuildSnapshot, SealedMonth


def test_tables_and_columns():
    assert ApiToken.__tablename__ == "api_tokens"
    assert McpAuditLog.__tablename__ == "mcp_audit_log"
    assert SealedMonth.__tablename__ == "sealed_months"
    assert RebuildSnapshot.__tablename__ == "rebuild_snapshots"

    cols = {c.name for c in ApiToken.__table__.columns}
    assert {"user_id", "name", "token_prefix", "token_hash", "scopes",
            "created_at", "last_used_at", "revoked_at"} <= cols

    assert {c.name for c in McpAuditLog.__table__.columns} >= {
        "token_id", "user_id", "tool", "params_json", "ok", "error_code",
        "detail", "client_info", "duration_ms", "created_at"}

    assert {c.name for c in SealedMonth.__table__.columns} >= {
        "month", "note", "created_by", "created_at"}

    assert {c.name for c in RebuildSnapshot.__table__.columns} >= {
        "month", "audit_id", "payload_json", "row_count", "created_at"}


def test_token_prefix_is_unique_indexed():
    assert ApiToken.__table__.columns["token_prefix"].unique is True


def test_sealed_month_pk_is_month():
    assert [c.name for c in SealedMonth.__table__.primary_key.columns] == ["month"]


def test_audit_nullable_fields():
    """401 时无 token/user/tool，必须可空（spec §8）。"""
    for name in ("token_id", "user_id", "tool", "ok", "error_code"):
        assert McpAuditLog.__table__.columns[name].nullable is True
