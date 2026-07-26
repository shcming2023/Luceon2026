# 04 教学语义与 ElegantBook 映射规范

状态：`Normative Baseline v0.8`

生效日期：`2026-07-23`

上位契约：[AGENTS.md](../AGENTS.md)  
上游规范：[03 内容完整性及唯一证据账本规范](03-content-evidence-ledger-spec.md)

## 0. 共同硬原则

1. 原 PDF 不可访问时，只能执行受限诊断；正式链路保持 `blocked`，不得取得来源忠实或最终产品通过；
2. 任何未关闭的人工判断或 review queue 项都阻止相应规范进入 `passed`；
3. 最终 PDF 必须逐页自动渲染和检查，高风险、异常及语义争议必须人工闭环，黄金样本还必须由用户明确接受。

本规范的局部 `passed` 不等于最终产品通过，并且不得替代规范 06 的全页及用户验收。

## 1. 目的

本规范先确定来源内容的教学语义、结构层级和范围，再把已确认语义确定性映射到目标 ElegantBook 模板。

本规范只负责语义 assignment、构件 binding 和冻结 `render_plan`，不执行 LaTeX 序列化、正文插入或编译。
唯一渲染执行阶段是规范 05。

它防止：

- 章节、课题和局部栏目混级；
- 语义标注成为不被渲染器消费的旁路报告；
- 普通正文被随意装盒；
- 为当前样本硬编码栏目名称；
- 源可见答案被错误隐藏；
- 转换器自行新增模板构件。

### 1.1 输入

- 规范 03 在 `source_reconciled` 检查点通过的不可变 ledger snapshot；
- 当前 `canonical_decision_index.json` 快照；
- 规范 01 的 `template_intake.json` 和只读目标模板实物；
- 已关闭的范围、顺序和内容身份决定。

### 1.2 分阶段符合性：Spec 04-A、04-B、04-C、04-D 与完整 Spec 04

为避免在同一步骤同时猜测结构、语义范围和模板构件，本规范允许按不可变子阶段逐项建立证据，但任何子阶段
`passed` 都不得冒充完整规范 04 通过：

- **Spec 04-A** 只冻结“来源目录—正文层级—抽象最终目录”，并把全部来源 title candidate 精确划分为
  结构证据或局部标题；
- **Spec 04-B** 必须机械消费活动 Spec 04-A promotion 及其精确 ledger、decision index、
  `source_outline_ledger` 和 `final_toc_plan`，只冻结全部 included source atom 的唯一语义 span 分区，
  以及有来源页面证据的教学栏目成员关系；
- **Spec 04-C** 必须机械消费活动 Spec 04-B promotion 及其精确 semantic span、教学栏目组、ledger 和
  decision index，从本次只读模板实物原生提取 `template_capability_manifest.json`，并只为已确认教学语义对象
  选择模板真实存在的构件及参数；
- **Spec 04-D** 必须同时消费活动 Spec 04-C、Spec 04-A 与 Spec 03 promotion，机械继承已冻结构件、结构和
  媒体表示，再补齐普通正文、来源顺序、精确 payload、媒体 binding 和输出锚点，冻结完整 `render_plan`。
  04-D 不得重新选择语义、盒子或媒体表示。

Spec 04-B 的安全降级不是“未处理”：未获可靠栏目范围证据的原子必须明确落入 `local_heading`、
`plain_body` 或 `fragile_or_media` 等保守 span。教学栏目组必须有非空正文成员、同页证据和关闭的人工决定；
公式、表格、图片等脆弱原子不得被吸入栏目正文。OCR/Popo 把视觉栏目标签误标为 `text` 时，可以依据精确
页面证据和关闭评审将其作为栏目标记，但不得覆盖 Spec 04-A 已确认的结构节点。

Spec 04-B 不得包含 `target_construct`、盒子/style、render node、`render_plan`、LaTeX、公式重建或表格重建决定，
并必须记录 `full_spec04_status=not_evaluated`。完整规范 04 只有在 SM-H01 至 SM-H19 全部通过时才可进入
`passed`。

