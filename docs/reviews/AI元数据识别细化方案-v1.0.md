# AI 元数据识别细化方案 v1.0

> 依据：《文本资料 LLM 分类与标签标准 v0.2》
>
> 适用项目：Luceon2026
>
> 方案定位：作为后续 lucode 修订 AI Metadata Worker、Material 元数据结构、成果库筛选与人工复核页面的实现依据。

## 1. 背景与问题定义

Luceon2026 当前已经基本打通：

1. 文件上传。
2. MinIO 原始文件存储。
3. MinerU 本地解析。
4. parsed 产物入库。
5. AI Metadata Job 创建。
6. 成果库展示。

但 AI 元数据识别阶段仍处于早期简化形态。

当前 `metadata-worker.mjs` 主要识别：

- title
- subject
- grade
- materialType
- language
- curriculum
- tags
- summary
- confidence
- needsReview

当前 `Material.metadata` 主要承载：

- subject
- grade
- language
- country
- type
- summary
- standard
- region
- aiConfidence

这套字段可以支撑“列表展示”，但不足以支撑真正的资料管理。它缺少以下关键能力：

- 多级分类：一级资料域、二级集合、课程体系、学段、年级、科目。
- 套系关系：series、edition、volume、unit、component。
- 组件语义：学生用书、教师用书、答案、词汇表、讲义、课件、过程材料等。
- 证据链：为什么这样判断，依据来自文件名、MinIO 对象键、资料逻辑归类、封面、目录还是正文。
- 置信度语义：high / medium / low，而不是单纯数字。
- 人工复核原因：低置信度、疑似重复、疑似节选、OCR 差、路径冲突等。
- 推荐逻辑归类路径：只建议资料管理目录，不直接移动 MinIO 对象。
- 保留价值与风险：核心保留、组件保留、低优先级归档、未经人工确认不得删除等。

因此，本方案的核心不是“多生成几个标签”，而是把 AI metadata 从展示字段升级为资料管理事实层。

## 2. 设计原则

### 2.1 MinerU 与 AI Metadata 分层

MinerU 只负责把原始文件转换为可读产物：

- full.md
- middle.json
- model.json
- content_list.json
- images / tables / layout 等附属产物

AI Metadata 负责基于 parsed 产物进行资料治理判断：

- 资料是什么。
- 属于哪个分类体系。
- 是否属于某套教材或考试体系。
- 是主资源还是组件。
- 是否低置信度、疑似重复、疑似节选或过程材料。
- 是否需要人工复核。
- 推荐放到哪里。

两者不得混合。AI Metadata 的失败不得回滚 MinerU 成果。

### 2.2 Luceon 使用 MinIO 管理文件，LLM 只建议逻辑归类，不执行对象移动或删除

Luceon2026 当前项目事实源不是本机物理目录，而是 MinIO 对象存储与数据库记录：

| 层级 | 当前项目事实源 | 说明 |
|---|---|---|
| 原始文件 | `Material.metadata.objectName` | 例如 `originals/{materialId}/xxx.pdf`，位于原始资料 bucket |
| 解析产物 | `Material.metadata.parsedPrefix` / `markdownObjectName` / `zipObjectName` | 例如 `parsed/{materialId}/full.md`，位于 parsed bucket |
| 资料管理分类 | `Material.metadata.aiClassificationV02` | AI 识别出的逻辑分类，不等同于物理路径 |
| 成果库展示 | `Material` + `ParseTask` + `aiClassificationV02` 投影 | 页面筛选与展示的事实源 |

因此，标准 v0.2 中的 `original_path/current_path/recommended_path` 在 Luceon 中必须重新解释：

| 标准字段 | Luceon 项目内含义 |
|---|---|
| `original_path` | 可选的原始来源线索；如果上传时没有物理来源路径，则为空或记录浏览器上传文件名，不作为事实源 |
| `current_path` | 不表示物理路径；应映射为当前 MinIO 对象引用摘要，如 raw bucket/objectName 与 parsed bucket/prefix |
| `recommended_path` | 推荐的资料库逻辑归类路径，建议改名或镜像为 `recommended_catalog_path` |
| `path_change_type` | 推荐的逻辑归类动作，不代表移动文件 |

