# -*- coding: utf-8 -*-
"""日志必须可诊断，且不得泄漏凭据（spec §5.6）。"""
import json

from mcp_service.reqlog import RequestLogger, redact_headers, redact_path


def test_redact_path_strips_query_string():
    """查询串可能带凭据，必须剥离。"""
    assert redact_path("/mcp?token=SECRET&x=1") == "/mcp"
    assert redact_path("/mcp") == "/mcp"


def test_redact_headers_keeps_names_only():
    got = redact_headers([(b"authorization", b"Bearer SECRET"),
                          (b"host", b"127.0.0.1:8765")])
    assert got == ["authorization", "host"]
    assert "SECRET" not in json.dumps(got)


def test_logger_writes_one_json_line_per_request(tmp_path):
    p = tmp_path / "requests.jsonl"
    log = RequestLogger(str(p))
    log({"ts": "2026-09-22T00:00:00Z", "method": "POST", "path": "/mcp",
         "status": 401, "auth_header_seen": False})
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["status"] == 401


def test_logger_appends_not_overwrites(tmp_path):
    p = tmp_path / "requests.jsonl"
    log = RequestLogger(str(p))
    log({"ts": "a", "status": 200})
    log({"ts": "b", "status": 401})
    assert len(p.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_logger_never_writes_credential_values(tmp_path):
    p = tmp_path / "requests.jsonl"
    log = RequestLogger(str(p))
    log({"ts": "x", "path": "/mcp", "header_names": ["authorization"]})
    assert "SECRET" not in p.read_text(encoding="utf-8")


def test_logger_creates_parent_dir(tmp_path):
    p = tmp_path / "nested" / "deeper" / "requests.jsonl"
    RequestLogger(str(p))({"ts": "x"})
    assert p.exists()
