#!/bin/sh
# 本地启动 MCP 服务（只读连本地库）。
# 用法：VISIT_MCP_TOKEN=xxx mcp_service/run.sh [path/to/db]
set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="${1:-$REPO_ROOT/store_settle_live.db}"
: "${VISIT_MCP_TOKEN:?必须设置 VISIT_MCP_TOKEN}"
export DATABASE_URL="sqlite:///file:${DB}?mode=ro&uri=true"
exec "$REPO_ROOT/mcp_service/.venv/bin/python" "$REPO_ROOT/mcp_service/server.py"