LLM 输出中的 `recommended_catalog_path` 只是资料库逻辑目录建议。

LLM 不得：

- 删除文件。
- 移动 MinIO 对象。
- 覆盖文件。
- 自动合并重复文件。
- 将低置信度资料标记为无需审核。

后续如果要做资料整理动作，应该先改变数据库中的分类字段或人工审核状态；是否重写 MinIO object key 必须另建迁移流程，不能由 AI Metadata 阶段隐式执行。

### 2.3 保留兼容投影

现有成果库、任务详情和资产详情仍依赖简化字段。为了避免大范围 UI 断裂，必须同时保留：

1. 完整结构化字段：`metadata.aiClassificationV02`
2. 兼容投影字段：`metadata.subject / grade / standard / type / summary / aiConfidence`
3. 扁平标签：`Material.tags`

完整结构用于未来资料治理；兼容投影用于当前页面平滑过渡。

### 2.4 证据优先

任何关键分类都必须尽量附带证据。

证据来源包括：

- filename
- minio_object_name
- parsed_prefix
- catalog_context
- title_page
- copyright_page
- toc
- header_footer
- body
- markdown_metadata
- mineru_artifact_signal

如果证据不足，必须进入低置信度或人工复核，而不是强行分类。

## 3. 目标元数据结构

### 3.1 Material.metadata 顶层新增字段

建议在 `Material.metadata` 中新增以下字段：

```json
{
  "aiClassificationStandardVersion": "llm_text_classification_v0.2",
  "aiClassificationAnalyzedAt": "2026-04-30T00:00:00.000Z",
  "aiClassificationProvider": "ollama",
  "aiClassificationModel": "qwen3.5:9b",
  "aiClassificationInputHash": "sha256:...",
  "aiClassificationV02": {}
}
```

说明：

| 字段 | 说明 |
|---|---|
| `aiClassificationStandardVersion` | 当前使用的分类标准版本 |
| `aiClassificationAnalyzedAt` | 本次识别完成时间 |
| `aiClassificationProvider` | 使用的 AI provider |
| `aiClassificationModel` | 使用的模型 |
| `aiClassificationInputHash` | 本次抽样输入哈希，用于判断是否需要重跑 |
| `aiClassificationV02` | 完整分类结果 |

### 3.2 aiClassificationV02 结构

```json
{
  "manifest_id": "",
  "source": {
    "material_id": "",
    "file_name": "",
    "file_ext": "",
    "file_size": 0,
    "raw_bucket": "",
    "raw_object_name": "",
    "parsed_bucket": "",
    "parsed_prefix": "",
    "markdown_object_name": "",
    "original_path_hint": "",
    "current_catalog_hint": ""
  },
  "filename": "",
  "recommended_catalog_path": "",
  "catalog_change_type": "keep_current",
  "classification": {
    "l1_domain": {"zh": "", "en": ""},
    "l2_collection": {"zh": "", "en": ""},
    "country_region": {"zh": "", "en": ""},
    "curriculum_standard": {"zh": "", "en": ""},
    "education_stage": {"zh": "", "en": ""},
    "grade_level": {"zh": "", "en": ""},
    "subject": {"zh": "", "en": ""},
    "resource_type": {"zh": "", "en": ""},
    "component_type": {"zh": "", "en": ""},
    "series_title": "",
    "edition": {"zh": "", "en": ""},
    "volume_level": {"zh": "", "en": ""},
    "unit_lesson": "",
    "year": "",
    "publisher_org": ""
  },
  "tags": {
    "content_tags": [],
    "skill_tags": [],
    "format_tags": [],
    "quality_tags": [],
    "relationship_tags": [],
    "retention_tags": [],
    "risk_tags": []
  },
  "evidence": [],
  "confidence": "low",
  "human_review_required": true,
  "human_review_reason": "",
  "duplicate_candidate": false,
  "primary_resource_candidate": false,
  "markdown_quality": "partial",
  "proposed_new_tags": []
}
```

