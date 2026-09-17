# -*- coding: utf-8 -*-
"""侦察新服务器环境（凭据从环境变量读，不落盘）。"""
import os
import sys
import paramiko

HOST = os.environ.get("NEW_HOST", "8.216.55.230")
USER = os.environ.get("NEW_USER", "root")
PW = os.environ["NEW_PW"]

CMDS = [
    "uname -a",
    "cat /etc/os-release | head -2",
    "docker --version 2>/dev/null || echo NO_DOCKER",
    "docker compose version 2>/dev/null || echo NO_COMPOSE",
    "which nginx certbot 2>/dev/null || echo NO_NGINX_CERTBOT",
    "df -h / | tail -1",
    "free -m | head -2",
    "ss -lntp 2>/dev/null | grep -E ':(80|443|5433|3660|8000)\\b' || echo PORTS_FREE",
    "ls /opt 2>/dev/null || echo NO_OPT",
    "curl -s -m 5 -o /dev/null -w '%{http_code}' https://api.xytoken.xyb2b.com/v1/models || echo NET_ISSUE",
]

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    c.connect(HOST, username=USER, password=PW, timeout=25)
except Exception as e:
    print("连接失败:", type(e).__name__, e)
    sys.exit(1)
print(f"=== {HOST} 侦察 ===")
for cmd in CMDS:
    _i, out, err = c.exec_command(cmd, timeout=40)
    o = out.read().decode(errors="replace").strip()
    e2 = err.read().decode(errors="replace").strip()
    print(f"$ {cmd}\n  {o or e2}")
c.close()