Spec 04-C 只允许对 Spec 04-B 已确认的非空教学栏目组和独立语义标签作构件绑定。每个对象必须恰好绑定一次，
每条绑定必须引用同一活动父项、来源页面证据、关闭评审、规则归属和本次模板能力清单哈希。独立标签没有已确认
正文成员时禁止生成空盒，也禁止在本阶段把相邻普通正文重新吸入栏目；只能选择模板已有的非盒式局部标题等
安全降级。Spec 04-C 不得生成 render node、payload、输出锚点、`render_plan` 或 LaTeX，也不得重建公式/表格，
并必须记录 `full_spec04_status=not_evaluated`。

Spec 04-D 必须把全部 included source atom 划分为恰好一次逻辑输出；任何图片、表格、图表或公式原子都必须
由活动 Spec 03 的关闭表示覆盖，禁止直接读取 `asset_ref`、OCR 文本或历史 render plan 绕过媒体合同。
一对多媒体 fragment 必须保持为同一媒体节点。来源结构证据与媒体原子重合时，不得复制该原子：允许生成
引用已关闭 source outline 的 `virtual_source_supported` 结构节点，同时该原子只在媒体节点输出一次。
虚拟结构节点不计作来源原子输出，也不得脱离 `source_outline_ledger` 创造新标题。

04-D 只有在 SM-H01 至 SM-H19 全部成立、review queue 关闭、ledger checkpoint 更新为
`semantic_frozen` 且独立 promotion 通过时，才可声明 `full_spec04_status=passed`。该状态只关闭规范 04，
不代表 Spec 05 LaTeX、编译或 Spec 06 逐页验收已经执行。

## 2. 分层规则模型

### 2.1 通用内核

只处理跨材料稳定的不变量，例如：

- 结构父子关系；
- 来源顺序；
- 范围和 block ID；
- 环境嵌套合法性；
- 模板构件存在性；
- 答案可见性状态；
- 映射审计和回归协议。

### 2.2 材料类型/学科/语言 profile

描述教材、试卷、练习册、讲义等类型的常见结构和教学角色，以及不同学科和语言的标签惯例。

profile 必须数据化、版本化、可测试；不得通过修改通用代码增加出版社或语言标签。

### 2.3 单书配置

只允许记录：

- 来源证据确认的栏目别名；
- 特定层级名称；
- 产品范围例外；
- 当前模板允许的映射选择；
- 经人工关闭的局部例外。

不得记录书中正文、答案、固定页码触发器、图片哈希触发器或用来“让样本通过”的替换文本。

## 3. 语义对象

语义模型至少能表达：

- 书级结构：部分、单元、章、节、课题；
- 教学栏目：目标、概念、定义、定理、方法、提示、总结、警示；
- 教学实例：例题、分析、解答、变式；
- 学生活动：练习、题组、测评、探究、讨论、书写任务；
- 答案与解析；
- 普通叙述、图表、公式、图注和说明；
- 无法确定但必须保留的 `unknown/plain_body`。

通用语义类型保持宽泛，具体栏目名称放入 profile 的 `role` 或别名，不为单书增加核心 schema 字段。

## 4. 映射记录

每个语义范围至少记录：

- `semantic_id`；
- 来源 block/span IDs；
- 来源可见标签；
- 结构层级和父节点；
- `semantic_type`、`role` 和范围边界；
- profile/rule ID 和版本；
- 置信度和 review 状态；
- 目标模板构件；
- 目标构件参数；
- 为什么使用或不使用盒子；
- 预期输出节点/锚点和 payload 哈希。

## 5. 目标模板能力清单与 ElegantBook 映射原则

规范 04 是 `template_capability_manifest.json` 的唯一生产者。这里的目标模板固定为
`2025教材模版新版.zip`（批准 SHA-256 `ebc5d6575153239552785265d2765de03b76c27d0532137712952f20ad0a9d4d`），
不存在替换模板或跨模板选择。映射前必须用确定性检查器从该精确模板实物提取并版本化，至少记录：

