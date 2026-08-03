# 01 输入与事实来源规范

状态：`Normative Baseline v0.2`

生效日期：`2026-07-18`

上位契约：[AGENTS.md](../AGENTS.md)

## 0. 共同硬原则

1. 原 PDF 不可访问时，只能执行受限诊断；正式链路保持 `blocked`，不得取得来源忠实或最终产品通过；
2. 任何未关闭的人工判断或 review queue 项都阻止相应规范进入 `passed`；
3. 最终 PDF 必须逐页自动渲染和检查，高风险、异常及语义争议必须人工闭环，黄金样本还必须由用户明确接受。

本规范的局部 `passed` 不等于最终产品通过，并且不得替代规范 06 的全页及用户验收。

## 1. 目的

本规范确保每次任务处理的是正确材料、正确 MinerU/MinerU-Popo 血缘和正确目标模板。
它解决串书、错版本、错运行、对象缺失、来源不可追溯以及把下游产物误当真值的问题。

本项目的正式处理起点是已完成 MinerU 和 MinerU-Popo 的可追溯任务包；原 PDF 仍是最终来源忠实核验依据。

## 2. 适用范围

适用于：

- 本地样本组；
- MinIO 中的 PDF、MinerU、MinerU-Popo 任务；
- 修复或比较已有 Markdown、LaTeX ZIP、编译 PDF 的任务；
- 后续新增的教育材料类型和语言。

本规范不负责执行 PDF 上传、GPU MinerU 或 MinerU-Popo 上游生产。

## 3. 统一状态

- `blocked`：缺少必要输入、权限或可证明血缘；
- `failed`：输入存在但明确违反硬门禁；
- `needs_review`：存在多个合理来源或运行，需要人工决定；
- `passed`：全部硬门禁通过且人工决策项已关闭。

任何开放的 `needs_review` 都阻止 `passed`。
`diagnostic_only` 只能作为 `run_mode` 或 `capability_status`，不能作为第五种 `spec_status`；
处于该模式时，本规范的正式状态必须保持 `blocked`。

## 4. 正式逻辑输入

### 4.1 原始来源

至少记录：

- `source_object_ref`；
- 原始文件名和媒体类型；
- `pdf_sha256`；
- `page_count`；
- 文件大小；
- 来源材料 ID；
- 当前可访问状态。

### 4.2 MinerU 证据

至少记录：

- MinerU manifest/object ref；
- MinerU run ID；
- 与原 PDF 的血缘字段；
- 内容块、bbox、公式、表格和图片清单；
- 关键文件哈希或清单哈希；
- 解析版本和运行环境信息。

### 4.3 MinerU-Popo 证据

至少记录：

- Popo manifest/object ref；
- Popo run ID；
- 上游 MinerU 引用；
- 后处理块、结构、顺序、关系和媒体引用；
- 关键文件哈希或清单哈希；
- Popo 版本和运行信息。

不得假定 MinerU run ID 与 Popo run ID 相同。必须通过 manifest、材料 ID、哈希和上游引用解析血缘。

### 4.4 目标模板输入

本工作区不存在模板选择：所有正式样本唯一允许的目标模板是根目录 `2025教材模版新版.zip`，
当前批准 SHA-256 为 `ebc5d6575153239552785265d2765de03b76c27d0532137712952f20ad0a9d4d`。
输入身份或哈希不符时必须阻断，不得改用其他模板。

至少记录：

- 模板对象或本地路径；
- 模板 ZIP 哈希；
- `elegantbook.cls` 哈希；
- 模板入口文件和原始文件清单；
- 用户声明的冻结原则、候选 metadata 允许项和正文插入提示。

本规范只确认模板输入身份和任务约束，不生成权威冻结合同。
唯一权威 `template_contract.json` 由规范 05 基于模板实物、能力清单和用户约束生成。

### 4.5 可选基线

已有 Markdown、语义 JSON、LaTeX ZIP、编译 PDF 或历史报告可以作为比较和修复基线，
但必须标记 `optional_baseline`，不得覆盖 PDF/MinerU/Popo 事实。

## 5. 必需产物

### 5.1 `input_contract.json`

至少包含：

- schema/version；
- 当前运行 ID 和隔离运行目录；
- 材料身份；
- PDF、MinerU、Popo 和模板引用；
- 可选基线；
- 哈希与页数；
- 访问模式；
- 能力状态；
- 当前 gate 状态；
- 人工决定引用。

### 5.2 `source_trace.json`

记录从原 PDF 到 MinerU、Popo、当前运行及后续产物的完整有向血缘。

### 5.3 `materialized_manifest.json`

记录隔离运行目录中每个文件来自哪里、何时读取、哈希为何。物化副本不得反向覆盖上游对象。

### 5.4 `input_validation_report.json`

记录全部自动检查、失败码、质量提示、人工决定引用、运行模式和最终 `spec_status`。

### 5.5 `template_intake.json`

记录模板输入哈希、入口、原始文件清单、用户声明约束和待规范 04–05 核验的候选配置点；
不得把候选允许项表述为已经冻结的白名单。

### 5.6 `canonical_decision_index.json`

创建全链路决定索引的首个不可变快照，登记本规范的决定事件文件及哈希。
后续规范只能创建带父索引哈希的新快照，不得原地修改已交接索引。

索引至少包含：`schema_version`、`decision_index_id`、`snapshot_id`、`version`、`parent_index_hash`、
决定时已经存在的来源证据或父快照哈希、各阶段决定事件文件及哈希、每个 `decision_id` 的当前状态与失效关系，
以及 open/stale/closed 统计。索引事件状态必须能区分 `open`、`closed`、`superseded`、`stale` 和 `invalidated`。