兼容说明：

- 如果需要完全贴合外部标准 v0.2，可在导出给 LLM 的 prompt 中继续包含 `original_path/current_path/recommended_path/path_change_type`。
- 但在 Luceon 内部落库时，应以 `source.raw_object_name`、`source.parsed_prefix`、`recommended_catalog_path`、`catalog_change_type` 为准。
- 不得让 lucode 按物理路径移动本地文件。

### 3.3 兼容投影字段

AI worker 在保存完整结构后，应同时生成兼容字段：

```json
{
  "subject": "英语",
  "grade": "Level 2",
  "standard": "国际教材通用",
  "type": "学生用书",
  "summary": "……",
  "aiConfidence": "medium"
}
```

映射规则：

| 兼容字段 | 来源 |
|---|---|
| `subject` | `classification.subject.zh` |
| `grade` | 优先 `classification.grade_level.zh`，其次 `volume_level.zh` |
| `standard` | `classification.curriculum_standard.zh` |
| `type` | 优先 `classification.resource_type.zh`，其次 `component_type.zh` |
| `summary` | AI 生成的简短摘要；若无，则从 classification 生成一句描述 |
| `aiConfidence` | `confidence` |
| `tags` | 从 7 类标签中提取 zh，去重后扁平化 |

## 4. 受控分类体系

### 4.1 一级分类 l1_domain

必须从以下值选择：

| zh | en |
|---|---|
| `01_出版教材与成套课程` | `Published Textbooks and Course Series` |
| `02_考试测评与真题` | `Exams Assessments and Past Papers` |
| `03_学校与机构专属资料` | `School and Institution Specific Materials` |
| `04_中国课标与同步教辅` | `Chinese Curriculum and Synchronized Supplements` |
| `05_专项训练与讲义` | `Special Training and Handouts` |
| `06_公司行政经营资料` | `Company Administration and Business Documents` |
| `99_待识别与低置信度` | `Unidentified or Low Confidence` |

### 4.2 二级集合 l2_collection

第一阶段先支持标准 v0.2 中列出的主要集合：

- Reading Explorer
- Oxford Discover
- Oxford Reading Tree
- Wonders
- Journeys
- Our World
- Grammar Cue
- Grammar in Use
- Grammar in Context
- Great Writing
- Time Zones
- Envision
- 新加坡数学
- Cambridge IGCSE
- KET_PET
- TOEFL Junior_Primary
- 中国课标同步教辅各学科
- 学校或机构专属资料
- 专项训练与讲义

如果无法命中受控集合：

- 允许 `l2_collection.zh = "其他国际教材"` 或 `"未知"`
- 必须降低置信度或标记人工复核
- 如果模型提出新集合，写入 `proposed_new_tags` 或未来 `proposed_new_collections`

### 4.3 资料类型与组件类型分离

必须区分：

- `resource_type`：资料本体类型，例如教材、试卷、真题、讲义、课件。
- `component_type`：在套系里的组件角色，例如学生用书、练习册、教师用书、答案、词汇表、视频脚本。

例子：

| 文件 | resource_type | component_type |
|---|---|---|
| Reading Explorer 2 Student Book | 学生用书 | 学生用书 |
| Reading Explorer 2 Answer Key | 答案解析 | 答案 |
| Cambridge IGCSE Additional Mathematics Coursebook | 教材 | 主体资料 |
| Unit 3 Vocabulary Handout | 讲义 | 词汇默写或练习 |

## 5. Markdown 抽样策略

当前 AI worker 只截取 full.md 前部文本，这对教材类资料不够可靠。建议新增 `metadata-sampler`。

### 5.1 输入

输入应包含：