- 模板 ID、模板 ZIP/class/入口文件原始哈希；
- 可用章节命令、ElegantBook/class 公共环境、模板局部自定义命令／环境和 tcolorbox style/key 的名称及签名，
  并将模板局部自定义命令／环境标记为 `inventory_only_forbidden_in_generated_body`；
- 构件的标题参数、可见性、编号、目录行为、可分页性和已知嵌套限制；
- 观察到的 metadata 入口和正文插入候选边界，明确标记为待规范 05 冻结；
- `template_intake.json` 哈希及清单生成器版本。

规范 04 根据该清单选择构件并在每条映射中绑定清单哈希；规范 05 必须字节级原样携带该清单，
对模板实物重新验证后另产验证报告和权威模板合同，不得覆盖或同名重生成清单。
不能只凭对 ElegantBook 的一般记忆或另一份模板推断当前模板具备某个构件。

- 书级结构使用模板支持的 `chapter/section/subsection` 等构件；
- 局部教学栏目不得因为视觉醒目而进入书级目录；
- 原生 `definition`、`theorem`、`example`、`exercise`、`problem`、`note`、`solution` 只在真实语义匹配时使用；
- tcolorbox 必须使用模板已有 style，不能把 style key 当成不存在的环境；
- 生成正文不得定义或调用模板局部自定义命令／环境；模板局部定义只为冻结完整性库存。
  教学盒子必须序列化为标准 `tcolorbox` 环境加模板已有 style key，不得生成新的命令或环境；
- 不要求使用所有盒子；与材料无关的构件应保持不用；
- 长表格、复杂公式、跨页题组和大段正文避免被包进脆弱的大盒子；
- 源可见答案必须保持可见，除非产品合同明确要求学生版隐藏且模板提供合法机制；
- 语义标题可以样式化，但不得吞掉其后正文或改变来源措辞；
- 映射只改变结构和呈现，不得修补不确定 OCR、公式含义或缺失内容。

## 6. 必需产物

- `semantic_profile.json` 或等价版本化 profile；
- `book_config.json`（仅在确有必要时）；
- `template_capability_manifest.json`；
- `source_outline_ledger.json`；
- `semantic_mapping_ledger.json`；
- `render_plan.json`：按来源顺序列出确定性 render binding、目标构件、参数、payload 和输出节点；
- `volume_partition_plan.json`：把完整 render node 流冻结为一个或两个连续、互斥、完备的卷；单卷为默认，
  单卷内部 Tex 分片不改变卷数；两卷必须绑定在清理未引用资产、字节复用、安全图片传输编码和受控 Tex 分片后
  单卷仍无法满足项目级交付门禁的证据、来源支持的完整语义切点、每卷封面标签和关闭决定。每卷还须冻结
  `body_units`：从来源层级机械取得的章节、单元、整套试卷、讲次或其他完整顶层语义单元及其连续 render node
  成员关系；不得依赖标题字符串、文件名、material ID 或固定页码。`delivery_capacity_preflight` 记录正文序列化
  字节上界、900,000 字节叶分片预算、最大不可分割 render node／Tex 行上界、单图片字节上界和证据引用；
- `semantic_review_queue.json`；
- `semantic_mapping_report.json`；
- `semantic_mapping_report.md`；
- `semantic_decisions.jsonl`；
- 含语义和 render binding overlay 的新不可变 canonical ledger 子快照；
- 基于输入 ledger/template 证据冻结的新 `canonical_decision_index.json` 子快照；
- `semantic_stage_manifest.json`：单向绑定决定索引、输出 ledger、能力清单、render plan 和报告哈希。
- `spec04d_preflight.json`：列出所有未获关闭 Spec 03 表示的高风险原子及跨阶段冲突；失败时不得生成 passed run；
- `spec04d_render_plan_stage_manifest.json`：完整规范 04 的 E→D→L→M 提交清单。