先冻结决定索引，再生成引用该索引的 `input_contract.json`；决定索引不得反向引用随后生成的 input contract。

### 5.7 `input_decisions.jsonl`

保存本规范的追加式决定事件；canonical decision index 只索引其哈希和事件状态，不复制或另造决定正文。

## 6. 硬门禁

- `IN-H01 source_pdf_available`：原 PDF 可访问、可读取并可计算哈希；
- `IN-H02 source_identity_consistent`：来源 ID、PDF 哈希、页数和文件身份无未解释冲突；
- `IN-H03 mineru_evidence_available`：必需 MinerU 证据存在且可读取；
- `IN-H04 popo_evidence_available`：必需 Popo 证据存在且可读取；
- `IN-H05 lineage_proven`：Popo 能追溯到正确 MinerU，MinerU 能追溯到正确 PDF；
- `IN-H06 template_input_identified`：目标模板精确为批准的 `2025教材模版新版.zip`，入口、哈希和用户声明约束明确；
- `IN-H07 upstream_read_only`：原 PDF、MinerU 和 Popo 来源保持只读；
- `IN-H08 hashes_recorded`：正式输入和模板关键哈希均已记录；
- `IN-H09 isolated_workspace`：正式运行使用隔离目录，不覆盖历史运行；
- `IN-H10 no_unresolved_identity_conflict`：不存在尚未处理的串书、错版或错运行风险。
- `IN-H11 archive_safe_to_materialize`：ZIP 等归档无路径穿越、绝对路径写入、符号链接逃逸或不支持的危险成员；
- `IN-H12 page_coordinate_basis_recorded`：PDF 物理页索引、上游页索引、旋转、裁切框和 bbox 坐标基准已记录并可换算。
- `IN-H13 source_pdf_parseable`：原 PDF 可完整解析，全部页面可访问，声明页数与实际页数一致；
- `IN-H14 manifest_objects_verified`：manifest 声明的必要对象存在，大小和哈希无未解释差异；
- `IN-H15 materialization_hashes_match`：隔离运行目录副本与只读来源逐对象一致；
- `IN-H16 schema_and_versions_supported`：输入 schema 可识别，关键工具、适配器和上游版本均已记录。
- `IN-H17 decision_index_initialized`：canonical decision index 首个不可变快照已由既有来源证据建立，
  随后生成的 input contract 单向引用该索引。

缺少原 PDF 时可以把 `run_mode` 标记为 `diagnostic_only`，但 `spec_status` 必须为 `blocked`，
不得通过 `IN-H01`，也不得进入最终来源忠实通过状态。

## 7. 人工决策门禁

- `IN-R01 authoritative_edition`：同一材料存在多个 PDF/版本时选择权威版本；
- `IN-R02 authoritative_run`：多次 MinerU/Popo 运行均可用时选择正式基线；
- `IN-R03 source_conflict_resolution`：manifest、文件名、哈希或页数冲突时确定处理方式；
- `IN-R04 template_identity_conflict`：发现路径、字节、哈希或入口与固定模板不一致时，只允许裁决输入错误及退回方式，
  不允许选择其他模板；
- `IN-R05 upstream_return_or_diagnostic`：上游证据不完整时决定退回上游或只做受限诊断。

人工决定必须记录问题、候选、证据引用、决定、理由、决定者和时间，不能只写“已确认”。
每个决定事件都必须登记到 `canonical_decision_index.json` 的当前不可变快照。

## 8. 质量提示

以下情况默认提示，但不单独构成失败：

- 缺少可选历史基线；
- 源文件名不规范但哈希和 manifest 血缘明确；
- 旧运行缺少非关键统计字段；
- 原 PDF 低分辨率但仍可核验；
- 上游版本较旧但输入合同完整。

如果提示影响来源识别或内容核验，应升级为硬门禁或人工决策门禁。

## 9. 失败码

- `INPUT_SOURCE_PDF_MISSING`
- `INPUT_SOURCE_IDENTITY_CONFLICT`
- `INPUT_MINERU_MISSING`
- `INPUT_POPO_MISSING`
- `INPUT_LINEAGE_UNPROVEN`
- `INPUT_TEMPLATE_MISSING`
- `INPUT_HASH_MISMATCH`
- `INPUT_UPSTREAM_WRITE_RISK`
- `INPUT_ARCHIVE_UNSAFE`
- `INPUT_PAGE_MAPPING_UNPROVEN`
- `INPUT_SOURCE_PDF_UNREADABLE`
- `INPUT_MANIFEST_OBJECT_MISSING`
- `INPUT_MATERIALIZATION_HASH_MISMATCH`
- `INPUT_SCHEMA_UNSUPPORTED`
- `INPUT_DECISION_INDEX_INVALID`
- `INPUT_REVIEW_OPEN`

每个失败必须包含证据路径和下一步动作。

## 10. 验收与交接

只有状态为 `passed` 时，才能把以下冻结引用交给规范 02：

- `input_contract.json` 及其哈希；
- `source_trace.json`；
- `materialized_manifest.json`；
- `input_validation_report.json`；
- `input_decisions.jsonl`；
- `template_intake.json`；
- `canonical_decision_index.json` 当前快照及哈希；
- 原 PDF、MinerU、Popo 和模板的只读物化路径；
- 已关闭的人工决定记录。

规范 02 不得自行更换来源版本或 Popo 运行；需要更换时必须退回本规范重新验收。