```json
{
  "manifest_id": "material-123",
  "materialId": 123,
  "filename": "",
  "file_ext": ".pdf",
  "file_size": 123456,
  "rawBucket": "eduassets",
  "rawObjectName": "originals/123/upload.pdf",
  "parsedBucket": "eduassets-parsed",
  "parsedPrefix": "parsed/123/",
  "markdownObjectName": "parsed/123/full.md",
  "parsedFilesCount": 100,
  "mineruExecutionProfile": {}
}
```

### 5.2 抽样内容

建议抽样：

| 样本段 | 用途 |
|---|---|
| front_pages | 封面、标题、版权页、出版社、版次 |
| toc_pages | 目录、单元结构、完整性 |
| middle_pages | 正文内容、学科、难度、题型 |
| back_pages | 答案、索引、词汇表、附录 |
| header_footer | 书名、级别、章节、页码 |
| artifact_signal | parsedFilesCount、是否扫描件、是否 OCR 不稳定 |

### 5.3 抽样方式

第一版可不做页级复杂定位，先对 Markdown 文本做结构切片：

- head：前 25%
- middle：中间 25% 附近抽样
- tail：后 25%
- 如果有标题/目录关键词，额外提取附近段落
- 总字符建议控制在 60k 至 100k，具体由 AI provider 设置决定

如果文档特别长，优先保留：

1. 文件名、MinIO objectName、parsedPrefix、当前资料库逻辑上下文。
2. 封面/标题/版权。
3. 目录。
4. 页眉页脚。
5. 尾部答案/索引/附录信号。

## 6. Prompt 与输出校验

### 6.1 Prompt 结构

Prompt 应拆成三层：

1. 系统角色：教育资料整理专家。
2. 受控标准摘要：L1/L2、字段定义、禁止行为。
3. 当前文件输入：文件名、MinIO 对象引用、当前资料库逻辑上下文、抽样 Markdown。

禁止把完整 1000 行标准原样塞入每次调用。应在代码中维护压缩后的标准 prompt。

### 6.2 输出要求

必须严格 JSON。

必须包含：

- `classification`
- `tags`
- `evidence`
- `confidence`
- `human_review_required`
- `markdown_quality`

### 6.3 校验规则

AI 返回后必须做本地校验与归一：

| 场景 | 处理 |
|---|---|
| JSON 解析失败 | 生成低置信度骨架，进入 review-pending |
| 缺少 classification | 生成低置信度骨架 |
| 标签不是中英文对象 | 过滤或转为 proposed_new_tags |
| confidence 不在 high/medium/low | 归一为 low |
| low 但 human_review_required=false | 强制改 true |
| proposed_new_tags 非空 | 强制 human_review_required=true |
| 关键字段未知过多 | 强制 low 或 medium |
| recommended_catalog_path 为空 | 允许，但 catalog_change_type 应为 needs_human_review |

## 7. 状态语义

AI Metadata Job 的终态应根据 v0.2 输出决定：

| AI 输出 | Job 状态 | ParseTask/Material 表现 |
|---|---|---|
| high 且无需人工审核 | confirmed / analyzed | 可作为已分析成果 |
| medium 且无需人工审核 | confirmed 或 review-pending，取决于设置 |
| low | review-pending | 解析完成，待人工复核 |
| human_review_required=true | review-pending | 展示具体审核原因 |
| JSON 失败 / provider fallback | review-pending | 标记低置信度 |
| AI provider 失败且无法降级 | failed 或 create-failed | 不影响 MinerU 成果 |

建议第一版保守：

- high + 无风险：confirmed
- 其他：review-pending

这符合资料管理场景，因为误归类比晚一点确认更危险。

## 8. UI 使用方式

### 8.1 成果库列表

第一阶段成果库列表仍显示简化字段：

- 标题
- 学科
- 年级/级别
- 资料类型
- 产物数
- AI 状态
- 置信度
- 是否待复核

但筛选数据应逐步改为来自 `aiClassificationV02.classification`。

建议新增筛选：

