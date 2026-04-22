# Luceon2026 本地 MinerU + AI 元数据识别应用 PRD

版本：v0.2  
日期：2026-04-21（初稿）/ 2026-04-22（数据一致性专项修订）  
项目仓库：https://github.com/shcming2023/Luceon2026  
目标形态：Docker 部署的 Web 应用，基于本地部署 MinerU 接口，复刻官方 MinerU 核心交互，并增强 AI 元数据识别能力。  
变更说明：本次新增第 11 节「数据一致性与流水线管理」，原 11-21 节顺延为 12-22。

## 1. 背景与问题

Luceon2026 当前已经具备资料上传、MinIO 存储、本地 MinerU 调用、官方 MinerU API 调用、AI 元数据分析和 Docker 部署能力。但现有交互不是围绕“文档解析任务”设计，而是围绕资料库列表做逐项手动操作，导致用户在上传、解析、等待、查看结果、AI 分析、审核入库之间频繁断流。

用户反馈的两个主要问题：

1. 当前应用没有复刻官方 MinerU 客户端/在线版的任务式交互，操作链路不流畅，有很多断点。
2. 本地 MinerU 接口与本地 Ollama AI 模型接入不流畅，解析产物到 AI 元数据识别之间缺少稳定编排。

本 PRD 的第一目标不是立即堆功能，而是把产品工作流重新对齐到官方 MinerU 的任务体验：上传即建任务、任务队列持续推进、状态可恢复、结果可查看、失败可重试、产物可导出。Luceon2026 的差异化能力是在这个流畅任务体验后增加“教育资源 AI 元数据识别、置信度审核、资源库沉淀”。

## 2. 取证范围

本次拆解参考了以下来源：

| 来源 | 取证内容 | 结论用途 |
| --- | --- | --- |
| 官方在线 MinerU 页面 `https://mineru.net/OpenSourceTools/Extractor/` | 页面标题、Web 构建资源、公开前端包入口 | 确认官方 Web 版入口、构建方式和资源版本 |
| `/Applications/MinerU.app` | Electron 应用包、`app.asar`、`Info.plist`、preload、main 进程代码 | 拆解桌面客户端架构、任务状态、队列、存储、快捷入口 |
| `/Users/concm/MinerU/config.json` | 官方客户端本地配置 | 拆解默认解析参数、历史目录、快捷键和平台集成配置 |
| `/Users/concm/MinerU/data/mineru.db` | 官方客户端 SQLite 表结构 | 拆解任务、收藏、批注、编辑块、反馈等数据模型 |
| `/Users/concm/Library/Logs/MinerU/main.log` | 官方客户端运行日志 | 确认启动流程、队列恢复、轮询行为、最大并发 |
| Luceon2026 本地仓库 | React/Vite 前端、Express 上传代理、DB 服务、设置页、现有文档 | 对照当前断点与重构范围 |

限制说明：官方在线页面的登录态属于用户本机浏览器会话，当前取证无法继承该登录态读取用户私有任务数据。因此本 PRD 使用公开 Web 构建、官方桌面客户端本地包和当前仓库代码进行结构拆解；登录后的个性化任务数据不纳入结论。

## 3. 官方 MinerU 核心拆解

### 3.1 产品形态

官方 MinerU 同时存在 Web 版和 Electron 桌面版。两者共享非常接近的前端交互模型：

- Web 版入口：`/OpenSourceTools/Extractor/`
- 桌面版：Electron + React 渲染层 + preload IPC + main 进程服务
- 桌面版版本：`0.13.1`
- Web 构建显示客户端版本：`0.12.0`
- 桌面版支持打开文件类型：`pdf`、`md`、`doc`、`docx`、`ppt`、`pptx`
- 渲染能力包含：PDF 预览、Markdown 渲染、KaTeX/MathJax、JSON 预览、化学分子/反应相关模块、批量导出

官方产品不是“资料管理系统”，而是“文档解析任务工作台”。资料上传后立即进入任务状态管理，任务列表是用户的主工作区。

### 3.2 信息架构

官方前端暴露的核心导航：

| 导航 | 路由/入口 | 作用 |
| --- | --- | --- |
| 创建任务 | `/` | 上传文件、设置解析参数、提交解析 |
| 任务管理 | `/task-management` | 查看全部任务、筛选状态、重试、删除、下载、批量操作 |
| 收藏/集合 | `/collections` | 管理从解析结果中收藏的内容块 |
| 设置 | `/settings` | 语言、快捷键、更新、平台配置等 |
| 更多能力 | 弹出层 | 关键字段抽取、集成平台、开发者 API |

桌面版还有托盘和快捷键：

