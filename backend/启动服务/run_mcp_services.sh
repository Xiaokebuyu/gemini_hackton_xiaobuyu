#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PY="$ROOT/venv/bin/python"
if [ ! -x "$PY" ]; then
  PY="python"
fi

TRANSPORT="${MCP_TRANSPORT:-streamable-http}"
HOST="${MCP_HOST:-127.0.0.1}"
TOOLS_PORT="${MCP_TOOLS_PORT:-9101}"
COMBAT_PORT="${MCP_COMBAT_PORT:-9102}"
LOG_DIR="${MCP_LOG_DIR:-$ROOT/logs}"

mkdir -p "$LOG_DIR"
TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
TOOLS_LOG="$LOG_DIR/mcp_game_tools_$TIMESTAMP.log"
COMBAT_LOG="$LOG_DIR/mcp_combat_$TIMESTAMP.log"

echo "Starting MCP services..."

"$PY" -m app.mcp.game_tools_server \
  --transport "$TRANSPORT" \
  --host "$HOST" \
  --port "$TOOLS_PORT" \
  >"$TOOLS_LOG" 2>&1 &
TOOLS_PID=$!

"$PY" -m app.combat.combat_mcp_server \
  --transport "$TRANSPORT" \
  --host "$HOST" \
  --port "$COMBAT_PORT" \
  >"$COMBAT_LOG" 2>&1 &
COMBAT_PID=$!

sleep 0.5
if ! kill -0 "$TOOLS_PID" 2>/dev/null; then
  echo "Game Tools MCP failed to start. Log: $TOOLS_LOG"
  exit 1
fi
if ! kill -0 "$COMBAT_PID" 2>/dev/null; then
  echo "Combat MCP failed to start. Log: $COMBAT_LOG"
  kill "$TOOLS_PID" 2>/dev/null || true
  exit 1
fi

echo "Game Tools MCP PID: $TOOLS_PID"
echo "Combat MCP PID: $COMBAT_PID"
echo "Transport: $TRANSPORT"
echo "Endpoints:"
echo "  http://$HOST:$TOOLS_PORT/mcp"
echo "  http://$HOST:$COMBAT_PORT/mcp"
echo "Logs:"
echo "  $TOOLS_LOG"
echo "  $COMBAT_LOG"
echo ""
echo "Set these env vars for FastAPI:"
echo "  MCP_TOOLS_TRANSPORT=streamable-http"
echo "  MCP_TOOLS_ENDPOINT=http://$HOST:$TOOLS_PORT/mcp"
echo "  MCP_COMBAT_TRANSPORT=streamable-http"
echo "  MCP_COMBAT_ENDPOINT=http://$HOST:$COMBAT_PORT/mcp"
echo ""
echo "Press Ctrl+C to stop."

cleanup() {
  echo "Shutting down MCP services..."
  kill "$TOOLS_PID" "$COMBAT_PID" 2>/dev/null || true
  wait "$TOOLS_PID" "$COMBAT_PID" 2>/dev/null || true
}

trap cleanup INT TERM
wait "$TOOLS_PID" "$COMBAT_PID"