- 一级分类
- 二级集合
- 课程体系
- 学段
- 年级/级别
- 科目
- 资料类型
- 组件类型
- 置信度
- 是否需要人工复核

### 8.2 资产详情页

资产详情页应新增“AI 元数据识别”区块：

1. 分类结果。
2. 套系/版次/级别/组件。
3. 标签分组。
4. 证据链。
5. 置信度。
6. 人工复核原因。
7. 推荐资料库逻辑归类路径。

页面必须明确：

- 推荐目录只是资料管理建议。
- LLM 未执行 MinIO 对象移动。
- 低置信度必须人工确认。

### 8.3 人工复核

第一版不必实现完整审核工作台，但必须在数据层预留：

- `human_review_required`
- `human_review_reason`
- `reviewDecision`
- `reviewedBy`
- `reviewedAt`
- `reviewNotes`

## 9. 与现有数据兼容

### 9.1 旧数据

旧数据没有 `aiClassificationV02` 时：

- 成果库继续使用旧字段。
- 资产详情页显示“尚未执行 v0.2 元数据识别”。
- 不强制批量迁移。

### 9.2 新数据

新数据应同时写：

- 完整结构：`metadata.aiClassificationV02`
- 兼容字段：`metadata.subject / grade / standard / type / summary / aiConfidence`
- 扁平标签：`Material.tags`

### 9.3 重新识别

后续可支持“重新执行 AI 元数据识别”。

重跑时必须记录：

- previous standard version
- previous result snapshot
- current result snapshot
- input hash

不应静默覆盖人工确认结果。

## 10. 数据安全边界

本方案不修改：

- MinerU 官方源码。
- MinerU 提交参数。
- pipeline 默认策略。
- 上传队列。
- parsed-zip。
- MinIO 入库逻辑。
- 任务取消/reset-test-env。
- 成果库准入口径。

本方案只定义 AI Metadata 识别、落库、兼容投影和 UI 展示方案。

## 11. 第一阶段实现任务建议

建议下一步给 lucode 的任务不是泛化重构，而是一个可验收的小补丁：

《P1 Patch：AI 元数据识别 v0.2 Schema、Markdown 抽样与兼容落库收口》

### 11.1 修改范围

允许修改：

- `server/services/ai/metadata-worker.mjs`
- `server/services/ai/metadata-job-client.mjs`
- 新增 `server/services/ai/metadata-standard-v0.2.mjs`
- 新增 `server/services/ai/metadata-sampler.mjs`
- `src/store/types.ts`
- 必要的 smoke tests
- `说明文档.md`

暂不修改：

- 成果库筛选准入口径。
- MinIO 对象移动/删除。
- MinerU 解析链路。
- parsed-zip。
- MinIO 入库。

### 11.2 实现要求

1. 新增 v0.2 schema 与 prompt builder。
2. 新增 Markdown sampler。
3. AI worker 使用 v0.2 prompt 输出完整 JSON。
4. 本地校验 AI 输出。
5. 写入 `Material.metadata.aiClassificationV02`。
6. 写入兼容字段。
7. 写入 `Material.tags` 扁平标签。
8. `human_review_required=true` 时 AI Job 进入 `review-pending`。
9. JSON 失败或字段不足时，不失败回滚，进入低置信度 review-pending。
10. `recommended_catalog_path` 只写入 metadata，不改变 MinIO objectName。

### 11.3 Smoke 测试

至少覆盖：

1. Reading Explorer Student Book。
2. Cambridge IGCSE Coursebook。
3. 答案/Answer Key 组件。
4. 低质量 OCR 或 evidence 不足。
5. AI 返回缺字段。
6. proposed_new_tags 触发 review。

断言：

- `aiClassificationStandardVersion === "llm_text_classification_v0.2"`
- `aiClassificationV02.classification.l1_domain` 存在。
- `tags.content_tags` 等分组存在。
- `evidence` 至少 1 条。
- 兼容字段 subject/grade/type 被正确投影。
- low confidence 必须 review。
- 不产生 MinIO 删除/移动动作。