- 打开 MinerU：`Ctrl + Shift + O`
- 上传文件：`Ctrl + Shift + U`
- 截图上传：`Ctrl + Shift + S`
- 打开历史：`Ctrl + \`

对 Luceon2026 的启示：当前 `/workspace`、`/library`、`/settings` 的信息架构需要调整为“新建解析任务 + 任务管理 + 结果详情 + 资源库”四段式，而不是把解析按钮散落在资料列表行内。

### 3.3 官方任务状态模型

官方客户端内置任务状态枚举：

| 状态 | 含义 | UX 表现 |
| --- | --- | --- |
| `loading` | 本地创建/初始化中 | 列表显示等待或加载态 |
| `waiting-file` | 等待文件上传 | 可恢复上传 |
| `uploading` | 文件上传中 | 显示上传进度 |
| `pending` | 远端排队中 | 显示排队态 |
| `running` | 解析执行中 | 显示解析中 |
| `done` | 远端解析完成 | 进入下载/结果处理 |
| `waiting-download` | 等待下载解析包 | 下载前置状态 |
| `downloading` | 下载解析包 | 显示下载进度 |
| `unzipping` | 解压解析包 | 显示解压中 |
| `unzipped` | 本地结果就绪 | 可查看、导出、收藏 |
| `failed` | 解析失败 | 可重试 |
| `download-failed` | 下载失败 | 可重试下载 |
| `unzip-failed` | 解压失败 | 可重试解压 |
| `quota-exceeded` | 额度不足 | 提示额度问题 |
| `expire` | 任务或链接过期 | 归入过期筛选 |
| `aborted` | 已中止 | 可按规则清理 |
| `local-deleted` | 本地文件已删除 | 提示文件不存在 |
| `unknown` | 未知状态 | 兜底错误态 |

官方状态模型覆盖了上传、远端解析、下载、解压、结果可用、失败恢复全过程。Luceon2026 当前只有 `pending/processing/completed/failed/reviewing`、`mineruStatus`、`aiStatus` 这些粗粒度状态，不足以支持平滑恢复和精确重试。

### 3.4 官方任务队列机制

官方桌面客户端 main 进程包含 `TaskQueueManager`，关键行为：

- 使用 `p-queue` 管理轮询任务。
- 远端解析轮询间隔为 `5000ms`。
- 轮询最大并发 `POLLING_LIMIT = 10`。
- 支持单任务轮询 `/api/v4/extract-results/batch/{batch_id}`。
- 支持批量轮询 `/api/v4/extract-results/batches/{batch_id_list}`。
- 批量轮询按每组 5 个 batch id 分组。
- 网络恢复后自动重建轮询队列。
- 启动时恢复未完成任务。
- 屏幕解锁后重置进度态并重建轮询队列。
- 对 429 限流进行分钟级或小时级延迟恢复。
- 失败后支持重试上传、重试远端轮询、重试下载、重试解压。

这是官方体验流畅的核心。它把“用户发起任务”和“任务最终可用”之间的长时间过程封装进持久队列，用户不需要停留在某个页面等待。

### 3.5 官方数据模型

官方客户端 SQLite 表：

| 表 | 作用 |
| --- | --- |
| `taskData` | 正式任务列表与任务状态 |
| `taskDemoData` | 示例任务 |
| `collection` | 用户收藏内容块 |
| `annotation` | 批注 |
| `edited_block` | 被编辑过的内容块 |
| `downvote` | 用户反馈/点踩内容块 |

`taskData` 关键字段：

- `id`
- `file_name`
- `type`
- `state`
- `createdAt`
- `full_md_link`
- `full_zip_url`
- `err_msg`
- `err_code`
- `jobID`
- `task_id`
- `thumb`
- `url`
- `file_url`
- `data_id`
- `batch_id`
- `taskType`
- `path`
- `extract_progress`
- `retry_time`
- `unzip_file_path`
- `unzip_file_output_path`
- `origin_file_path`
- `model_version`
- `cover_path`
- `chem`
- `is_chem`
- `file_size`
- `rank`
- `can_retry`
- `is_expire`

对 Luceon2026 的启示：必须把“资料 Material”和“解析任务 Task”拆开建模。一个资料可以有一次或多次解析任务，AI 元数据识别也应该是独立任务或子任务，不能只靠 Material 的几个状态字段承载所有过程。

### 3.6 官方解析参数

官方配置中的核心解析参数：

- `enable_formula`
- `enable_table`
- `language`
- `is_ocr`
- `model_version`
- `layout_model`
- `custom_page`
- `history_path`
- `client_api_token`

本机官方客户端当前配置：

- `enable_formula: true`
- `enable_table: true`
- `language: null`
- `is_ocr: false`
- `model_version: vlm`
- `layout_model: doclayout_yolo`
- `history_path: /Users/concm/Library/CloudStorage/OneDrive-个人/Mac/教研资料/mineru`

Luceon2026 已有同类参数，但分散在设置页和上传/解析调用里。重构后应把参数绑定到每个解析任务快照中，保证任务重试时使用的是提交时的参数，而不是当前全局设置。

### 3.7 官方 API 调用链

官方 Web 版公开构建中可识别的云端 API 链路：

1. 申请上传地址：`POST /api/v4/file-urls/batch`
2. 上传文件到返回的对象存储预签名 URL
3. 创建解析任务：`POST /api/v4/extract/task/batch`
4. 查询单个批次：`GET /api/v4/extract-results/batch/{batch_id}`
5. 批量查询批次：`GET /api/v4/extract-results/batches/{batch_ids}`
6. 获取任务详情：`GET /api/v4/extract/task/{task_id}`
7. 删除任务：`DELETE /api/v4/extract/task/{task_id}`
8. 查询整体状态：`GET /api/v4/extract/status`
9. 批量转换/导出：通过 conversion batch 轮询获取下载 URL

Luceon2026 的本地部署不需要完全复刻官方云 API，但应该复刻它的“任务抽象”和“状态流”：

- 本地 MinerU adapter 负责把本地 `/tasks`、`/gradio_api/to_markdown` 等差异封装为统一任务接口。
- 前端只认统一任务状态，不关心底层是本地 MinerU 还是官方 API。

## 4. Luceon2026 当前状态对照

### 4.1 当前技术栈

| 层 | 当前实现 |
| --- | --- |
| 前端 | React 18 + Vite + React Router + Tailwind + lucide-react |
| 上传服务 | `server/upload-server.mjs` Express |
| DB 服务 | `server/db-server.mjs` JSON 文件持久化 |
| 对象存储 | MinIO 或 tmpfiles fallback |
| 本地 MinerU | `src/utils/mineruLocalApi.ts` + `/parse/local-mineru` |
| 官方 MinerU | `src/utils/mineruApi.ts` + `/__proxy/mineru` |
| AI 元数据 | `/parse/analyze`，支持多 AI provider fallback |
| Docker | `docker-compose.yml`、`Dockerfile`、`server/Dockerfile` |

### 4.2 已有能力

- 上传文件到 MinIO。
- 解析产物回存到 `parsed/{materialId}/full.md`。
- 本地 MinerU 健康检查。
- 本地 MinerU 调用支持 SSE 进度。
- 兼容本地 MinerU FastAPI `/tasks` 和 Gradio 降级路径。
- AI provider 支持 OpenAI-compatible `/chat/completions`。
- Ollama 本地端点支持 Docker 环境 rewrite。
- AI 响应 JSON 提取有一定健壮性。
- 设置页支持 AI provider、MinerU、存储、备份、调试日志。

### 4.3 当前主要断点

1. `docs/reviews/前端架构修订总结.md` 显示，项目之前移除了后端 Batch Queue，改为 Material 工作台直驱模式。这与官方 MinerU 的核心体验相反。
2. `SourceMaterialsPage` 中轮询 `/__proxy/db/materials` 目前只 `console.log`，没有实际刷新 store。
3. 上传完成后不自动进入解析任务管理，而是提示用户回列表手动点击解析。
4. 解析和 AI 分析是两个离散按钮，缺少“解析完成自动触发 AI 元数据识别”的任务编排。
5. 本地 MinerU 调用是单个请求/响应式流程，即使有 SSE，也要求调用链不断开；缺少后端持久任务队列兜底。
6. Material 状态、MinerU 状态、AI 状态之间存在重复和不一致风险。
7. `upload-server.mjs` 单文件过大，MinerU adapter、AI adapter、存储、备份、审计、调试日志都混在一起。
8. Ollama 连接依赖 Docker 网络和 JSON 输出稳定性，一旦模型返回非 JSON 或 thinking 内容，就会阻塞元数据识别。

## 5. 产品定位

Luceon2026 不是做一个“MinerU 外壳”，而是做一个面向教育资源生产的本地私有化文档解析与元数据工作台。

一句话定位：

> 一个可 Docker 部署的本地文档解析工作台，复刻 MinerU 官方任务体验，使用本地 MinerU 完成文档结构化解析，并用本地 Ollama/多模型 AI 自动识别教育资源元数据，最终沉淀为可审核、可检索、可复用的教学资源库。

## 6. 目标用户

| 用户 | 核心诉求 |
| --- | --- |
| 教研资料整理人员 | 批量上传教材、试卷、讲义，自动解析并归类 |
| 教学内容生产人员 | 快速从 PDF/DOCX 中提取 Markdown、图片、公式、表格并二次加工 |
| 管理员 | 本地部署、配置 MinerU/Ollama/MinIO、监控任务失败 |
| 审核人员 | 查看 AI 元数据结果，校正低置信字段，确认入库 |
| 开发维护人员 | 拥有稳定的任务状态、日志、重试、备份与可扩展 adapter |

## 7. 产品目标

### 7.1 一期目标

1. 复刻官方 MinerU 的核心任务流：上传、排队、解析、结果、下载、重试、删除。
2. 将本地 MinerU 接口封装为统一任务 adapter。
3. 将 AI 元数据识别纳入任务流水线，而不是作为断开的手动按钮。
4. 建立持久化任务队列，支持页面刷新、服务重启后的状态恢复。
5. 提供可审核的教育资源元数据面板。
6. 保持 Docker 部署友好，默认支持本地宿主机 MinerU 与 Ollama。

### 7.2 非目标

一期不追求：

- 完整复制官方客户端的化学分子解析高级能力。
- 完整复制官方客户端的截图上传。
- 直接反向使用官方客户端私有接口 token。
- 做多租户权限系统。
- 做复杂工作流编排引擎。
- 做在线支付、额度、账号体系。

## 8. 核心用户流程

### 8.1 单文件顺滑流程

1. 用户进入“新建任务”。
2. 拖入 PDF/DOCX/PPTX 文件。
3. 右侧选择解析参数：模型、OCR、语言、公式、表格、页码范围。
4. 点击“开始解析”。
5. 系统创建 `material` 和 `parse_task`，文件上传到 MinIO。
6. 后端队列自动提交到本地 MinerU。
7. 用户自动跳转到任务管理页，看到任务状态从“上传中 -> 排队中 -> 解析中 -> 结果处理中”推进。
8. MinerU 解析完成后，系统自动保存 Markdown/JSON/图片/ZIP。
9. 系统自动触发 AI 元数据识别。
10. AI 完成后进入“待审核”或“已完成”。
11. 用户点击任务进入详情页，左侧看原文/预览，右侧看 Markdown、JSON、AI 元数据。
12. 用户确认元数据，资产进入资料库。

### 8.2 批量文件流程

1. 用户一次拖入多个文件。
2. 系统展示待提交文件清单，逐个校验格式、大小、重复。
3. 用户统一设置解析参数，也可单文件覆盖。
4. 点击“开始批量解析”。
5. 后端按并发限制上传与解析。
6. 任务管理页显示总进度、成功数、失败数、当前运行数。
7. 失败任务支持单个重试、批量重试。
8. 已完成任务可批量导出结果包或批量进入资料库。

### 8.3 失败恢复流程

1. 用户或服务中途关闭页面。
2. 后端队列继续运行；若服务重启，启动时从 DB 恢复未完成任务。
3. 前端重新打开后从任务 API 获取最新状态。
4. 对于 `upload-failed`、`parse-failed`、`download-failed`、`ai-failed` 提供明确重试动作。
5. 重试时保留原始任务参数快照。

## 9. 信息架构重构

### 9.1 顶层导航

| 新导航 | 路径 | 说明 |
| --- | --- | --- |
| 新建任务 | `/workspace` 或 `/tasks/new` | 默认首页，上传和提交解析 |
| 任务管理 | `/tasks` | 官方 MinerU 风格任务表 |
| 结果详情 | `/tasks/:id` | 查看解析结果、AI 元数据、审核 |
| 资源库 | `/library` | 已完成并确认入库的教育资源 |
| 收藏 | `/collections` | 解析结果内容块收藏，可二期实现 |
| 设置 | `/settings` | MinerU、AI、存储、队列、备份、日志 |

### 9.2 当前路由调整建议

| 当前路由 | 调整 |
| --- | --- |
| `/workspace` | 改成“新建任务 + 批量提交”主工作台 |
| `/legacy/source-materials` | 废弃或迁移为任务管理 |
| `/asset/:id` | 改为兼容 `/tasks/:taskId` 和 `/asset/:materialId` |
| `/library` | 只展示审核通过/已入库资源 |
| `/settings` | 保留，但新增队列和 adapter 诊断 |

## 10. 功能需求

### 10.1 新建任务页

#### 10.1.1 上传区

必须支持：

- 拖拽上传。
- 点击选择文件。
- 多文件选择。
- 文件类型校验：PDF、DOC、DOCX、PPT、PPTX、MD。
- 文件大小校验，默认 200MB，可配置。
- 文件列表展示：文件名、大小、类型、页数估算、校验状态。
- 移除单个文件。
- 清空全部。
- 重复文件提示。

交互要求：

- 上传区是第一屏核心，不做营销式首页。
- 文件进入后立即显示“待提交任务清单”。
- 用户点击“开始解析”后不留在空页面，跳转任务管理并显示新建任务。

#### 10.1.2 解析参数

任务提交时保存参数快照：

- 解析引擎：本地 MinerU / 官方 API。
- 本地 MinerU 地址。
- 模型/后端：`pipeline`、`vlm`、`hybrid-auto-engine`、`vlm-auto-engine`。
- 是否 OCR。
- OCR 语言。
- 是否识别公式。
- 是否识别表格。
- 页码范围或最大页数。
- 是否启用 AI 元数据识别。
- AI provider 策略。
- 低置信度是否进入人工审核。

### 10.2 任务管理页

任务管理页是核心页面，必须代替当前资料列表作为主操作区。

#### 10.2.1 列表字段

| 字段 | 说明 |
| --- | --- |
| 选择框 | 支持批量操作 |
| 文件名 | 支持搜索和点击进入详情 |
| 类型 | PDF/DOCX/PPTX/MD |
| 模型 | pipeline/vlm/hybrid |
| 阶段 | 上传、排队、解析、结果处理、AI、审核、完成 |
| 进度 | 百分比、页数、当前消息 |
| AI 状态 | 待分析、分析中、待审核、已确认、失败 |
| 置信度 | AI 元数据总置信度 |
| 创建时间 | 排序 |
| 耗时 | 上传到完成总耗时 |
| 操作 | 查看、重试、下载、删除、更多 |

#### 10.2.2 筛选

必须支持：

- 全部。
- 运行中。
- 排队中。
- 已完成。
- 失败。
- 待审核。
- 已过期。
- 按模型筛选。
- 按 AI 状态筛选。
- 按学科/年级筛选。
- 搜索文件名/标签/元数据。

#### 10.2.3 操作

单任务操作：

- 查看详情。
- 重试当前失败阶段。
- 从头重新解析。
- 重新 AI 分析。
- 下载 Markdown。
- 下载完整结果包。
- 删除任务。
- 删除任务并清理文件。

批量操作：

- 批量开始。
- 批量暂停。
- 批量恢复。
- 批量重试失败。
- 批量下载结果。
- 批量确认入库。
- 批量删除。

#### 10.2.4 任务状态可视化

每个任务至少显示 5 个阶段：

1. 文件上传。
2. MinerU 解析。
3. 产物入库。
4. AI 元数据识别。
5. 人工审核/完成。

阶段状态：

- `pending`
- `running`
- `success`
- `failed`
- `skipped`

### 10.3 后端持久任务队列

必须恢复后端队列架构，但不是简单回滚旧代码，而是以官方状态模型为参考重新实现。

#### 10.3.1 队列能力

- 服务端持久化任务。
- 后端 worker 执行，不依赖页面存活。
- 支持并发限制。
- 支持暂停/恢复。
- 支持取消。
- 支持重试。
- 支持服务重启后恢复。
- 支持任务事件日志。
- 支持 SSE 或 WebSocket 推送任务变化。

#### 10.3.2 推荐并发默认值

| 队列 | 默认并发 | 说明 |
| --- | --- | --- |
| upload | 3 | 文件上传/转存 |
| mineru-submit | 2 | 向本地 MinerU 提交任务 |
| mineru-poll | 10 | 对齐官方轮询最大并发 |
| result-store | 3 | 下载/保存解析产物 |
| ai-metadata | 1 | 本地 Ollama 默认串行，防止模型阻塞 |

#### 10.3.3 轮询策略

- 默认 MinerU 轮询间隔：5 秒。
- 排队状态可降频到 8-10 秒。
- 解析中状态保持 5 秒。
- 失败后不无限重试，进入可人工重试状态。
- 对本地接口连接失败使用指数退避。

### 10.4 本地 MinerU Adapter

目标：前端和任务队列只面对统一接口，不直接依赖具体 MinerU 部署形态。

#### 10.4.1 Adapter 输入

```json
{
  "fileObjectName": "originals/123/file.pdf",
  "fileName": "file.pdf",
  "mimeType": "application/pdf",
  "options": {
    "backend": "hybrid-auto-engine",
    "parseMethod": "auto",
    "ocrLanguage": "ch",
    "enableOcr": false,
    "enableFormula": true,
    "enableTable": true,
    "maxPages": 1000
  }
}
```

#### 10.4.2 Adapter 输出

```json
{
  "externalTaskId": "mineru-task-id-or-local-id",
  "status": "submitted",
  "message": "任务已提交"
}
```

解析完成结果：

```json
{
  "markdownObjectName": "parsed/{materialId}/full.md",
  "markdownUrl": "presigned-url",
  "resultPrefix": "parsed/{materialId}/",
  "parsedFilesCount": 12,
  "rawResult": {}
}
```

#### 10.4.3 本地接口兼容

当前后端已经兼容：

- FastAPI 风格：`POST {localEndpoint}/tasks`
- 任务等待：`waitMinerUTask(...)`
- 结果获取：`fetchMinerUResult(...)`
- Gradio 降级：`/gradio_api/to_markdown`

重构后这些逻辑应拆到：

- `server/services/mineru/localAdapter.mjs`
- `server/services/mineru/cloudAdapter.mjs`
- `server/services/mineru/resultParser.mjs`

`upload-server.mjs` 不再直接承载所有实现细节。

### 10.5 AI 元数据识别

AI 元数据识别是 Luceon2026 的核心差异化能力，必须成为解析流水线的一环。

#### 10.5.1 触发时机

支持三种模式：

| 模式 | 说明 |
| --- | --- |
| 自动 | MinerU 解析成功后立即触发 AI 元数据识别 |
| 手动 | 用户在详情页点击“重新识别” |
| 批量 | 对已解析但未识别任务批量执行 |

默认：自动触发，但低置信度进入审核。

#### 10.5.2 输入

- `full.md` 全文或截断上下文。
- 文件名。
- 文件类型。
- 页数。
- MinerU 解析参数。
- 可选：用户预设目录、学科范围、年级范围。

#### 10.5.3 输出 Schema

```json
{
  "title": "资料标题",
  "subject": "数学",
  "grade": "G8",
  "semester": "上册",
  "materialType": "练习册",
  "language": "中文",
  "country": "中国",
  "curriculum": "沪教版",
  "publisher": "出版社",
  "examType": "二模卷",
  "difficulty": "中等",
  "knowledgePoints": ["一次函数", "几何证明"],
  "tags": ["八年级", "数学", "练习"],
  "summary": "2-3 句话摘要",
  "confidence": 86,
  "fieldConfidence": {
    "subject": 98,
    "grade": 90,
    "materialType": 75
  },
  "needsReview": true,
  "warnings": ["资料类型置信度较低"]
}
```

#### 10.5.4 Ollama 接入要求

必须支持：

- OpenAI-compatible `/v1/chat/completions`。
- 本地 `http://localhost:11434` 自动 rewrite 到 `host.docker.internal`。
- 连接测试分为“网络连通性”和“JSON 元数据输出测试”两种。
- 模型列表拉取 `/api/tags`。
- 非流式输出 `stream: false`。
- thinking mode 可配置，默认关闭。
- 自动清理 `<think>...</think>`。
- 支持 Markdown 代码块 JSON。
- 支持前后文本包裹 JSON。
- 支持失败时保存原始响应片段到任务日志。

