# EduAsset CMS — Docker 部署说明

## 一、架构概览

```
宿主机
│
├── Docker: cms-frontend（Nginx，对外端口 ${CMS_PORT:-8080}）
│   ├── 托管前端静态文件（/cms/）
│   ├── /__proxy/upload/   → upload-server:8788
│   ├── /__proxy/db/       → db-server:8789
│   ├── /api/              → host.docker.internal:3001（备份后端，可选）
│   ├── /__proxy/mineru-cdn/ → cdn-mineru.openxlab.org.cn
│   ├── /__proxy/mineru/   → mineru.net
│   ├── /__proxy/kimi/     → api.kimi.ai
│   ├── /__proxy/moonshot/ → api.moonshot.cn
│   └── /__proxy/tmpfiles/ → tmpfiles.org（MinIO 降级 fallback）
│
├── Docker: upload-server（Node.js，内部端口 8788）
│   ├── 接收文件上传（最大 200 MB），转存到 MinIO
│   ├── MinIO 不可用时自动降级到 tmpfiles.org
│   └── 提供 MinerU 解析结果转存接口
│
├── Docker: db-server（Node.js，内部端口 8789）
│   ├── SQLite REST API（完整 CRUD）
│   ├── 数据库：/data/cms.db（数据卷 cms-db-data 持久化）
│   └── 支持表：materials、asset_details、process_tasks、tasks、products、flexible_tags、ai_rules、settings
│
├── Docker: minio（MinIO 对象存储）
│   ├── MinIO API：内部端口 9000
│   ├── MinIO Web 控制台：9001（可对外暴露用于管理）
│   └── 数据卷：cms-minio-data
│
└── [可选] backup-backend（独立部署，非本仓库）
    └── 提供 /api/* 接口（宿主机默认端口 3001）
```

**数据持久化层次**：
- `cms-minio-data` 卷 → 上传的原始文件（PDF、图片等）
- `cms-db-data` 卷 → 业务数据（资料库、标签、规则等 SQLite 数据库）
- 浏览器 `localStorage` → 当前会话缓存，启动时优先从 db-server 加载

---

## 二、三个子系统说明

### 2.1 教育资产管理系统（EduAsset CMS）

**功能**：管理教育资料从原始文件到成品的完整生命周期。

**核心流水线**：原始资料 → MinerU 解析（Rawcode）→ AI 清洗（Cleancode）→ 成品

**依赖的外部服务**：

| 服务 | 地址 | 用途 | 配置方式 |
|------|------|------|---------|
| MinerU API | `https://mineru.net` | PDF/文档 OCR 解析 | 系统设置页面 → MinerU 配置 |
| Kimi/Moonshot | `https://api.moonshot.cn` | AI 识别与标注 | 系统设置页面 → AI 配置 |
| MinIO | `minio:9000`（内部） | 持久化文件存储 | `.env` 中配置，upload-server 自动使用 |
| tmpfiles.org | `https://tmpfiles.org` | 临时文件中转（MinIO 降级方案） | 自动切换，无需配置 |

> MinerU API Key 和 Kimi API Key 在系统设置页面配置，保存到浏览器 localStorage，**不写入代码仓库**。

### 2.2 Overleaf 备份系统

**功能**：备份 Overleaf 实例的所有项目，支持灾备备份、文件浏览、定时调度。

**依赖**：需要独立的备份后端服务（`backup-backend`，不在本仓库），前端通过 `/api/*` 接口通信。

**认证方式**：请求头 `x-access-token`，可通过 URL 参数 `?token=xxx` 自动注入浏览器。

**访问地址**：`http://your-host:8080/cms/backup`

### 2.3 LaTeX 工具集

**功能**：对 LaTeX ZIP 压缩包进行图片去冗余和大图压缩（>1MB 压缩至 ≤1MB）。

**特点**：完全在浏览器本地处理，**无需后端，无网络请求**。

**访问地址**：`http://your-host:8080/cms/backup/latex`

---

## 三、快速部署步骤

### 前置要求

- 宿主机已安装 Docker 20.10+ 和 Docker Compose v2
- 已克隆本仓库到宿主机

### 步骤

```bash
# 1. 进入项目目录
cd /path/to/Luceon2026

# 2. 复制并配置环境变量
cp .env.example .env
# 用编辑器修改 .env，至少检查并设置以下关键项：
#   CMS_PORT            — 前端对外端口（默认 8080）
#   MINIO_ACCESS_KEY    — MinIO 访问密钥（生产环境请替换）
#   MINIO_SECRET_KEY    — MinIO 私钥（生产环境请替换）
#   OVERLEAF_ACCESS_TOKEN — 如需备份功能请填写

# 3. 构建并启动全部服务
docker compose up -d --build

# 4. 验证服务状态（应显示 healthy）
docker compose ps

# 5. 查看各服务日志
docker compose logs -f cms-frontend
docker compose logs -f upload-server
docker compose logs -f db-server

# 6. 访问应用
open http://localhost:8080/cms/
```

### 停止服务

```bash
docker compose down
```

### 更新部署

```bash
git pull
docker compose up -d --build
```

---

## 四、环境变量说明

完整的环境变量列表见 `.env.example`，以下是关键配置项说明：

### 通用配置

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `CMS_PORT` | `8080` | 前端对外暴露的宿主机端口 |
| `UPLOAD_PORT` | `8788` | 上传服务内部端口（通常无需修改） |
| `TZ` | `Asia/Shanghai` | 容器时区 |

