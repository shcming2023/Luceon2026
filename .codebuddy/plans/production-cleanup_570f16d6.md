---
name: production-cleanup
overview: 为生产发布整理项目：删除冗余文件和测试文件，补全必要配置，更新所有技术文档与代码注释，确保 Docker 镜像自包含。
todos:
  - id: delete-files
    content: 删除冗余文件：package-lock.json、PLAYWRIGHT.md、UPLOAD_DEBUG_REPORT.md、UATReport/、test/、e2e/、playwright.config.ts、server/test-mineru.mjs、docs/、guidelines/、dist/
    status: completed
  - id: clean-package-json
    content: 清理 package.json：删除 3 个 E2E 脚本和 @playwright/test devDependency
    status: completed
    dependencies:
      - delete-files
  - id: fix-configs
    content: 补全 .env.example（MinIO 变量）、.dockerignore（e2e/、guidelines/、docs/）、.gitignore（dist/ 确认）
    status: completed
    dependencies:
      - delete-files
  - id: fix-code-comments
    content: 修正 App.tsx 路由注释和 Layout.tsx 版本号（v0.1.0→v0.0.1）及 Token 逻辑注释；补充 server/upload-server.mjs 头部服务注释
    status: completed
  - id: update-docs
    content: 使用 [subagent:code-explorer] 扫描当前路由和类型，更新说明文档.md（架构图加入 db-server、路由表、技术栈表）和 DEPLOY.md（架构图加入 db-server、MinIO 环境变量配置说明）
    status: completed
    dependencies:
      - fix-code-comments
---

## 用户需求

整理整个项目，为生产发布（Docker 镜像交付）做最终清理和准备，具体包括：

## 产品概述

EduAsset CMS（Luceon2026）是一个教育资产管理平台，含三个子系统：EduAsset CMS、Overleaf 备份系统、LaTeX 工具。目标是将代码仓库整理为可直接提交给生产部门构建 Docker 镜像的干净状态。

## 核心功能（需完成的整理工作）

### 1. 删除冗余文件

- `package-lock.json`（项目使用 pnpm，npm lock 文件冗余）
- `PLAYWRIGHT.md`、`UPLOAD_DEBUG_REPORT.md`（调试文档）
- `UATReport/` 目录（UAT 评审报告，非生产必要）
- `test/` 目录（测试 PDF 文件）
- `e2e/` 目录 + `playwright.config.ts`（E2E 测试，非生产）
- `server/test-mineru.mjs`（测试脚本）
- `docs/` 目录（内部调试文档）
- `guidelines/Guidelines.md`（开发规范文档）
- `dist/` 目录（构建产物，应由 Docker 构建时生成）

### 2. 清理 package.json

- 删除 E2E 脚本：`test:e2e`、`test:e2e:ui`、`test:e2e:headed`
- 删除 `@playwright/test` devDependency

### 3. 补全配置文件

