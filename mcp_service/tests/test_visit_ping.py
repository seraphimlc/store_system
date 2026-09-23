# -*- coding: utf-8 -*-
"""visit_ping 必须回显凭据头是否到达——这是诊断"Bearer 被剥离"的唯一手段。"""
from mcp_service.tools import ping_payload


def test_reports_auth_header_seen_true():
    data = ping_payload(
        headers={"Authorization": "Bearer SECRET", "Mcp-Session-Id": "abc"},
        protocol_version="2025-11-25",
        client_info="WorkBuddy/4.23.0",
    )
    assert data["auth_header_seen"] is True
    assert data["session_id_seen"] is True
    assert data["protocol_version"] == "2025-11-25"
    assert data["client_info"] == "WorkBuddy/4.23.0"
    assert "SECRET" not in str(data)          # 绝不回显凭据值


def test_reports_auth_header_seen_false_when_absent():
    data = ping_payload(headers={"Host": "127.0.0.1:8765"},
                        protocol_version=None, client_info=None)
    assert data["auth_header_seen"] is False
    assert data["session_id_seen"] is False


def test_tolerates_missing_headers():
    """stdio 传输下 ctx.headers 为 None。"""
    data = ping_payload(headers=None, protocol_version=None, client_info=None)
    assert data["auth_header_seen"] is False


def test_sdk_version_is_not_hardcoded_placeholder():
    """sdk_version 应来自已安装包版本（评审建议），而不是写死的占位。"""
    data = ping_payload(headers=None, protocol_version=None, client_info=None)
    assert data["sdk_version"] not in ("", "unknown", "2.2.0-hardcoded")
    assert data["sdk_version"].count(".") == 2  # 形如 x.y.z
