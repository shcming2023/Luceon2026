#!/bin/bash
# MinerU API 启动脚本
# 负责以 conda 环境启动 MinerU API，并将 stdout 和 stderr 持续追加到指定日志路径中，
# 以便 Luceon sidecar (通过 Docker mount) 进行只读解析与观测。
#
# ⚠️ 关键要求（Patch 16.2.6 强化）：
# 1. 日志文件在启动前必须 touch，确保 Docker bind mount 能看到稳定的 inode。
# 2. 不得使用 >（覆盖重定向），只能 >>（追加），否则容器看到的是旧 inode。
# 3. 不得轮换（rotate）/截断（truncate）日志文件本身，否则容器会读到旧文件句柄。
#    如需轮换请使用 copytruncate 方式（复制后清空，而非 mv + 重建）。

LOG_DIR="/Users/concm/ops/logs"
LOG_FILE="$LOG_DIR/mineru-api.log"
ERR_FILE="$LOG_DIR/mineru-api.err.log"

echo "======================================"
echo "Starting MinerU API (FastAPI)"
echo "Timestamp: $(date '+%Y-%m-%d %H:%M:%S')"
echo "Conda Env: mineru"
echo "Port:      8083"
echo "Log File:  $LOG_FILE"
echo "Err File:  $ERR_FILE"
echo "======================================"

# ── 确保日志目录和文件提前存在 ──
# Docker bind mount 挂载的是目录 /Users/concm/ops/logs → /host/mineru-logs:ro
# 容器启动时如果文件不存在，某些 Docker Desktop 后端（VirtioFS/gRPC-FUSE）
# 可能无法即时看到后续创建的文件。因此必须在容器启动前（或紧接着容器启动后）
# 确保文件已存在，且后续仅追加、不替换 inode。
mkdir -p "$LOG_DIR"
touch "$LOG_FILE"
touch "$ERR_FILE"

# 尝试切换到 mineru conda 环境并启动 API
if command -v conda >/dev/null 2>&1; then
    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate mineru
else
    echo "Warning: conda not found in PATH, attempting to proceed anyway"
fi

# 启动 MinerU API，将输出重定向到日志文件。
# GNU/Linux 优先使用 stdbuf 开启行缓冲；macOS 默认没有 stdbuf，
# 此时通过 PYTHONUNBUFFERED=1 保证 Python 输出尽快刷入日志。
# ⚠️ 使用 exec + >> 确保不替换文件 inode、持续追加写入。
if command -v stdbuf >/dev/null 2>&1; then
    exec stdbuf -oL mineru-api --host 0.0.0.0 --port 8083 >> "$LOG_FILE" 2>> "$ERR_FILE"
else
    exec env PYTHONUNBUFFERED=1 mineru-api --host 0.0.0.0 --port 8083 >> "$LOG_FILE" 2>> "$ERR_FILE"
fi
