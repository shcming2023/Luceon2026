# 05 模板冻结与编译规范

状态：`Normative Baseline v0.6`

生效日期：`2026-07-23`

上位契约：[AGENTS.md](../AGENTS.md)  
上游规范：[04 教学语义与 ElegantBook 映射规范](04-semantic-elegantbook-mapping-spec.md)

## 0. 共同硬原则

1. 原 PDF 不可访问时，只能执行受限诊断；正式链路保持 `blocked`，不得取得来源忠实或最终产品通过；
2. 任何未关闭的人工判断或 review queue 项都阻止相应规范进入 `passed`；
3. 最终 PDF 必须逐页自动渲染和检查，高风险、异常及语义争议必须人工闭环，黄金样本还必须由用户明确接受。

本规范的局部 `passed` 不等于最终产品通过，并且不得替代规范 06 的全页及用户验收。

## 1. 目的

本规范确保已确认语义的正文只被写入目标模板允许的位置，模板冻结区不发生越权变化，
并且最终交付 ZIP 在固定、干净、可复现的环境中使用目标 ElegantBook 入口成功编译。

本规范是冻结 render plan 的唯一 LaTeX 渲染执行阶段，也是权威 `template_contract.json` 的唯一生产者。

本规范证明模板完整性和工程可编译性，不替代来源忠实、语义正确或最终视觉验收。

## 2. 输入

- 固定目标模板 `2025教材模版新版.zip`，批准 SHA-256 为
  `ebc5d6575153239552785265d2765de03b76c27d0532137712952f20ad0a9d4d`；
- 已通过规范 04 的不可变 canonical ledger snapshot、render plan 和映射报告；
- 规范 04 的 `semantic_stage_manifest.json`；
- 规范 04 唯一生成并已绑定哈希的 `template_capability_manifest.json`；
- 规范 01 的 `template_intake.json`；
- 当前 `canonical_decision_index.json` 快照；
- 正文和经闭环确认的图片/表格/公式资产；
- 允许修改的 metadata 配置；
- 经 schema 校验、决定关闭且与精确资产绑定的版本化 `spec05_presentation_config.json`；
- 编译环境合同；
- 允许补充的旁路依赖清单；
- 当前构建和交付目录。

正式编译只能消费已冻结的 render plan，不得在此阶段重新猜测结构、语义或盒子类型。

## 3. 模板冻结模型

规范 05 只能从模板实物、`template_intake.json`、用户约束和规范 04 的能力清单生成一次权威模板合同。
模板合同至少包含：

- 原始模板 ZIP SHA-256；
- `elegantbook.cls` 和其他不可变文件的逐文件哈希；
- `main.tex` 模板骨架及其哈希；
- `\documentclass` 名称和选项；
- 包加载列表、顺序和选项；
- 自定义命令、环境、theorem、tcolorbox style/key 的库存和签名；
- 正文插入边界；
- metadata 白名单、参数位置和允许值类型；
- cover、logo 各自的呈现模式、精确资产身份、来源／批准证据、确定性裁切与适配参数，以及对应关闭决定；
- 可补充但不得改变模板行为的旁路依赖清单；
- 正式编译引擎和最低环境要求。

规范 04 的 `template_capability_manifest.json` 必须字节级原样携带并保持原哈希。
本规范另产 `template_capability_validation_report.json`，证明清单与当前模板实物一致；
不得覆盖、修补或同名重生成能力清单。验证失败必须退回规范 01/04 修正固定模板身份或能力合同；
不得更换为其他模板输入。

由于 metadata 和封面配置可能位于 `\begin{document}` 之后，不能只比较传统导言区。
必须把允许修改的 metadata 参数和正文插入区替换为稳定占位符，再计算 `masked_main_sha256`；
模板骨架的其他字节均视为冻结。

## 4. 允许修改

仅允许：

- 修改模板合同列出的 `\title`、`\subtitle`、`\author`、`\institute`、`\date`、`\extrainfo` 等参数值；
- 修改合同允许的 `\logo`、`\cover` 或封面配置值；
- 在正文插入区写入 render plan 生成的正文；
- 复制账本已确认的资产；
- 增加模板合同明确允许的非可执行静态旁路依赖，并记录来源、用途和哈希。

