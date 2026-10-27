#!/usr/bin/env bash

# Shared helpers for non-systemd frontend restarts.

frontend_pid_file() {
  echo "${RESTREAM_FRONTEND_PID_FILE:-${REPO_DIR}/state/restream-frontend.pid}"
}

stop_manual_frontend() {
  local pid_file pid deadline
  pid_file="$(frontend_pid_file)"
  if [[ ! -f "$pid_file" ]]; then
    echo "No frontend PID file at $pid_file; skipping manual stop."
    return 0
  fi

  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
    rm -f "$pid_file"
    echo "Frontend PID file was invalid; removed $pid_file."
    return 0
  fi

  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$pid_file"
    echo "Frontend PID $pid is not running; removed stale PID file."
    return 0
  fi

  kill "$pid"
  deadline=$((SECONDS + 10))
  while kill -0 "$pid" 2>/dev/null && [[ "$SECONDS" -lt "$deadline" ]]; do
    sleep 1
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$pid_file"
}

start_manual_frontend() {
  local pid_file port
  pid_file="$(frontend_pid_file)"
  port="${PORT:-3000}"
  mkdir -p "$(dirname "$pid_file")"
  cd "$REPO_DIR/frontend"
  nohup npm run start -- -H 127.0.0.1 -p "$port" > /var/log/restream-frontend.log 2>&1 &
  echo "$!" > "$pid_file"
  echo "Frontend started manually with PID $(cat "$pid_file") on port $port."
}

restart_manual_frontend() {
  stop_manual_frontend
  start_manual_frontend
}