## 12. 面向 Luceon 项目目标的分类与标签修订

标准 v0.2 的原始分类与标签体系适合“文件资料整理”。但 Luceon2026 的产品目标是“任务式文档解析与元数据审核工作台”，并且要支撑大量 PDF 的持续解析、成果库检索、人工复核和安全治理。因此需要对标准进行项目化收敛。

修订原则：

1. 分类字段用于稳定筛选与资产管理，不做自由标签。
2. 标签字段用于检索、内容发现与治理信号，不承载主分类。
3. MinIO objectName 是存储事实源，分类只改变数据库元数据。
4. review-pending 要有明确原因，而不是泛泛“待审核”。
5. 面向上千 PDF 批量处理，字段必须稳定、可校验、可索引。

### 12.1 分类字段分层

建议把 classification 拆成四层，而不是把所有字段同等对待。

#### A. 主筛选分类 Primary Facets

这些字段进入成果库主筛选、统计和批量审核：

| 字段 | UI 名称 | 说明 |
|---|---|---|
| `domain` | 资料域 | 对应原 `l1_domain` |
| `collection` | 套系/集合 | 对应原 `l2_collection`，可命中 Reading Explorer、Cambridge IGCSE 等 |
| `curriculum` | 课程体系 | Cambridge IGCSE、中国课标、Common Core、IB 等 |
| `stage` | 学段 | 小学、初中、高中、全学段等 |
| `level` | 年级/级别 | G1-G12、Level 1-6、Foundation 等 |
| `subject` | 学科 | 数学、英语、语文、科学等 |
| `resource_type` | 资料类型 | 教材、试卷、讲义、练习册、答案解析等 |
| `component_role` | 套系角色 | 主体资料、学生用书、教师用书、答案、课件、词汇表等 |

项目内建议字段命名：

```json
{
  "primary_facets": {
    "domain": {"zh": "01_出版教材与成套课程", "en": "published_textbook_series"},
    "collection": {"zh": "Cambridge IGCSE", "en": "cambridge_igcse"},
    "curriculum": {"zh": "Cambridge IGCSE", "en": "cambridge_igcse"},
    "stage": {"zh": "高中", "en": "senior_secondary"},
    "level": {"zh": "IGCSE", "en": "igcse"},
    "subject": {"zh": "数学", "en": "mathematics"},
    "resource_type": {"zh": "教材", "en": "textbook"},
    "component_role": {"zh": "主体资料", "en": "main_resource"}
  }
}
```

说明：

- `resource_type` 与 `component_role` 应保留区分，但 UI 可以合并展示。
- `collection` 不应完全固定死；已知套系命中受控值，未知套系允许进入 `collection.zh = "其他"` 并保留 `series_title`。
- `domain=06_公司行政经营资料` 是必要安全兜底，但在教育资料成果库中默认应进入人工复核或隔离视图。

#### B. 描述性元数据 Descriptive Metadata

这些字段用于详情页、搜索和后续整理，不作为第一屏主筛选：

| 字段 | 说明 |
|---|---|
| `series_title` | 系列名 |
| `edition` | 版次 |
| `volume_level` | 册/Level |
| `unit_lesson` | 单元/课时 |
| `year` | 出版年/考试年 |
| `publisher_org` | 出版社/机构 |
| `language` | 中文/英文/双语 |
| `exam_board` | Cambridge、Edexcel、AQA 等，可选扩展 |
| `paper_code` | 试卷代码，可选扩展 |

#### C. 治理状态 Governance

这些字段驱动 review-pending、低价值归档、重复候选等，不应混进普通标签：

```json
{
  "governance": {
    "confidence": "high | medium | low",
    "human_review_required": true,
    "human_review_reason": "",
    "markdown_quality": "good | partial | poor | unreadable",
    "primary_resource_candidate": false,
    "duplicate_candidate": false,
    "retention_policy": "keep_primary | keep_component | keep_pending_review | archive_low_priority",
    "risk_flags": []
  }
}
```

