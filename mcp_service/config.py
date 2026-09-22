# -*- coding: utf-8 -*-
"""MCP 服务的 env 读取与启动期校验。

**关键**：`load()` 必须在 import 任何 `app.*` 之前调用。
原因：`app/config.py` 的 `get_settings()` 带 `@lru_cache(maxsize=1)`，而 `app/db.py`
在 import 时就调用它并固化缓存；此后修改 `os.environ` 完全无效，会静默回落到默认库
`sqlite:///./store_settle.db`，表现为"本月 0 行 0 点"而不是报错（实测确认）。
"""
import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SQLITE_URL = "sqlite:///./store_settle.db"


class ConfigError(RuntimeError):
    """启动期配置不合法：一律拒绝启动，不做静默兜底。"""


@dataclass(frozen=True)
class McpSettings:
    token: str
    host: str
    port: int
    database_url: str
    log_path: str
    server_name: str = "visit-settle-mcp"


def load() -> McpSettings:
    token = (os.environ.get("VISIT_MCP_TOKEN") or "").strip()
    if not token:
        raise ConfigError(
            "VISIT_MCP_TOKEN 未设置或为空：拒绝启动。"
            "空凭证会让「错误凭据被拒」这条验收项静默通过。"
        )

    database_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not database_url:
        raise ConfigError(
            "DATABASE_URL 未设置：拒绝启动。"
            "app/config.py 的 get_settings() 带 @lru_cache 且 app/db.py 在 import 时即调用它，"
            f"不显式指定会静默回落到默认库 {DEFAULT_SQLITE_URL}（本地为空库）。"
        )
    if database_url == DEFAULT_SQLITE_URL:
        raise ConfigError(
            f"DATABASE_URL 指向默认空库 {DEFAULT_SQLITE_URL}：拒绝启动（同上原因）。"
        )

    host = (os.environ.get("VISIT_MCP_HOST") or "127.0.0.1").strip()
    port = int((os.environ.get("VISIT_MCP_PORT") or "8765").strip())
    log_path = (os.environ.get("VISIT_MCP_LOG") or "").strip() or str(
        REPO_ROOT / "mcp_service" / "logs" / "requests.jsonl"
    )
    return McpSettings(token=token, host=host, port=port,
                       database_url=database_url, log_path=log_path)
