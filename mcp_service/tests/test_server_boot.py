# -*- coding: utf-8 -*-
"""服务能组装、传输安全未被关闭、进程级启动行为正确（spec §8 T2）。

- test_tools_are_registered：工具清单 = visit_ping + visit_month_summary
- test_exit_nonzero_on_bad_config：空配置启动必须非零退出（spec §8 T2）
- test_boots_and_listens：好配置必须真的监听端口（"能启动"的冒烟证据）
"""
import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from mcp_service.server import build_server, transport_security_settings

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVER = REPO_ROOT / "mcp_service" / "server.py"

TOKEN = "t" * 32


def _base_env(tmp_path):
    env = dict(os.environ)
    env.update({
        "VISIT_MCP_TOKEN": TOKEN,
        "DATABASE_URL": f"sqlite:///file:{tmp_path}/empty.db?mode=ro&uri=true",
        "VISIT_MCP_LOG": str(tmp_path / "r.jsonl"),
        "VISIT_MCP_PORT": "0",   # 占位，由测试改写
    })
    return env


def test_tools_are_registered(monkeypatch, tmp_path):
    monkeypatch.setenv("VISIT_MCP_TOKEN", TOKEN)
    monkeypatch.setenv("DATABASE_URL",
                       f"sqlite:///file:{tmp_path}/empty.db?mode=ro&uri=true")
    monkeypatch.setenv("VISIT_MCP_LOG", str(tmp_path / "r.jsonl"))
    mcp = build_server()
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == {"visit_ping", "visit_month_summary"}


def test_dns_rebinding_protection_is_not_disabled():
    s = transport_security_settings("example.test")
    assert s.enable_dns_rebinding_protection is True
    assert "127.0.0.1:*" in s.allowed_hosts
    assert "example.test:*" in s.allowed_hosts


def test_exit_nonzero_on_bad_config(tmp_path):
    """空 Token 启动必须非零退出（否则「错误凭据被拒」会静默通过）。"""
    env = _base_env(tmp_path)
    env["VISIT_MCP_TOKEN"] = ""
    proc = subprocess.run(
        [sys.executable, str(SERVER)], env=env, capture_output=True, timeout=30)
    assert proc.returncode == 2, f"期望退出码 2，实际 {proc.returncode}"


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
    raise TimeoutError(f"服务未在 {timeout}s 内监听 {port}")


def test_boots_and_listens(tmp_path):
    """好配置必须真的启动并监听端口（Chunk 1"能启动"的冒烟证据）。"""
    import sqlite3
    con = sqlite3.connect(tmp_path / "empty.db")
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY);")
    con.commit()
    con.close()

    port = _free_port()
    env = _base_env(tmp_path)
    env["VISIT_MCP_PORT"] = str(port)
    proc = subprocess.Popen(
        [sys.executable, str(SERVER)], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        _wait_port(port)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
