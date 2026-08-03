# 03 内容完整性及唯一证据账本规范

状态：`Normative Baseline v0.1`

生效日期：`2026-07-15`

上位契约：[AGENTS.md](../AGENTS.md)  
上游规范：[02 正文范围与阅读顺序规范](02-body-scope-and-reading-order-spec.md)

## 0. 共同硬原则

1. 原 PDF 不可访问时，只能执行受限诊断；正式链路保持 `blocked`，不得取得来源忠实或最终产品通过；
2. 任何未关闭的人工判断或 review queue 项都阻止相应规范进入 `passed`；
3. 最终 PDF 必须逐页自动渲染和检查，高风险、异常及语义争议必须人工闭环，黄金样本还必须由用户明确接受。

本规范的局部 `passed` 不等于最终产品通过，并且不得替代规范 06 的全页及用户验收。

## 1. 目的

本规范建立贯穿全链路的 canonical evidence ledger，防止正文块、图片、表格、公式、脚注和答案被静默遗漏、
重复、错挂或在阶段转换中失去来源。

账本不是一次性阶段产物。它从来源物化后建立，在语义映射、LaTeX 渲染、编译和最终验收中沿同一哈希血缘
创建不可变新快照；任何阶段都不得原地追加已交接文件，也不得另起一套无法回连的“新真值”。

## 2. 核心不变量

每个原子来源内容必须有唯一范围裁决：

1. 纳入正文并进入后续映射；或
2. 由规范 02 以明确、允许且可审查的理由排除；或
3. 处于开放的 `needs_review`，并因此阻断对应检查点通过。

在最终输出覆盖检查点，每个已纳入正文的原子来源内容还必须恰好映射到一个逻辑输出终态，
不得用规范 03 新增的“排除”绕过规范 02 已确认的正文范围。

“唯一终态”不等于机械的一块对应一段。允许多个来源块合并为一个输出对象，也允许一个来源块按可追踪范围拆分，
但每个原子来源 span 只能被覆盖一次，不能遗漏或重叠复用。

来源本身重复的内容应以不同来源实例分别记录，不得因为文字相同就误判为输出重复。

## 3. 数据模型

### 3.1 文档级字段

- `schema_version`；
- `ledger_id`、`ledger_version`；
- `parent_ledger_hash` 和 `current_ledger_hash`；
- `material_identity`；
- `input_contract_ref/hash`；
- `source_scope_ledger_ref/hash`；
- `reading_order_ledger_ref/hash`；
- `canonical_decision_index_ref/hash`；
- 当前处理版本、规则版本和 profile 版本；
- 账本生成时间和最近更新时间；
- 当前 gate 状态和统计摘要。

### 3.2 来源块字段

每个来源块至少包含：

- `block_id`；
- 上游 block/object ref；
- PDF 页码、上游 `page_idx`、bbox 和来源顺序；
- 最终流水顺序；
- 来源类型：文本、标题、列表、公式、表格、图片、图注、脚注、空白响应区等；
- 原始内容或稳定内容引用；
- 原始内容哈希；
- 图片/表格/公式资产引用；
- MinerU 与 Popo 置信度或异常标志；
- 正文范围状态；
- 当前语义状态；
- 当前渲染状态；
- 当前终态和理由；
- 人工决定引用。

### 3.3 映射字段

每个来源 span 到输出的映射至少记录：

- `mapping_id`；
- 一个或多个 `block_id` 及 span；
- 输出对象 ID、文件、行/节点位置；
- 输出内容哈希；
- 映射类型：一对一、合并、拆分、保持图片或沿用规范 02 的 `scope_excluded`；
- 转换规则 ID 和版本；
- 是否改变可见文字；
- before/after 证据；
- 验证状态。

### 3.4 媒体字段

图片、表格和公式至少记录：

- 源资产 ID、路径、哈希、尺寸或结构摘要；
- 所属来源块；
- 语义角色；
- 最终表示方式；
- 最终文件/LaTeX 引用；
- 保留、替换表示、排除或复核理由；
- 双向闭环状态。

### 3.5 输出事件字段

每个逻辑输出至少记录：

- `emission_id`；
- 来源 block/span IDs；
- 输出节点、文件和稳定锚点；
- 输出类型：来源正文、允许的表示转换或模板生成内容；
- 可见内容哈希和资产哈希；
- 语义 assignment、mapping rule 和 template capability manifest 引用；
- 当前验证状态。