#### 10.5.5 Prompt 要求

Prompt 必须包含：

- 明确角色：教育资源元数据提取助手。
- 明确只返回 JSON。
- 明确字段枚举或可选值。
- 明确 tags 数量。
- 明确 confidence 规则。
- 明确无法判断时返回空字符串或 `unknown`，不要编造。

#### 10.5.6 审核规则

进入人工审核的条件：

- 总置信度低于阈值，默认 80。
- 关键字段缺失：`subject`、`grade`、`materialType`。
- AI 返回 JSON 被修复解析。
- 模型调用经过 fallback 才成功。
- 用户在设置中启用“全部需要审核”。

### 10.6 结果详情页

详情页应成为“解析产物 + AI 元数据 + 审核”的主页面。

推荐布局：

- 左侧：原文件预览。
- 中间：Markdown / JSON / 图片资源 / 日志 tabs。
- 右侧：AI 元数据面板。

必须功能：

- Markdown 渲染。
- Markdown 原文查看。
- JSON 查看。
- 解析文件列表。
- 下载 `full.md`。
- 下载完整 ZIP。
- 复制 Markdown。
- AI 元数据字段编辑。
- 保存审核结果。
- 重新解析。
- 重新 AI 识别。
- 查看任务事件日志。

### 10.7 资源库

