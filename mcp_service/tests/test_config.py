# -*- coding: utf-8 -*-
"""启动期配置校验：空 Token 与未指定 DATABASE_URL 都必须拒绝启动（spec §5.2）。

这两条不是形式主义：空 Token 会让「错误凭据被拒」的验收项静默通过；
未指定 DATABASE_URL 会因 app/config.py 的 @lru_cache 静默连到空库，
返回"本月 0 行 0 点"这种看起来正常的错误结果。
"""
import pytest

from mcp_service import config


def _clear(monkeypatch):
    for k in ("VISIT_MCP_TOKEN", "DATABASE_URL", "VISIT_MCP_HOST",
              "VISIT_MCP_PORT", "VISIT_MCP_LOG"):
        monkeypatch.delenv(k, raising=False)


def test_missing_token_refuses_startup(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///file:/tmp/x.db?mode=ro&uri=true")
    with pytest.raises(config.ConfigError) as exc:
        config.load()
    assert "VISIT_MCP_TOKEN" in str(exc.value)


def test_empty_token_refuses_startup(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VISIT_MCP_TOKEN", "   ")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///file:/tmp/x.db?mode=ro&uri=true")
    with pytest.raises(config.ConfigError):
        config.load()


def test_missing_database_url_refuses_startup(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VISIT_MCP_TOKEN", "t" * 16)
    with pytest.raises(config.ConfigError) as exc:
        config.load()
    assert "DATABASE_URL" in str(exc.value)


def test_default_sqlite_url_refuses_startup(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VISIT_MCP_TOKEN", "t" * 16)
    monkeypatch.setenv("DATABASE_URL", config.DEFAULT_SQLITE_URL)
    with pytest.raises(config.ConfigError):
        config.load()


def test_load_ok(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VISIT_MCP_TOKEN", "t" * 16)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///file:/tmp/x.db?mode=ro&uri=true")
    s = config.load()
    assert s.token == "t" * 16
    assert s.host == "127.0.0.1"
    assert s.port == 8765
    assert s.log_path.endswith("requests.jsonl")


def test_load_honours_overrides(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VISIT_MCP_TOKEN", "t" * 16)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///file:/tmp/x.db?mode=ro&uri=true")
    monkeypatch.setenv("VISIT_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("VISIT_MCP_PORT", "9999")
    monkeypatch.setenv("VISIT_MCP_LOG", "/tmp/mcp-test.jsonl")
    s = config.load()
    assert (s.host, s.port, s.log_path) == ("0.0.0.0", 9999, "/tmp/mcp-test.jsonl")
