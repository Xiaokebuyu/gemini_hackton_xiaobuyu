#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PY="$ROOT/venv/bin/python"
if [ ! -x "$PY" ]; then
  PY="python"
fi

HOST="${API_HOST:-0.0.0.0}"
PORT="${API_PORT:-8000}"
RELOAD="${API_RELOAD:-true}"
CHECK_MCP="${CHECK_MCP:-true}"
PROBE_TIMEOUT="${MCP_PROBE_TIMEOUT_SECONDS:-2}"

MCP_TOOLS_TRANSPORT="${MCP_TOOLS_TRANSPORT:-streamable-http}"
MCP_TOOLS_ENDPOINT="${MCP_TOOLS_ENDPOINT:-http://127.0.0.1:9101/mcp}"
MCP_COMBAT_TRANSPORT="${MCP_COMBAT_TRANSPORT:-streamable-http}"
MCP_COMBAT_ENDPOINT="${MCP_COMBAT_ENDPOINT:-http://127.0.0.1:9102/mcp}"

probe_endpoint() {
  local server_type="$1"
  "$PY" - "$server_type" "$PROBE_TIMEOUT" <<'PY'
import asyncio
import json
import sys

from app.services.mcp_client_pool import MCPClientPool

server_type = sys.argv[1]
timeout = float(sys.argv[2])

async def main() -> None:
    pool = await MCPClientPool.get_instance()
    try:
        result = await pool.probe(server_type=server_type, timeout_seconds=timeout)
        print(json.dumps(result, ensure_ascii=False))
        if not result.get("ok"):
            raise SystemExit(1)
    finally:
        await MCPClientPool.shutdown()

asyncio.run(main())
PY
}

needs_http_probe() {
  local transport="$1"
  [[ "$transport" == "streamable-http" || "$transport" == "streamable_http" || "$transport" == "sse" ]]
}

CMD=("$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT")
if [ "$RELOAD" = "true" ]; then
  CMD+=("--reload")
fi

if [ "$CHECK_MCP" = "true" ]; then
  if needs_http_probe "$MCP_TOOLS_TRANSPORT"; then
    if ! probe_endpoint "game_tools"; then
      echo "Game Tools MCP 不可用: $MCP_TOOLS_ENDPOINT"
      echo "请先启动: bash 启动服务/run_mcp_services.sh"
      exit 1
    fi
  fi

  if needs_http_probe "$MCP_COMBAT_TRANSPORT"; then
    if ! probe_endpoint "combat"; then
      echo "Combat MCP 不可用: $MCP_COMBAT_ENDPOINT"
      echo "请先启动: bash 启动服务/run_mcp_services.sh"
      exit 1
    fi
  fi
fi

echo "Starting FastAPI..."
echo "Host: $HOST"
echo "Port: $PORT"
echo "Reload: $RELOAD"
echo "Check MCP: $CHECK_MCP"
exec "${CMD[@]}"
