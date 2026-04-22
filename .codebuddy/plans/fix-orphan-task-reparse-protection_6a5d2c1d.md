---
name: fix-orphan-task-reparse-protection
overview: 修复孤儿任务与 Reparse 破坏完成态问题：在 reparseTask 增加前置校验（Material存在性、objectName存在性、MinIO原始对象存在性），保护已完成任务的完成态不被破坏，删除Material时完善级联策略，前端任务详情页按资源状态禁用危险动作按钮。
todos:
  - id: add-pre-validation
    content: 重构 task-actions-routes.mjs：reparseTask/reAiTask/retryTask 增加 Material 和 MinIO 文件前置校验，registerTaskActionRoutes 增加 deps 参数
    status: completed
  - id: wire-deps-upload-server
    content: 修改 upload-server.mjs：registerTaskActionRoutes 调用处传入 deps（getMinioClient/getMinioBucket/getStorageBackend/getParsedBucket）
    status: completed
    dependencies:
      - add-pre-validation
  - id: fix-frontend-delete-path
    content: 统一 WorkspacePage.tsx 删除路径为级联删除接口 DELETE /__proxy/upload/materials/:id
    status: completed
  - id: task-detail-resource-aware
    content: TaskDetailPage.tsx 加载关联 Material 并根据资源状态动态禁用动作按钮，显示资源缺失提示
    status: completed
  - id: enhance-consistency-audit
    content: consistency-routes.mjs 增加 resourceLost 标记策略：已完成但原文件缺失的任务标记为 canceled+resourceLost 而非 failed
    status: completed
  - id: update-docs-and-verify
    content: 更新说明文档.md 进度记录，Vite 生产构建验证
    status: completed
    dependencies:
      - add-pre-validation
      - wire-deps-upload-server
      - fix-frontend-delete-path
      - task-detail-resource-aware
      - enhance-consistency-audit
---

## 产品概述

修复"已完成任务因 Reparse 被降级为失败"的核心缺陷，以及相关联的数据一致性防护不足问题

## 核心功能

1. **Reparse 前置校验**：reparseTask 执行前必须校验 Material 存在、objectName 存在、MinIO 原始对象真实存在；任一校验失败返回 409，不得修改任务状态
2. **Reparse 不破坏完成态**：对 completed/review-pending 任务重跑前必须先通过资源校验；retryTask 同理需校验资源存在性
3. **统一前端删除路径为级联删除**：WorkspacePage 当前使用 `/__proxy/db/materials` batch DELETE 不走级联，需统一为 `DELETE /__proxy/upload/materials/:id` 级联删除路径
4. **任务详情页资源感知按钮禁用**：Material 不存在时禁用 Reparse/Retry/Re-AI；原始 object 缺失时禁用 Reparse/Retry；Markdown 产物缺失时禁用 Re-AI；显示明确提示文案
5. **一致性审计增强**：对孤儿任务提供明确修复策略（删除而非仅标记 failed）；对已完成但原文件缺失的任务标记为"结果可查看但不可重跑"

## 技术栈

- 后端：Node.js + Express 5.x（现有 `server/` 目录下的 `.mjs` 模块）
- 前端：React 18 + TypeScript + Tailwind CSS（现有 `src/` 目录）
- 存储：MinIO SDK + db-server JSON REST API
- 构建工具：Vite 6.x

## 实施方案

### 核心策略

采用"前置校验 + 资源感知"双层防护策略：

1. **后端拦截层**：在 `reparseTask`/`reAiTask`/`retryTask` 执行前增加 Material 存在性和 MinIO 文件存在性校验，校验失败直接返回 409 Conflict，不触碰任务状态
2. **前端防护层**：TaskDetailPage 加载时获取关联 Material 信息，根据资源存在性动态禁用动作按钮并显示提示
3. **删除统一层**：所有前端删除 Material 的路径统一走后端级联删除接口

### 关键技术决策

- **reparseTask 前置校验依赖注入**：参考 `registerConsistencyRoutes(app, deps)` 的模式，给 `registerTaskActionRoutes` 增加 `deps` 参数，注入 `getMinioClient`/`getMinioBucket`/`getStorageBackend`/`getParsedBucket`，避免在 task-actions-routes.mjs 中重复初始化 MinIO 客户端
- **已完成任务保护**：不采用"快照恢复"复杂方案，而是简单直接的前置校验——校验不通过则不修改状态，从根本上杜绝"成功降级为失败"
- **前端删除统一**：使用已有的 `DELETE /__proxy/upload/materials/:id` 级联接口，逐个调用（Material 删除通常是少量操作，串行可接受），避免新增批量级联接口增加复杂度
- **一致性审计新增 `resource-lost` 标记**：对已完成但原文件缺失的任务，在 repair 中标记为 `canceled` 并附加 `resourceLost: true` 元数据，前端据此显示"结果可查看但不可重跑"提示，而非标记为普通 failed

## 实施注意事项

- **registerTaskActionRoutes 签名变更**：增加 `deps` 参数后，需同步修改 `upload-server.mjs` 中的调用点，传入与 `registerConsistencyRoutes` 相同的 deps 对象
- **WorkspacePage 删除路径**：从 batch DB DELETE 改为逐个调用级联 DELETE，需处理部分失败场景（某个 ID 删除失败不应阻塞其余）
- **TaskDetailPage 额外请求**：加载任务详情时需额外请求 Material 信息，可复用现有的 `fetch(/__proxy/db/materials/${materialId})`，404 时标记为不存在
- **reAiTask 已有部分校验**：当前已检查 `task.metadata?.markdownObjectName`，需补充 Material 存在性校验和 MinIO 文件存在性校验
- **性能考虑**：MinIO statObject 调用在 Reparse 前置校验中仅执行一次，不构成性能瓶颈

## 目录结构

```
project-root/
├── server/
│   └── lib/
│       └── task-actions-routes.mjs    # [MODIFY] 重构 reparseTask/reAiTask/retryTask 增加前置校验；registerTaskActionRoutes 增加 deps 参数
├── server/
│   └── upload-server.mjs             # [MODIFY] registerTaskActionRoutes 调用处传入 deps 对象
├── src/app/pages/
│   └── TaskDetailPage.tsx            # [MODIFY] 加载关联 Material 信息，根据资源状态动态禁用动作按钮，显示资源缺失提示
├── src/app/pages/
│   └── WorkspacePage.tsx             # [MODIFY] deleteMaterials 改用级联删除接口
├── server/lib/
│   └── consistency-routes.mjs        # [MODIFY] orphan-task 修复策略增加 resourceLost 标记；已完成但原文件缺失的任务标记为 canceled + resourceLost
└── 说明文档.md                         # [MODIFY] 更新进度记录
```