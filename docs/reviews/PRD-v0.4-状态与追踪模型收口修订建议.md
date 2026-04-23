# PRD v0.4 修订建议：状态与追踪模型收口

日期：2026-04-24  
作者：Lucia  
适用范围：Luceon2026 生产交付前收口阶段  
关联文档：`docs/prd/Luceon2026-PRD-v0.4.md`

## 1. 结论

当前系统出现“任务列表、工作台、资产详情页显示信息不一致，用户难以追踪查询”的现象，不是合理的产品复杂度，也不是单个页面的小 bug。

根因是：**PRD v0.4 的目标已经转向任务中心，但数据模型、页面职责和实施优先级仍保留了 Material 中心时期的多事实源结构。**

因此，PRD v0.4 方向正确，但还不够硬。它需要在“状态与追踪模型”上做一次契约级修订：

> ParseTask 是唯一任务生命周期事实源；Material 是输入资料与最终元数据事实源；assetDetails 不再作为持久事实源，只能作为由 Material + ParseTask 派生出来的视图。

## 2. 第一性原理

Luceon2026 的根本任务目标不是“做一个文件列表”，而是：

> 以 MinerU 官方在线产品为基石，接入本地 MinIO、MinerU、Ollama，形成教育内容管理平台。

其中最核心的用户行为是一次长耗时加工：

1. 用户提交文件。
2. 系统创建解析任务。
3. 任务进入队列。
4. MinerU 解析。
5. AI 提取元数据。
6. 人工审核。
7. 结果进入素材/成果管理。

所以第一性问题是：

> 哪一个对象代表“这一次加工请求”的真实生命周期？

答案只能是：`ParseTask`。

`Material` 表达“这个资料是什么”，不应表达“这一次加工任务现在跑到哪一步”。  
`assetDetails` 表达“资产详情页如何展示”，不应作为第三套持久数据事实。

## 3. 官方 MinerU 的启发

MinerU 官方 Extractor 的关键不是 UI 像素，而是任务式心智：

- Create Task
- Tasks
- Collections / Results
- 每个任务可查询、可查看结果、可重试或导出

用户理解的是：

> 我创建了一个 task，然后看 task 的状态和结果。

Luceon2026 做了本地化扩展：加入教育元数据、Ollama AI、人工审核、成果库。这些扩展不改变底层事实：**任务生命周期必须以 task 为中心。**

## 4. 当前 PRD v0.4 的正确判断

PRD v0.4 已经识别出主要方向：

1. 从 `Material` 对象视图转向 `ParseTask` 任务视图。
2. 产品目标是复刻 MinerU Extractor 的任务式交互。
3. 任意任务状态应能在任务列表与任务详情页准确看到。
4. 不应出现“前端说在处理、后端其实已经失败”的漂移。
5. 需要统一 Canonical State Machine。

这些判断是正确的，应保留。

## 5. 当前 PRD v0.4 的问题

### 5.1 目标是任务中心，但模型仍允许多事实源

PRD 说用户主要交互对象是任务，但仍继续给 `Material` 定义 `status / mineruStatus / aiStatus`，并允许前端页面基于这些字段推导处理阶段。

这会导致：

- `/tasks` 看 `ParseTask.state`
- `/workspace` 看 `Material.status / mineruStatus / aiStatus`
- `/asset/:id` 看 `assetDetails.status/title/metadata`

三个页面各自解释现实，必然出现漂移。

### 5.2 Material 状态枚举与代码现实不一致

PRD v0.4 中 Material 示例仍写有：

- `status: processing | analyzed | failed | idle`
- `mineruStatus: pending | running | done | failed`

但当前代码和数据中实际使用：

- `status: processing | reviewing | completed | failed`
- `mineruStatus: pending | processing | completed | failed`
- `aiStatus: pending | analyzing | analyzed | failed`

这说明 PRD 作为工程契约仍不够精确。状态枚举不一致会让开发继续按各自理解写映射。

### 5.3 “资产详情页向任务详情页过渡”不应是 P1

PRD v0.4 把资产详情页向任务详情页过渡放在 P1，但当前问题正是从这里爆发的。

资产详情页现在是用户追踪处理状态的重要入口。如果它继续以 `assetDetails` 为主数据源，就会持续和任务页、工作台冲突。

因此该项应从 P1 提升为 P0。

