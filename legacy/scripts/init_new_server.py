# -*- coding: utf-8 -*-
"""新服务器初始化：装 SSH 公钥 + docker/compose + nginx/certbot。
用法：NEW_PW=xxx ./.venv/bin/python scripts/init_new_server.py [--key-only]
"""
import os
import sys
import paramiko

HOST = os.environ.get("NEW_HOST", "8.216.55.230")
USER = os.environ.get("NEW_USER", "root")
PW = os.environ["NEW_PW"]
PUBKEY = os.environ.get("PUBKEY", "")

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PW, timeout=25)


def run(cmd, timeout=1800, quiet=False):
    _i, out, err = c.exec_command(cmd, timeout=timeout)
    o = out.read().decode(errors="replace")
    e = err.read().decode(errors="replace")
    rc = out.channel.recv_exit_status()
    if not quiet:
        tail = (o or e).strip().splitlines()[-6:]
        print(f"$ {cmd[:80]}\n   rc={rc} " + " | ".join(tail))
    return rc, o + e


# 1) SSH 公钥
if PUBKEY:
    run("mkdir -p ~/.ssh && chmod 700 ~/.ssh", quiet=True)
    sftp = c.open_sftp()
    try:
        with sftp.open("/root/.ssh/authorized_keys", "a") as f:
            f.write(PUBKEY.strip() + "\n")
    except IOError:
        with sftp.open("/root/.ssh/authorized_keys", "w") as f:
            f.write(PUBKEY.strip() + "\n")
    sftp.close()
    run("chmod 600 ~/.ssh/authorized_keys && wc -l ~/.ssh/authorized_keys")

if "--key-only" in sys.argv:
    c.close()
    sys.exit(0)

# 2) docker（Alibaba Cloud Linux 4 ≈ RHEL9）
run("dnf install -y dnf-plugins-core >/dev/null 2>&1; "
    "dnf config-manager --add-repo "
    "https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo "
    ">/dev/null 2>&1; echo repo-ok")
run("dnf install -y docker-ce docker-ce-cli containerd.io "
    "docker-compose-plugin 2>&1 | tail -3")
run("systemctl enable --now docker && sleep 3 && docker --version && "
    "docker compose version")

# 3) nginx + certbot
run("dnf install -y nginx certbot python3-certbot-nginx 2>&1 | tail -2")
run("systemctl enable --now nginx && nginx -v")

c.close()
print("=== 初始化完成 ===")
