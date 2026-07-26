# 06 最终逐页验收及产品通过规范

状态：`Normative Baseline v0.3`

生效日期：`2026-07-23`

上位契约：[AGENTS.md](../AGENTS.md)  
上游规范：[05 模板冻结与编译规范](05-template-freeze-and-compile-spec.md)

## 0. 共同硬原则

1. 原 PDF 不可访问时，只能执行受限诊断；正式链路保持 `blocked`，不得取得来源忠实或最终产品通过；
2. 任何未关闭的人工判断或 review queue 项都阻止相应规范进入 `passed`；
3. 最终 PDF 必须逐页自动渲染和检查，高风险、异常及语义争议必须人工闭环，黄金样本还必须由用户明确接受。

本规范的局部 `passed` 不等于最终产品通过；规范 06 的 `passed` 也不等于黄金样本已获用户接受。

## 1. 目的

本规范以最终用户实际看到的 PDF 为验收表面，证明正文范围、内容完整性、流水阅读顺序、
教学语义、ElegantBook 映射、模板完整性和视觉输出共同成立。

它不要求像素级复刻原 PDF。ElegantBook 可以改变换行、分页和视觉布局，但不能改变来源内容、关系和阅读顺序。

最终验收只发现、记录和路由缺陷；不得在本阶段直接修改正文来制造通过。

## 2. 输入

- 原 PDF 及 SHA-256；
- 规范 01 的输入合同和来源血缘；
- 规范 02 的正文范围和阅读顺序账本；
- 规范 02 绑定原 PDF 哈希的全页来源 render ledger；
- 规范 03 的 canonical ledger、覆盖和媒体账本；
- 规范 04 的来源目录、语义和映射账本；
- 规范 05 的 `delivery_set_manifest.json`，以及其中每卷最终 ZIP、PDF、模板完整性和编译报告；
- 规范 05 每卷独立 producer 与 promotion evaluator 的正文传输/Overleaf 可编辑文本容量报告；
- 每卷的 `final_render_pack/manifest.json` 及其全部 raster；单卷兼容输入视为仅含一卷的 delivery set；
- 当前 `canonical_decision_index.json` 不可变快照；
- 所有人工决定和 warning baseline；
- 可选历史黄金基线。

所有输入必须属于同一材料并形成可验证的有向无环血缘。原 PDF 和规范 01–02 等早期不可变产物无需、也不得
反向引用未来 ledger/build；当前 `render_coverage` ledger 和 build manifest 必须单向绑定这些上游哈希。
只有在 ledger/build 之后生成的派生产物，才必须引用当前 ledger snapshot 和 build ID/hash。

## 3. 必需产物

- `page_review.json`；
- `final_page_hash_ledger.jsonl`；
- `source_page_coverage.json`；
- `source_block_coverage.json`；
- `reading_order_audit.json`；
- `formula_table_image_audit.json`；
- `semantic_mapping_audit.json`；
- `toc_page_number_audit.json`；
- `automated_anomalies.json`；
- `page_review.html`；
- `page_contact_sheet.pdf`；
- `human_review_queue.json`；
- `final_decisions.jsonl`：终验阶段唯一决定事件文件；
- `final_acceptance.json`：版本化 acceptance/commit manifest；
- `final_acceptance.md`；
- 登记全部终验决定的新 `canonical_decision_index.json` 子快照 D；
- `ledger_checkpoint=final_verified`、单向绑定 D 的新不可变 canonical ledger 子快照 L；
- 同时绑定 D 与 L 的 `final_acceptance.json` commit 版本 M。

## 4. 全页账本

必须按卷直接消费并验证规范 05 生成的不可变 `final_render_pack/manifest.json`，不得在规范 06 另行生成一套
无法与正式 build 对齐的页面证据。任一卷 manifest 或 raster 缺失、哈希不符时必须退回规范 05 重建。

每页至少记录：

