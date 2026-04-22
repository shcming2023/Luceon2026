# Luceon2026 PRD v0.3 —— 任务式文档解析与元数据审核工作台

- 文档版本：v0.3（修订稿）
- 发布日期：2026-04-22
- 作者：Manus AI（基于项目现状与独立评审交叉核查后产出）
- 适用范围：Luceon2026 仓库（`shcming2023/Luceon2026`）下一阶段开发与联调
- 文档定位：**工程契约型 PRD**。本版本是一份独立自包含文档，替代 v0.2，不需要与 v0.2 合并阅读即可完整理解需求与约束。

## 目录
1. 产品背景与本版修订动机
2. 产品目标与非目标
3. 官方原型对标：MinerU Extractor
4. 当前基线事实（Baseline Facts）
5. 用户角色与主流程
6. 统一状态机（Canonical State Machine）
7. 数据模型与对象命名约定
8. API 契约清单（已实现 / 待补齐 / 待废弃）
9. 一致性不变量与修复动作（Invariants & Repair Actions）
10. 下一阶段开发任务（Scope of v0.3）
11. 明确不做的事项（Out of Scope）
12. 验收标准（UAT）与度量指标
13. 风险、回退与发布策略
14. 对独立评审意见的核查结论
15. 术语表
16. 变更记录

---

## 1. 产品背景与本版修订动机

Luceon2026 起源于一个面向本地教育资源（以 PDF、Markdown 为主）的资料库管理工具。在过去数个迭代中，项目经历了两次关键转向：

- **第一次转向（v0.1 → v0.2）**：从"文件列表 + 元数据编辑"的资料库工具，转向"带解析管线与 AI 元数据识别的内容加工系统"。引入 MinerU（本地 FastAPI / 云端 API）作为解析引擎，引入 Ollama / OpenAI 兼容 Provider 作为元数据识别后端。
- **第二次转向（v0.2 → v0.3，本版）**：从以 `Material`（资料）为核心的"对象视图"，转向以 `ParseTask`（解析任务）为核心的"任务视图"，对齐官方 MinerU Extractor 的任务式交互范式。

项目进入 v0.3 阶段时，代码现状已经跑在 PRD v0.2 描述的"中后段"：Docker Compose、MinIO、`db-server`、`POST /tasks` 统一入口、`ParseTaskWorker`、`AiMetadataWorker`、Markdown 直通、AI 回填均已实现，并在 Mac Mini Docker 环境完成过端到端验证。但同时暴露出几个结构性问题：

1. **状态机事实上由多个模块共同解释**：前端列表页、资产详情页、`ParseTaskWorker`、`AiMetadataWorker`、`db-server` 各自对状态字符串做判断与映射（例如前端把 `success`、`ai-pending`、`completed` 都计入"已完成"，而 AI 回调又把 `confirmed` 映射为 `completed`），没有单一事实来源。
2. **新旧两条链路共存**：`POST /tasks` 已是新主入口，但 `POST /parse/analyze`、`POST /parse/local-mineru`、`POST /parse/download` 等旧端点仍与 `Material.metadata` 直接耦合，前端资产详情页的 `handleAiAnalyze` 仍走旧链路。
3. **ID 约定不一致**：`db-server` 已按字符串 ID 进行路由与存取，但 `materials` 列表排序仍按数字比较，部分一致性扫描脚本与旧代码仍隐含"数字 ID"假设。
4. **PRD v0.2 为"愿景型"而非"契约型"**：把"增加任务模型、队列、Worker、AI Job"列为下一阶段开发任务，但这些在代码上均已实现，PRD 已落后于代码。

因此 v0.3 的修订动机是：**把 PRD 从"愿景型"升级为"工程契约型"**。把当前事实清清楚楚写下来，把字段与状态收敛成唯一契约，把下一阶段开发任务改为"稳定状态机、补齐 Retry/Re-AI、完善任务详情视图、修复一致性扫描"，让开发、测试、部署三方围绕同一份可验证文档工作。

## 2. 产品目标与非目标

### 2.1 产品目标

Luceon2026 在 v0.3 阶段的产品目标，用一句话表述为：

> **复刻官方 MinerU Extractor 的任务式交互，服务于本地教育资源的解析与元数据审核。**

具体拆解为以下四条：

