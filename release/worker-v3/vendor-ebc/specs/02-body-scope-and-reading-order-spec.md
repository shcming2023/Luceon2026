# 02 正文范围与阅读顺序规范

状态：`Normative Baseline v0.1`

生效日期：`2026-07-15`

上位契约：[AGENTS.md](../AGENTS.md)  
上游规范：[01 输入与事实来源规范](01-input-and-provenance-spec.md)

## 0. 共同硬原则

1. 原 PDF 不可访问时，只能执行受限诊断；正式链路保持 `blocked`，不得取得来源忠实或最终产品通过；
2. 任何未关闭的人工判断或 review queue 项都阻止相应规范进入 `passed`；
3. 最终 PDF 必须逐页自动渲染和检查，高风险、异常及语义争议必须人工闭环，黄金样本还必须由用户明确接受。

本规范的局部 `passed` 不等于最终产品通过，并且不得替代规范 06 的全页及用户验收。

## 1. 目的

本规范确定两个问题：

1. 原始来源中哪些页面和内容属于当前产品范围；
2. PDF 的二维页面布局如何还原成忠实、连续的流水阅读顺序。

它防止误收封面广告、误删正文、正文尾部截断、多栏错序、题图错挂以及脚注和图注脱离语境。

## 2. 输入

- 已通过规范 01 的 `input_contract.json` 和 `source_trace.json`；
- 规范 01 创建的 `canonical_decision_index.json` 不可变快照；
- 原 PDF；
- MinerU 页、块、bbox、布局和媒体证据；
- MinerU-Popo 块、结构、顺序及关系证据；
- 当前产品的范围策略，例如 `body_only`；
- 可选来源目录、书签、页码映射和已有正文基线。

## 3. 范围不是通用关键词表

“正文”必须由产品类型和来源证据共同定义。

- 封面、版权页、原目录、广告、空白页和后封底通常排除；
- 正文内的答案、复习、测评、知识导图和局部附录可能属于产品范围；
- 独立答案册、教师版附录、词汇表和索引是否保留，由产品合同决定；
- 不得只因为页面出现 `Answers`、`Index`、`答案` 等字符串就提前截断；
- 不得把第一组样本的页面边界提升为通用规则。

## 4. 必需产物

### 4.1 `source_scope_ledger.json`

每个 PDF 页面以及其中需要独立裁决的候选区域/块至少记录：

- PDF 物理页码和上游 `page_idx`；
- 来源区域或 block ID（页级决定可为空）；
- `included`、`excluded` 或 `review`；
- 页面类别；
- 范围理由；
- PDF/MinerU/Popo/目录证据引用；
- 是否包含跨页内容；
- 人工决定引用；
- 当前置信度和 gate 状态。

### 4.2 `reading_order_ledger.json`

每个纳入页面至少记录：

- 页内 block ID 列表；
- bbox 和栏位；
- 最终阅读序号；
- 跨栏、跨页和父子关系；
- 题干—选项—图片—解答关系；
- 表格、公式、图注和脚注锚点；
- 原始顺序与最终顺序差异；
- 自动判定依据；
- 是否需要人工复核。

### 4.3 `source_page_render_ledger.jsonl`

使用固定渲染器和配置渲染原 PDF 全部页面。每页至少记录 PDF 哈希、物理页索引、尺寸、旋转、
渲染配置、raster 路径和 raster 哈希。该账本供本规范逐页定界，并由规范 03 和 06 复用。

### 4.4 复杂页可视化证据

复杂页面必须有可点击或可并排查看的 PDF 页面、bbox/块编号和最终顺序视图。

### 4.5 决定事件与索引

生成 `scope_order_decisions.jsonl`，并基于 input contract、原 PDF 和候选范围/顺序证据冻结
`canonical_decision_index.json` 不可变子快照；随后生成的范围/顺序账本单向引用该索引，索引不得反向引用它们。

## 5. 硬门禁：正文范围

- `SC-H01 every_source_unit_classified`：每个来源页面及需独立裁决的区域/块都有明确状态；
- `SC-H02 body_boundaries_proven`：正文起止边界有来源证据；
- `SC-H03 exclusions_reasoned`：每个排除页面、区域或块有允许且可审查的理由；
- `SC-H04 no_body_content_dropped`：不存在已知正文页、区域或块被排除；
- `SC-H05 no_nonbody_content_included`：不存在已知封面、广告、页眉页脚噪声、空白页或后封底被收入正文；
- `SC-H06 cross_page_content_protected`：跨页表格、题组、解答和文章没有因边界被截断；
- `SC-H07 no_open_scope_review`：范围相关人工决策全部关闭。
- `SC-H08 every_source_page_rendered`：原 PDF 全部页面均已渲染并进入来源页账本。
- `SC-H09 decision_index_updated`：范围与顺序决定全部登记到当前 canonical decision index 快照。