## 4. 必需产物

- `canonical_block_ledger.jsonl`：逐块不可丢失的主账本；
- `block_coverage_ledger.json`：来源 span 到输出的覆盖关系；
- `media_ledger.json`：图片、表格、公式和相关资产闭环；
- `completeness_report.json`；
- `completeness_report.md`；
- `evidence_decisions.jsonl`：人工和自动决定记录；
- 基于父 ledger 和既有证据冻结的 `canonical_decision_index.json` 不可变子快照；
- 绑定该决定索引的新 canonical ledger snapshot。

具体文件格式可以版本化演进，但职责不得省略。

## 5. 写入与版本规则

- 每个账本快照在计算哈希并发布后不可变，不得原地追加或覆盖；
- 下游追加语义、渲染、编译或验收状态时，必须创建新的 `ledger_snapshot_id` 和 `ledger_version`，
  并引用精确的 `parent_ledger_hash`；“同一账本”只表示同一 ledger ID 和父子哈希血缘；
- 新快照必须先完整生成和校验，再原子发布；不得让下游读取半写入状态；
- 来源字段写入后不得被下游改写；修正来源字段必须生成新版本并记录旧值、证据和理由；
- 每个账本版本必须绑定其上游合同、决定索引和所有派生输入哈希；
- 已交接的快照永远保留；任何消费者必须声明实际消费的 snapshot ID、版本和哈希；
- 任何重排、合并、拆分或排除都必须留下映射记录；
- 不得用同名报告覆盖不同职责的旧报告；
- 同一输入和规则版本应产生可复现的 block ID 和覆盖结果。

每次含人工决定的账本提交必须严格按“父 ledger/证据 → 决定索引 D → 子 ledger L”执行。
D 可以引用父 ledger 和已有构建证据，L 可以引用 D；D 不得引用 L。需要同时承诺 D 与 L 时，
由阶段报告或独立 commit manifest 单向绑定两者。

### 5.1 生命周期检查点

规范 03 是跨阶段规范，必须在同一账本上分检查点复验：

- `source_reconciled`：来源页、原子块、范围、顺序、媒体库存和上游冲突已经闭环，可交给规范 04；
- `render_coverage`：规范 04–05 已把全部纳入块绑定到正式输出事件，资产和输出哈希闭环，可进入规范 06；
- `final_verified`：由规范 06 在实质门禁 `FR-H01`–`FR-H18` 通过并冻结最终决定索引 D 后，
  创建绑定 D、最终 ZIP、PDF、全页账本和自动异常的新不可变子快照，证明没有遗漏、重复、无来源新增或错挂。

`ledger_checkpoint` 与 `spec_status` 是两个字段。`spec_status` 始终只使用四态，并针对声明的检查点判定；
低检查点的 `passed` 不得被表述为高检查点或最终产品通过。任何上游账本变化都会使后续检查点失效。

## 6. 硬门禁

### 6.1 `source_reconciled` 门禁

- `CV-H01 ledger_identity_valid`：schema、账本 ID、快照 ID、版本和父子哈希链有效，已发布快照未被原地修改；
- `CV-H02 every_source_span_inventoried`：每个来源原子 span 有稳定 ID、来源证据和范围裁决；
- `CV-H03 scope_and_order_bound`：范围与阅读顺序绑定规范 02 的已通过账本及哈希；
- `CV-H04 media_inventory_complete`：图片、表格、公式、图注和相关资产库存完整；
- `CV-H05 provenance_preserved`：来源页、bbox、上游 block ID、内容和顺序均可追溯；
- `CV-H06 no_premature_pruning`：闭环完成前没有删除未引用图片或来源证据；
- `CV-H07 no_open_source_review`：来源、内容和媒体身份复核项全部关闭。

### 6.2 `render_coverage` 门禁

除 `CV-H01`–`CV-H07` 外，还必须满足：

