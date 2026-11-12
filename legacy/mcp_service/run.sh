#!/bin/sh
# 本地启动 MCP 服务（只读连本地库）。
# 用法：VISIT_MCP_TOKEN=xxx mcp_service/run.sh [path/to/db]
set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# 加载 .env（AI key 等；与 scripts/dev_server.sh 同口径）——上传时可能需要 AI 布局解析
if [ -f "$REPO_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$REPO_ROOT/.env"
  set +a
  echo "[run.sh] 已加载 .env" >&2
fi
DB="${1:-$REPO_ROOT/store_settle_live.db}"
: "${VISIT_MCP_TOKEN:?必须设置 VISIT_MCP_TOKEN}"
# VISIT_MCP_RW=0 → 只读打开（仅只读部署）；默认读写（P1 写工具需要）
if [ "${VISIT_MCP_RW:-1}" = "0" ]; then
  export DATABASE_URL="sqlite:///file:${DB}?mode=ro&uri=true"
  echo "[run.sh] 数据库以**只读**模式打开（VISIT_MCP_RW=0）" >&2
else
  export DATABASE_URL="sqlite:///${DB}"
  echo "[run.sh] 数据库以**读写**模式打开（写工具可用；只读请设 VISIT_MCP_RW=0）" >&2
fi
exec "$REPO_ROOT/mcp_service/.venv/bin/python" "$REPO_ROOT/mcp_service/server.py"