### 5.4 “不做全面前端状态重构”需要重新表述

PRD v0.4 的非目标写着不做前端大规模状态重构。这个约束的原意是降低风险，但现在变成了事实源收口的阻碍。

建议改为：

> 不做框架级重构；但必须做状态事实源收口。前端可以继续使用 Context + Reducer，但页面不得再以多个持久副本各自解释任务生命周期。

## 6. 当前代码事实

### 6.1 工作台以 Material 推导状态

`WorkspacePage.tsx` 用 `Material.status / mineruStatus / aiStatus` 判断“待处理、处理中、失败、完成”。

风险：

- 如果任务已经 failed，但 Material 未被同步更新，工作台仍可能显示 pending/processing。

### 6.2 任务列表以 ParseTask 为主

`TaskManagementPage.tsx` 读取 `/tasks`，以 `ParseTask.state` 为主要状态。

这是更接近官方任务模型的方向，但它又拿 Material 做诊断矩阵，因此当 Material 漂移时，诊断会变成“任务事实 + 素材副本”的混合判断。

### 6.3 资产详情页以 assetDetails 为主

`AssetDetailPage.tsx` 使用 `state.assetDetails[numId]` 作为 `detail`，标题、状态、metadata 均可能来自旧快照。

`assetDetails` 由 `ADD_MATERIAL` 创建，并在后续 reducer 中“尽量同步”部分字段。这不是可靠事实源。

实际观察到的例子：

- `materials[3212565272602346].title = concurrency-test-A`
- `assetDetails[3212565272602346].title = 单页快速测试`
- 任务已 failed，但 material 仍显示 `processing / pending / pending`

这说明数据漂移已经发生。

## 7. 修订后的对象职责

### 7.1 ParseTask

唯一表达任务生命周期：

- 当前状态
- 阶段
- 进度
- 失败原因
- 关联素材
- 事件日志
- 操作入口：Retry / Reparse / Re-AI / Cancel / Review / Export

用户追踪处理进度时，必须最终落到 `ParseTask`。

### 7.2 Material

只表达资料事实：

- 原始文件
- 文件名
- 类型
- 大小
- MinIO objectName
- 最终确认后的元数据
- 当前推荐展示任务 ID（派生或后端维护）

Material 可以保留摘要性字段，但不得成为任务生命周期的唯一判断来源。

建议字段：

```ts
currentTaskId?: string
lastTaskId?: string
lastCompletedTaskId?: string
lastFailedTaskId?: string
```

这些字段用于导航和摘要，不替代 `ParseTask.state`。

### 7.3 assetDetails

建议降级为视图模型：

- 不再作为持久化事实源。
- 资产详情页运行时由 `Material + ParseTask + AI metadata + parsed files` 生成。
- 若短期必须保留，必须声明为 legacy cache，并逐步去除写入。

## 8. 修订后的页面职责

### 8.1 工作台 `/workspace`

定位：资料入口 + 当前任务摘要。

每一行至少展示：

- Material 标题/文件名
- 当前任务 ID
- 当前任务状态
- 最近更新时间
- 失败原因摘要
- “查看任务”入口

状态展示必须优先来自当前任务的 `ParseTask.state`。

### 8.2 任务管理 `/tasks`

定位：任务追踪中心。

必须展示：

- Task ID
- Material 标题/文件名
- Task state
- Stage / progress
- 最近事件
- 失败原因
- 操作按钮

任务管理页应成为用户查问题的第一入口。

### 8.3 任务详情 `/tasks/:id`

定位：单次加工的完整事实页。

必须展示：

- Task timeline
- 当前状态
- 关联 Material
- MinerU 产物
- AI 结果
- 人工审核
- 操作历史

### 8.4 资产详情 `/asset/:id`

定位：资料详情 + 任务历史入口。

不再承担“当前加工状态事实页”的职责。它应该展示：

- Material 基础信息
- 最终元数据
- 当前任务卡片
- 最近任务列表
- 跳转任务详情

资产详情页里的“开始解析”应被重新定义：

- 若无当前活跃任务：创建新任务
- 若有当前活跃任务：显示并跳转该任务
- 若有 failed/completed/review-pending 任务：通过明确动作 Retry / Reparse / Re-AI，而不是重新 POST `/tasks`

## 9. 状态契约修订建议