允许项不是模糊的文件范围，而是具体宏参数、正文插入区和文件清单。

cover 与 logo 必须分别显式选择 `template_default`、`source_region_asset` 或
`approved_static_asset`，不得从书名、语言、学科、样本 ID 或文件名推断。非默认资产必须使用新文件名复制到
正式工程，绑定路径、SHA-256、MIME、像素尺寸和确定性 fit/crop；不得覆盖模板原有 cover/logo 文件。
`source_region_asset` 还必须绑定原 PDF、页面 raster、页码、bbox 和正文范围决定，并证明物化资产与来源裁片
逐像素一致。原书封面、扉页即使被规范 02 排除出正文，仍可作为 presentation identity 证据；这不会改变其
正文范围状态，也不得把其中内容重新排入正文。

## 5. 禁止修改

- 不得修改 `elegantbook.cls`；
- 不得修改 `\documentclass` 选项；
- 不得新增、删除或重定义宏包、命令、环境、颜色和 style；
- 不得在生成正文中定义或调用模板局部自定义命令、模板局部自定义环境；
- 不得把 tcolorbox style key 当成自定义环境调用；盒子只能序列化为标准 `tcolorbox` 环境并引用冻结模板已有 style key；
- 不得用任意 `\input`、`\include`、`\AtBeginDocument` 等方式在正文侧绕过冻结区；唯一例外是受
  `BODY-SEMANTIC-SHARDING-900K-V1` 约束的正文加载图：模板正文插入区只能出现一次标准
  `\input{body/generated-body.tex}`；该文件只能以连续编号、固定顺序直接加载
  `body/units/unit-NNNN/part-NNNN.tex`。unit 必须对应规范 04-D 冻结的来源支持语义单元；所有叶 part 按序连接后
  必须与 `rendered_body.tex` 字节完全一致；loader 不得有其他语句，part 不得嵌套加载或定义 TeX 行为；
- 不得重定义 `\clearpage`、`\cleardoublepage` 或破坏章节和文档尾部 flush；
- 不得使用任何不同于固定 `2025教材模版新版.zip` 的模板或 class；
- 不得为单本样本增加临时“安全修复”宏；
- 不得使用 demo image、占位图或 fallback 文档冒充正式输出；
- 不得在编译阶段删除难处理正文、公式、表格或图片。

旁路依赖默认只允许冻结模板已经引用的静态图片、字体，以及不含 `@preamble`、命令/环境定义或脚本的纯数据型
`.bib` 文件。除上述唯一、哈希绑定的受控正文 loader/part 集合外，禁止新增 `.tex`、`.sty`、`.cls`、
`.cfg`、`.def`、`.lua`、`.py`、`.sh`、可执行文件、符号链接和任何能定义或改变 TeX 行为的内容。
正文载荷不属于旁路依赖；`body/units/` 是 renderer 对冻结计划的机械视图，不是人工编辑或重新裁决章节的入口。
需要其他此类文件时只能建立经用户批准的新模板版本并重新冻结。

最终工程必须递归检查 `\input`、`\include`、class、package 和配置加载图，并对展开后的包、命令、环境、
theorem、颜色和 tcolorbox style/key 库存作机械比较；新增文件不得成为绕过冻结区的入口。

## 6. 编译合同

### 6.1 唯一渲染执行

规范 05 的确定性 renderer 只能执行已冻结的 render binding、必要的合法 LaTeX 转义和序列化。
它先生成规范化暂存产物 `rendered_body.tex`，再机械消费规范 04-D 冻结的 `body_units`。每个 unit 写入
`body/units/unit-NNNN/part-NNNN.tex`；单元超过 `900,000` 字节时，只在该单元内部按完整 render node／完整行边界
继续分片。每个 part、根 `main.tex` 和唯一 loader 均必须严格小于 `900,000` 字节。连接全部 part 必须逐字节重建
暂存载荷。`body/generated-body.tex` 只按冻结顺序直接加载全部叶 part。根 `main.tex` 保持模板骨架，只在允许的正文插入区写入一次标准
`\input{body/generated-body.tex}`。ZIP 内主入口固定为根 `main.tex`，不得把正文文件设为主入口或重命名模板入口。

