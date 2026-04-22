# EduAsset CMS — UAT 测试指南

**默认目标环境：** `http://localhost:8081`（可通过环境变量覆盖）  
**MinIO 控制台：** `http://localhost:9001`

---

## 一、部署方式

### 方式 A：Docker 部署（推荐）

在宿主机上执行：

```bash
# 1. 进入项目目录
cd /path/to/Luceon2026

# 2. 确认 .env 已配置（CMS_PORT=8081 已设置）
cat .env | grep CMS_PORT

# 3. 启动所有服务
docker compose up -d --build

# 4. 查看服务状态
docker compose ps

# 5. 验证部署
./uat/smoke-test.sh
```

**全新部署（清空所有数据）：**

```bash
docker compose down -v          # 停止并删除数据卷
docker compose up -d --build    # 重新构建并启动
```

### 方式 B：直接 Node.js 部署（开发容器内，无 Docker 环境）

适用于 CI 容器或无 Docker CLI 的环境：

```bash
# 一键构建并启动（前台运行，Ctrl+C 停止）
./start-uat.sh --build

# 仅启动（dist/ 已存在时）
./start-uat.sh

# 停止所有服务
./start-uat.sh stop

# 查看服务日志
tail -f /tmp/cms-upload-server.log
tail -f /tmp/cms-db-server.log
tail -f /tmp/cms-proxy-server.log
```

> **注意：** 方式 B 中服务运行在容器内部，需要宿主机侧 Docker 将容器端口 `8081` 映射到宿主机，
> 才能从局域网访问。若容器已通过 `-p 8081:8081` 映射，则无需额外操作。

---

## 二、关键配置（`.env`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CMS_PORT` | `8081` | 对外暴露端口 |
| `CMS_HOST` | `localhost` | 对外展示的主机名（启动信息中使用） |
| `MINIO_ENDPOINT` | `localhost` | MinIO 主机（容器内可达地址） |
| `MINIO_PUBLIC_ENDPOINT` | — | presigned URL 公开地址（如 `http://YOUR_HOST:8081/minio`） |
| `MINIO_ACCESS_KEY` | `minioadmin` | ⚠️ 生产环境请更换强密码 |
| `MINIO_SECRET_KEY` | `minioadmin` | ⚠️ 生产环境请更换强密码 |

---

## 三、冒烟测试（快速验证）

部署完成后运行：

```bash
./uat/smoke-test.sh

# 或指定目标地址
BASE_URL=http://YOUR_HOST:8081 ./uat/smoke-test.sh
```

**检查项（9 项）：**

| # | 检查项 | 预期结果 |
|---|--------|---------|
| 1 | 根路径重定向 `/` | HTTP 302 → `/cms/` |
| 2 | CMS 主页 `/cms/` | HTTP 200，HTML 内容正常 |
| 3 | SPA 路由 `/cms/source-materials` | HTTP 200 |
| 4 | `upload-server` 健康检查 | `{"ok":true}` |
| 5 | `db-server` 健康检查 | `{"ok":true}` |
| 6 | DB API `/materials` | HTTP 200，JSON 数组 |
| 7 | DB API `/settings` | HTTP 200 |
| 8 | MinIO 代理 `/minio/minio/health/live` | HTTP 200 |
| 9 | MinIO 控制台 `:9001` | HTTP 200 |

---

## 四、自动化 E2E 测试（Playwright）

```bash
cd uat
npm install
npx playwright install chromium

# 运行所有测试（默认 localhost:8081）
npx playwright test

# 指定目标地址和公网主机名
BASE_URL=http://YOUR_HOST:8081 PUBLIC_HOST=YOUR_HOST npx playwright test

# 有头模式（可见浏览器）
npx playwright test --headed

# 只运行特定测试组
npx playwright test --grep "MinIO"

# 查看 HTML 报告
npx playwright show-report playwright-report
```

**环境变量说明：**

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `BASE_URL` | `http://localhost:8081` | 测试目标地址 |
| `PUBLIC_HOST` | — | presigned URL 应包含的公网主机名（如 `192.168.31.33`），未设置时跳过主机名匹配 |

**测试套件覆盖范围：**

| 测试组 | 测试内容 |
|--------|---------|
| 【1】页面加载与 SPA 路由 | 根路径重定向、各页面可访问性 |
| 【2】后端服务健康检查 | `upload-server`、`db-server` 健康端点 |
| 【3】DB API 基础功能 | 素材列表、设置读写（含自动清理） |
| 【4】MinIO Nginx 代理 | `/minio/` 可达性、presigned URL 地址验证 |
| 【5】文件上传流程 | 文件上传、presigned URL 局域网可访问性 |
| 【6】页面导航交互 | 核心路由 SPA 切换无错误 |

---

## 五、手动验证清单

- [ ] 浏览器打开 `http://YOUR_HOST:8081`，自动跳转到 `/cms/source-materials`
- [ ] 页面正常渲染，侧边栏导航可切换
- [ ] 在「原始资料库」页面上传一个 PDF/图片文件
- [ ] 上传成功后文件可在列表中显示，预览可正常加载
- [ ] 检查文件 URL 格式为 `http://YOUR_HOST:8081/minio/...`（非 `minio:9000`）
- [ ] 添加/修改数据后刷新页面，数据保持不变
- [ ] 访问「系统设置」→「测试 MinIO 连接」显示成功
- [ ] 访问 `http://YOUR_HOST:9001` 可登录 MinIO 控制台

---

## 六、常见问题排查

### Docker 部署模式

```bash
docker compose ps                              # 查看容器状态
docker compose logs -f upload-server           # 实时日志
docker compose restart upload-server           # 重启单个服务
```

### Node.js 直接部署模式

```bash
cat /tmp/cms-upload-server.log                 # upload-server 日志
cat /tmp/cms-db-server.log                     # db-server 日志
./start-uat.sh stop && ./start-uat.sh --build  # 重启所有服务
```

### MinIO 文件无法打开（presigned URL 报 403）

```bash
grep MINIO_PUBLIC_ENDPOINT .env   # 确认配置正确
grep MINIO_ENDPOINT .env          # 确认 MinIO 主机地址可达
curl http://YOUR_MINIO_HOST:9000/minio/health/live  # 验证 MinIO 可达
```

---

## 七、架构说明

```
局域网浏览器
    │
    ▼ http://YOUR_HOST:8081
┌─────────────────────────────────────────┐
│  Nginx (Docker) / proxy-server.mjs (Node)│
│  /cms/                → 静态文件 dist/   │
│  /__proxy/upload/*    → upload-server    │
│  /__proxy/db/*        → db-server        │
│  /minio/*             → MinIO:9000       │
│  /__proxy/mineru-local → :8083           │
│  /__proxy/tmpfiles    → tmpfiles.org     │
└─────────────────────────────────────────┘
         │                     │
    ┌────┘                     └────┐
    ▼                               ▼
upload-server (8788)          db-server (8789)
    │                               │
    ▼                               ▼
MinIO (MINIO_HOST:9000)       db-data.json
```

**MinIO presigned URL 修复原理：**

```
上传文件
  → upload-server 调用 MinIO SDK
  → SDK 生成: http://minio:9000/eduassets/originals/xxx.pdf?X-Amz-...
  → rewritePresignedUrl() 替换为:
             http://YOUR_HOST:8081/minio/eduassets/originals/xxx.pdf?X-Amz-...
  → 浏览器 GET http://YOUR_HOST:8081/minio/...
  → 代理转发到 MinIO:9000
  → 文件正常加载 ✓
```