资源库只展示已完成且审核通过的资产。

字段：

- 标题。
- 学科。
- 年级。
- 类型。
- 语言。
- 国家/地区。
- 标签。
- 摘要。
- 来源任务。
- 解析时间。
- AI 识别时间。
- 审核状态。

操作：

- 搜索。
- 筛选。
- 进入详情。
- 导出元数据。
- 导出 Markdown。
- 批量修改标签。

### 10.8 设置页

设置页需要按职责拆分：

| Tab | 内容 |
| --- | --- |
| MinerU | 本地地址、后端模式、健康检查、并发、超时、默认参数 |
| AI | provider、模型、Ollama 连接测试、JSON 输出测试、prompt、阈值 |
| 存储 | MinIO、bucket、预签名有效期、连接测试 |
| 队列 | 并发、自动恢复、重试次数、轮询间隔、任务保留天数 |
| 备份 | DB + MinIO 完整备份/恢复 |
| 日志 | upload-server、queue worker、AI 原始错误、安全审计 |

## 11. 数据一致性与流水线管理

> 本节是 Luceon2026 运维与长期健康的核心约束。所有开发和部署行为必须遵循本节规则。

### 11.1 系统边界与持久化能力

Luceon2026 由四个相互独立的有状态系统串行构成，每个系统的持久化能力不同：