必须生成 `render_execution_report.json`，记录：

- 输入 ledger snapshot、render plan、profile、book config 和能力清单哈希；
- `rendered_body.tex` 哈希；
- 最终工程中承载正文的文件、插入边界和正文 payload 哈希；
- 正文语义 unit、part 顺序、逐文件哈希、重建哈希、每片字节数及全部可编辑文本字节总数；
- 每个 output node/emission 到实际 LaTeX 锚点的映射；
- renderer、转义器和序列化规则版本；
- 确定性重跑结果。

renderer 不得重新选择语义、构件、参数、浮动或分页方案。无法执行计划时必须失败并退回规范 04，
不得在本规范内人工改选布局。

### 6.2 交付物先确定

正式交付以 `delivery_set` 为权威对象，只允许一卷或两卷。每一卷的正式 ZIP 都必须先封装并计算哈希，再解压到
该卷全新隔离构建目录中编译；由此证明每卷最终 PDF 都来自与该卷交付 ZIP 完全相同的源文件，而不是另一份临时工程。
单卷计划保持既有单 ZIP/PDF 路径及兼容字段；两卷计划必须生成两个独立工程、两个 ZIP、两个 PDF、两个 render pack，
以及绑定全部分卷 artifact 和跨卷覆盖结果的 `delivery_set_manifest.json`。规范 05 不得推断或改变 04-D 的切点。

交付集合中的外层 ZIP/PDF 文件名必须由冻结封面 `title` 和可选的冻结卷标机械派生，两个扩展名共享同一 stem；
ZIP 内入口仍固定为 `main.tex`。文件名采用 Unicode NFC，机械替换控制字符和跨平台禁用字符，防止路径穿越、保留名、
碰撞和超长名称；不得使用统一的 `elegantbook-project.zip`、样本 ID、material ID 或源文件名代替封面身份。

每卷每个正文叶分片 `.tex`、根 `main.tex` 和唯一 loader 必须严格小于 `900,000` 字节；等于上限即失败。
producer 必须在封装后现场枚举 ZIP 成员、拒绝重复路径/符号链接/不受控 Tex，并生成容量报告。单个正文单元超限只触发
该语义单元内部的受控 Tex 分片，不直接触发分卷；单个不可分割 render node／完整行达到上限时必须退回 04-D 或拥有
阶段，不得删内容。全部可编辑文本字节数继续记录但不再套用未经证明的 7 MB 社区版通用硬上限。

正式 ZIP 还必须严格小于 `50,000,000` 字节。大小以文件系统实测字节数为准，比较关系固定为 `<`；
`50,000,000` 字节本身不通过。producer 必须在编译前生成绑定 ZIP 路径、SHA-256、实测字节数、上限和比较关系的
`delivery_size_report.json`，超限时停止，不得进入正式编译或声明 `spec_status=passed`。

如需缩减交付体积，只允许对新构建的交付副本执行已批准、确定且可审计的传输编码优化；不得改写上游来源资产、
历史快照、媒体数量、语义锚点或可见内容。优化前后必须记录逐资产哈希、格式、像素尺寸和质量/视觉核验依据。
Spec 05 不得因此重新选择图片、PDF 裁片、公式图、表格或图表表示。安全优化仍不能满足上限时，必须退回
Spec 03/04 的媒体表示合同阶段重新裁决，禁止删图、占位、静默降质或样本特例修补。

正式 ZIP 的文件实体数必须严格小于 `2,000`。producer 必须生成 `delivery_asset_report.json`，逐项记录最终 ZIP
普通文件、引用状态、媒体类型、SHA-256、字节数和（适用时）像素尺寸，并证明不存在未引用的生成媒体。
允许多个 render node 复用同一份字节完全相同的栅格图片；这不改变逻辑媒体数量、来源绑定或可见内容。
每个栅格图片文件还必须严格小于 `1,000,000` 字节；等于上限即失败。producer 只核验冻结资产，不能在本阶段
临时压缩、降质、裁切、删图或换图；超限必须退回规范 03/04 形成带视觉质量证据的新媒体表示快照。