- 最终 PDF SHA-256；
- 渲染配置及哈希；
- 页索引和页标签；
- MediaBox/CropBox、旋转和尺寸；
- raster 文件和 SHA-256；
- 可选 perceptual hash；
- 提取文本及规范化哈希；
- ink/content bbox；
- 映射的来源页、来源块和章节；
- 自动检查结果；
- 风险等级；
- 人工决定引用；
- 最终页面状态。

## 5. 全页自动检查

自动检查必须覆盖每一页，至少包括：

- 页面能否成功渲染；
- 缺页、重复页、异常空白页和异常低内容密度；
- 文本、图片、公式、表格、盒子是否越界、裁切或重叠；
- 缺字、乱码、不可用字体或异常替代；
- 图片缺失、损坏、尺寸异常或身份错误；
- 可见调试文字、Markdown、HTML、源路径或内部标记；
- 目录条目、章节标题、页码和页标签；
- 正文开头、各章节实质尾部和全书尾部；
- 来源正文页和来源块覆盖率；
- 无来源新增、异常重复和阻断级阅读顺序逆转；
- 图片、表格和公式双向闭环；
- 语义盒子是否来自模板已有构件；
- 源可见答案是否被错误隐藏；
- 来源封面、版权、原目录、广告、空白页和后封底是否误入正文。

自动检测不能替代规范 03 的账本闭环，而应验证最终渲染与账本一致。

## 6. 硬门禁

- `FR-H01 all_upstream_specs_passed`：规范 01、02、04、05 均为 `passed`，
  且规范 03 在 `ledger_checkpoint=render_coverage` 上为 `passed`；
- `FR-H02 artifacts_same_build`：ZIP、PDF、账本、模板和报告绑定同一 build/ledger；
- `FR-H03 page_ledger_complete`：render manifest 和最终页账本数量均等于 PDF 页数且索引连续；
- `FR-H04 every_page_rendered`：每页 raster/path/hash 存在并与 render manifest 一致；
- `FR-H05 no_unexplained_blank_or_duplicate_page`：无无法解释的空白或重复页；
- `FR-H06 no_content_clipping_or_overlap`：无阻断级裁切、越界和重叠；
- `FR-H07 source_page_coverage_complete`：全部来源正文页有覆盖或明确来源空白决定；
- `FR-H08 source_block_coverage_complete`：必需来源块覆盖率 100%；
- `FR-H09 no_unexplained_addition`：无来源新增教材内容为 0；
- `FR-H10 no_unexplained_omission_or_duplicate`：无解释遗漏和重复为 0；
- `FR-H11 reading_order_valid`：无阻断级流水顺序错误；
- `FR-H12 media_formula_table_closed`：图片、表格、公式闭环；
- `FR-H13 semantic_mapping_valid`：目录、角色、盒子和答案可见性正确；
- `FR-H14 toc_and_page_numbers_valid`：目录和页码与最终正文一致；
- `FR-H15 substantive_tail_present`：最后一个来源教学项目及实质尾部存在；
- `FR-H16 no_visible_internal_residue`：无内部标记、路径或调试残留；
- `FR-H17 no_blocking_anomaly`：无已确认 V0/V1，也无尚未裁决的 V0/V1 检测候选；
- `FR-H18 no_open_human_review`：所有 V2 和人工决策项关闭；
- `FR-H19 final_decision_index_frozen`：最终决定索引 D 无开放/过期项，绑定既有 `render_coverage` 父 ledger、
  ZIP、PDF、render manifest、页账本和终验报告，但不引用未来 `final_verified` 子 ledger；
- `FR-H20 final_verified_snapshot_created`：已从 `render_coverage` 父快照创建单向绑定 D 的 `final_verified` 子快照 L；
- `FR-H21 acceptance_commit_created`：已创建同时绑定 D 与 L 的版本化 `final_acceptance.json` commit M。
- `FR-H22 delivery_set_all_volumes_reviewed`：每卷全部页面均完成自动检查，风险页与人工队列分别闭环，
  不得因另一卷通过而跳过失败卷；