1. **任务式主流程**：用户与系统的主要交互对象是"任务（Task）"而不是"资料（Material）"。用户的心智是"我创建了一个解析任务 → 我看着它在队列里推进 → 我查看它的结果 → 失败了我重试 → 成功了我导出"。
2. **可见的管线状态**：任意时刻，任意任务的状态都可以在任务列表与任务详情页被准确看到，不会出现"前端说在处理、后端其实已经失败"这类漂移。
3. **可控的失败恢复**：任何一步失败都不会让任务"消失"或"永远卡住"。系统既有 Worker 侧的超时自愈，也有用户侧的 Retry / Reparse / Re-AI 显式操作。
4. **可审可用的 AI 元数据**：AI 识别结果以结构化字段写回 `Material`，并对低置信度结果进入 `review-pending` 状态，等待人工在任务详情页审核确认。

### 2.2 非目标（本版不做）

v0.3 明确不承担以下职责，避免再次出现"PRD 堆功能、工程跟不上"的偏差：

- 不引入新的大模型 Provider，不重写 Prompt 编排框架。现有 Ollama / OpenAI 兼容 Provider 足够。
- 不做多租户、多用户权限、审计日志这类企业化能力。
- 不做 Markdown 在线富文本编辑，仅提供只读预览与元数据表单编辑。
- 不做前端的大规模架构重构（例如从 Context + Zustand 混合状态迁移到完全服务端状态）。本版聚焦于"让现状稳定、让契约清晰"。

## 3. 官方原型对标：MinerU Extractor

