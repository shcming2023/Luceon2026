# ============================================================
# EduAsset CMS — 服务端 Dockerfile（共用镜像）
#
# 支持的服务：
#   upload-server.mjs  ── 文件上传代理（端口 8788）
#   db-server.mjs      ── SQLite REST API（端口 8789）
#
# 通过 docker-compose 的 command 字段选择启动哪个服务：
#   command: ["node", "server/upload-server.mjs"]
#   command: ["node", "server/db-server.mjs"]
# ============================================================

FROM node:20-alpine

WORKDIR /app

# 仅复制服务端依赖声明（避免复制整个前端项目）
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./

# 安装 pnpm 并仅安装生产依赖
RUN corepack enable && corepack prepare pnpm@latest --activate \
  && pnpm install --frozen-lockfile --prod

# 复制服务端源码
COPY server/upload-server.mjs ./server/
COPY server/db-server.mjs ./server/

# 默认启动上传服务（db-server 通过 docker-compose command 覆盖）
EXPOSE 8788 8789

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://localhost:8788/health || wget -qO- http://localhost:8789/health || exit 1

CMD ["node", "server/upload-server.mjs"]