- `FR-H23 cross_volume_source_complete`：跨卷 source atom、render node、阅读顺序和媒体锚点与冻结分卷计划一致，
  并集完整、交集为空，卷边界两侧不存在丢失、重复或被拆散的依赖组；
- `FR-H24 delivery_set_artifacts_bound`：最终接受提交绑定 delivery set manifest、全部分卷 ZIP/PDF/render pack/page ledger
  的精确哈希；任一卷字节变化都会使整套接受失效。
- `FR-H25 overleaf_delivery_capacity_bound`：每卷最终接受对象绑定经 producer 与独立 evaluator 双重核验的
  来源支持语义 unit／正文叶分片加载图、900,000 字节分片、1,000,000 字节单图片、2,000 文件实体和
  50,000,000 字节 ZIP 报告；分片不得被误计为分卷，分卷也不得掩盖单卷超限。全部可编辑文本总字节只作兼容性观测。

## 7. 异常分级

- `V0 FATAL`：PDF/hash 不一致、页面无法渲染、页账本缺失或属于旧产物；
- `V1 BLOCKING`：正文遗漏/编造/重复、错序、裁切、重叠、缺字、缺图、公式表格错误、正文范围泄漏；
- `V2 HUMAN_REQUIRED`：OCR 歧义、多栏顺序、语义盒子或复杂图表存在合理争议；
- `V3 INFO`：不影响内容、关系、顺序和可读性的分页、换行或轻微间距差异。

自动检测器首先产生 `detected_candidate`，并记录 `candidate_severity`、证据和检测器版本。
异常状态只允许：`detected_candidate`、`confirmed`、`false_positive`、`resolved_by_rebuild`。
具有确定性证据的 V0/V1 可以直接进入 `confirmed`；其他高风险候选必须人工核验。

人工可以用反证把检测候选判定为 `false_positive` 并说明正确分级，但这表示检测器未证明缺陷，不是豁免缺陷。
一旦 V0/V1 被证据确认，就绝不能人工关闭为通过，只能修复并用新产物重验。V2 必须形成证据化人工决定；
V3 可以保留，但必须记录。

## 8. 人工审查范围

必须人工处理：

- 所有高风险自动异常；
- OCR、MinerU、Popo 与 PDF 视觉冲突；
- 多栏、跨页和侧栏阅读顺序争议；
- 教学语义和 ElegantBook 构件争议；
- 复杂表格、公式、图形和答案可见性疑似偏差；
- 自动算法无法确认的正文范围问题；
- 任何影响教材可读性和教学关系的视觉问题。

人工可以确认“版式不同但内容、关系和顺序忠实”，也可以证据化否定自动检测假阳性；
不能豁免已确认的遗漏、新增、模板漂移、编译错误或缺失资产。

人工决定必须记录来源页、最终页、block ID、候选、证据、理由、决定者和时间，并绑定当前 PDF/ZIP/ledger 哈希。
终验决定事件必须登记到 canonical decision index 的新不可变快照。

## 9. 黄金样本用户接受

黄金样本只有满足以下条件后才能呈交用户：

- 规范 01、02、04、05 全部通过，且规范 03 的 `render_coverage` 检查点通过；
- 全页自动检查通过；
- 所有 V2 已人工关闭；
- 人工决定绑定当前产物哈希；
- 无开放异常。

在进入 `ready_for_user_acceptance` 前，规范 06 必须依次：

1. 在 `FR-H01`–`FR-H18` 通过后冻结最终 decision index D；
2. 创建绑定 D、最终 ZIP/PDF、render manifest、最终页账本和异常报告的 `final_verified` ledger L；
3. 创建同时绑定 D 与 L 的 `final_acceptance.json` commit M。

D 不得反向引用 L。M 是二者的共同承诺点，不属于 canonical ledger 或 decision index。

呈交时状态为 `ready_for_user_acceptance`。

用户接受记录必须绑定：