`source_asset_image` 与 `source_region_image` 必须保持为编译合同支持的栅格图片格式。禁止把图片封装为单页或多页
PDF、PDF sprite 或其他容器来绕过文件上限；Spec 05 不得改变媒体表示类型。清理未引用资产和完全相同图片复用后
仍不能低于 2,000 个文件时，必须失败并退回 Spec 02/03，对纯装饰、导航控件、页眉页脚噪声、OCR 碎片与真实
教学媒体作来源证据化裁决，再冻结新的账本与 render plan。

上述大小、实体、模板、媒体表示和编译门禁逐卷独立执行，数值与严格比较关系均不因拆卷改变。随后必须执行
`delivery_set` 聚合门禁：分卷数量与冻结计划一致；每个 render node 和 included source atom 恰好属于一卷；
卷序连接保持完整来源顺序；正文 payload 不跨卷重复；所有跨卷引用已消解或在合法边界处关闭。任何一卷失败，
整个 Spec 05 失败。不得以一卷通过代替整套通过，也不得把两个工程打进一个大 ZIP 冒充两卷交付。

### 6.3 干净构建

- 禁止依赖旧 `.aux`、`.toc`、缓存或历史 PDF；
- 使用模板合同规定的 XeLaTeX/latexmk 或其他目标引擎；
- 记录 TeX 版本、容器/主机、字体、依赖和命令；
- 连续编译到目录、页码和引用收敛，并设置最大轮数；
- 构建日志、输入哈希、ZIP 哈希和 PDF 哈希必须绑定同一 build ID。

### 6.4 全页渲染包

编译通过后启动一次绑定最终 PDF 哈希和固定渲染配置的全页 render job，并生成
`final_render_pack/manifest.json` 及全部页面 raster。manifest 至少包含：

- `schema_version`、`render_job_id`、`build_id`；
- 最终 PDF 路径、SHA-256 和页数；
- renderer 名称、版本、二进制哈希；
- DPI、色彩空间、页面格式、配置及配置哈希；
- 每页连续索引、页标签、尺寸、旋转、raster 相对路径和 SHA-256；
- 完成状态、生成时间和 manifest 自身哈希规则。

该不可变 render pack 由规范 06 直接消费；PDF 哈希和渲染配置未变时不得重建另一套互不一致的页面证据。

## 7. 必需产物

- `template_contract.json`；
- `spec05_presentation_config.json` 及其 schema、哈希和决定绑定；
- 规范 04 的原样 `template_capability_manifest.json` 及其原哈希；
- `template_capability_validation_report.json`；
- `template_integrity_report.json`；
- `template_integrity_report.md`；
- `rendered_body.tex`；
- `render_execution_report.json`；
- `build_environment.json`；
- `build_manifest.json`；
- `compile_report.json`；
- `compile_report.md`；
- `delivery_size_report.json`；
- `delivery_asset_report.json`；
- `compile_warnings.json`；
- 正式 ElegantBook ZIP；
- 最终编译 PDF；
- 编译日志；
- `final_render_pack/manifest.json` 及全部页面 raster；
- `compile_decisions.jsonl`；
- 本规范消费或新建的 `canonical_decision_index.json` 不可变快照及哈希。

除目标模板本来就要求的工程文件和已批准的正文资产外，上述合同、报告、账本、决定文件、render pack 和
`rendered_body.tex` 暂存件均为审计 sidecar，不得擅自塞入正式 ElegantBook ZIP。

## 8. 模板硬门禁