### MinIO 对象存储

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `STORAGE_BACKEND` | `minio` | 存储后端：`minio`（私有存储）或 `tmpfiles`（公开临时存储） |
| `MINIO_ENDPOINT` | `minio` | MinIO 服务端点（Docker 内部 DNS，通常无需修改） |
| `MINIO_PORT` | `9000` | MinIO API 端口 |
| `MINIO_ACCESS_KEY` | `minioadmin` | **生产环境请替换为强密钥** |
| `MINIO_SECRET_KEY` | `minioadmin` | **生产环境请替换为强密钥** |
| `MINIO_BUCKET` | `eduassets` | 存储桶名称（首次启动自动创建） |
| `MINIO_PRESIGNED_EXPIRY` | `3600` | 预签名 URL 有效期（秒），控制文件临时访问时间 |

### Overleaf 备份系统

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `OVERLEAF_BASE_URL` | `http://host.docker.internal:80` | Overleaf 实例地址 |
| `OVERLEAF_ADMIN_EMAIL` | — | Overleaf 管理员邮箱 |
| `OVERLEAF_ADMIN_PASSWORD` | — | Overleaf 管理员密码 |
| `HOST_BACKUP_PATH` | — | 备份文件宿主机存储路径（绝对路径） |
| `OVERLEAF_ACCESS_TOKEN` | — | 备份系统 API Token（见第五节） |

### AI 服务（前端直连，非容器内）

| 变量名 | 说明 |
|--------|------|
| `MINERU_API_KEY` | MinerU API Key，也可在系统设置页面配置 |
| `KIMI_API_KEY` | Kimi/Moonshot API Key，也可在系统设置页面配置 |

---

## 五、API Token 配置（Overleaf 备份）

备份系统使用 `x-access-token` 请求头认证，有两种注入方式：

**方式一（推荐）**：通过 URL 参数自动写入浏览器 localStorage：

```
http://your-host:8080/cms/?token=YOUR_TOKEN_HERE
```

访问后 Token 自动保存，后续无需重复传入。支持任意页面进入方式：
- 硬刷新带 token 参数 → `main.tsx` 启动时处理
- SPA 内部跳转带 token 参数（如外部链接跳转到 `/backup`）→ `Layout.tsx` 自动捕获，并清除 URL 中的 token 参数

**方式二**：在系统设置页面（`/cms/settings`）的"连接设置" Tab 中查看当前 Token 状态。

---

## 六、Overleaf 备份后端配置

### 方式 A：后端在宿主机独立运行（推荐）

备份后端已在宿主机独立运行（监听 3001 端口），`docker/nginx.conf` 中代理配置默认为：

```nginx
location /api/ {
    proxy_pass http://host.docker.internal:3001/;
}
```

修改后重启前端容器：

```bash
docker compose restart cms-frontend
```

### 方式 B：后端作为 Docker 服务运行

取消注释 `docker-compose.yml` 中的 `backup-backend` 服务块，并确保 `docker/nginx.conf` 中 `/api/` 指向 `http://backup-backend:3001/`。

---

## 七、生产环境安全建议

1. **修改 MinIO 默认密钥**：将 `.env` 中 `MINIO_ACCESS_KEY` 和 `MINIO_SECRET_KEY` 替换为强密钥（随机字符串）
2. **MinIO 控制台端口**：如不需要对外暴露，可在 `docker-compose.yml` 中移除 `9001` 端口映射
3. **HTTPS**：建议在 Docker 前面加 Caddy 或 Nginx 反向代理处理 HTTPS：
   ```
   Internet → Caddy（443）→ Docker cms-frontend（8080）
   ```
4. **Token 有效期**：根据实际需要调整 `MINIO_PRESIGNED_EXPIRY`（文件访问有效期）

---

## 八、常见问题

**Q: 访问 `/cms/` 后页面空白？**
A: 检查 `docker compose logs cms-frontend`，确认 Nginx 启动正常。打开浏览器 Console 查看 JS 错误。

**Q: 文件上传失败？**
A: 检查 `docker compose logs upload-server`。访问 `http://localhost:8080/__proxy/upload/health` 验证服务健康。

**Q: MinIO 无法启动？**
A: 检查 `docker compose logs minio`，确认数据卷 `cms-minio-data` 可用。MinIO 默认密钥为 `minioadmin`，请通过 `.env` 修改。

**Q: 文件上传时 MinIO 连接失败？**
A: 确认 `docker compose ps minio` 显示 healthy。MinIO 不可用时 upload-server 会自动降级到 tmpfiles.org（临时存储，有效期 24 小时）。

**Q: 如何访问 MinIO Web 控制台？**
A: 浏览器访问 `http://localhost:9001`，使用 `.env` 中配置的 `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` 登录。

**Q: 如何切换存储后端？**
A: 修改 `.env` 中 `STORAGE_BACKEND=minio`（MinIO）或 `STORAGE_BACKEND=tmpfiles`（临时存储），然后 `docker compose restart upload-server`。

**Q: MinerU 解析超时？**
A: 在系统设置页面增大 MinerU 超时时间（默认 1200 秒），或检查宿主机网络是否能访问 `https://mineru.net`。

**Q: Overleaf 备份 API 返回 401/403？**
A: 通过 URL `?token=xxx` 重新注入 Token，或检查备份后端的 Token 配置。

**Q: Overleaf 备份 API 返回 503？**
A: 备份后端未启动，或 `docker/nginx.conf` 中的代理地址不正确。

**Q: Docker 构建失败（better-sqlite3 native 编译）？**
A: `server/Dockerfile` 已包含 `python3`、`make`、`g++` 等 native 编译依赖，确保 Docker 版本 ≥ 20.10，或尝试 `docker compose build --no-cache`。

**Q: LaTeX 工具处理失败？**
A: LaTeX 工具完全在浏览器本地运行。如果失败，检查上传的 ZIP 文件是否包含 `.tex` 文件和 `images/` 目录。