- `.env.example`：补充 MinIO 相关环境变量（`STORAGE_BACKEND`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`、`MINIO_BUCKET`、`MINIO_PRESIGNED_EXPIRY`）
- `.dockerignore`：补充 `e2e/`、`guidelines/`、`docs/`、`*.md`（调试报告）等忽略项
- `.gitignore`：补充 `dist/` 构建产物（如缺失则补充）

### 4. 更新代码注释

- `src/app/App.tsx`：路由注释补全，标注各路由所属子系统及完成状态
- `src/app/components/Layout.tsx`：Token SPA 跳转逻辑注释补全，版本号从 `v0.1.0` 更新为 `v0.0.1`

### 5. 更新技术文档

- `说明文档.md`：更新架构图（加入 db-server 服务说明）、路由表（与当前 App.tsx 一致）、技术栈表（补充 Express、better-sqlite3、MinIO SDK、MUI）、进度记录（与实际状态一致）
- `DEPLOY.md`：补充 db-server 服务说明（当前架构图缺失该服务），更新部署步骤，补充 MinIO 相关环境变量配置说明

## 技术栈

项目已有完整技术栈（无需变更），本次为纯整理/文档更新任务：

- **前端**：React 18 + TypeScript + Vite 6 + Tailwind CSS 4 + shadcn/ui + React Router 7
- **服务端**：Node.js ESM + Express 5 + better-sqlite3 + MinIO SDK + multer + JSZip
- **容器化**：Docker 多阶段构建 + Docker Compose + Nginx
- **包管理**：pnpm（lockfile 为 `pnpm-lock.yaml`）

## 实施策略

本次任务为**生产发布前收尾整理**，不涉及功能变更，分三个层面执行：

1. **文件删除**：删除所有调试/测试/构建产物文件，降低镜像构建上下文体积和仓库噪声
2. **配置修正**：补全 `.env.example` 中缺失的 MinIO 变量，完善 `.dockerignore` 和 `.gitignore` 覆盖范围
3. **注释与文档同步**：使代码注释、`说明文档.md`、`DEPLOY.md` 与当前实际代码状态完全一致

### 关键发现（探索结果）

- **`.env.example` 缺失 MinIO 变量**：当前文件没有 `STORAGE_BACKEND`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`、`MINIO_BUCKET`、`MINIO_PRESIGNED_EXPIRY`，但 `docker-compose.yml` 和 `upload-server.mjs` 都引用了这些变量，必须补充
- **`.dockerignore` 缺少 `e2e/`、`guidelines/`、`docs/`**：虽然这些目录将被删除，但 `.dockerignore` 补充后更安全，即使文件被误恢复也不会进入镜像
- **`说明文档.md` 架构图缺少 `db-server`**：当前文档未提及 SQLite REST API 服务，但 `docker-compose.yml` 和 `server/db-server.mjs` 均已实现
- **`说明文档.md` 路由表过时**：文档列出了已删除的路由（`/process-workbench`、`/products`、`/tasks`），当前 `App.tsx` 实际路由为 7 条（含 backup 子路由）
- **`Layout.tsx` 版本号显示 `v0.1.0`**：与 `package.json` 中的 `0.0.1` 不一致，需修正
- **`说明文档.md` 技术栈表缺少服务端技术**：Express、better-sqlite3、MinIO SDK 均未列出
- **`server/upload-server.mjs` 头部无注释**：与其他文件风格不一致，需补充服务头部注释
- **`DEPLOY.md` 架构图缺少 `db-server`**：当前架构图只有 `cms-frontend`、`upload-server`、`minio`，缺少 `db-server`

## 实施注意事项

- **不修改任何业务逻辑代码**，所有变更仅限于注释、文档和文件删除
- `dist/` 目录虽在 `.gitignore` 中列为忽略（构建输出），但实际上该目录存在于工作区，需显式删除（`git rm -r --cached dist/` 或直接删除目录）
- `package-lock.json` 删除前确认 `pnpm-lock.yaml` 完整存在（已确认存在）
- 版本号统一使用 `package.json` 中的 `0.0.1`

## 目录结构变更

```
project-root/
├── package.json              # [MODIFY] 删除 3 个 E2E 脚本、删除 @playwright/test devDep
├── .env.example              # [MODIFY] 补充 MinIO 相关环境变量（5个）
├── .dockerignore             # [MODIFY] 补充 e2e/、guidelines/、docs/ 忽略项
├── .gitignore                # [MODIFY] 确认/补充 dist/ 条目
├── 说明文档.md                # [MODIFY] 更新架构图、技术栈表、路由表、进度记录
├── DEPLOY.md                 # [MODIFY] 补充 db-server 到架构图、补充 MinIO 环境变量说明
├── src/app/App.tsx           # [MODIFY] 路由注释完善（子系统标注+完成状态标注）
├── src/app/components/Layout.tsx  # [MODIFY] 版本号 v0.1.0 → v0.0.1，注释补全
├── server/upload-server.mjs  # [MODIFY] 补充头部服务说明注释
│
│── package-lock.json         # [DELETE] npm lock，项目用 pnpm
│── PLAYWRIGHT.md             # [DELETE] 调试文档
│── UPLOAD_DEBUG_REPORT.md    # [DELETE] 调试报告
│── UATReport/                # [DELETE] 整个目录
│── test/                     # [DELETE] 测试 PDF 文件目录
│── e2e/                      # [DELETE] E2E 测试目录
│── playwright.config.ts      # [DELETE] E2E 配置
│── server/test-mineru.mjs    # [DELETE] 测试脚本
│── docs/                     # [DELETE] 内部调试文档目录
│── guidelines/               # [DELETE] 开发规范文档目录
└── dist/                     # [DELETE] 构建产物（由 Docker 构建时生成）
```

## Agent 扩展

### SubAgent

- **code-explorer**
- 用途：在执行文档更新前，扫描 `src/app/pages/`、`src/store/types.ts`、`vite.config.ts` 等文件，确认当前路由列表、数据类型定义和构建配置的精确状态，确保 `说明文档.md` 更新内容与代码完全一致
- 预期结果：获取当前所有页面路由的准确列表及各页面完成状态，作为文档更新的可信依据