顺序必须是：先冻结语义/布局决定索引 D，再生成绑定 D 的 render plan 与 ledger 子快照 L，
最后由 `semantic_stage_manifest.json` 同时绑定 D 与 L；D 不得反向引用 L 或 render plan。

## 7. 硬门禁

- `SM-H01 source_outline_closed`：来源书级目录与正文结构闭环；
- `SM-H02 hierarchy_valid`：无层级跳跃、错父节点、重复课题或局部栏目污染目录；
- `SM-H03 every_body_range_semantically_disposed`：每个正文范围有语义角色或安全的 `plain_body`；
- `SM-H04 render_plan_consumes_annotation`：每个 render binding 都来自已通过的语义 assignment，计划生成器未旁路猜测；
- `SM-H05 mapping_traceable`：每个样式映射可追溯到来源和规则；
- `SM-H06 template_construct_exists`：目标构件在当前只读模板实物和能力清单中真实存在；
- `SM-H07 no_new_template_construct`：未新增环境、命令或 style；
- `SM-H07A no_template_local_custom_api_usage`：render plan 不选择、payload 不定义或调用模板局部自定义命令／环境；
  tcolorbox 映射只通过标准环境引用现有 style key；
- `SM-H08 answer_visibility_correct`：答案可见性符合来源和产品合同；
- `SM-H09 source_order_preserved`：映射未改变来源流水顺序；
- `SM-H10 fragile_content_protected`：表格、公式和长练习未被不安全包裹；
- `SM-H11 no_sample_identifier_logic`：规则不依赖书名、文件名、任务 ID、页码或哈希；
- `SM-H12 layer_ownership_recorded`：每条规则明确属于 core、profile 或 book config；
- `SM-H13 no_open_semantic_review`：语义和映射人工决策全部关闭；
- `SM-H22 body_partition_semantically_closed`：每卷 `body_units` 连续、互斥、完备地覆盖全部 render node；
  单元边界来自已冻结来源层级，超大单元只允许在其内部按完整 render node／完整行机械分片。
- `SM-H14 mapping_bound_to_template_manifest`：全部映射绑定本次目标模板能力清单及其哈希；
- `SM-H15 render_plan_deterministic`：相同账本、profile、book config 和模板能力清单产生相同 render plan；
- `SM-H16 planning_execution_separated`：本规范未写入实际 LaTeX 正文，实际渲染留给规范 05；
- `SM-H17 decision_index_updated`：语义和布局决定全部登记到新的 canonical decision index 快照。
- `SM-H18 semantic_stage_commit_acyclic`：semantic stage manifest 以无环顺序绑定决定索引和输出快照。
- `SM-H19 toc_representation_renderable`：每个要求进入最终目录的来源结构节点，都必须同时记录来源语义层级、
  模板 TOC entry type、冻结模板的有效 `tocdepth` 证据及实际序列化策略。原生深度不足时，只允许使用已经
  在模板能力清单中声明、保持原 entry type 与 PDF outline 层级、局部生效且不修改 class、导言区、宏包、
  自定义命令或环境的策略；不得通过把二级标题伪装成一级目录项、删除目录项或在 Spec 05 临时改深度取得通过。
- `SM-H20 volume_partition_closed`：卷数只允许 1 或 2；各卷 render order 区间连续且按原顺序连接，
  render node 与 included source atom 的跨卷并集完整、交集为空，切点不穿过教学组、题组、图注关系或其他冻结依赖；
- `SM-H21 single_volume_preferred`：单卷预计能满足交付上限时不得无依据拆卷；两卷计划必须有关闭的触发证据和
  合法结构边界，且每卷预算估计均严格低于 900,000 字节正文分片、1,000,000 字节单图片、2,000 文件实体和
  50,000,000 字节 ZIP 上限；单个正文单元超限但在单元内受控分片后其余项目级门禁可满足时不得拆卷；
  无法找到合法两卷切点时本规范失败而不是输出三卷；