本项目的参考原型为 [MinerU Extractor](https://mineru.net/OpenSourceTools/Extractor)。对标的重点不是 API 细节，也不是 UI 像素级复刻，而是**任务式交互体验**。从原型中可以清晰看到以下要点：

- 左侧导航以 **Create Task / Tasks / My Collections** 三段为主，Tasks 是一等公民。
- 创建任务支持 **Upload File** 与 **Web Link** 两种入口，并在入口处展示配额与限制（"5k/day、≤200p/file"）。
- 任务创建后进入队列，Tasks 页面列出每个任务的状态、进度与结果入口。
- 结果可被查看、导出（Markdown、JSON、ZIP）、加入 Collections 以便复用。
- 任务是独立的可寻址资源，可以被点开查看详情、失败可重试。

Luceon2026 v0.3 继承该范式，但做以下本地化裁剪：

| 维度 | 官方 MinerU Extractor | Luceon2026 v0.3 |
| :--- | :--- | :--- |
| 输入源 | 文件上传、Web 链接 | 仅文件上传（PDF 与 Markdown 直通） |
| 任务组织 | Create Task / Tasks / Collections | 资料库 / 任务管理 / 任务详情 |
| 引擎 | MinerU 云端服务 | 本地 MinerU FastAPI（首选） + 云端 MinerU（回退） |
| AI 元数据 | 不提供 | 提供（Ollama / OpenAI 兼容，结果回写 Material） |
| 审核 | 不提供 | 提供（`review-pending` 状态 + 任务详情页表单审核） |
| 账号体系 | 登录强制 | 本地部署，默认无强制登录 |

## 4. 当前基线事实（Baseline Facts）

以下事实已在仓库 `shcming2023/Luceon2026` 当前 HEAD 中验证，v0.3 以此为不可回退的基线。

### 4.1 部署与基础设施

- Docker Compose 包含：Nginx（反代 8081 → Node.js 服务）、`upload-server`、`db-server`、MinIO、本地 MinerU FastAPI（外挂，通过 `host.docker.internal:8083`）。
- 对象存储使用 MinIO，分为 `raw`（原始文件）与 `parsed`（解析产物）两个 Bucket。Markdown 与切图写入 `parsed` Bucket，以 `parsed/{materialId}/...` 为前缀。
- 持久化由 `db-server` 提供：内存 `dbCache` + 防抖落盘 + 备份文件恢复，具备优雅停机与数据不丢失的保证。
- v0.7.0 里程碑在 Mac Mini（`192.168.31.33:8081`）完成 Docker 部署端到端验证。

### 4.2 统一任务入口

- `POST /tasks`（`upload-server.mjs`）已实现：接收 multipart 上传，统一完成
  1. 写入 MinIO（`originals/{materialId}/{fileName}`）。
  2. Upsert `Material`（状态置为 `processing`，带 `metadata.provider/bucket/objectName`）。
  3. 创建 `ParseTask`（`engine: 'local-mineru'`, `state: 'pending'`, `stage: 'upload'`, 携带合并后的 `optionsSnapshot`）。
- 前端上传 hook `useFileUpload.ts` 已切换为调用 `/__proxy/upload/tasks`，并在注释中明确说明"不再手动 upsert Material，由后端 `/tasks` 统一负责"。

### 4.3 解析与 AI Worker

- `ParseTaskWorker`（`server/services/queue/task-worker.mjs`）已实现：
  - 轮询 `state=pending` 的任务，调用本地 MinerU FastAPI（`/tasks` + `/tasks/{id}` + `/tasks/{id}/result`），将 Markdown 与切图写入 MinIO。
  - 对 Markdown 原始输入走直通路径（不调用 MinerU）。
  - 完成后推进任务至 `ai-pending`，并通过 `metadata-job-client.mjs` 创建 `AiMetadataJob`（带去重保护）。
- `AiMetadataWorker`（`server/services/ai/metadata-worker.mjs`）已实现：
  - 严格串行（每轮最多 1 个 job），按 `createdAt` 升序挑选 `pending` job。
  - 支持 stale-running 自愈（超过 `defaultTimeoutMs + 60s` 缓冲期重置为 `pending`）。
  - 支持 Ollama / OpenAI 兼容 Provider，按 `providers` 数组的 `enabled` 与 `priority` 选取。
  - 内容长度超过 32000 字符时截断并记录 `ai-content-truncated` 事件。
  - Provider 全部失败时 `degradeToSkeleton` 兜底，保证链路闭合。
- `AiMetadataWorker` 通过 `onComplete(job, update)` 将结果回填（`upload-server.mjs` 第 3377–3434 行）：
  - 写 `Material.aiStatus`（`confirmed | review-pending → analyzed`，其它 → `failed`），并把结果并入 `Material.metadata`。
  - 写 `ParseTask.state`（`confirmed → completed`，`review-pending → review-pending`，其它 → `failed`），并把结果写入 `ParseTask.metadata`。

### 4.4 数据与 ID

- `db-server` 中 `materials`、`tasks`、`aiMetadataJobs`、`taskEvents`、`settings`、`secrets` 均为一等资源，路由全部按 `String(req.params.id)` 处理。
- `materials` 列表 `GET /materials` 返回时仍按数字做排序（遗留），这是一致性扫描需要修复的点之一。
- ParseTask ID 形如 `task-{timestamp}`，Material ID 既可能是前端生成的数字字符串，也可能是 `mat-{timestamp}`。

### 4.5 已知遗留链路

- 旧端点仍然存在并被部分前端页面调用：
  - `POST /parse/local-mineru`：直接触发本地 MinerU 解析（非任务式）。
  - `POST /parse/analyze`：同步调用 AI 并直接改写 `Material.metadata`，不经由 `AiMetadataJob`。
  - `POST /parse/download`：将外部 ZIP 拉回并按 `parsed/{materialId}/` 前缀拆包到 MinIO。
- 资产详情页 `AssetDetailPage.tsx` 的 `handleMineruParse` 已迁移至 `/tasks`，但 `handleAiAnalyze` 仍走旧 `/parse/analyze`，写回目标仍是 `material.metadata`。

v0.3 不要求一次性删除这些旧端点，但要求把它们**明确标为遗留**，并在下一阶段改造到任务视图。

## 5. 用户角色与主流程

### 5.1 角色

本版本只定义一个角色：**本地操作员（Operator）**。负责上传资料、观察任务、审核 AI 结果、导出产物。

### 5.2 主流程

1. **上传与建任务**。Operator 在资料库页或任务管理页点击上传，前端通过 `/__proxy/upload/tasks` 发起 multipart 请求，后端同步完成 MinIO 落盘、Material upsert 与 ParseTask 创建，返回 `{ taskId, materialId }`。
2. **队列推进**。`ParseTaskWorker` 轮询 `pending` 任务，推进到 `running`，调用 MinerU 生成产物，切换到 `result-store` 写入 MinIO，完成后切换到 `ai-pending` 并创建 `AiMetadataJob`。
3. **AI 元数据**。`AiMetadataWorker` 串行处理 `pending` 的 AI Job，提取结构化元数据；高置信度直接终态 `confirmed`，低置信度进入 `review-pending`，全部失败时降级为骨架并进入 `failed`/`review-pending`（按 PRD 规则）。`onComplete` 回填 Material 与 ParseTask。
4. **审核**。Operator 在任务详情页看到 `review-pending` 的任务，直接查看 Markdown、JSON 与可编辑的元数据表单，确认或修改后将任务置为 `completed`。
5. **重试与重跑**。对于 `failed` 任务，Operator 可发起 Retry（整任务重跑）或 Reparse（仅解析阶段重跑）；对 `completed`/`review-pending` 任务可发起 Re-AI（仅 AI 阶段重跑）。
6. **导出**。Operator 在任务详情页或任务列表中下载解析 ZIP（`/parsed-zip`）、Markdown、JSON。

## 6. 统一状态机（Canonical State Machine）

本章是 v0.3 的核心契约，所有前后端组件必须严格遵守。不允许任何模块把多个状态合并解释、或引入未列入下表的状态字符串。

### 6.1 状态定义与写入责任

| 状态 | 含义 | 唯一合法写入方 | 典型驻留时长 |
| :--- | :--- | :--- | :--- |
| `uploading` | 前端正在上传文件，后端尚未返回 `/tasks` 响应 | 前端 upload hook（可选本地态，不落 `tasks` 表） | 秒级 |
| `pending` | 任务已入库，等待 `ParseTaskWorker` 调度 | `upload-server` `POST /tasks` | 秒级–分钟级 |
| `running` | `ParseTaskWorker` 正在调用 MinerU 并轮询结果 | `ParseTaskWorker` | 分钟级 |
| `result-store` | 解析完成，正在把 Markdown / 图片写入 MinIO | `ParseTaskWorker` | 秒级 |
| `ai-pending` | 解析产物已落盘，`AiMetadataJob` 已创建、待 AI Worker 拾取 | `ParseTaskWorker` | 秒级–分钟级 |
| `ai-running` | `AiMetadataWorker` 正在调用 Provider | `AiMetadataWorker` | 秒级–分钟级 |
| `review-pending` | AI 结果置信度低于阈值或 `needsReview=true`，待人工审核 | `AiMetadataWorker.onComplete` | 天级 |
| `completed` | 全链路完成（高置信度自动终态，或人工审核通过） | `AiMetadataWorker.onComplete` / 审核 API | 持久终态 |
| `failed` | 解析或 AI 阶段发生不可恢复错误（降级到骨架后仍视为失败） | 各 Worker 异常分支 / `onComplete` | 持久终态（可重试） |
| `canceled` | Operator 主动取消 | 取消 API | 持久终态 |

> 说明：`ai-running` 为 v0.3 新增的显式状态，用于把"AI 阶段正在跑"从 `ai-pending` 里分离出来。当前代码中 `AiMetadataJob.state` 已有 `running`，在 `ParseTask.state` 中暂以 `ai-pending` 表达，v0.3 要求补齐 `ai-running` 在 ParseTask 上的写入。

### 6.2 合法流转边

下列是唯一允许的状态迁移集合：

```
uploading   → pending
pending     → running | canceled | failed
running     → result-store | failed | canceled
result-store→ ai-pending | failed
ai-pending  → ai-running | canceled | failed
ai-running  → review-pending | completed | failed
review-pending → completed | failed | canceled
failed      → pending            （Retry / Reparse 触发）
completed   → ai-pending         （Re-AI 触发，仅清理 AI 产物与状态）
review-pending → ai-pending      （Re-AI 触发）
```

任何其它迁移都必须被视作一致性缺陷，由一致性扫描记录与修复。

### 6.3 前端展示桶（Display Buckets）

为了避免"前端自行把多个状态合并成一个 UI 桶"这种分散解释，v0.3 统一如下展示桶：

| 展示桶 | 包含状态 |
| :--- | :--- |
| 等待中 | `pending`, `ai-pending` |
| 处理中 | `running`, `result-store`, `ai-running` |
| 待审核 | `review-pending` |
| 已完成 | `completed` |
| 已失败 | `failed` |
| 已取消 | `canceled` |

前端可以基于上表做筛选，但禁止把 `review-pending` 或 `ai-pending` 计入"已完成"桶（v0.2 代码中存在这种误判，v0.3 要求修复）。

## 7. 数据模型与对象命名约定

### 7.1 Material

```
{
  id: string,                  // 前端数字字符串或 mat-{ts}
  title: string,
  fileName: string,
  fileSize: number,
  mimeType: string,
  status: 'processing' | 'analyzed' | 'failed' | 'idle',
  mineruStatus?: 'pending' | 'running' | 'done' | 'failed',
  aiStatus?: 'pending' | 'running' | 'analyzed' | 'failed',
  metadata: {
    provider: 'minio',
    bucket: string,
    objectName: string,                   // originals/{materialId}/{fileName}
    markdownObjectName?: string,          // parsed/{materialId}/full.md
    parsedPrefix?: string,                // parsed/{materialId}/
    aiJobId?: string,
    aiAnalyzedAt?: string,
    // AI 提取的结构化元数据（subject/grade/chapter/tags/summary 等）
    ...
  },
  createTime: number,
  updateTime: number
}
```

关键约定：

- `id` 一律按字符串处理。后端不再对 `materials` 做数字排序，改为按 `updateTime DESC` 排序。
- `metadata.objectName` 与 `metadata.markdownObjectName` 必须使用 `originals/{id}/…` 与 `parsed/{id}/…` 前缀，禁止使用 `originals/{taskId}/…` 以保证"同一资料的产物收敛在同一前缀下"。

### 7.2 ParseTask

```
{
  id: string,                  // task-{ts}
  materialId: string,
  engine: 'local-mineru' | 'cloud-mineru' | 'markdown-passthrough',
  stage: 'upload' | 'mineru' | 'result-store' | 'ai' | 'review' | 'done',
  state: <Canonical State>,
  progress: number,            // 0–100，按 stage 分段
  message?: string,
  optionsSnapshot: {
    localEndpoint, localTimeout, backend, ocrLanguage,
    enableOcr, enableFormula, enableTable, maxPages,
    material                    // 创建时的 Material 快照
  },
  metadata: {
    markdownObjectName?: string,
    parsedPrefix?: string,
    aiJobId?: string,
    aiCompletedAt?: string,
    ...AI 结果
  },
  retryOf?: string,             // 若由 Retry/Reparse 产生，指向前一个 task-id
  createdAt: string,
  updatedAt: string
}
```

### 7.3 AiMetadataJob

```
{
  id: string,                  // ai-job-{ts}-{rand}
  parseTaskId: string,
  materialId: string,
  state: 'pending' | 'running' | 'review-pending' | 'confirmed' | 'failed',
  progress: number,
  providerId?: 'ollama' | 'openai-compatible',
  model?: string,
  inputMarkdownObjectName?: string,
  result?: object,             // 归一化的结构化元数据
  confidence?: number,
  needsReview?: boolean,
  message?: string,
  createdAt: string,
  updatedAt: string
}
```

v0.3 要求把 `AiMetadataJob.state` 的终态命名统一为 `confirmed | review-pending | failed`，废弃 `succeeded` 字面量。`metadata-job-client.mjs` 的去重集合需相应更新为 `{ pending, running, confirmed, review-pending }`。

### 7.4 TaskEvent

`taskEvents` 用于记录任务生命周期中的关键事件（`task-created`、`mineru-started`、`mineru-completed`、`ai-provider-request-started/succeeded/failed`、`ai-content-truncated`、`ai-stale-running-recovered`、`retry-requested` 等）。`taskId` 允许为 ParseTask ID 或 AI Job ID，`taskType` 区分 `parse | ai`。

## 8. API 契约清单

下表分三类：**已实现并保留**、**v0.3 必须补齐**、**标记为遗留待废弃**。

### 8.1 已实现并保留

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| `POST` | `/tasks` | 上传 + 建 Material + 建 ParseTask 的唯一入口 |
| `GET` | `/tasks` | 任务列表（支持按 state 过滤） |
| `GET` | `/tasks/:id` | 任务详情 |
| `PATCH` | `/tasks/:id` | Worker 写状态、前端写审核结果 |
| `DELETE` | `/tasks` | 批量删除任务记录（不影响 Material） |
| `GET` | `/ai-metadata-jobs` | AI Job 列表 / 过滤 |
| `GET` | `/ai-metadata-jobs/:id` | AI Job 详情 |
| `PATCH` | `/ai-metadata-jobs/:id` | AI Worker 更新 |
| `POST` | `/task-events` | 追加事件 |
| `GET` | `/task-events` | 查询事件（按 taskId） |
| `GET` | `/presign` | 为对象签发同源 proxy URL |
| `GET` | `/proxy-file` | 流式读取 MinIO 对象，支持 Range |
| `POST` | `/parsed-zip` | 将 `parsed/{materialId}/` 打包为 ZIP 返回 |

### 8.2 v0.3 必须补齐

| 方法 | 路径 | 语义 |
| :--- | :--- | :--- |
| `POST` | `/tasks/:id/retry` | 将 `failed` 任务整体重跑：克隆出新 ParseTask（`retryOf` 指向原任务），重新进入 `pending`，并清理 `parsed/{materialId}/` 下属于原任务的残留产物 |
| `POST` | `/tasks/:id/reparse` | 仅重跑解析阶段：保留 Material 的原文件，直接把当前任务从 `failed`/`completed` 置回 `pending`，Worker 会重新生成 Markdown 与图片 |
| `POST` | `/tasks/:id/re-ai` | 仅重跑 AI 阶段：删除或置失效当前 `aiJobId`，创建新 AI Job，并把 ParseTask 置回 `ai-pending` |
| `POST` | `/tasks/:id/cancel` | 将 `pending`/`ai-pending`/`review-pending` 任务置为 `canceled`，通知 Worker 放弃拾取 |
| `POST` | `/tasks/:id/review` | 人工审核接口：接受修正后的元数据，写回 `Material.metadata`，并把任务置为 `completed` |

### 8.3 标记为遗留、v0.4 再下线

| 方法 | 路径 | 遗留原因 | 过渡期要求 |
| :--- | :--- | :--- | :--- |
| `POST` | `/parse/local-mineru` | 绕过任务模型直接触发解析 | v0.3 内部不再新增调用点，仅保留兼容 |
| `POST` | `/parse/analyze` | 直接改写 `Material.metadata`，不经由 AI Job | 同上；资产详情页的 `handleAiAnalyze` 在 v0.3 内迁移到 `/tasks/:id/re-ai` |
| `POST` | `/parse/download` | 为"云端 MinerU ZIP 导入"而设，与任务模型解耦 | 维持现状，未来接入云端引擎时并入 ParseTask |

## 9. 一致性不变量与修复动作

> 这一章不写"系统会自然保证"，而写"**可被验证的不变量 + 对应修复动作**"。一致性扫描脚本必须能对每一条不变量生成可执行的修复建议。

### 9.1 ID 与引用

| 不变量 | 修复动作 |
| :--- | :--- |
| `ParseTask.materialId` 必须对应存在的 `Material.id`（字符串严格相等） | 若 Material 缺失：保留 Task，但写 `state=failed, message='orphan-task: material missing'`，并在任务详情页提示 |
| `AiMetadataJob.parseTaskId` 必须对应存在的 `ParseTask.id` | 若 ParseTask 缺失：把 Job 置为 `failed`，`message='orphan-ai-job'` |
| `Material.metadata.aiJobId`（若存在）必须对应一个真实 Job | 若对应 Job 缺失：清空 `aiJobId` 并把 `Material.aiStatus` 重置为 `pending` |
| 一致性扫描必须按字符串全匹配，不允许把 ID 转成 Number 比较 | 修复路由与脚本的数字比较遗留 |

### 9.2 对象存储

| 不变量 | 修复动作 |
| :--- | :--- |
| `Material.metadata.objectName` 必须以 `originals/{materialId}/` 开头 | 扫描发现异常时记录告警；不自动移动文件 |
| `Material.metadata.markdownObjectName`（若存在）必须以 `parsed/{materialId}/` 开头 | 同上 |
| `ParseTask.state ∈ {completed, review-pending}` 时，`parsed/{materialId}/full.md` 必须存在 | 若缺失：把 ParseTask 置为 `failed`，提示运行 Reparse |

### 9.3 状态与 AI Job 的联动

| 不变量 | 修复动作 |
| :--- | :--- |
| `ParseTask.state=ai-running` 时，必存在一个 `AiMetadataJob.state ∈ {pending, running}` 且 `parseTaskId` 匹配 | 若缺失：把 ParseTask 置回 `ai-pending`，让 Worker 重新创建 Job |
| `AiMetadataJob.state=running` 且 `updatedAt` 超过 `timeoutMs + 60s` | `AiMetadataWorker.recoverStaleRunningJobs` 将其重置为 `pending`（已实现） |
| `ParseTask.state=running` 且 `updatedAt` 超过 `MINERU_TIMEOUT + 60s` | v0.3 待补：`ParseTaskWorker` 增加对应 stale-recovery |

### 9.4 前端桶与状态字符串

| 不变量 | 修复动作 |
| :--- | :--- |
| 前端任何列表/筛选必须完整支持第 6.3 节"展示桶"表，禁止自创桶 | 在一致性扫描中对前端枚举进行静态检查（可通过约定的 `STATE_BUCKETS` 常量集中管理） |
| `review-pending` 不得被算进"已完成"桶 | 修复 `TaskManagementPage.tsx` 中把 `ai-pending` 误并入"completed" 的逻辑 |

## 10. 下一阶段开发任务（Scope of v0.3）

按优先级从高到低列出。v0.3 的里程碑目标是：**所有 P0 项完成并通过 UAT；P1 项作为收敛项；P2 项按排期推进。**

### 10.1 P0 — 稳定状态机

1. **对齐 Canonical 状态机**。在 `server/services/queue/task-worker.mjs`、`server/services/ai/metadata-worker.mjs`、`server/upload-server.mjs` 内把所有状态字面量统一到第 6.1 节十项。
2. **废弃 `succeeded`**，统一使用 `confirmed` 作为 AI Job 的成功终态；更新 `metadata-job-client.mjs` 的去重集合。
3. **补齐 `ai-running` 写入**。`AiMetadataWorker` 进入 Provider 调用前，除了写 `AiMetadataJob.state=running`，也要把对应 `ParseTask.state` 写为 `ai-running`。
4. **补齐 `ParseTaskWorker` 的 stale-recovery**，超时阈值读取 `optionsSnapshot.localTimeout`。

### 10.2 P0 — Retry / Reparse / Re-AI / Cancel / Review API

按第 8.2 节实现并提供 OpenAPI 样例。每个接口都要写 `taskEvents`。

### 10.3 P0 — 任务详情页（Task Detail Page）

在前端实现独立路由 `/tasks/:id`，页面结构：

- **头部**：任务 ID、Material 标题、引擎、创建时间、当前状态徽章、进度条。
- **操作区**：按状态动态呈现 `重试 / 重新解析 / 重新 AI / 取消 / 审核通过 / 保存元数据 / 下载 ZIP` 按钮。
- **Tab 1 Markdown**：从 `/presign` 取 `markdownObjectName` 渲染只读 Markdown。
- **Tab 2 原件预览**：PDF 使用 `/proxy-file` 嵌入预览（Range 支持已具备）。
- **Tab 3 AI 元数据**：以表单呈现 `result` 字段；`review-pending` 时可编辑并提交至 `/tasks/:id/review`。
- **Tab 4 事件日志**：按时间倒序展示 `taskEvents`，支持级别过滤。

### 10.4 P1 — 一致性扫描对 String ID 的完全支持

1. 修正 `GET /materials` 的排序为 `updateTime DESC`，去掉数字比较。
2. 重写一致性扫描脚本（`server/lib/consistency-routes.mjs`），对每条不变量生成具体的修复建议，打印到日志并回写到 `taskEvents`（`consistency-checked`）。
3. 在任务详情页"事件日志"中展示扫描结果。

### 10.5 P1 — 资产详情页向任务详情页过渡

- `AssetDetailPage.tsx` 的 `handleAiAnalyze` 改为调用 `POST /tasks/:id/re-ai`；
- 页面上方增加 "查看任务"跳转；
- 原"手动触发 MinerU"按钮保留，但底层走 `/tasks/:id/reparse`。

### 10.6 P2 — 文档与运维

- README、DEPLOY.md 更新至 v0.3 的任务式心智。
- Docker Compose 健康检查：为 `upload-server`、`db-server`、`MinIO` 增加 `healthcheck`。
- 新增 `npm run consistency-check` 脚本一键运行扫描并输出报告。

## 11. 明确不做的事项（Out of Scope）

- **云端 MinerU 的任务化改造**：本版仍以本地 MinerU FastAPI 为主链路，云端接入留待 v0.4。
- **多用户登录与权限**：本版仍是单操作员本地部署。
- **Markdown 在线编辑器**：仅保留只读预览。
- **新 Provider 接入**：v0.3 不新增 AI Provider。
- **全面前端状态重构**：v0.3 只纠正状态字符串与展示桶，不做状态管理框架的更替。

## 12. 验收标准（UAT）与度量指标

### 12.1 功能验收清单

1. 上传一个 20 MB 内 PDF，任务依次经过 `pending → running → result-store → ai-pending → ai-running → completed`，全程前端桶展示正确。
2. 上传一个 Markdown 文件，任务经过 `pending → ai-pending → ai-running → completed`，跳过 MinerU。
3. 故意让本地 MinerU 不可用，任务在 `running` 超时后自动 `failed`；Operator 点 Retry 后任务重新从 `pending` 开始。
4. 强制 AI Provider 失败，任务进入 `failed`；Operator 点 Re-AI 后 AI 阶段重跑，成功进入 `review-pending` 或 `completed`。
5. 对 `review-pending` 任务，Operator 修改元数据并提交，任务进入 `completed`，Material 元数据同步更新。
6. 重启 `upload-server` 与 `db-server`，所有任务状态保持，`running`/`ai-running` 中的僵尸任务在宽限期后被自愈回 `pending`。
7. 一致性扫描对一个人为制造的 orphan 任务生成修复建议，并在任务详情页事件日志中可见。

### 12.2 度量指标（观测用，不做硬门槛）

- `/tasks` 创建到 `pending` 落库的 P95 延迟 < 1s。
- `AiMetadataWorker` 每轮扫描耗时 < 2s（空载）。
- 一次完整 PDF 任务（20 MB 以内）端到端 P95 < 180s（依赖本地 MinerU 性能）。

## 13. 风险、回退与发布策略

### 13.1 主要风险

1. **旧端点与新 API 并存导致的数据漂移**：`AssetDetailPage` 在迁移期间仍可能走 `/parse/analyze`，导致 AI 结果不经过 Job。缓解：在迁移期内，`/parse/analyze` 内部改为"创建一个 AiMetadataJob 并立即 run"的桥接实现。
2. **状态字面量历史遗留**：数据库中可能存在 `success`、`succeeded` 等旧值。缓解：v0.3 启动时跑一次性的状态归一化迁移。
3. **一致性扫描的误杀**：修复动作若直接改写状态，可能误伤正在进行中的任务。缓解：所有修复动作默认"仅记录、不自动写回"，由 Operator 在详情页点击确认后再执行。

### 13.2 回退策略

- v0.3 通过 Docker Compose 发布，镜像带明确 Tag；如发现严重问题可回退至 v0.7.0（Docker 里程碑）镜像。
- 数据库为 JSON，回退后使用同目录备份文件恢复即可。

### 13.3 发布节奏建议

- 第 1 周：状态机归一化 + Retry/Reparse/Re-AI API + 状态字面量迁移脚本。
- 第 2 周：任务详情页上线；资产详情页的 `handleAiAnalyze` 迁移。
- 第 3 周：一致性扫描改造 + UAT 全量验收 + 文档刷新。

## 14. 对独立评审意见的核查结论

用户在下达本次修订指令时附上了一份独立评审分析。本节记录 Manus AI 的独立核查与采纳结论，避免盲目采纳或盲目否决。

| 评审观点 | 核查结论 | 是否采纳 |
| :--- | :--- | :--- |
| "PRD v0.2 方向正确：从资料库工具转成文档解析任务工作台" | 与仓库现状一致：`/tasks` 已是主入口，Worker 已跑通 | 采纳为 v0.3 的产品目标 |
| "官方原型的关键不是某个 API 细节，而是任务式体验" | 通过访问 `mineru.net/OpenSourceTools/Extractor` 交叉验证：Create Task / Tasks / Collections 是一等导航 | 采纳，写入第 3 章 |
| "PRD v0.2 最大问题是落后于代码" | 交叉核对 `upload-server.mjs`、`task-worker.mjs`、`metadata-worker.mjs`、`db-server.mjs` 后确认 | 采纳，引入第 4 章"Baseline Facts"概念 |
| "必须定义唯一 canonical 状态机，不允许页面各自解释" | 在 `TaskManagementPage.tsx` 发现把 `ai-pending` 误并入"completed"桶的分散解释证据；在 `onComplete` 中发现 `confirmed/review-pending → completed` 的二次映射 | 采纳，新增第 6 章 |
| "一致性条款改成可验证不变量 + 修复动作" | 当前 `consistency-routes.mjs` 对字符串 ID 的处理不完整；修复需要显式动作 | 采纳，新增第 9 章并显式 P1 任务 |
| "下一步不是加新模型而是稳定状态机、补 Retry/Reparse/Re-AI、修一致性扫描、补任务详情视图、扩 UAT" | 与仓库现状吻合；已有的 `/tasks/:id` 前端跳转存在但详情页尚未成熟 | 采纳，作为第 10 章 P0/P1 |
| "PRD 需要从愿景型升级为工程契约型" | 这是 v0.3 的根本定位调整 | 采纳，作为文档版本定位写在扉页 |
| "评审建议的 canonical 状态集 `uploading, queued, mineru-running, result-storing, ai-pending, ai-running, review-pending, completed, failed, canceled`" | 与当前代码字面量（`pending`、`running`、`result-store`、`ai-pending`、`review-pending`、`completed`、`failed`）存在命名差异。若直接采用评审命名（`queued`、`mineru-running`、`result-storing`）会产生一次数据迁移且语义收益有限 | **部分采纳**：引入 `ai-running` 作为补齐；保留 `pending`、`running`、`result-store` 的既有命名，减少一次破坏性迁移。状态集合与流转见第 6 章 |

结论：评审意见的方向与重点全部采纳；在**状态命名**上选择"最小破坏 + 语义补齐"的折中方案，避免一次无必要的全库迁移。

## 15. 术语表

- **Material**：一条资料记录，绑定一个原始文件与其 MinIO 对象。
- **ParseTask**：一次解析任务的生命周期对象。
- **AiMetadataJob**：一次 AI 元数据提取的子任务。
- **Canonical State**：第 6.1 节定义的状态集合。
- **Display Bucket**：前端把若干 Canonical State 合并成的 UI 分组，定义在第 6.3 节。
- **Baseline Fact**：已经在仓库 HEAD 中实现、v0.3 不可回退的既定事实，见第 4 章。
- **Invariant**：系统必须保持为真的断言，违反即是一致性缺陷，见第 9 章。

## 16. 变更记录

- **v0.3（2026-04-22）**：重写为工程契约型 PRD。确立 Canonical 状态机、引入 Baseline Facts 与 Invariants、将下一阶段开发聚焦到 Retry/Reparse/Re-AI API、任务详情页与一致性扫描；对独立评审意见做交叉核查，按"最小破坏 + 语义补齐"折中处理命名差异。
- **v0.2**：首次以"任务式"视角重写 PRD，但以愿景驱动为主，列出任务模型、队列、Worker 等作为待开发项，与后续落地代码产生偏差，由本版替代。
- **v0.1**：资料库管理工具起步版，以 Material 为核心。
