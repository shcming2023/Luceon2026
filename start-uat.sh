#!/usr/bin/env bash
# ============================================================
#  EduAsset CMS — UAT 环境统一启动脚本
#
#  用法：
#    ./start-uat.sh          # 前台运行（Ctrl+C 停止所有）
#    ./start-uat.sh --build  # 先构建前端再启动
#    ./start-uat.sh stop     # 停止所有已启动的服务进程
#
#  依赖：Node.js v18+（当前 v22），pnpm
#  访问：http://localhost:8081（可通过 .env 中 CMS_HOST 指定）
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
PID_FILE="$SCRIPT_DIR/.uat-pids"

# ── 颜色输出 ──────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ── 停止模式 ──────────────────────────────────────────────
if [[ "${1:-}" == "stop" ]]; then
  if [[ -f "$PID_FILE" ]]; then
    info "停止所有 UAT 服务进程..."
    while IFS= read -r pid; do
      if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" && echo "  已停止 PID $pid"
      fi
    done < "$PID_FILE"
    rm -f "$PID_FILE"
    success "所有服务已停止"
  else
    warn "未找到 PID 文件，服务可能未运行"
  fi
  exit 0
fi

# ── 加载 .env ─────────────────────────────────────────────
if [[ ! -f "$ENV_FILE" ]]; then
  error ".env 文件不存在，请先创建（参考 .env.example）"
  exit 1
fi

# 导出 .env 中的变量（忽略注释和空行）
set -o allexport
# shellcheck source=.env
source "$ENV_FILE"
set +o allexport

info "已加载配置文件: $ENV_FILE"
info "  CMS_PORT=$CMS_PORT  MINIO_ENDPOINT=$MINIO_ENDPOINT  STORAGE_BACKEND=$STORAGE_BACKEND"

# ── 构建前端（可选）─────────────────────────────────────
if [[ "${1:-}" == "--build" ]]; then
  info "构建前端..."
  cd "$SCRIPT_DIR"
  pnpm build
  success "前端构建完成 → dist/"
fi

# ── 检查 dist/ 是否存在 ──────────────────────────────────
if [[ ! -f "$SCRIPT_DIR/dist/index.html" ]]; then
  warn "dist/index.html 不存在，正在构建前端..."
  cd "$SCRIPT_DIR"
  pnpm build
  success "前端构建完成"
fi

# ── 检查端口是否已占用 ───────────────────────────────────
check_port() {
  local port=$1
  if ss -tlnp 2>/dev/null | grep -q ":$port "; then
    return 0  # 已占用
  fi
  return 1   # 空闲
}

PROXY_PORT="${CMS_PORT:-8081}"
UPLOAD_PORT="${UPLOAD_PORT:-8788}"
DB_PORT="${DB_PORT:-8789}"

# ── 清空旧 PID 文件 ──────────────────────────────────────
> "$PID_FILE"

# ── 启动 upload-server ───────────────────────────────────
if check_port "$UPLOAD_PORT"; then
  warn "upload-server 端口 $UPLOAD_PORT 已被占用，跳过启动（使用现有进程）"
else
  info "启动 upload-server (port $UPLOAD_PORT)..."
  cd "$SCRIPT_DIR"
  node --env-file="$ENV_FILE" server/upload-server.mjs \
    > /tmp/cms-upload-server.log 2>&1 &
  UPLOAD_PID=$!
  echo "$UPLOAD_PID" >> "$PID_FILE"
  sleep 1
  if kill -0 "$UPLOAD_PID" 2>/dev/null; then
    success "upload-server 已启动 (PID $UPLOAD_PID)"
  else
    error "upload-server 启动失败，查看日志: /tmp/cms-upload-server.log"
    tail -20 /tmp/cms-upload-server.log >&2
    exit 1
  fi
fi

# ── 启动 db-server ───────────────────────────────────────
if check_port "$DB_PORT"; then
  warn "db-server 端口 $DB_PORT 已被占用，跳过启动（使用现有进程）"
else
  info "启动 db-server (port $DB_PORT)..."
  cd "$SCRIPT_DIR"
  node --env-file="$ENV_FILE" server/db-server.mjs \
    > /tmp/cms-db-server.log 2>&1 &
  DB_PID=$!
  echo "$DB_PID" >> "$PID_FILE"
  sleep 1
  if kill -0 "$DB_PID" 2>/dev/null; then
    success "db-server 已启动 (PID $DB_PID)"
  else
    error "db-server 启动失败，查看日志: /tmp/cms-db-server.log"
    tail -20 /tmp/cms-db-server.log >&2
    exit 1
  fi
fi

# ── 等待后端服务就绪 ─────────────────────────────────────
info "等待后端服务就绪..."
for i in $(seq 1 10); do
  upload_ok=$(curl -sf "http://localhost:$UPLOAD_PORT/health" -o /dev/null && echo "y" || echo "n")
  db_ok=$(curl -sf "http://localhost:$DB_PORT/health" -o /dev/null && echo "y" || echo "n")
  if [[ "$upload_ok" == "y" && "$db_ok" == "y" ]]; then
    success "后端服务就绪 (upload ✓, db ✓)"
    break
  fi
  [[ $i -eq 10 ]] && { error "后端服务未能在 10 秒内就绪"; exit 1; }
  sleep 1
done

# ── 启动代理服务器 ───────────────────────────────────────
if check_port "$PROXY_PORT"; then
  warn "代理端口 $PROXY_PORT 已被占用，请先运行 ./start-uat.sh stop"
  exit 1
fi

info "启动反向代理 (port $PROXY_PORT)..."
cd "$SCRIPT_DIR"

# 使用环境变量方式（不用 --env-file，避免覆盖已有变量）
PROXY_PORT="$PROXY_PORT" \
UPLOAD_PORT="$UPLOAD_PORT" \
DB_PORT="$DB_PORT" \
MINIO_HOST="${MINIO_ENDPOINT:-192.168.31.33}" \
MINIO_PORT="${MINIO_PORT:-9000}" \
  node server/proxy-server.mjs &
PROXY_PID=$!
echo "$PROXY_PID" >> "$PID_FILE"

sleep 1
if ! kill -0 "$PROXY_PID" 2>/dev/null; then
  error "代理服务器启动失败"
  exit 1
fi

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         EduAsset CMS UAT 环境启动成功！              ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║  访问地址：http://192.168.31.33:${PROXY_PORT}              ║${NC}"
echo -e "${GREEN}║  日志文件：/tmp/cms-upload-server.log                ║${NC}"
echo -e "${GREEN}║            /tmp/cms-db-server.log                   ║${NC}"
echo -e "${GREEN}║  停止服务：./start-uat.sh stop                       ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
echo ""

# 等待代理进程（前台运行）
wait "$PROXY_PID"