- 原 PDF SHA-256；
- 最终 PDF SHA-256；
- 最终 ZIP SHA-256；
- 模板合同/完整性报告哈希；
- `final_verified` canonical ledger 和最终页账本哈希；
- canonical decision index 最终快照哈希。

用户未明确接受时，不能标记 `human_accepted`。用户拒绝本身只改变 `acceptance_status`，不自动把已通过规范改成失败；
如果拒绝理由揭示了硬门禁缺陷，必须路由到责任规范并使相关规范、`final_verified` 快照和终验状态失效后重做。

`final_acceptance.json` 必须采用不可变版本链：首次 M 记录 `ready_for_user_acceptance`；
用户接受、拒绝或产物失效时创建带 `parent_acceptance_hash` 的新版本，不覆盖旧记录。

## 10. 状态

`spec_status` 统一使用：

- `blocked`；
- `failed`；
- `needs_review`；
- `passed`。

另记录产品接受状态：

- `not_ready`；
- `ready_for_user_acceptance`；
- `human_accepted`；
- `human_rejected`；
- `invalidated`。

对黄金样本，只有 `spec_status=passed` 且 `acceptance_status=human_accepted` 才能成为黄金基线。

## 11. 失败码

- `FINAL_UPSTREAM_SPEC_NOT_PASSED`
- `FINAL_ARTIFACT_HASH_MISMATCH`
- `FINAL_PAGE_RENDER_FAILED`
- `FINAL_PAGE_LEDGER_INCOMPLETE`
- `FINAL_BLANK_OR_DUPLICATE_PAGE`
- `FINAL_CLIPPING_OVERLAP_OR_UNREADABLE`
- `FINAL_SOURCE_PAGE_UNCOVERED`
- `FINAL_SOURCE_BLOCK_OMITTED`
- `FINAL_UNSOURCED_ADDITION`
- `FINAL_CONTENT_DUPLICATED`
- `FINAL_READING_ORDER_INVALID`
- `FINAL_MEDIA_FORMULA_TABLE_INVALID`
- `FINAL_SEMANTIC_MAPPING_INVALID`
- `FINAL_TOC_PAGE_NUMBER_INVALID`
- `FINAL_BODY_TAIL_MISSING`
- `FINAL_VISIBLE_INTERNAL_RESIDUE`
- `FINAL_HUMAN_REVIEW_OPEN`
- `FINAL_VERIFIED_LEDGER_INVALID`
- `FINAL_DECISION_INDEX_INVALID`
- `FINAL_ACCEPTANCE_COMMIT_INVALID`
- `FINAL_OVERLEAF_DELIVERY_CAPACITY_INVALID`

以下是接受状态迁移/原因码，不是规范失败码：

- `ACCEPTANCE_PENDING`
- `ACCEPTANCE_USER_ACCEPTED`
- `ACCEPTANCE_USER_REJECTED`
- `ACCEPTANCE_INVALIDATED`

## 12. 修复后的重验

所有产物修复后都必须重新执行全页自动渲染和检查。

人工重审范围可以按影响收敛：

- 正文或语义修改：修改块相关页、相邻页、章节、目录及所有因重排而变化的页面；
- 图片、表格、公式修复：对应页、相邻溢出风险页及媒体闭环；
- metadata 修改：封面、目录、页标签和所有 hash 变化页；
- 字体、模板、class、页边距或编译环境变化：规范 05 重新执行，整本人工决定失效；
- 只补充人工决定且产物哈希不变：无需重新渲染，但必须重新计算接受状态；
- 任何 PDF 字节变化：旧 render pack、page ledger、`final_verified` 快照和最终通过状态全部失效。

## 13. 最终通过

最终报告必须分别声明：

- 六份规范各自状态；
- 全页自动检查状态；
- 人工决策项数量；
- 黄金样本用户接受状态；
- 残余 V3 提示；
- 当前产物和账本哈希。

不得用“PDF 能打开”“脚本成功”“抽样页面正常”或“编译通过”代替最终产品通过。
