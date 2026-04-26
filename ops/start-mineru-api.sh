#!/bin/bash
# MinerU API 启动脚本
# 负责以 conda 环境启动 MinerU API，并将 stdout 和 stderr 持续追加到指定日志路径中，
# 以便 Luceon sidecar (通过 Docker mount) 进行只读解析与观测。

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

# 确保日志目录存在
mkdir -p "$LOG_DIR"

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
if command -v stdbuf >/dev/null 2>&1; then
    exec stdbuf -oL mineru-api --host 0.0.0.0 --port 8083 >> "$LOG_FILE" 2>> "$ERR_FILE"
else
    exec env PYTHONUNBUFFERED=1 mineru-api --host 0.0.0.0 --port 8083 >> "$LOG_FILE" 2>> "$ERR_FILE"
fi
