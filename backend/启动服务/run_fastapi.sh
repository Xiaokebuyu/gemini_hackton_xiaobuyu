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

CMD=("$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT")
if [ "$RELOAD" = "true" ]; then
  CMD+=("--reload")
fi

echo "Starting FastAPI..."
echo "Host: $HOST"
echo "Port: $PORT"
echo "Reload: $RELOAD"
exec "${CMD[@]}"