- `TP-H01 template_zip_hash_matches`：使用正确模板版本；
- `TP-H02 class_hash_unchanged`：class 和不可变文件哈希不变；
- `TP-H03 masked_scaffold_hash_matches`：除白名单 metadata 和正文区外，模板骨架不变；
- `TP-H04 custom_api_inventory_unchanged`：命令、环境和 style 库存及签名不变；
- `TP-H05 documentclass_unchanged`：类和选项不变；
- `TP-H06 package_inventory_unchanged`：包、顺序和选项不变；
- `TP-H07 metadata_changes_allowlisted`：所有 metadata 差异均在白名单；
- `TP-H08 body_only_in_insertion_region`：正文只写入允许区域；
- `TP-H09 no_behavioral_bypass`：不存在正文侧绕过冻结区的定义；
- `TP-H10 ancillary_files_allowlisted`：旁路依赖均为严格白名单内的静态文件，并有来源、用途和哈希；
- `TP-H11 capability_manifest_verified`：原样能力清单与当前模板实物、合同和哈希一致，且验证报告独立存在；
- `TP-H12 transitive_tex_api_unchanged`：递归加载图和展开后的包、命令、环境、颜色及 style 库存无漂移；
- `TP-H13 no_executable_ancillary`：不存在宏定义型、配置型、脚本型、可执行或符号链接旁路依赖。
- `TP-H14 no_template_local_custom_api_usage`：生成正文中没有模板局部自定义命令／环境的定义或调用；
  tcolorbox 仅通过标准环境引用能力清单批准的既有 style key。

模板冻结违规不能人工豁免。需要改变模板时，必须由用户批准新模板版本并重新冻结。

## 8.1 呈现配置硬门禁

- `PR-H01 explicit_cover_logo_contract`：cover 与 logo 均有显式模式和完整合同，不存在隐式推断；
- `PR-H02 presentation_decisions_closed`：来源身份、默认资源适用性和非默认资源使用决定均已关闭；
- `PR-H03 frozen_presentation_assets_preserved`：模板原有 cover/logo 与其他冻结文件字节不变，新增资产不覆盖原文件；
- `PR-H04 presentation_assets_hash_bound`：配置、模板合同、物化资产、最终 ZIP 和决定索引中的路径与哈希完全一致。

formal-native Spec 05 promotion 必须独立重算上述四项并以
`S5-PG-H13-presentation-contract` 绑定；任一项不成立时不得 promotion。

## 9. 编译硬门禁

- `CP-H01 final_zip_is_build_input`：编译输入就是最终 ZIP 的解包内容；
- `CP-H02 clean_build`：使用全新构建目录；
- `CP-H03 compiler_exit_zero`：正式编译进程退出码为 0；
- `CP-H04 no_tex_error`：无 LaTeX Error、Undefined control sequence、Emergency stop、runaway argument；
- `CP-H05 all_dependencies_resolve`：图片、字体和必要文件完整；
- `CP-H06 references_converged`：目录、交叉引用和引用已收敛；
- `CP-H07 no_missing_glyph`：无缺失字形或未经批准的字体替代；
- `CP-H08 pdf_valid`：PDF 可解析且页数大于 0；
- `CP-H09 build_artifacts_bound`：ZIP、日志、manifest、PDF 和 render job 绑定同一 build ID/hash；
- `CP-H10 no_fallback_or_demo`：正式构建未使用 fallback、demo 或占位资产；
- `CP-H11 no_blocking_warning`：不存在 C0/C1 warning；
- `CP-H12 no_open_compile_review`：所有 C2 warning 已关闭；
- `CP-H13 frozen_render_plan_executed`：实际 LaTeX 逐项来自冻结 render plan，无重新分类或布局选择；
- `CP-H14 rendered_body_bound_to_delivery`：暂存正文与最终 ZIP 中正文 payload 哈希一致且锚点完整；
- `CP-H15 final_render_manifest_complete`：全页 render manifest 绑定当前 build/PDF，页数、路径和哈希完整；
- `CP-H16 decision_index_consistent`：本阶段决定已登记；决定索引只引用其冻结时已存在的 build、ZIP、PDF、
  warning 和父 ledger 证据，不引用随后生成的 `render_coverage` 子 ledger；
- `CP-H17 semantic_stage_commit_verified`：输入 ledger、render plan、能力清单和决定索引哈希与
  `semantic_stage_manifest.json` 完全一致。
