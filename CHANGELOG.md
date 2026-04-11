# Changelog

## [v0.6.1] - 2026-04-11

### 🚀 稳定性与健壮性提升 (Stability & Robustness)

本次更新基于深度代码评审，重点修复了系统在极端情况下的稳定性隐患，优化了内存使用，并清理了冗余依赖。

#### 服务端 (Server)
- **优雅停机 (Graceful Shutdown)**: `db-server` 和 `upload-server` 新增了对 `SIGTERM` 和 `SIGINT` 信号的处理。在容器重启或意外退出时，`db-server` 会强制同步内存中的防抖数据到磁盘，避免数据丢失；`upload-server` 会等待进行中的请求完成。
- **内存优化 (OOM Prevention)**: `upload-server` 的 `multer` 存储引擎从 `memoryStorage` 迁移至 `diskStorage`。大文件上传时不再将完整内容驻留内存，而是使用临时文件缓冲，并在处理完成后自动清理，彻底解决了并发上传大文件时的 OOM 风险。
- **输入验证 (Input Validation)**: `db-server` 新增了基础的请求体验证中间件，拦截非对象请求体和原型链污染攻击，防止脏数据破坏内存缓存。

#### 前端 (Frontend)
- **静默失败提示 (Error Notification)**: 优化了 `appContext.tsx` 中的 `db-server` 同步逻辑。当后端服务不可用时，不再完全静默失败，而是在连续失败 3 次后通过 `sonner` 弹窗提示用户，并在服务恢复后自动通知。
- **全局错误恢复 (Error Boundary)**: 增强了 `ErrorBoundary` 组件，新增了错误堆栈展示和“重试”按钮，允许用户在不刷新页面的情况下尝试恢复组件状态。

#### 配置与依赖 (Config & Dependencies)
- **依赖清理 (Dependency Cleanup)**: 移除了 `package.json` 中未使用的 `@mui/material`、`@emotion/react` 和 `better-sqlite3` 依赖，减小了项目体积和构建时间。
- **Nginx 缓存修复 (Nginx Cache)**: 修复了 `docker/nginx.conf` 中静态资源长期缓存的 `alias` 路径映射问题，改用 `root` 指令确保带 hash 的 JS/CSS 文件能被正确缓存。
- **安全加固 (Security)**: 
  - 移除了 `vite.config.ts` 中已废弃的 `X-Frame-Options`，简化了 `allowedHosts` 配置。
  - 更新了 `.env.example` 和 `docker-compose.yml`，强制要求用户在生产环境中修改 MinIO 的默认密钥 (`minioadmin`)。
- **类型修复 (TypeScript)**: 修复了 `types.ts` 和 `appContext.tsx` 中遗留的 TypeScript 类型报错，为配置接口添加了索引签名。
