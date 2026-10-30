#!/usr/bin/env sh
set -eu

run_worker() {
  worker_module="$1"
  restart_delay=2
  worker_pid=""

  stop_worker() {
    if [ -n "$worker_pid" ]; then
      kill "$worker_pid" 2>/dev/null || true
      wait "$worker_pid" 2>/dev/null || true
    fi
    exit 0
  }

  trap stop_worker INT TERM

  while true; do
    started_at=$(date +%s)
    python -m "$worker_module" &
    worker_pid=$!

    set +e
    wait "$worker_pid"
    exit_status=$?
    set -e
    worker_pid=""

    runtime=$(( $(date +%s) - started_at ))
    echo "$worker_module exited with status $exit_status after ${runtime}s; restarting in ${restart_delay}s." >&2
    sleep "$restart_delay"

    if [ "$runtime" -ge 60 ]; then
      restart_delay=2
    elif [ "$restart_delay" -lt 30 ]; then
      restart_delay=$((restart_delay * 2))
      if [ "$restart_delay" -gt 30 ]; then
        restart_delay=30
      fi
    fi
  done
}

run_worker app.worker &
worker_supervisor_pid=$!
run_worker app.analysis_worker &
analysis_supervisor_pid=$!
uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-5000}" &
api_pid=$!

cleanup() {
  kill "$api_pid" "$worker_supervisor_pid" "$analysis_supervisor_pid" 2>/dev/null || true
  wait "$worker_supervisor_pid" "$analysis_supervisor_pid" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

wait "$api_pid"