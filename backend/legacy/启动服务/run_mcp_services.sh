#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PY="$ROOT/venv/bin/python"
if [ ! -x "$PY" ]; then
  PY="python"
fi

TRANSPORT="${MCP_TRANSPORT:-streamable-http}"
HOST="${MCP_HOST:-127.0.0.1}"
COMBAT_PORT="${MCP_COMBAT_PORT:-9102}"
LOG_DIR="${MCP_LOG_DIR:-$ROOT/logs}"

mkdir -p "$LOG_DIR"
TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
COMBAT_LOG="$LOG_DIR/mcp_combat_$TIMESTAMP.log"

echo "Starting MCP Combat service..."

"$PY" -m app.combat.combat_mcp_server \
  --transport "$TRANSPORT" \
  --host "$HOST" \
  --port "$COMBAT_PORT" \
  >"$COMBAT_LOG" 2>&1 &
COMBAT_PID=$!

sleep 0.5
if ! kill -0 "$COMBAT_PID" 2>/dev/null; then
  echo "Combat MCP failed to start. Log: $COMBAT_LOG"
  exit 1
fi

echo "Combat MCP PID: $COMBAT_PID"
echo "Transport: $TRANSPORT"
echo "Endpoint: http://$HOST:$COMBAT_PORT/mcp"
echo "Log: $COMBAT_LOG"
echo ""
echo "Set these env vars for FastAPI:"
echo "  MCP_COMBAT_TRANSPORT=streamable-http"
echo "  MCP_COMBAT_ENDPOINT=http://$HOST:$COMBAT_PORT/mcp"
echo ""
echo "Press Ctrl+C to stop."

cleanup() {
  echo "Shutting down MCP service..."
  kill "$COMBAT_PID" 2>/dev/null || true
  wait "$COMBAT_PID" 2>/dev/null || true
}

trap cleanup INT TERM
wait "$COMBAT_PID"
