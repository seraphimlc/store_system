#!/bin/bash
# 本地开发服务：加载项目根 .env 后启动 uvicorn
set -a
source "$(dirname "$0")/../.env"
set +a
exec "$(dirname "$0")/../.venv/bin/python" -m uvicorn app.main:app \
  --host 127.0.0.1 --port 8000 --reload