## 6. 硬门禁：阅读顺序

- `RO-H01 every_included_block_ordered`：每个纳入范围的来源块都有流水顺序；
- `RO-H02 order_evidence_recorded`：顺序基于 bbox、栏结构、语义关系或来源视觉证据；
- `RO-H03 semantic_groups_preserved`：题干、选项、图片、答案、表格、图注和脚注关系未被拆散；
- `RO-H04 multi_column_validated`：多栏和复杂页面已完成自动验证或人工关闭；
- `RO-H05 cross_page_order_validated`：跨页内容顺序明确；
- `RO-H06 no_array_order_assumption`：不能仅因 flat content list 数组顺序而直接判定通过；
- `RO-H07 no_open_order_review`：阅读顺序人工决策全部关闭。
- `RO-H08 composite_relationship_closed`：当来源 bbox 显示媒体位于上方文本与下方文本之间、但上游树序把
  下方文本排在媒体之前时，必须生成原子级候选；只有带来源证据、角色分区和关闭评审的
  `stem_media_options` 关系才能重排并通过。未绑定候选、成员重叠、跨页冒充同页、来源几何不支持、
  或关系成员未按“题干—媒体—选项”恰好连续输出，均阻止交接规范 03。

## 7. 人工决策门禁

- `SC-R01 ambiguous_front_body_boundary`：目录、导读、单元扉页与正文边界不清；
- `SC-R02 ambiguous_body_back_boundary`：答案、附录、索引、资源页与正文边界不清；
- `SC-R03 product_scope_exception`：产品合同要求保留通常会排除的内容；
- `RO-R01 multi_column_order`：多栏、穿插栏或浮动框存在多种顺序；
- `RO-R02 sidebar_anchor`：侧栏、方法提示或知识框锚点不明确；
- `RO-R03 figure_caption_anchor`：图片、图注或 OCR 标签归属不明确；
- `RO-R04 table_formula_cross_page`：表格、公式或题组跨页关系需要确认；
- `RO-R05 footnote_locality`：脚注或局部说明应放置的语义位置不明确。
- `RO-R06 composite_stem_media_options`：题干、图／表和选项的二维位置与上游树序冲突，需要确认成员边界
  和流水顺序。通用内核只能基于类型、bbox 和顺序差异提出候选，不得用题号、语言字符串或样本标识
  自动猜测角色；具体成员必须由 profile／单书配置引用稳定来源原子，并绑定可视证据关闭。

人工决定必须记录候选顺序、证据截图/块引用、选择和理由。
每个决定事件必须登记到 canonical decision index；开放或因账本变化而过期的事件均阻止通过。

## 8. 质量提示

- 连续页面缺少文本但视觉上可能是结构响应页；
- 页面密度显著异常；
- Popo 顺序与 bbox 几何顺序差异较大；
- 目录页码与物理 PDF 页码存在偏移；
- 页眉、页脚或重复标签疑似混入正文块；
- 脚注被上游单独提取但局部锚点较弱。

提示影响正文完整性或阅读关系时必须升级为人工决策门禁。

## 9. 失败码

- `SCOPE_PAGE_UNCLASSIFIED`
- `SCOPE_BOUNDARY_UNPROVEN`
- `SCOPE_BODY_PAGE_DROPPED`
- `SCOPE_NONBODY_INCLUDED`
- `SCOPE_CROSS_PAGE_TRUNCATED`
- `SCOPE_SOURCE_PAGE_RENDER_INCOMPLETE`
- `SCOPE_DECISION_INDEX_INVALID`
- `ORDER_BLOCK_UNASSIGNED`
- `ORDER_MULTICOLUMN_UNRESOLVED`
- `ORDER_SEMANTIC_GROUP_BROKEN`
- `ORDER_CROSS_PAGE_UNRESOLVED`
- `ORDER_COMPOSITE_RELATION_UNRESOLVED`
- `ORDER_COMPOSITE_RELATION_INVALID`
- `SCOPE_ORDER_REVIEW_OPEN`

## 10. 验收与交接

交给规范 03 的内容必须包括：

- 已通过的 `source_scope_ledger.json`；
- 已通过的 `reading_order_ledger.json`；
- `source_page_render_ledger.jsonl`；
- `scope_order_decisions.jsonl` 及当前 canonical decision index 快照和哈希；
- 所有纳入块的稳定顺序；
- 所有排除页和排除块的理由；
- 复杂页面可视化证据；
- `composite_reading_relationships.json` 及其在阅读顺序账本、决定索引和提交清单中的哈希绑定；
- 已关闭的人工决定记录。

规范 03 可以发现遗漏或重复并退回本规范，但不得自行改变页面范围或阅读顺序而不更新账本。
