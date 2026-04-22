#!/usr/bin/env bash
# ============================================================
#  EduAsset CMS — 聚合测试入口
#
#  按顺序执行：smoke → server-unit → e2e
#  任一阶段失败则终止并报错。
#
#  用法：
#    pnpm test                          # 使用默认地址
#    BASE_URL=http://YOUR_HOST:8081 pnpm test
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

GREEN="\033[32m"; RED="\033[31m"; CYAN="\033[36m"; NC="\033[0m]

BASE_URL="${BASE_URL:-http://localhost:8081}"
EXIT_CODE=0

echo -e "${CYAN}============================================================${RESET}"
echo -e "${CYAN}  EduAsset CMS 聚合测试${NC}"
echo -e "${CYAN}  目标地址：${BASE_URL}${NC}"
echo -e "${CYAN}  时间：$(date '+%Y-%m-%d %H:%M:%S')${NC}"
echo -e "${CYAN}============================================================${NC}"
echo ""

# ── 阶段 1：冒烟测试 ────────────────────────────────────────
echo -e "${CYAN}【阶段 1/3】冒烟测试 (smoke)${NC}"
echo ""
cd "$PROJECT_DIR"
if BASE_URL="$BASE_URL" bash uat/smoke-test.sh; then
  echo -e "${GREEN}✓ 冒烟测试通过${NC}"
else
  echo -e "${RED}✗ 冒烟测试失败，跳过后续阶段${NC}"
  exit 1
fi
echo ""

# ── 阶段 2：服务端单元测试 ──────────────────────────────────
echo -e "${CYAN}【阶段 2/3】服务端单元测试 (server)${NC}"
echo ""
cd "$PROJECT_DIR"
if node server/tests/worker-smoke.mjs; then
  echo -e "${GREEN}✓ 服务端测试通过${NC}"
else
  echo -e "${RED}✗ 服务端测试失败${NC}"
  EXIT_CODE=1
fi
echo ""

# ── 阶段 3：E2E 测试 ───────────────────────────────────────
echo -e "${CYAN}【阶段 3/3】E2E 测试 (playwright)${NC}"
echo ""
cd "$PROJECT_DIR/uat"
if BASE_URL="$BASE_URL" npx playwright test; then
  echo -e "${GREEN}✓ E2E 测试通过${NC}"
else
  echo -e "${RED}✗ E2E 测试失败${NC}"
  EXIT_CODE=1
fi
echo ""

# ── 汇总 ────────────────────────────────────────────────────
echo -e "${CYAN}============================================================${NC}"
if [[ $EXIT_CODE -eq 0 ]]; then
  echo -e "${GREEN}  ✅ 所有测试通过${NC}"
else
  echo -e "${RED}  ❌ 部分测试失败，请查看上方日志${NC}"
fi
echo -e "${CYAN}============================================================${NC}"

exit $EXIT_CODE
