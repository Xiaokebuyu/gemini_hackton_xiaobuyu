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
  local endpoint="$1"
  "$PY" - "$endpoint" "$PROBE_TIMEOUT" <<'PY'
import socket
import sys
from urllib.parse import urlsplit

endpoint = sys.argv[1]
timeout = float(sys.argv[2])
parsed = urlsplit(endpoint)
host = parsed.hostname
if not host:
    raise SystemExit(2)
port = parsed.port or (443 if parsed.scheme == "https" else 80)

try:
    with socket.create_connection((host, port), timeout=timeout):
        pass
except OSError as exc:
    print(f"{type(exc).__name__}: {exc}")
    raise SystemExit(1)
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
    if ! probe_endpoint "$MCP_TOOLS_ENDPOINT"; then
      echo "Game Tools MCP 不可用: $MCP_TOOLS_ENDPOINT"
      echo "请先启动: bash 启动服务/run_mcp_services.sh"
      exit 1
    fi
  fi

  if needs_http_probe "$MCP_COMBAT_TRANSPORT"; then
    if ! probe_endpoint "$MCP_COMBAT_ENDPOINT"; then
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
