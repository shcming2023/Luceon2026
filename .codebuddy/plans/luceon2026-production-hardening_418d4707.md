---
name: luceon2026-production-hardening
overview: 针对 Luceon2026 生产化准备，重点修复 SSRF 漏洞、收紧安全边界、升级输入校验、清理历史遗留，让项目从"可演示"提升到"可内网生产运行"的安全状态。
todos:
  - id: fix-ssrf-upload-server
    content: 修复 upload-server.mjs：ossUrl SSRF 校验、aiEndpoint SSRF 防御、zipUrl 域名检查、补充原型链防护中间件
    status: completed
  - id: fix-db-server
    content: 修复 db-server.mjs：升级原型链防御为递归深度检查、settings key 白名单、增加内网 Token 校验中间件
    status: completed
  - id: fix-proxy-server
    content: 修复 proxy-server.mjs：db 路由注入内网 token、serveStatic basePath 越界检查、rejectUnauthorized 改为环境变量控制、修正过时注释
    status: completed
    dependencies:
      - fix-db-server
  - id: verify-and-push
    content: 验证 TypeScript 编译和 lint 无误，使用 [mcp:GitHub MCP Server] push_files 推送全部修改到 origin/main
    status: completed
    dependencies:
      - fix-ssrf-upload-server
      - fix-db-server
      - fix-proxy-server
---

## 用户需求

对 Luceon2026 项目进行生产化修订，结合独立评审进行交叉核对与独立分析，针对安全漏洞、边界收口、代码质量等问题提出并执行修订方案。

## 产品概述

Luceon2026 是一个面向教育资料处理的内网 CMS 系统，核心链路包括前端 React SPA、proxy-server 反向代理、upload-server 业务服务、db-server JSON 持久化服务和 MinIO 对象存储。当前系统功能成熟、容错设计强，但安全边界偏松、历史演进未完全收口，需在保持现有功能不破坏的前提下完成生产加固。

## 核心修复内容

**P0 安全修复（必须）**

- 修复 `/parse/oss-put` SSRF：对 `ossUrl` 增加协议、域名白名单、私网 IP 拦截校验
- 修复 `/parse/analyze` SSRF：对 `aiApiEndpoint` 增加协议校验和私网地址拦截（允许白名单域名或本地 Ollama 模式）

**P1 安全加固（优先）**

- `proxy-server.mjs` 对 `/__proxy/db/*` 路由增加内网来源校验或共享密钥保护，防止 db-server 完全裸露
- 收紧运行时 CORS：upload-server 和 db-server 从 `cors()` 无参改为允许配置的来源白名单
- `proxy-server.mjs` 的 HTTPS 代理恢复 `rejectUnauthorized: true`，或通过环境变量控制
- 升级 db-server 原型链污染防御为深度遍历校验，同时在 upload-server 补充同等防护

**P2 质量收口（建议）**

- `serveStatic()` 增加路径越界检查（basePath 白名单）
- `/parse/download` 对 `zipUrl` 增加域名校验
- `PUT /settings/:key` 对 key 增加白名单限制
- 修正 `proxy-server.mjs` 顶部注释（路径已变更但注释未更新）
- `proxy-server.mjs` 启动日志修正（仍显示旧路由规则）

## 技术栈

- **后端**：Node.js ESM（`.mjs`），Express（upload-server/db-server），原生 http 模块（proxy-server）
- **前端**：React + TypeScript + Vite（不在本轮修改范围）
- **安全工具**：纯手工校验函数，不引入新依赖（保持零新依赖原则，避免锁文件变化）

## 实施策略

采用**最小侵入原则**：所有修改集中在 3 个服务器文件，不触碰前端逻辑，不引入新 npm 包，通过环境变量控制行为（生产严格、开发宽松），保持向后兼容。

**核心技术决策**：

1. SSRF 防御用纯函数实现（不依赖第三方库），对 ossUrl 检查协议（仅 `https:`）+ 域名后缀白名单（`*.aliyuncs.com`、`*.oss-cn-*.aliyuncs.com`）+ 私网 IP 正则拦截
2. aiEndpoint SSRF 采用双层防御：协议白名单（`http/https`）+ 私网地址拦截 + 可选的 `ALLOWED_AI_HOSTS` 环境变量白名单（空=不限制外部域，允许用户自带 key 的产品设计）
3. CORS 改为读取 `CORS_ORIGIN` 环境变量，默认值 `http://localhost:5173` 覆盖开发场景，生产部署时设为具体内网地址
4. db-server 路由保护：增加 `X-Internal-Token` 请求头校验中间件，proxy-server 转发时自动注入，token 由 `INTERNAL_API_TOKEN` 环境变量配置（未配置时跳过检查，保持向后兼容）
5. 原型链污染升级为递归键名检查函数，替换当前字符串 includes 方案，同时在 upload-server 的写操作前复用同一函数

## 实施说明

- **不破坏现有功能**：SSRF 校验函数对 MinerU 返回的真实 OSS URL（`*.oss-cn-*.aliyuncs.com`）必须放行；对 Ollama 本地 endpoint（`http://localhost:11434`）需通过环境变量豁免
- **环境变量优先**：所有安全策略通过环境变量开关控制，便于内网分层部署
- **basePath 检查**：使用 `path.resolve()` 后与 `DIST_DIR` 做前缀比较，防止路径穿越
- **日志不泄露**：错误日志中截断 URL（仅显示前 80 字符），不输出被拦截的完整 URL

## 架构设计

```mermaid
graph TD
    subgraph proxy-server 8081
        A[客户端请求] --> B{路由匹配}
        B -->|/__proxy/db/*| C[内网 Token 校验]
        C -->|通过| D[db-server 8789]
        C -->|拒绝| E[401]
        B -->|/__proxy/upload/*| F[upload-server 8788]
        B -->|静态文件| G[serveStatic + basePath 检查]
    end
    subgraph upload-server 8788
        F --> H[/parse/oss-put]
        H --> I{ossUrl SSRF 校验}
        I -->|白名单通过| J[PUT to OSS]
        I -->|拒绝| K[400]
        F --> L[/parse/analyze]
        L --> M{aiEndpoint 校验}
        M -->|通过| N[call AI]
        M -->|拒绝| O[400]
    end
    subgraph db-server 8789
        D --> P[原型链污染递归检查]
        P --> Q[数据读写]
    end
```

## 目录结构

```
server/
├── upload-server.mjs   [MODIFY] 修复 ossUrl SSRF、aiEndpoint SSRF、补充原型链防护、/parse/download zipUrl 校验
├── db-server.mjs       [MODIFY] 升级原型链防御为递归检查、settings key 白名单、内网 token 中间件
└── proxy-server.mjs    [MODIFY] db 路由增加 token 注入、serveStatic basePath 检查、修正注释、rejectUnauthorized 配置
```

## 使用的 Agent Extensions

### MCP

- **GitHub MCP Server**
- 用途：在所有修改完成并验证通过后，将本地修改推送到 GitHub 远程仓库
- 期望结果：以单次多文件提交将三个服务器文件的修改同步到 `origin/main`，提交信息清晰描述安全加固内容