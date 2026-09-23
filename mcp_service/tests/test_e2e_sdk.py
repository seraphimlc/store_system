# -*- coding: utf-8 -*-
"""官方 MCP Python client SDK 端到端：证明"服务端是对的"（spec §8 T1/T2）。

这是 T5（人工 WorkBuddy）之前的进展门：本文件全绿后，T5 失败才能归因到客户端侧。

SDK 用法修正（计划评审 #8）：
- 符号名是 streamable_http_client（不是 streamablehttp_client）
- headers 必须经 create_mcp_http_client(headers=...) 传入
- 产出 2 元组 (read, write)，不是 3 元组
- 客户端字段名是 structured_content（不是 structuredContent）

重要：所有 session 调用必须在 `async with` 上下文**内部**完成——在上下文
退出后使用 session 会得到 "Connection closed"（本文件第一版踩过这个坑）。
"""
import asyncio
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import (
    create_mcp_http_client,
    streamable_http_client,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVER = REPO_ROOT / "mcp_service" / "server.py"

TOKEN = "t" * 32


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_port(port: int, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), 0.3).close()
            return
        except OSError:
            time.sleep(0.2)
    pytest.fail(f"服务未在 {timeout}s 内监听 {port}")


@pytest.fixture()
def server(tmp_path):
    """拉起真实服务进程，返回端口；teardown 时终止。"""
    # 空库（有 formal_records 表、无数据）——验"空月 ok:true 且计数为 0"
    con = sqlite3.connect(tmp_path / "empty.db")
    con.executescript("""
        CREATE TABLE formal_records (
            id INTEGER PRIMARY KEY, import_id INTEGER NOT NULL,
            raw_record_id INTEGER NOT NULL, person_code VARCHAR(32),
            store_id_raw TEXT NOT NULL, japan_date DATE, points INTEGER NOT NULL,
            created_at DATETIME NOT NULL);
    """)
    con.commit()
    con.close()

    port = _free_port()
    env = dict(os.environ)
    env.update({
        "VISIT_MCP_TOKEN": TOKEN,
        "DATABASE_URL": f"sqlite:///file:{tmp_path}/empty.db?mode=ro&uri=true",
        "VISIT_MCP_LOG": str(tmp_path / "r.jsonl"),
        "VISIT_MCP_PORT": str(port),
        "VISIT_MCP_HOST": "127.0.0.1",
    })
    proc = subprocess.Popen(
        [sys.executable, str(SERVER)], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        _wait_port(port)
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_sdk_roundtrip(server):
    async def run():
        url = f"http://127.0.0.1:{server}/mcp"
        async with streamable_http_client(
                url,
                http_client=create_mcp_http_client(
                    headers={"Authorization": f"Bearer {TOKEN}"})) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                assert names == {"visit_ping", "visit_month_summary"}

                # 信封 + 凭据回显（A4 的服务端侧证据）
                res = await session.call_tool("visit_ping", {})
                sc = res.structured_content
                assert sc is not None
                assert sc["ok"] is True
                assert sc["data"]["auth_header_seen"] is True
                assert sc["data"]["client_info"] is not None

                # 月份 pattern 出现在发布的 schema 里（spec §5.4）
                schema = str([t.input_schema for t in tools.tools
                              if t.name == "visit_month_summary"])
                assert "pattern" in schema

                # 空月：ok:true 且各计数为 0 + hint（spec §5.4）
                res2 = await session.call_tool("visit_month_summary",
                                               {"month": "2026-09"})
                sc2 = res2.structured_content
                assert sc2["ok"] is True
                assert sc2["data"]["formal_rows"] == 0
                assert sc2["data"]["points_total"] == 0
                assert "hint" in sc2["data"]

    asyncio.run(run())


def test_wrong_token_rejected_at_http_layer(server):
    """错误 Token 在 HTTP 层被 401 拒绝（A3 的服务端侧证据）。"""

    async def run():
        import httpx2

        url = f"http://127.0.0.1:{server}/mcp"
        async with httpx2.AsyncClient() as client:
            r = await client.post(
                url, headers={"Authorization": "Bearer wrong"},
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert r.status_code == 401
        assert r.headers.get("www-authenticate", "").lower().startswith("bearer")
        body = r.json()
        assert body["error"]["code"] == "UNAUTHORIZED"

    asyncio.run(run())
