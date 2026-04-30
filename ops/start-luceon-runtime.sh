#!/bin/bash
set -e

echo "=== Luceon Runtime Startup ==="

echo "[1/4] Starting Docker services (MinIO, DB, Upload Server, Frontend)..."
docker compose up -d

echo "[2/4] Starting MinerU API in tmux session (luceon-mineru)..."
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmux kill-session -t luceon-mineru 2>/dev/null || true
tmux new-session -d -s luceon-mineru "cd '$REPO_ROOT' && bash ops/start-mineru-api.sh" || { echo "Error: tmux is required but not found or failed to start."; exit 1; }

echo "[3/4] Starting MinerU Log Observer in tmux session (luceon-sidecar)..."
tmux kill-session -t luceon-sidecar 2>/dev/null || true
tmux new-session -d -s luceon-sidecar "UPLOAD_SERVER_URL=http://127.0.0.1:8081/__proxy/upload node ops/mineru-log-observer.mjs"

echo "[4/4] Starting Dependency Supervisor in tmux session (luceon-supervisor)..."
tmux kill-session -t luceon-supervisor 2>/dev/null || true
tmux new-session -d -s luceon-supervisor "node ops/luceon-dependency-supervisor.mjs"

echo "=== All services started! ==="
echo "You can check status with: GET /ops/dependency-health"