## 8. 人工决策门禁

- `SM-R01 ambiguous_structure_level`：短标题是书级结构还是局部栏目；
- `SM-R02 ambiguous_semantic_role`：同一标签在上下文中有多个可能角色；
- `SM-R03 scope_boundary`：栏目、例题、分析、解答或练习的结束边界；
- `SM-R04 construct_choice`：普通正文、原生环境和已有盒子之间的选择；
- `SM-R05 profile_extension`：新材料类型/语言需要增加 profile 规则；
- `SM-R06 answer_policy`：答案属于正文、教师版或需隐藏版本；
- `SM-R07 exceptional_layout`：正确语义构件会损害复杂内容的可读性或编译稳定性。

人工决定必须引用来源块、视觉证据、候选构件和选择理由。
同一本书内重复出现的同类决定只能登记为 profile 候选；只有具备一般性依据、明确适用条件并通过跨样本回归后，
才可提升为 profile 规则。否则必须保留在 book config，不能进入 profile 或通用代码。

所有影响构件参数、浮动、分页或合法布局路径的选择都必须在冻结 render plan 前关闭；
规范 05 不得再次进行布局选择。

## 9. 质量提示

- 大量正文退化为 `unknown/plain_body`；
- 同一来源标签映射到多个角色；
- 盒子占比或书级标题密度异常；
- 语义范围过长、跨越表格或多个题组；
- 某个 profile 只对一个样本生效；
- book config 规则数量持续增长；
- 某个模板构件从未使用或被过度使用。

提示本身不要求“为了覆盖率而强行分类”。它用于发现 profile 不匹配和潜在过拟合。

## 10. 失败码

- `SEMANTIC_OUTLINE_MISMATCH`
- `SEMANTIC_HIERARCHY_INVALID`
- `SEMANTIC_ROLE_UNRESOLVED`
- `SEMANTIC_SCOPE_INVALID`
- `SEMANTIC_ANNOTATION_NOT_CONSUMED`
- `MAPPING_TEMPLATE_CONSTRUCT_MISSING`
- `MAPPING_TEMPLATE_MANIFEST_MISMATCH`
- `MAPPING_TEMPLATE_MUTATION_REQUIRED`
- `MAPPING_RENDER_PLAN_NONDETERMINISTIC`
- `MAPPING_EXECUTION_BOUNDARY_VIOLATED`
- `MAPPING_DECISION_INDEX_INVALID`
- `MAPPING_STAGE_COMMIT_INVALID`
- `MAPPING_ANSWER_VISIBILITY_WRONG`
- `MAPPING_SOURCE_ORDER_CHANGED`
- `MAPPING_FRAGILE_CONTENT_WRAPPED`
- `MAPPING_FRAGILE_MEDIA_REPRESENTATION_MISSING`
- `MAPPING_MEDIA_FRAGMENT_DUPLICATED`
- `MAPPING_CROSS_STAGE_ROLE_COLLISION_UNRESOLVED`
- `MAPPING_TOC_LEVEL_UNRENDERABLE`
- `MAPPING_OVERFIT_RULE`
- `SEMANTIC_REVIEW_OPEN`

## 11. 验收与交接

规范 05 只能消费：

- 已通过的语义账本；
- 新的不可变 ledger snapshot ID、版本和哈希；
- 已冻结的 profile 和 book config 版本；
- 规范 04 唯一生成且已绑定哈希的 `template_capability_manifest.json`；
- 已冻结并绑定账本哈希的 `render_plan.json`；
- 已冻结并绑定同一 render plan/hash 的 `volume_partition_plan.json`；
- 已关闭的人工决定；
- 当前 canonical decision index 快照及哈希；
- `semantic_stage_manifest.json`；
- 明确的目标模板构件；
- 未改变教材措辞和来源顺序的渲染输入。

如果渲染需要新增模板命令、猜测公式、补写内容或隐藏未授权答案，必须退回本规范或更上游，不能在编译阶段解决。