- `CV-H08 every_included_span_emitted`：每个纳入来源 span 有唯一逻辑输出终态；
- `CV-H09 no_untracked_drop`：不存在无理由丢失；
- `CV-H10 no_duplicate_coverage`：不存在来源 span 被重复覆盖；
- `CV-H11 no_output_without_source`：除白名单模板框架外，不存在无法指向来源的教材内容；
- `CV-H12 output_hashes_recorded`：输出对象、映射和可见内容哈希完整；
- `CV-H13 image_bidirectional_closure`：来源教学图片和最终图片引用双向闭环；
- `CV-H14 table_formula_disposed`：每个表格和公式有最终表示，不存在未关闭的阻断状态；
- `CV-H15 assets_resolve`：所有正式输出资产引用可解析；
- `CV-H16 body_head_tail_present`：正文开头、各结构尾部和全书尾部均有实质内容证据；
- `CV-H17 transformations_audited`：拆分、合并、表示转换和可见内容变化均有 before/after 与规则记录；
- `CV-H18 no_open_output_review`：输出覆盖相关人工决策全部关闭。

`final_verified` 还必须由规范 06 的全页检查证明 `CV-H08`–`CV-H18` 在最终 PDF 中真实成立。

## 7. 人工决策门禁

- `CV-R01 decorative_or_instructional_image`：判断图片是教学内容、装饰、图标还是 OCR 碎片；
- `CV-R02 uncertain_ocr_span`：低置信度文字是否可接受、需重识别或只能保留来源图像；
- `CV-R03 complex_table_representation`：复杂表格选择结构化重建或来源图像；
- `CV-R04 formula_uncertainty`：公式 token 或结构存在来源歧义；
- `CV-R05 blank_response_surface`：视觉空白是有意义的答题区还是无内容噪声；
- `CV-R06 duplicate_source_or_ocr_artifact`：相似内容是来源真实重复还是 OCR 重复；
- `CV-R07 fallback_representation`：无法安全结构化时选择何种保真表示。

人工决定可以选择表示方式，不能编造缺失教材内容。
每个决定事件必须进入 `evidence_decisions.jsonl` 并登记到 canonical decision index；
账本或产物哈希变化后，相关决定必须显式标记为过期并重新关闭。

## 8. 质量提示

- 某页来源块数量、图片数量或公式数量异常；
- 大量块被合并到单一输出对象；
- 相同输出对象覆盖过多来源页；
- 图片存在但未被上游文本引用；
- OCR 文本与图片内可见文字高度重复；
- 表格或公式只能以图片形式保留；
- 内容哈希因无可见意义的空白规范化而变化。

提示涉及遗漏、重复或含义变化时必须升级为硬门禁或人工决策门禁。

## 9. 失败码

- `LEDGER_IDENTITY_INVALID`
- `LEDGER_CHECKPOINT_STALE`
- `LEDGER_SNAPSHOT_MUTATED`
- `LEDGER_DECISION_INDEX_MISMATCH`
- `COVERAGE_SOURCE_SPAN_MISSING`
- `COVERAGE_SOURCE_SPAN_DUPLICATED`
- `COVERAGE_OUTPUT_WITHOUT_SOURCE`
- `COVERAGE_MAPPING_HASH_MISSING`
- `MEDIA_SOURCE_ASSET_UNDISPOSED`
- `MEDIA_OUTPUT_REFERENCE_MISSING`
- `TABLE_UNDISPOSED`
- `FORMULA_UNDISPOSED`
- `PROVENANCE_LOST`
- `TRANSFORMATION_UNAUDITED`
- `BODY_TAIL_TRUNCATED`
- `EVIDENCE_REVIEW_OPEN`

## 10. 验收与交接

规范 04 只能消费 `ledger_checkpoint=source_reconciled` 且 `spec_status=passed` 的账本版本，
并创建带父哈希的新不可变子快照来追加语义字段，不能修改已交接快照。

交接至少包括：

- 主账本和哈希；
- 精确的 ledger snapshot ID、版本、父哈希和当前哈希；
- 当前 `canonical_decision_index.json` 快照及哈希；
- 完整性报告；
- 媒体闭环报告；
- 来源协调阶段未发生无记录删除的证明；
- 已关闭的人工决定；
- 明确的正文顺序和结构候选。

规范 04 不得把无法分类的内容删除；只能保留为普通正文、进入 review queue 或退回本规范。

规范 06 开始前，规范 03 必须以规范 04–05 的正式产物重验到
`ledger_checkpoint=render_coverage` 且 `spec_status=passed`。
