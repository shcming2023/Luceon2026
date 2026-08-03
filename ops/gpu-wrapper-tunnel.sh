#!/bin/sh
set -eu

GPU_SSH_HOST="${GPU_SSH_HOST:-113.31.105.253}"
GPU_SSH_PORT="${GPU_SSH_PORT:-23}"
GPU_WRAPPER_LOCAL_PORT="${GPU_WRAPPER_LOCAL_PORT:-38080}"
GPU_WRAPPER_REMOTE_PORT="${GPU_WRAPPER_REMOTE_PORT:-18080}"
GPU_SSH_KEY="${GPU_SSH_KEY:-$HOME/.ssh/id_ed25519_trae_dev}"
GPU_TUNNEL_STATUS_FILE="${GPU_TUNNEL_STATUS_FILE:-/Users/concm/prod_workspace/luceonweb2026/runtime/backend/gpu-wrapper-tunnel-status.json}"

if [ ! -r "$GPU_SSH_KEY" ]; then
  echo "GPU SSH key is not readable: $GPU_SSH_KEY" >&2
  exit 1
fi

mkdir -p "$(dirname "$GPU_TUNNEL_STATUS_FILE")"
last_recovery_at=""
if [ -r "$GPU_TUNNEL_STATUS_FILE" ] && grep -q '"connection_verified":true' "$GPU_TUNNEL_STATUS_FILE"; then
  last_recovery_at="$(sed -n 's/.*"last_recovery_at":"\([^"]*\)".*/\1/p' "$GPU_TUNNEL_STATUS_FILE" | head -1)"
fi

write_status() {
  status="$1"
  attempted_at="$2"
  recovered_at="$3"
  connection_verified="false"
  if [ -n "$recovered_at" ]; then
    connection_verified="true"
  fi
  temporary_status="${GPU_TUNNEL_STATUS_FILE}.tmp"
  printf '{"schema":"luceon.gpu-tunnel-status/v1","status":"%s","last_attempt_at":"%s","last_recovery_at":"%s","connection_verified":%s,"local_port":%s,"remote_port":%s,"host":"%s","auto_recovery":true}\n' \
    "$status" "$attempted_at" "$recovered_at" "$connection_verified" "$GPU_WRAPPER_LOCAL_PORT" "$GPU_WRAPPER_REMOTE_PORT" "$GPU_SSH_HOST" > "$temporary_status"
  mv "$temporary_status" "$GPU_TUNNEL_STATUS_FILE"
}

started_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
write_status "connecting" "$started_at" "$last_recovery_at"

/usr/bin/env -i HOME="$HOME" PATH="/usr/bin:/bin:/usr/sbin:/sbin" /usr/bin/ssh -NT \
  -p "$GPU_SSH_PORT" \
  -o BatchMode=yes \
  -o ConnectTimeout=15 \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -i "$GPU_SSH_KEY" \
  -L "127.0.0.1:${GPU_WRAPPER_LOCAL_PORT}:127.0.0.1:${GPU_WRAPPER_REMOTE_PORT}" \
  "root@${GPU_SSH_HOST}" &
ssh_pid="$!"

cleanup() {
  kill "$ssh_pid" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

attempt=0
while [ "$attempt" -lt 20 ]; do
  if ! kill -0 "$ssh_pid" 2>/dev/null; then
    wait "$ssh_pid" || exit_code="$?"
    write_status "unavailable" "$started_at" "$last_recovery_at"
    exit "${exit_code:-1}"
  fi
  if /usr/bin/nc -z 127.0.0.1 "$GPU_WRAPPER_LOCAL_PORT" >/dev/null 2>&1; then
    recovered_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    write_status "connected" "$started_at" "$recovered_at"
    wait "$ssh_pid" || exit_code="$?"
    write_status "disconnected" "$started_at" "$recovered_at"
    exit "${exit_code:-1}"
  fi
  attempt=$((attempt + 1))
  sleep 1
done

write_status "timeout" "$started_at" "$last_recovery_at"
exit 1