- `CP-H18 delivery_zip_under_50mb`：正式交付 ZIP 的实测大小严格小于 `50,000,000` 字节，
  `delivery_size_report.json` 与最终 ZIP 路径、SHA-256、大小和比较关系完全一致；producer 与独立 promotion evaluator
  均现场复算，任何超限或报告漂移都阻止通过。
- `CP-H19 delivery_file_entities_under_2000`：正式交付 ZIP 的普通文件实体数严格小于 `2,000`，
  producer 与独立 promotion evaluator 均现场复算，目录不计数，任何报告漂移或达到上限均阻止通过；
- `CP-H20 native_image_representation_preserved`：所有 `source_asset_image`/`source_region_image` 输出均为受支持的
  栅格图片，引用解析到 `delivery_asset_report.json` 中同一哈希的文件；不存在图片转 PDF、PDF pack/sprite、
  未引用生成媒体或未经上游合同冻结的媒体表示变化。
- `CP-H21 delivery_set_cardinality`：交付集合卷数严格为 1 或 2，且与冻结 `volume_partition_plan` 完全一致；
- `CP-H22 per_volume_independent_pass`：每卷分别通过 TP-H01–TP-H14、CP-H01–CP-H20，并有独立 ZIP/PDF/build/render pack；
- `CP-H23 cross_volume_exact_coverage`：跨卷 render node 与 included source atom 连续、互斥、完备，正文 payload 无跨卷重复；
- `CP-H24 no_spec05_repartition`：Spec 05 实际分卷边界、标签和节点归属逐项等于 04-D 冻结计划。
- `CP-H25 overleaf_root_main_and_body_binding`：每卷 ZIP 恰有一个根 `main.tex` 和一个
  `body/generated-body.tex`；根入口在正文区恰好调用一次批准的标准 `\input`；loader 仅直接加载冻结语义 unit 下的
  连续有序叶 part，part 不可嵌套或定义行为，按序重建结果与冻结 `rendered_body.tex` 字节一致；producer 与独立 evaluator 均重算；
- `CP-H26 delivery_name_matches_cover_identity`：每卷外层 ZIP/PDF 名称与冻结 `title` 及可选卷标的规范化结果完全一致，
  同卷 ZIP/PDF 共享 stem，不存在路径、不安全字符、碰撞或统一占位名；producer 与独立 evaluator 独立重算。
- `CP-H27 overleaf_body_shard_capacity`：每卷根 `main.tex`、唯一 loader 和每个正文叶 part 严格小于
  `900,000` 字节；正文语义 unit／part 加载图与冻结 `body_units` 及容量报告一致，producer 与独立 evaluator 分别现场重算。
- `CP-H28 delivery_raster_image_under_1mb`：每个正式 ZIP 内栅格图片严格小于 `1,000,000` 字节；
  producer 与独立 evaluator 逐文件重算并绑定资产报告，任何达到上限、报告漂移或 Spec 05 临时改图均阻止通过。

## 10. Warning 分级

- `C0 FATAL`：TeX 错误、非零退出、PDF 损坏或构建结果缺失；
- `C1 BLOCKING`：缺字、字体替代、缺图、缺文件、未解析引用、构建不收敛、确认的内容裁切或严重越界；
- `C2 REVIEW_REQUIRED`：可能影响布局但无法自动确认的浮动、盒子、overfull、类兼容或字体 warning；
- `C3 INFO`：经证据证明不影响内容和可读性的固定模板 warning 或轻微 underfull。

固定模板 warning 可以形成带消息指纹、位置、数量和模板哈希的 baseline。
新 warning、位置变化或数量变化不能自动继承旧决定。

## 11. 人工决策门禁

- `CP-R01 metadata_values`：确认白名单 metadata 值；
- `CP-R02 ancillary_dependency_provenance`：只确认严格静态白名单内、且已被冻结模板引用的依赖来源与身份；
- `CP-R03 warning_classification`：对 C2 warning 结合渲染证据作决定；
- `CP-R04 presentation_assets`：确认 cover/logo 模式、产品身份适用性，以及非默认资产的来源或批准证据；
- `CP-R05 template_revision`：现有模板确实无法满足产品时，决定是否建立新模板版本。