#### D. 证据 Evidence

证据是项目目标里的“可审可用”核心。任何低置信度、套系判断、组件判断，都应该能在详情页看到依据。

```json
{
  "evidence": [
    {
      "type": "filename | title_page | toc | header_footer | body | minio_object_name",
      "quote_or_summary": "",
      "supports": ["subject", "collection", "component_role"]
    }
  ]
}
```

### 12.2 标签体系修订

标准 v0.2 中 7 组标签是有价值的，但不应在 Luceon 里全部作为同一种“标签”使用。建议拆成三类。

#### A. 检索标签 Search Tags

用于成果库搜索、卡片展示、全文检索辅助。

建议保留：

- `topic_tags`：由原 `content_tags` 收敛而来，表示知识主题或内容主题。
- `skill_tags`：能力标签。

示例：

```json
{
  "search_tags": {
    "topic_tags": [
      {"zh": "代数", "en": "algebra"},
      {"zh": "非虚构阅读", "en": "nonfiction_reading"}
    ],
    "skill_tags": [
      {"zh": "阅读理解", "en": "reading_comprehension"},
      {"zh": "问题解决", "en": "problem_solving"}
    ]
  }
}
```

#### B. 系统标签 System Tags

这些不应由 LLM 自由判断，应优先由系统确定：

| 标签组 | 来源 |
|---|---|
| `format_tags` | 文件后缀、MIME、MinerU 解析方式、是否 OCR |
| `artifact_tags` | parsedFilesCount、是否有 images/tables/model output |
| `engine_tags` | pipeline / hybrid / vlm、OCR/table/formula 开关 |

示例：

```json
{
  "system_tags": {
    "format_tags": [
      {"zh": "PDF", "en": "pdf"},
      {"zh": "OCR PDF", "en": "ocr_pdf"}
    ],
    "artifact_tags": [
      {"zh": "含图片产物", "en": "has_images"},
      {"zh": "含表格产物", "en": "has_tables"}
    ]
  }
}
```

#### C. 治理信号 Governance Signals

原标准里的 `quality_tags / relationship_tags / retention_tags / risk_tags` 不建议继续叫普通标签。它们应该进入治理信号，用于审核、清理和安全提示。

```json
{
  "governance_signals": {
    "quality": ["complete_book", "ocr_uncertain"],
    "relationship": ["official_component", "same_series"],
    "retention": ["keep_primary"],
    "risk": ["needs_human_review", "possible_duplicate"]
  }
}
```

这样做的好处：

- 成果库筛选不会被“低清晰度、疑似重复、未经人工确认不得删除”这类治理词污染。
- 人工审核可以直接按风险信号排队。
- 后续清理环境或删除素材时，可以读取 retention/risk，而不是依赖普通 tags。

### 12.3 项目化后的推荐结构

建议最终保存为：

```json
{
  "aiClassificationV02": {
    "source": {},
    "primary_facets": {},
    "descriptive": {},
    "search_tags": {},
    "system_tags": {},
    "governance": {},
    "governance_signals": {},
    "evidence": [],
    "recommended_catalog_path": "",
    "catalog_change_type": "keep_current"
  }
}
```

同时保留标准 v0.2 原始输出镜像：

```json
{
  "standard_raw": {
    "classification": {},
    "tags": {},
    "confidence": "",
    "human_review_required": true
  }
}
```

这样既不丢标准兼容性，也让 Luceon 的页面和后续任务更好用。

### 12.4 成果库使用口径

成果库主筛选使用：

- domain
- collection
- curriculum
- stage
- level
- subject
- resource_type
- component_role
- confidence
- human_review_required

成果库搜索使用：

- title
- filename
- series_title
- publisher_org
- topic_tags
- skill_tags

成果库风险/审核队列使用：

- markdown_quality
- duplicate_candidate
- retention_policy
- risk_flags
- evidence 缺失情况

### 12.5 对原标准的保留与修订关系

