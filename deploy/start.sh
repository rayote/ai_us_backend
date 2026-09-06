#!/usr/bin/env sh
set -eu

python -m app.worker &
worker_pid=$!
uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" &
api_pid=$!

cleanup() {
  kill "$api_pid" "$worker_pid" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

wait "$api_pid"