所有布局选择必须已在规范 04 的 render plan 冻结前关闭。
人工不能授权可执行旁路依赖，也不能豁免模板漂移、C0/C1、缺失字形、缺图、未解析引用或非目标编译结果。
本阶段每个决定事件都必须登记到 canonical decision index 的新不可变快照。

## 12. 失败码

- `TEMPLATE_ARCHIVE_HASH_MISMATCH`
- `TEMPLATE_CLASS_DRIFT`
- `TEMPLATE_SCAFFOLD_DRIFT`
- `TEMPLATE_CUSTOM_API_DRIFT`
- `TEMPLATE_METADATA_NOT_ALLOWLISTED`
- `TEMPLATE_BEHAVIORAL_BYPASS`
- `TEMPLATE_ANCILLARY_FILE_UNAPPROVED`
- `TEMPLATE_CAPABILITY_MANIFEST_MISMATCH`
- `TEMPLATE_TRANSITIVE_API_DRIFT`
- `TEMPLATE_EXECUTABLE_ANCILLARY`
- `PRESENTATION_CONFIG_MISSING`
- `PRESENTATION_CONFIG_UNRESOLVED`
- `PRESENTATION_ASSET_HASH_MISMATCH`
- `PRESENTATION_SOURCE_SCOPE_CONFLICT`
- `PRESENTATION_INFERENCE_FORBIDDEN`
- `RENDER_PLAN_EXECUTION_MISMATCH`
- `RENDERED_BODY_DELIVERY_MISMATCH`
- `COMPILE_FINAL_ZIP_MISMATCH`
- `COMPILE_NONZERO_EXIT`
- `COMPILE_TEX_ERROR`
- `COMPILE_DEPENDENCY_MISSING`
- `COMPILE_REFERENCE_NOT_CONVERGED`
- `COMPILE_GLYPH_OR_FONT_FAILURE`
- `COMPILE_BLOCKING_WARNING`
- `COMPILE_PDF_INVALID`
- `COMPILE_RENDER_MANIFEST_INVALID`
- `COMPILE_DECISION_INDEX_INVALID`
- `COMPILE_SEMANTIC_STAGE_COMMIT_INVALID`
- `COMPILE_DELIVERY_ZIP_SIZE_LIMIT_EXCEEDED`
- `COMPILE_DELIVERY_SIZE_REPORT_INVALID`
- `COMPILE_DELIVERY_FILE_ENTITY_LIMIT_EXCEEDED`
- `COMPILE_DELIVERY_ASSET_REPORT_INVALID`
- `COMPILE_IMAGE_REPRESENTATION_CHANGED`
- `COMPILE_OVERLEAF_ROOT_MAIN_INCOMPATIBLE`
- `COMPILE_OVERLEAF_TEXT_CAPACITY_OR_TRANSPORT_INVALID`
- `COMPILE_DELIVERY_NAME_MISMATCH`
- `COMPILE_REVIEW_OPEN`

## 13. 失效与重验

- 正文、图片、metadata 或 cover/logo 配置及资产改动：重新冻结模板合同、重新封装、完整编译和全页自动渲染；
- 模板、class、包、字体或环境改动：模板合同失效，规范 05 从头执行；
- 只更新人工 warning 决定且 ZIP/PDF/hash 未变：可以不重新编译，但决定必须绑定现有哈希；
- 任意 PDF 字节变化：旧 render pack 和规范 06 结论全部失效。

## 14. 验收与交接

本规范的 `spec_status` 使用 `blocked/failed/needs_review/passed`。

只有 `passed` 时，才能把最终 ZIP、PDF、模板完整性报告、render execution、编译报告、warning 决定、
canonical decision index 快照和 `final_render_pack/manifest.json` 交给规范 03 的 `render_coverage` 重验及规范 06。
本规范通过仅表示模板与编译合格，不等于产品已完成。