| 原标准内容 | Luceon 修订 |
|---|---|
| `classification` | 保留，但映射到 `primary_facets + descriptive` |
| `content_tags` | 改为 `search_tags.topic_tags` |
| `skill_tags` | 保留为 `search_tags.skill_tags` |
| `format_tags` | 改为系统生成优先，LLM 只可补充 |
| `quality_tags` | 改为 `governance_signals.quality` |
| `relationship_tags` | 改为 `governance_signals.relationship` |
| `retention_tags` | 改为 `governance.retention_policy` 与 `governance_signals.retention` |
| `risk_tags` | 改为 `governance.risk_flags` |
| `recommended_path` | 改为 `recommended_catalog_path`，只表示逻辑归类 |

## 13. 第二阶段实现任务建议

第一阶段稳定后，再进入 UI 与资料治理增强：

《P1 Patch：成果库 v0.2 分类筛选与资产详情证据链展示》

目标：

1. 成果库支持 v0.2 分类筛选。
2. 资产详情页展示完整分类与证据。
3. review-pending 显示明确原因。
4. 推荐资料库逻辑归类路径只展示，不执行。

## 14. 第三阶段实现任务建议

再之后才考虑：

《P2 Patch：推荐资料库路径、重复候选与资料整理审核工作台》

目标：

1. 展示推荐资料库逻辑归类路径。
2. 展示疑似重复关系。
3. 支持人工确认分类。
4. 支持人工确认后更新资料库分类字段。

此阶段必须单独设计权限、审计和回滚；如果未来确实需要改写 MinIO object key，也必须作为独立迁移工具处理，不应混入第一阶段。

## 15. 结论

《文本资料 LLM 分类与标签标准 v0.2》应成为 Luceon2026 AI 元数据识别的正式细化标准。

当前系统的 AI metadata 是“展示字段提取器”；目标系统应升级为“资料管理分类器”。

最稳妥的落地方式是：

1. 先把 v0.2 完整结构写入 Material metadata。
2. 同时保留旧字段投影。
3. 暂不移动 MinIO 对象、不删除对象、不改变成果库准入。
4. 用 smoke tests 锁住 schema、review 规则和兼容字段。
5. 再逐步增强成果库筛选、资产详情证据链和人工审核。

这条路径对现有 MinerU/pipeline 稳定链路影响最小，但能把 Luceon 的资料管理能力真正补起来。

## 16. 当前实现对齐说明（2026-05-01）

本轮 `P1 Patch：AI Metadata v0.2 方案结构对齐、Evidence 归一与兼容投影修正` 已完成第一阶段代码对齐：

1. `aiClassificationV02` 保持以 `source / primary_facets / descriptive_metadata / search_tags / governance / evidence / recommended_catalog_path / catalog_change_type` 为主体结构。
2. 新增 `system_tags`，由真实执行上下文派生 `format_tags / artifact_tags / engine_tags`，用于表达 PDF、OCR、解析产物、Pipeline/Hybrid/VLM、表格识别、公式识别等系统事实。
3. 新增 `governance_signals`，从 `risk_flags`、置信度、人工复核状态、产物数量和 evidence 状态派生 `quality / relationship / retention / risk`。
4. Evidence 统一归一为数组对象，支持字符串 evidence 降级为 `{ type: "unknown", quote_or_summary, supports: [] }`，并限制最大保留数量。
5. 核心 facet 缺失、evidence 缺失、低置信度、模型提出新标签等场景会进入 `human_review_required`，并写入明确的 `risk_flags`。
6. 兼容投影已修正：`grade` 优先取 `level.zh`，再取 `stage.zh`；`materialType` 优先取 `resource_type.zh`，再取 `component_role.zh`；`tags` 合并搜索标签与系统标签；`summary` 由系列/科目、级别/学段、资源类型等摘要拼接。
7. 详情页只读展示已补充 `System Tags` 与 `Governance Signals`，历史缺失字段保持兼容，不影响旧数据打开。
