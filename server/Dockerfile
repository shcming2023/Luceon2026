# ============================================================
# EduAsset CMS — 上传代理服务 Dockerfile
#
# 负责接收前端上传的文件并转发到 tmpfiles.org
# 对外暴露 8788 端口（可通过 UPLOAD_PORT 环境变量覆盖）
# ============================================================

FROM node:20-alpine

WORKDIR /app

# 仅复制服务端依赖声明（避免复制整个前端项目）
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./

# 安装 pnpm 并仅安装生产依赖
RUN corepack enable && corepack prepare pnpm@latest --activate \
  && pnpm install --frozen-lockfile --prod

# 复制上传服务源码
COPY server/upload-server.mjs ./server/

# 暴露上传服务端口
EXPOSE 8788

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://localhost:8788/health || exit 1

CMD ["node", "server/upload-server.mjs"]
