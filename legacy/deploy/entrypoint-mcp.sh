#!/bin/sh
# MCP 服务容器入口：**不跑 alembic**（迁移由 web 容器统一负责），直接启动 MCP 服务。
set -e

echo "[entrypoint-mcp] 启动 MCP 服务（独立进程，复用同一镜像与业务代码）"
echo "[entrypoint-mcp] HOST=${VISIT_MCP_HOST:-0.0.0.0} PORT=${VISIT_MCP_PORT:-8765} PUBLIC_HOST=${VISIT_MCP_PUBLIC_HOST:-<未设>}"
echo "[entrypoint-mcp] 静态 Token: ${VISIT_MCP_STATIC_TOKENS:-1}（生产应设 0，只用库内 token）"

exec python mcp_service/server.py