| 系统 | 持久化介质 | 重启后状态 | 说明 |
| --- | --- | --- | --- |
| **CMS（db-server）** | JSON 文件 + MinIO 卷 | JSON 文件磁盘持久，MinIO 卷持久 | 状态可靠恢复 |
| **MinIO** | Docker named volume | 桶与对象持久 | 原始文件与解析产物存储 |
| **MinerU** | tmux 会话（纯内存队列） | **完全丢失** | 重启即清空，无状态持久 |
| **Ollama** | 模型文件在磁盘 | 模型不变，运行中任务丢失 | 建议设为常驻进程 |

**关键结论**：MinerU 是本系统中唯一**无状态**的组件。任何依赖 MinerU 内存队列恢复数据的做法都是不可靠的。

### 11.2 数据流水线：严格单向串行

整个系统是一条严格单向的串行链路，**不可能出现"有后道数据但无前道数据"的状况**：

```
① 原始上传  →  ② MinIO 存储  →  ③ MinerU 解析  →  ④ Ollama AI 元数据识别
   Material        对象                ParseTask              AiMetadataJob
```

| 阶段 | 产出 | 下游依赖 |
| --- | --- | --- |
| ① 原始上传 | MinIO 对象（`originals/{materialId}/xxx.pdf`） | 无 |
| ② MinerU 解析 | MinIO 对象（`parsed/{materialId}/full.md` 等） | 依赖 ① |
| ③ AI 元数据识别 | DB 记录（`AiMetadataJob.result`） | 依赖 ② |

**严格规则**：阶段 N 的输出，是阶段 N+1 的唯一输入。阶段之间不存在独立并行路径。

### 11.3 数据血缘与清理规则

#### 血缘关系

每个数据实体只能引用**已存在的上游数据**，禁止出现孤儿引用：

- `ParseTask.materialId` → 必须对应存在的 `Material.id`
- `ParseTask` 进入 `ai-pending` 状态时 → 必须存在对应的 `AiMetadataJob`
- `AiMetadataJob.parseTaskId` → 必须对应存在的 `ParseTask.id`