### 9.1 ParseTask.state 是唯一任务状态

保持 PRD v0.4 的状态集合：

```text
pending
running
result-store
ai-pending
ai-running
review-pending
completed
failed
canceled
```

`uploading` 仅允许作为前端本地临时态，不落 `tasks` 表。

### 9.2 Material 展示态改为派生

Material 的 `status / mineruStatus / aiStatus` 应从“事实源”改为“摘要缓存”或逐步废弃。

短期兼容：

- 后端可以继续回填这些字段，供旧页面展示。
- 但前端新逻辑不得只依赖它们判断任务状态。

中期目标：

- 工作台从 `tasks + materials` join 生成状态。
- 资产详情页从 `tasks + materials` join 生成状态。

### 9.3 assetDetails 不再新增事实

禁止新增只写入 `assetDetails` 而不写入 `materials/tasks` 的业务事实。

## 10. 一致性不变量修订建议

新增以下不变量：

1. 同一 Material 同时最多存在一个 active ParseTask。
2. 工作台展示的当前状态必须等于该 Material 当前 active/latest Task 的状态派生结果。
3. 资产详情页标题必须与 canonical Material 标题一致，或明确显示“AI 建议标题”和“文件名”两个不同字段。
4. `assetDetails[id]` 不得与 `materials[id]` 存在冲突性标题、状态、metadata。
5. `ParseTask.state=failed` 时，若该任务是 Material 当前任务，则工作台不得继续显示为 pending/processing。
6. 任何页面不得基于 `Material.mineruStatus=pending` 覆盖 `ParseTask.state=failed` 的事实。

## 11. 验收标准修订建议

新增 UAT：

1. 上传 PDF 后，工作台、任务列表、任务详情页对同一任务状态一致。
2. 强制 MinerU 失败后，工作台显示失败，任务列表显示失败，资产详情页当前任务卡片显示失败。
3. 同一素材多次点击解析，不产生多个 active tasks。
4. 同一素材存在历史 failed task + 最新 completed task 时，各页面明确显示“当前任务”和“历史任务”。
5. 修改 AI 元数据标题后，Material 标题、任务详情标题、资产详情标题口径一致。
6. 页面级 UAT 不只断言“不崩溃”，还断言跨页面同一事实一致。

## 12. 建议的执行顺序

### P0-1：修订 PRD 契约

把本建议合入 PRD v0.4 或升级为 PRD v0.5。

必须明确：

- ParseTask 是任务生命周期唯一事实源。
- Material 是资料与最终元数据事实源。
- assetDetails 是 legacy view/cache，不再作为业务事实源。

### P0-2：建立统一派生层

新增前端/后端共享的状态派生函数：

```ts
deriveMaterialTaskView(material, tasks)
deriveTaskBucket(task.state)
deriveCurrentTask(materialId, tasks)
```

工作台、资产详情、任务列表必须复用同一套派生规则。

### P0-3：工作台和资产详情页改为任务引用型

先不大改 UI，只改状态来源：

- 工作台状态来自 current/latest task。
- 资产详情页标题来自 Material canonical 字段。
- 资产详情页展示当前任务卡片和跳转入口。

### P0-4：一致性扫描增加跨页面事实不变量

把上面第 10 节的不变量加入 `/audit/consistency`。

### P1：逐步下线 assetDetails 写入

保留读取兼容，但不再向 `assetDetails` 写业务事实。

### P1：清理旧端点调用

资产详情页的 AI 分析、重新解析都必须走任务动作接口，不再绕过任务模型。

## 13. 对现状的最终判断

当前现状不是合理复杂度，而是“任务中心目标”和“资料中心遗留架构”并存造成的结构性漂移。

PRD v0.4 的方向是正确的，但它把关键转型留得太软：

- 目标写成任务中心；
- 数据模型仍保留多套状态；
- 资产详情页迁移排到 P1；
- 前端事实源收口被“非目标”弱化。

因此生产交付前必须先完成状态与追踪模型收口，否则后续会不断出现类似问题：

- 重复任务
- 页面状态不一致
- 标题不一致
- 用户找不到当前任务
- 审计 findings 稳定但用户体验仍不可信

Lucia 建议：下一步不要继续零散修 bug，先把本建议转为一份 P0 收口任务书，再由 lucode 分批实现。
