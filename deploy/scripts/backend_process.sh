#!/usr/bin/env bash

# Shared helpers for non-systemd backend restarts.
# They use a PID file so recovery scripts do not terminate unrelated uvicorn processes.

backend_pid_file() {
  echo "${RESTREAM_BACKEND_PID_FILE:-${REPO_DIR}/state/restream-backend.pid}"
}

backend_venv_dir() {
  if [[ -x "${REPO_DIR}/.venv/bin/uvicorn" ]]; then
    echo "${REPO_DIR}/.venv"
  elif [[ -x "${REPO_DIR}/venv/bin/uvicorn" ]]; then
    echo "${REPO_DIR}/venv"
  else
    echo "${REPO_DIR}/.venv"
  fi
}

stop_manual_backend() {
  local pid_file pid deadline
  pid_file="$(backend_pid_file)"
  if [[ ! -f "$pid_file" ]]; then
    echo "No backend PID file at $pid_file; skipping manual stop."
    return 0
  fi

  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
    rm -f "$pid_file"
    echo "Backend PID file was invalid; removed $pid_file."
    return 0
  fi

  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$pid_file"
    echo "Backend PID $pid is not running; removed stale PID file."
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

start_manual_backend() {
  local pid_file venv_dir
  pid_file="$(backend_pid_file)"
  venv_dir="$(backend_venv_dir)"
  mkdir -p "$(dirname "$pid_file")"
  cd "$REPO_DIR"
  nohup "${venv_dir}/bin/uvicorn" main:app --host 127.0.0.1 --port 8000 > /var/log/restream-backend.log 2>&1 &
  echo "$!" > "$pid_file"
  echo "Backend started manually with PID $(cat "$pid_file")."
}

restart_manual_backend() {
  stop_manual_backend
  start_manual_backend
}