#### 清理规则

| 场景 | 判定条件 | 处理方式 |
| --- | --- | --- |
| **有前道，无后道** | 上游数据存在，下游数据不存在或状态卡死 | 可从前道重试继续（如 MinerU 成功但 AI 卡死 → 重置 parseTask，重触 AI） |
| **有后道，无前道** | 下游数据存在，上游数据不存在（MinIO 对象已被删除） | **不可能**（逻辑错误），直接清除后道所有数据 |
| **删除前道** | 业务上需要删除某个 Material 及其所有关联数据 | 必须级联删除：MinIO 原始对象 → MinerU 解析产物 → parseTask → AI Job |
| **删除后道** | 仅清除某个阶段的数据（如仅删除 AI Job） | 可操作，后续可从最近的前道重新触发 |
| **MinerU 结果不存在** | `ParseTask` 状态为 `ai-pending` 或 `completed`，但 MinIO 解析产物已过期/丢失 | 重置为 `pending`，重新触发 MinerU 解析 |
| **整库重置（开发阶段）** | 每次重新构建部署镜像前 | `docker compose down -v && docker compose up --build -d`（清除所有 named volumes） |

### 11.4 孤儿与失步检测

#### 孤儿 parseTask（无对应 AI Job）

`parseTask.state = 'ai-pending'`，但不存在对应的 `AiMetadataJob.parseTaskId`。

**原因**：MinerU 解析完成后创建 AI Job 失败（如 Ollama 不可达），但 parseTask 状态已推进。

**处理**：删除该 AI Job（若存在），将 parseTask 重置为 `pending`，让 worker 重新触发 AI。

#### 失步 parseTask（AI Job 已是终态但 parseTask 未跟进）

`parseTask.state = 'ai-pending'`，`AiMetadataJob.state` ∈ {`confirmed`, `review-pending`, `failed`}。

**原因**：AI Job 处理完成，但回写 parseTask 状态失败（如网络抖动）。

**处理**：将 parseTask 状态同步推进到与 AI Job 终态一致。

#### 孤儿 AI Job（无对应 parseTask）

`AiMetadataJob` 存在，但 `parseTaskId` 对应的 parseTask 不存在。

**原因**：parseTask 被手动删除但 AI Job 未级联清除，或数据写入顺序问题。

**处理**：直接删除该 AI Job。

### 11.5 系统重启一致性规则

由于 MinerU 采用无状态设计，CMS 重启后**不信任自身状态**，必须主动重建与其他系统的一致性。

#### 重启后链路扫描流程

CMS 启动时，对所有 `pending` / `ai-pending` 状态的 parseTask 执行逐级检查：

```
扫描所有非终态 parseTask
  ↓
对于每一项，依次检查：
  ↓
① MinIO 原始对象存在？
   ├─ 否 → 删除该 parseTask 及所有下游（AI Job）
   └─ 是 → 继续
  ↓
② MinerU 解析产物存在？
   ├─ 否 → 重置为 pending，重新触发 MinerU 解析
   └─ 是 → 继续
  ↓
③ 存在对应的 AI Job？
   ├─ 否 → 重置为 ai-pending，重新触发 AI 识别
   └─ 是 → 检查 AI Job 状态
        ├─ 终态（confirmed/review-pending/failed）→ 同步 parseTask 到终态
        └─ 非终态 → 保持，worker 继续处理
```

#### MinerU 重启但 CMS 未重启

MinerU 重启后，其内存中的任务队列丢失，但 CMS 侧 parseTask 状态可能仍为 `running`。

**检测**：Worker 轮询 MinerU 超时或返回 404 → 判定为 MinerU 侧任务丢失。

**处理**：重置 parseTask 为 `pending`，重新提交 MinerU 解析。

### 11.6 开发期部署规范

**核心原则**：开发/调试阶段，每次重新构建部署镜像前，必须清除所有状态数据，确保三者在同一时间节点的状态一致。

#### 破坏性重建（开发期标准操作）

```bash
cd /Users/concm/prod_workspace/Luceon2026
git pull origin main
docker compose down -v    # 删除所有 named volumes（MinIO 数据卷 + DB 数据卷）
docker compose up --build -d
```

`docker compose down -v` 会清除：
- MinIO 数据卷（桶 + 所有对象）
- DB 数据卷（JSON 文件）

**警告**：此操作会清除所有未入库的资料和解析结果。生产环境严禁使用 `-v`。

#### 非破坏性重启（生产期标准操作）

```bash
cd /Users/concm/prod_workspace/Luceon2026
git pull origin main
docker compose up --build -d
```

不删除 volumes，仅重启容器。适用于：代码更新、配置调整、不需要清除历史数据的场景。

#### MinerU 独立重启

```bash
# 停止 MinerU tmux 会话
tmux kill-session -t mineru_api 2>/dev/null; tmux kill-session -t mineru_gradio 2>/dev/null

# 重启 MinerU
/Users/concm/ops/start-mineru-all.sh
```

MinerU 重启后，CMS 侧应能通过链路扫描自动恢复（见 11.5 节）。

### 11.7 运维诊断工具需求

建议实现一个诊断脚本，定期或在部署后执行，检查数据一致性：

```
检查项：
  1. 所有 parseTask 是否有对应的 MinIO 原始对象（无 → 标记孤儿）
  2. 所有 ai-pending parseTask 是否有对应 AI Job（无 → 标记孤儿 parseTask）
  3. 所有 AI Job 是否有对应 parseTask（无 → 标记孤儿 AI Job）
  4. 所有终态 AI Job 是否已同步 parseTask 状态（否 → 标记失步）
  5. MinerU 服务是否可达（否 → 告警）
  6. MinIO 服务是否可达（否 → 告警）

输出：
  - 诊断报告（正常 / 发现问题）
  - 可执行修复建议
  - 可选：自动执行修复（需明确提示破坏性）
```

### 11.8 本节与其他章节的关系

本节是所有涉及多系统协作功能的基础约束，优先级如下：

