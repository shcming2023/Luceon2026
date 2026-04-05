# EduAsset CMS — Mac Mini Docker 部署说明

## 一、架构概览

```
Mac Mini 宿主机
│
├── Docker 容器：cms-frontend（Nginx，端口 8080）
│   ├── 托管前端静态文件（/cms/）
│   ├── 反向代理 /__proxy/upload → upload-server:8788
│   ├── 反向代理 /api → backup-backend（Overleaf 备份后端）
│   └── 反向代理 /__proxy/mineru、/__proxy/kimi 等外部 AI API
│
├── Docker 容器：upload-server（Node.js，内部端口 8788）
│   └── 接收文件上传，转发到 tmpfiles.org（供 MinerU 使用）
│
└── Overleaf 备份后端（独立部署，非本仓库代码）
    └── 提供 /api/* 接口供前端调用（默认端口 3001）
```

---

## 二、三个子系统说明

### 2.1 教育资产管理系统（EduAsset CMS）

**功能**：管理教育资料从原始文件到成品的完整生命周期。

**核心流水线**：原始资料 → MinerU 解析（Rawcode）→ AI 清洗（Cleancode）→ 成品

**依赖的外部服务**：

| 服务 | 地址 | 用途 | 配置方式 |
|------|------|------|---------|
| MinerU API | `https://mineru.net` | PDF/文档解析 | 系统设置页面 → AI/MinerU 配置 |
| Kimi/Moonshot | `https://api.moonshot.cn` | AI 识别与标注 | 系统设置页面 → AI 配置 |
| tmpfiles.org | `https://tmpfiles.org` | 临时文件中转 | 自动（通过 upload-server） |

**注意**：MinerU API Key 和 Kimi API Key 请在系统设置页面配置，配置自动保存到浏览器 localStorage，不会存入代码仓库。

### 2.2 Overleaf 备份系统

**功能**：备份 Overleaf 实例的所有项目到本地目录，支持灾备备份、文件浏览、定时调度。

**依赖**：需要独立的备份后端服务（`backup-backend`，不在本仓库中），前端通过 `/api/*` 接口与其通信。

**认证方式**：通过 `x-access-token` 请求头传递 Token，可通过 URL 参数 `?token=xxx` 自动写入浏览器。

**访问方式**：`http://your-mac-mini:8080/cms/backup`

### 2.3 LaTeX 图片去重压缩系统

**功能**：对 LaTeX ZIP 压缩包进行图片去冗余（删除未引用图片）和大图压缩（>1MB 压缩到 ≤1MB），**完全在浏览器本地处理，无需后端，无网络请求**。

**访问方式**：`http://your-mac-mini:8080/cms/backup/latex`

---

## 三、快速部署步骤

### 前置要求

- Mac Mini 已安装 Docker Desktop 或 [OrbStack](https://orbstack.dev)（推荐，更省资源）
- 已克隆本仓库到 Mac Mini

### 步骤

```bash
# 1. 进入项目目录
cd /path/to/Ui

# 2. 复制并配置环境变量
cp .env.example .env
# 用编辑器打开 .env，至少填写 CMS_PORT（默认 8080）

# 3. 构建并启动服务
docker compose up -d --build

# 4. 查看服务状态（应显示 healthy）
docker compose ps

# 5. 查看日志
docker compose logs -f cms-frontend
docker compose logs -f upload-server

# 6. 访问应用
open http://localhost:8080/cms/
```

### 停止服务

```bash
docker compose down
```

### 更新部署（代码有变更时）

```bash
git pull
docker compose up -d --build
```

---

## 四、Overleaf 备份后端配置

Overleaf 备份系统需要独立的后端服务。有两种接入方式：

### 方式A：后端在 Mac Mini 宿主机上独立运行（推荐）

如果备份后端已在 Mac Mini 上独立运行（如直接运行 Node.js 进程，监听 3001 端口），修改 `docker/nginx.conf` 中的代理地址：

```nginx
location /api/ {
    # Docker 容器内访问宿主机服务使用 host.docker.internal
    proxy_pass http://host.docker.internal:3001/;
    ...
}
```

修改后重启前端容器：

```bash
docker compose restart cms-frontend
```

### 方式B：后端作为 Docker 服务运行

如果希望用 Docker 统一管理备份后端，取消注释 `docker-compose.yml` 中的 `backup-backend` 服务块，并填写正确的构建路径和环境变量。同时确保 `docker/nginx.conf` 中 `/api/` 指向 `http://backup-backend:3001/`。

---

## 五、API Token 配置（Overleaf 备份）

备份后端生成 Token 后，有两种方式传入前端：

**方式一（推荐）**：通过 URL 参数自动写入浏览器 localStorage：

```
http://your-mac-mini:8080/cms/?token=YOUR_TOKEN_HERE
```

访问后 Token 自动保存，后续无需重复传入。

**方式二**：在系统设置页面（`/cms/settings`）的"连接设置" Tab 中查看当前 Token 状态（只读展示，从后端读取）。

---

## 六、生产环境 HTTPS 配置（可选）

如需在 Mac Mini 上配置 HTTPS，建议在 Docker 容器前面加一层 [Caddy](https://caddyserver.com) 反向代理：

```
Internet → Caddy（443）→ Docker cms-frontend（8080）
```

Caddy 配置示例（`/etc/caddy/Caddyfile`）：

```
your-domain.com {
    reverse_proxy localhost:8080
}
```

---

## 七、常见问题

**Q: 访问 `/cms/` 后页面空白？**
A: 检查 `docker compose logs cms-frontend`，确认 Nginx 启动正常。如果是 JS 错误，打开浏览器开发者工具查看 Console。

**Q: 文件上传失败（MinerU 解析时）？**
A: 检查 `docker compose logs upload-server`，确认上传服务健康。也可访问 `http://localhost:8080/__proxy/upload/health` 验证。

**Q: MinerU 解析超时？**
A: 在系统设置页面增大 MinerU 超时时间（默认 1200 秒），或检查 Mac Mini 的网络能否访问 `https://mineru.net`。

**Q: Overleaf 备份 API 返回 503？**
A: 备份后端未启动或 nginx.conf 中的代理地址不正确。检查后端是否运行，并确认 `docker/nginx.conf` 中的 `proxy_pass` 地址正确。

**Q: Overleaf 备份 API 返回 401/403？**
A: 通过 URL `?token=xxx` 重新注入 Token，或检查备份后端的 Token 配置。

**Q: Docker 构建失败（pnpm 相关）？**
A: 确保 Docker 版本 ≥ 20.10，或尝试 `docker compose build --no-cache`。

**Q: LaTeX 工具页面处理失败？**
A: LaTeX 工具完全在浏览器本地运行，不依赖后端。如果失败，检查上传的 ZIP 文件格式是否正确（需包含 `.tex` 文件和 `images/` 目录）。
