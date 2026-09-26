#!/usr/bin/env bash
# 重启 MCP 服务并**验证工具清单**（一条命令替代手工 kill/start/核对）。
#
# 为什么需要：Python 不热加载——改完 mcp_service/ 代码必须重启才生效，
# 否则 WorkBuddy 仍走旧逻辑（本项目实测踩过：上传走错通道、DUPLICATE_FILE 误报）。
#
# 用法：scripts/mcp_restart.sh [--keep-token]
#   --keep-token  复用已在跑的进程的环境变量（默认用测试 Token）
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${VISIT_MCP_PORT:-8765}"
PY="$ROOT/mcp_service/.venv/bin/python"
PIDFILE="$ROOT/.mcp_service.pid"
LOG="$ROOT/data/mcp_service.log"
mkdir -p "$ROOT/data"

echo "[1/4] 停止旧进程"
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  kill "$(cat "$PIDFILE")" && echo "  已停止 PID $(cat "$PIDFILE")"
  sleep 1
else
  # 兜底：按端口找
  OLD=$(lsof -ti:"$PORT" -sTCP:LISTEN 2>/dev/null | head -1)
  if [ -n "${OLD:-}" ]; then kill "$OLD" && echo "  已停止占用 $PORT 的 PID $OLD"; sleep 1; fi
fi

echo "[2/4] 启动服务（日志：${LOG}）"
cd "$ROOT"
VISIT_MCP_TOKEN="${VISIT_MCP_TOKEN:-visit-test-rw-2026}" \
VISIT_MCP_READ_TOKEN="${VISIT_MCP_READ_TOKEN:-visit-test-ro-2026}" \
VISIT_MCP_ALLOW_LOCAL_PATH="${VISIT_MCP_ALLOW_LOCAL_PATH:-1}" \
VISIT_OAUTH_ISSUER="${VISIT_OAUTH_ISSUER:-http://127.0.0.1:8000}" \
  nohup mcp_service/run.sh >> "$LOG" 2>&1 &
echo $! > "$PIDFILE"
echo "  已启动 PID $(cat "$PIDFILE")"

echo "[3/4] 等待端口就绪"
for i in $(seq 1 40); do
  if lsof -ti:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then echo "  端口 $PORT 就绪（${i}s）"; break; fi
  sleep 1
  if [ "$i" = "40" ]; then echo "  ❌ 超时未就绪，见 $LOG"; exit 1; fi
done

echo "[3.5/4] 等待 MCP 应用就绪（握手；端口开放≠应用就绪）"
READY=0
for i in $(seq 1 20); do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "http://127.0.0.1:${PORT}/mcp" \
    -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
    -H "Authorization: Bearer ${VISIT_MCP_READ_TOKEN:-visit-test-ro-2026}" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"ready","version":"0"}}}' 2>/dev/null)
  if [ "${CODE}" = "200" ]; then echo "  就绪（第 ${i} 次探测）"; READY=1; break; fi
  sleep 1
done
if [ "${READY}" != "1" ]; then
  echo "  ❌ 应用未就绪（最后 HTTP ${CODE}）；日志尾部："
  tail -5 "$LOG" | sed 's/^/     /'
  exit 1
fi

echo "[4/4] 验证工具清单与自洽检查"
"$PY" - <<'PYEOF'
import asyncio, os
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
PORT = os.environ.get("VISIT_MCP_PORT", "8765")
async def main():
    async with streamable_http_client(
            f"http://127.0.0.1:{PORT}/mcp",
            http_client=create_mcp_http_client(
                headers={"Authorization": "Bearer visit-test-rw-2026"})) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            tools = sorted(t.name for t in (await s.list_tools()).tools)
            print(f"  ✅ 协议 {init.protocol_version} | 工具 {len(tools)} 个")
            need = {"visit_upload_file", "visit_month_salary", "visit_verify_integrity",
                    "visit_settlement_trace", "visit_payroll_mark_paid"}
            missing = need - set(tools)
            print(f"  {'✅' if not missing else '❌'} 关键工具齐全"
                  + (f"（缺 {sorted(missing)}）" if missing else ""))
            res = await s.call_tool("visit_verify_integrity", {})
            d = res.structured_content.get("data", {})
            sm = d.get("summary", {})
            print(f"  {'✅' if d.get('ok') else '❌'} 数据自洽检查: 通过 {sm.get('passed')} / "
                  f"失败 {sm.get('failed')} / 提示 {sm.get('info')}")
asyncio.run(main())
PYEOF
echo
echo "完成。若在 WorkBuddy 里看不到新工具，请**断开连接器再重连**（客户端会缓存工具清单）。"