| 规则来源 | 说明 |
| --- | --- |
| **本节（第 11 章）** | 跨系统数据一致性、清理规则、重启恢复 — **最高优先级** |
| 第 10.3 节（后端持久任务队列） | 队列实现参考本节一致性规则 |
| 第 10.4 节（本地 MinerU Adapter） | Adapter 结果写入必须遵守本节血缘规则 |
| 第 10.5 节（AI 元数据识别） | AI Job 创建与回写必须遵守本节清理规则 |
| 第 14 节（Docker 部署要求） | 部署命令参考本节 11.6 节规范 |

---

## 12. 数据模型建议

### 12.1 Material

资料原始实体，代表上传的文件。

关键字段：

- `id`
- `title`
- `fileName`
- `fileSize`
- `mimeType`
- `sourceObjectName`
- `sourcePreviewUrl`
- `createdAt`
- `updatedAt`
- `latestTaskId`
- `libraryStatus`

### 12.2 ParseTask

一次 MinerU 解析任务。

关键字段：

- `id`
- `materialId`
- `engine`
- `externalTaskId`
- `stage`
- `state`
- `progress`
- `message`
- `errorCode`
- `errorMessage`
- `retryCount`
- `optionsSnapshot`
- `resultPrefix`
- `markdownObjectName`
- `zipObjectName`
- `rawResult`
- `createdAt`
- `startedAt`
- `completedAt`
- `updatedAt`

### 12.3 AiMetadataJob

一次 AI 元数据识别任务。

关键字段：

- `id`
- `materialId`
- `parseTaskId`
- `providerId`
- `model`
- `state`
- `progress`
- `inputMarkdownObjectName`
- `inputChars`
- `truncated`
- `result`
- `rawResponseSnippet`
- `confidence`
- `needsReview`
- `errorMessage`
- `retryCount`
- `createdAt`
- `completedAt`

### 12.4 TaskEvent

任务事件日志。

关键字段：

- `id`
- `taskId`
- `taskType`
- `level`
- `event`
- `message`
- `payload`
- `createdAt`

## 13. 后端 API 建议

### 13.1 任务 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/tasks` | 创建解析任务 |
| `GET` | `/tasks` | 查询任务列表 |
| `GET` | `/tasks/:id` | 查询任务详情 |
| `POST` | `/tasks/:id/retry` | 重试失败阶段 |
| `POST` | `/tasks/:id/reparse` | 从头重新解析 |
| `POST` | `/tasks/:id/cancel` | 取消任务 |
| `DELETE` | `/tasks/:id` | 删除任务 |
| `GET` | `/tasks/events` | SSE 推送任务变化 |

### 13.2 MinerU API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/mineru/health` | 检查本地 MinerU |
| `POST` | `/mineru/submit` | 内部 adapter 提交任务 |
| `GET` | `/mineru/result/:taskId` | 内部 adapter 获取结果 |

### 13.3 AI API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/ai/test-connectivity` | 测试网络连通性 |
| `POST` | `/ai/test-json` | 测试 JSON 元数据输出 |
| `POST` | `/ai/metadata-jobs` | 创建 AI 识别任务 |
| `POST` | `/ai/metadata-jobs/:id/retry` | 重试 AI 识别 |
| `GET` | `/settings/ai-models` | 拉取模型列表 |

### 13.4 兼容现有代理

现有 `/__proxy/upload/...` 可以继续作为 Nginx/Vite 代理前缀，但后端内部应把职责拆开。前端业务代码不要直接调用 `/parse/local-mineru` 完成长流程，而是调用 `/tasks` 创建任务。

## 14. 状态映射

### 14.1 官方状态到 Luceon2026 阶段

| 官方状态 | Luceon 阶段 | Luceon 状态 |
| --- | --- | --- |
| `loading` | upload | running |
| `waiting-file` | upload | pending |
| `uploading` | upload | running |
| `pending` | mineru | pending |
| `running` | mineru | running |
| `done` | result-store | pending |
| `waiting-download` | result-store | pending |
| `downloading` | result-store | running |
| `unzipping` | result-store | running |
| `unzipped` | ai-metadata | pending |
| `failed` | mineru | failed |
| `download-failed` | result-store | failed |
| `unzip-failed` | result-store | failed |
| `quota-exceeded` | mineru | failed |
| `expire` | mineru | expired |
| `aborted` | task | canceled |
| `local-deleted` | storage | failed |
| `unknown` | task | failed |

### 14.2 Luceon2026 增强状态

新增 AI 和审核状态：

- `ai-pending`
- `ai-running`
- `ai-failed`
- `review-pending`
- `review-confirmed`
- `library-ready`

## 15. Docker 部署要求

默认部署形态：

- Web 前端容器。
- Upload/API server 容器。
- DB server 容器。
- MinIO 容器。
- 本地 MinerU 可运行在宿主机或独立容器。
- Ollama 可运行在宿主机或独立容器。

必须支持宿主机服务访问：

- Mac Docker：`host.docker.internal`
- Linux Docker：`extra_hosts: host.docker.internal:host-gateway`

环境变量要求：

- `LOCAL_MINERU_ENDPOINT`
- `ALLOW_LOCAL_AI_ENDPOINT=true`
- `OLLAMA_ENDPOINT`
- `AI_DEFAULT_MODEL`
- `MINIO_ENDPOINT`
- `MINIO_BUCKET`
- `MINIO_PARSED_BUCKET`
- `QUEUE_MINERU_CONCURRENCY`
- `QUEUE_AI_CONCURRENCY`

## 16. 非功能需求

### 16.1 稳定性

- 页面刷新不丢任务。
- 后端重启后恢复未完成任务。
- AI 模型超时不阻塞解析结果查看。
- MinerU 不可用时不影响已完成结果浏览。

### 16.2 性能

- 任务列表默认分页。
- 大 Markdown 按需加载。
- AI 输入默认截断，最大 200k 字符。
- PDF 预览懒加载。

### 16.3 安全

- 保持 SSRF 防护。
- 仅允许显式配置后访问本地/私网 AI endpoint。
- API Key 和密钥不进入前端 localStorage 明文。
- 任务日志中的密钥脱敏。
- 预签名 URL 有效期可配置。

### 16.4 可观测性

