# ============================================================
# EduAsset CMS — 前端应用 Dockerfile（多阶段构建）
#
# 阶段1 (builder): 使用 Node.js + pnpm 构建 Vite 静态产物
# 阶段2 (runtime): 使用 Nginx Alpine 托管静态文件并反向代理 API
# ============================================================

# ─── 阶段1：构建 ────────────────────────────────────────────
FROM node:20-alpine AS builder

# 安装 pnpm（与项目保持一致的包管理器）
RUN corepack enable && corepack prepare pnpm@latest --activate

WORKDIR /app

# 先复制依赖声明文件，充分利用 Docker 层缓存
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./

# 安装所有依赖（包括 devDependencies，构建时需要）
RUN pnpm install --frozen-lockfile

# 复制全部源码
COPY . .

# 执行 Vite 生产构建
# 构建产物输出到 /app/dist（base: '/cms/'）
RUN pnpm run build

# ─── 阶段2：运行时 ──────────────────────────────────────────
FROM nginx:1.27-alpine AS runtime

# 删除 Nginx 默认配置
RUN rm /etc/nginx/conf.d/default.conf

# 复制自定义 Nginx 配置
COPY docker/nginx.conf /etc/nginx/conf.d/cms.conf

# 从构建阶段复制静态产物到 Nginx 托管目录
COPY --from=builder /app/dist /usr/share/nginx/html/cms

# 暴露 HTTP 端口
EXPOSE 80

# 健康检查：确认 Nginx 正常响应
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://localhost/cms/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
