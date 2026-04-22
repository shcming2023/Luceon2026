#!/usr/bin/env bash
# ============================================================
# EduAsset CMS — 冒烟测试脚本
#
# 用途：部署后快速验证所有服务链路是否正常
# 依赖：curl（通常系统自带）
# 用法：
#   chmod +x uat/smoke-test.sh
#   ./uat/smoke-test.sh                         # 使用默认目标地址
#   BASE_URL=http://192.168.31.33:8081 ./uat/smoke-test.sh
# ============================================================

set -euo pipefail

BASE_URL="${BASE_URL:-http://192.168.31.33:8081}"
PASS=0
FAIL=0
SKIP=0

GREEN="\033[32m"
RED="\033[31m"
YELLOW="\033[33m"
CYAN="\033[36m"
RESET="\033[0m"

echo -e "${CYAN}============================================================${RESET}"
echo -e "${CYAN}  EduAsset CMS 冒烟测试${RESET}"
echo -e "${CYAN}  目标地址：${BASE_URL}${RESET}"
echo -e "${CYAN}  时间：$(date '+%Y-%m-%d %H:%M:%S')${RESET}"
echo -e "${CYAN}============================================================${RESET}"
echo ""

# ── 测试函数 ─────────────────────────────────────────────────

check() {
  local name="$1"
  local url="$2"
  local expected_status="${3:-200}"
  local expected_body="${4:-}"

  printf "  %-52s" "[$name]"

  local http_code
  local body
  body=$(curl -s -o /tmp/smoke_body -w "%{http_code}" \
    --max-time 10 \
    --connect-timeout 5 \
    "$url" 2>/dev/null) || { http_code="000"; }
  http_code="${body:-000}"
  local resp_body
  resp_body=$(cat /tmp/smoke_body 2>/dev/null || echo "")

  if [[ "$http_code" == "$expected_status" ]]; then
    if [[ -n "$expected_body" ]] && ! echo "$resp_body" | grep -qi "$expected_body"; then
      echo -e "${RED}✗ FAIL${RESET} (HTTP $http_code, body 不含 '$expected_body')"
      echo "    响应内容: $(echo "$resp_body" | head -c 200)"
      FAIL=$((FAIL + 1))
    else
      echo -e "${GREEN}✓ PASS${RESET} (HTTP $http_code)"
      PASS=$((PASS + 1))
    fi
  else
    echo -e "${RED}✗ FAIL${RESET} (期望 HTTP $expected_status, 实际 $http_code)"
    if [[ -n "$resp_body" ]]; then
      echo "    响应内容: $(echo "$resp_body" | head -c 200)"
    fi
    FAIL=$((FAIL + 1))
  fi
}

check_redirect() {
  local name="$1"
  local url="$2"

  printf "  %-52s" "[$name]"

  local http_code
  http_code=$(curl -s -o /dev/null -w "%{http_code}" \
    --max-time 10 \
    --connect-timeout 5 \
    "$url" 2>/dev/null) || http_code="000"

  if [[ "$http_code" == "301" || "$http_code" == "302" || "$http_code" == "200" ]]; then
    echo -e "${GREEN}✓ PASS${RESET} (HTTP $http_code)"
    PASS=$((PASS + 1))
  else
    echo -e "${RED}✗ FAIL${RESET} (期望 3xx/200, 实际 $http_code)"
    FAIL=$((FAIL + 1))
  fi
}

# ── 1. 前端访问 ───────────────────────────────────────────────
echo -e "${CYAN}【1】前端页面可达性${RESET}"
check_redirect "根路径重定向 /" "${BASE_URL}/"
check "CMS 主页 /cms/" "${BASE_URL}/cms/" "200" "<!doctype html"
check "SPA 路由 /cms/source-materials" "${BASE_URL}/cms/source-materials" "200" "<!doctype html"
echo ""

# ── 2. 后端健康检查 ──────────────────────────────────────────
echo -e "${CYAN}【2】后端服务健康检查（通过 Nginx 代理）${RESET}"
check "upload-server /health" \
  "${BASE_URL}/__proxy/upload/health" "200" '"ok":true'
check "db-server /health" \
  "${BASE_URL}/__proxy/db/health" "200" '"ok":true'
echo ""

# ── 3. DB API 基础功能 ────────────────────────────────────────
echo -e "${CYAN}【3】db-server REST API${RESET}"
check "获取素材列表 GET /materials" \
  "${BASE_URL}/__proxy/db/materials" "200"
check "获取设置 GET /settings" \
  "${BASE_URL}/__proxy/db/settings" "200"
echo ""

# ── 4. MinIO 代理可达性 ──────────────────────────────────────
echo -e "${CYAN}【4】MinIO 反向代理（/minio/）${RESET}"
# MinIO health 端点通过 Nginx /minio/ 代理访问
check "MinIO health via Nginx" \
  "${BASE_URL}/minio/minio/health/live" "200"
echo ""

# ── 5. MinIO 控制台（直接端口，仅 UAT 环境）─────────────────
echo -e "${CYAN}【5】MinIO 控制台（UAT 环境 9001 端口）${RESET}"
MINIO_CONSOLE_URL="${MINIO_CONSOLE_URL:-http://localhost:9001}"
printf "  %-52s" "[MinIO 控制台 $MINIO_CONSOLE_URL]"
http_code=$(curl -s -o /dev/null -w "%{http_code}" \
  --max-time 5 \
  --connect-timeout 3 \
  "$MINIO_CONSOLE_URL" 2>/dev/null) || http_code="000"
if [[ "$http_code" != "000" ]]; then
  echo -e "${GREEN}✓ PASS${RESET} (HTTP $http_code)"
  PASS=$((PASS + 1))
else
  echo -e "${YELLOW}⚠ SKIP${RESET} (控制台端口不可达，可忽略)"
  SKIP=$((SKIP + 1))
fi
echo ""

# ── 汇总 ─────────────────────────────────────────────────────
echo -e "${CYAN}============================================================${RESET}"
TOTAL=$((PASS + FAIL))
echo -e "  结果汇总：${GREEN}通过 $PASS${RESET} / ${RED}失败 $FAIL${RESET} / ${YELLOW}跳过 $SKIP${RESET} (共 $TOTAL 项)"
echo -e "${CYAN}============================================================${RESET}"
echo ""

if [[ $FAIL -gt 0 ]]; then
  echo -e "${RED}❌ 冒烟测试未通过，请检查上方失败项${RESET}"
  echo ""
  echo "  常见排查步骤："
  echo "  1. 确认服务已启动：./start-uat.sh --build（或先 pnpm build 再 ./start-uat.sh）"
  echo "  2. 查看 upload-server 日志：cat /tmp/cms-upload-server.log"
  echo "  3. 查看 db-server 日志：cat /tmp/cms-db-server.log"
  echo "  4. 确认 .env 中 CMS_PORT=8081 和 MINIO_PUBLIC_ENDPOINT 已正确配置"
  echo "  5. 确认 MinIO 在 \$MINIO_ENDPOINT:9000 可访问"
  exit 1
else
  echo -e "${GREEN}✅ 所有冒烟测试通过，系统运行正常${RESET}"
fi