- 每个任务有事件日志。
- 每个外部调用有 request id。
- AI 原始错误保留截断片段。
- 设置页可拉取最近日志。
- 任务失败要显示下一步建议。

## 17. 验收标准

### 17.1 官方交互复刻

- 用户可以从新建任务页上传文件并立即进入任务管理。
- 任务管理页能看到上传、解析、结果处理、AI、审核全阶段状态。
- 失败任务能在对应阶段重试。
- 页面刷新后任务状态仍然正确。
- 后端重启后未完成任务可恢复。

### 17.2 本地 MinerU

- 本地 MinerU 健康检查成功。
- 可以提交 PDF 到本地 MinerU。
- 可以获取 Markdown。
- Markdown 可以保存到 MinIO。
- 解析失败有明确错误和重试入口。

### 17.3 Ollama AI 元数据

- 设置页可以拉取 Ollama 模型列表。
- 连接测试能区分网络失败和 JSON 输出失败。
- 解析完成后自动触发 AI 元数据识别。
- AI 输出能写入结构化字段。
- 低置信度任务进入待审核。
- 重新识别不会破坏原解析产物。

### 17.4 资料库

- 审核通过的资料进入资源库。
- 可以按学科、年级、类型、标签检索。
- 可以回到任务详情查看来源和解析产物。

## 18. 里程碑

### M0（对应原 17 里程碑）：PRD 与官方拆解

- 完成官方客户端/Web 版拆解。
- 完成当前项目差距分析。
- 完成本 PRD。

### M1：后端任务模型与队列

- 新增 `ParseTask`、`AiMetadataJob`、`TaskEvent`。
- 新增持久队列 worker。
- 新增 `/tasks` API。
- 前端只创建任务，不直接跑长流程。

### M2：官方风格任务管理 UI

- 重构 `/workspace` 为新建任务。
- 新增 `/tasks` 任务管理页。
- 新增全局任务状态浮层或顶部状态条。
- 接入 SSE/WebSocket 实时更新。

### M3：本地 MinerU Adapter 稳定化

- 拆分 `upload-server.mjs` 中的本地 MinerU 逻辑。
- 支持任务提交、轮询、结果提取、产物保存。
- 支持重试和恢复。

### M4：AI 元数据流水线

- AI job 队列化。
- Ollama 连接测试分层。
- 元数据 schema 扩展。
- 审核状态落库。

### M5：结果详情与资源库

- 详情页三栏布局。
- Markdown/JSON/文件列表/日志 tabs。
- 元数据审核面板。
- 资源库检索和导出。

### M6：Docker/UAT

- 完整 docker-compose 验证。
- Mac Docker + 宿主机 MinerU/Ollama 验证。
- Linux Docker host-gateway 验证。
- UAT 自动化覆盖核心链路。

## 19. 技术重构建议

### 19.1 后端拆分

当前 `server/upload-server.mjs` 应拆为：

- `server/app.mjs`
- `server/routes/upload-routes.mjs`
- `server/routes/task-routes.mjs`
- `server/routes/mineru-routes.mjs`
- `server/routes/ai-routes.mjs`
- `server/routes/storage-routes.mjs`
- `server/services/mineru/local-adapter.mjs`
- `server/services/mineru/cloud-adapter.mjs`
- `server/services/ai/provider-client.mjs`
- `server/services/queue/task-queue.mjs`
- `server/services/storage/minio-service.mjs`
- `server/services/logging/task-events.mjs`

### 19.2 前端拆分

建议新增：

- `src/app/pages/NewTaskPage.tsx`
- `src/app/pages/TaskManagementPage.tsx`
- `src/app/pages/TaskDetailPage.tsx`
- `src/app/components/task/TaskStageTimeline.tsx`
- `src/app/components/task/TaskProgressCell.tsx`
- `src/app/components/task/TaskActionMenu.tsx`
- `src/app/components/metadata/AiMetadataReviewPanel.tsx`
- `src/app/api/tasks.ts`
- `src/app/api/ai.ts`
- `src/app/api/mineru.ts`

### 19.3 状态管理

前端 store 不应作为长任务事实来源。事实来源应是后端 DB。

前端职责：

- 展示当前任务状态。
- 乐观更新局部 UI。
- 订阅任务事件。
- 提交用户动作。

后端职责：

- 决定任务状态。
- 保存任务参数快照。
- 执行队列。
- 恢复未完成任务。

## 20. 风险与应对

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| 本地 MinerU 接口版本差异 | 提交/结果解析失败 | Adapter 分层，保留 FastAPI 与 Gradio 降级 |
| Ollama 模型输出不稳定 | 元数据 JSON 失败 | JSON 修复、重试、低置信审核、prompt 模板 |
| 大文件解析耗时长 | 用户误以为卡死 | 后端队列 + 阶段事件 + 可恢复状态 |
| `upload-server.mjs` 继续膨胀 | 维护困难 | M1 即拆服务层 |
| Docker 访问宿主机失败 | 本地 MinerU/Ollama 不通 | 设置页诊断 host.docker.internal、IP、端口 |
| MinIO 预签名 URL 过期 | 预览/下载失败 | 存 objectName，按需重新签名 |

## 21. 开放问题

1. 本地 MinerU 最终统一使用 FastAPI `/tasks` 还是继续保留 Gradio 降级？
2. 是否要求支持截图上传？如果支持，应作为二期。
3. AI 元数据字段是否需要按用户业务增加“教材版本、出版社、册别、章节、题型”等字段？
4. 是否需要多用户权限？一期建议不做。
5. 是否要把官方 API 作为本地 MinerU 失败时的 fallback？一期建议保留配置但默认关闭。

## 22. 下一步实施建议

推荐下一步直接进入 M1：

1. 新增后端任务数据模型。
2. 恢复持久化队列，但按本 PRD 重写，不回滚旧实现。
3. 新增 `/tasks` API。
4. 将当前 `/parse/local-mineru` 封装为队列 worker 内部调用。
5. 前端新增任务管理页原型。

完成 M1 后，再做 M2 UI。原因是只改前端无法解决断点问题；流畅体验的根本在后端任务事实源和可恢复队列。
