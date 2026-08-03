# LuceonWeb2026 五 PDF 健壮性修订与回归 UAT

> 状态：最终封版。当前开发 Mac mini 的代码修复、镜像部署、五本最小阶段恢复、数据库/MinIO/交付 ZIP 审计、公网页面复核和独立 XeLaTeX 复编均已通过。
>
> 范围：当前开发 Mac mini，不涉及另一台正式生产 Mac mini；本轮不启动 GPU，不重跑已冻结 MinerU/Popo。

## 1. 当前结论

- 解析链路：5/5 成功，run #99 为 `succeeded`，五个子项均停在 `popo_frozen`，MinIO 无活动标记。
- Worker V2.3：成功 5/5，阻断 0/5，运行 0/5；五个当前输出均 `promoted / passed / is_current`。
- 数字资产：五本 MinerU、Popo、Worker manifest 均可实际 `stat`；不是仅有数据库状态。
- 交付 ZIP：5/5 已下载并逐包审计；目录、体积、图片大小、图片文件名守恒和锁定模板哈希均通过。
- 公网页面：5/5 的资产身份、解析冻结语义、当前 Output、原 PDF 与编译 PDF 实际渲染均已由浏览器逐本验证并截图。
- 页面下载：5/5 的“下载 LaTeX ZIP”和“下载 PDF”均从页面真实触发，asset_id 2429–2433 与 Output 563–567 一一对应。
- 独立复编：5/5 均在目标 `sharelatex` / TeX Live 2025 环境完成两遍 XeLaTeX；缺字、未定义命令和大于 10pt 的 overfull 均为 0。
- 运行环境：backend、workflow-v2-worker、material-task-worker、backup-task-worker 使用同一 backend 镜像；frontend 使用本轮新镜像。容器无 OOM、无重启。

结论：**当前开发环境 UAT 通过**。本结论不等同于另一台正式生产 Mac mini 的安装验收，也不包含新的 GPU 压力测试；它证明本轮五本在当前封版镜像上的既定链路与交付门禁已经闭环。

## 2. 五个源文件与身份

| material PK | 文件 | material ID | 大小（bytes） | 页数 | SHA-256 |
|---:|---|---|---:|---:|---|
| 1339 | Cambridge Lower Secondary Science Learners Book 7 (Cambridge University Press)(Second Edition).pdf | `pdf-de6b27db13aeb80d` | 55,442,474 | 344 | `de6b27db13aeb80dd7a2dfe8da86d52a72b0a52f63281e3bb7ef216f818d5ab2` |
| 1340 | Cambridge Lower Secondary Science Learners Book 8 (Cambridge University Press)(Second Edition).pdf | `pdf-2e57426befa155be` | 160,629,790 | 127 | `2e57426befa155be22f8015d0c056ad0fad241292b205a3a0f74e263ebb9a0d4` |
| 1341 | Cambridge Lower Secondary Science Learners Book 9 (Cambridge University Press)(Second Edition).pdf | `pdf-ac357479938bb9b5` | 274,696,925 | 351 | `ac357479938bb9b5851dbc57036ff9631491ca44c0edcb09c74ee11617f29e3a` |
| 1342 | Cambridge Lower Secondary Science Workbook 7 (Cambridge University Press)(Second Edition).pdf | `pdf-ffaf7e6e6dd3b32d` | 50,008,662 | 182 | `ffaf7e6e6dd3b32d4021282c9623a5c02fd68d73798f0cd2d48b093fae889c78` |
| 1343 | Cambridge Lower Secondary Science Workbook 9 (Cambridge University Press)(Second Edition).pdf | `pdf-8e5464971a3e1d6b` | 29,110,746 | 200 | `8e5464971a3e1d6b40f5afc996dba67efc9af60e29d73ce10730e91c4fa12fad` |

## 3. 解析批次与冻结

run #99：

- 开始：2026-07-24 02:40:45
- 完成：2026-07-24 05:04:02
- `total / processed / success / failed = 5 / 5 / 5 / 0`
- 尝试：1
- 最终阶段：`finished`

| material PK | MinerU run ID | Popo run ID | MinIO manifest 大小 |
|---:|---|---|---|
| 1339 | `mineru-20260724024051-staged_mineru_20260724024050-21599e1d--003-staged_de6b27db13aeb80d_003` | `popo-20260724032035-staged_popo_20260724031739-93376d38--003-popo_de6b27db13aeb80d_003` | MinerU 686,950；Popo 429,594 |
| 1340 | `mineru-20260724024051-staged_mineru_20260724024050-21599e1d--004-staged_2e57426befa155be_004` | `popo-20260724032035-staged_popo_20260724031739-93376d38--004-popo_2e57426befa155be_004` | MinerU 237,028；Popo 167,944 |
| 1341 | `mineru-20260724024051-staged_mineru_20260724024050-21599e1d--005-staged_ac357479938bb9b5_005` | `popo-20260724032035-staged_popo_20260724031739-93376d38--005-popo_ac357479938bb9b5_005` | MinerU 659,575；Popo 413,600 |
| 1342 | `mineru-20260724024051-staged_mineru_20260724024050-21599e1d--002-staged_ffaf7e6e6dd3b32d_002` | `popo-20260724032035-staged_popo_20260724031739-93376d38--002-popo_ffaf7e6e6dd3b32d_002` | MinerU 289,809；Popo 198,694 |
| 1343 | `mineru-20260724024051-staged_mineru_20260724024050-21599e1d--001-staged_8e5464971a3e1d6b_001` | `popo-20260724032035-staged_popo_20260724031739-93376d38--001-popo_8e5464971a3e1d6b_001` | MinerU 309,170；Popo 209,883 |

逐本均满足：

- SQLite 子项 `succeeded / popo_frozen`；
- `mineru_done_frozen`、`popo_done_frozen`、`done` 同时存在于 source/material 双路径；
- `active_error=false`，原始 error marker 为 0；
- 全局活动 marker 为 0；
- MinerU/Popo manifest 实际存在且大小非零。

## 4. Worker V2.3 恢复结果

| material PK | Worker public ID | 原始最小恢复阶段 | 当前状态 | Output ID | Review asset |
|---:|---|---|---|---:|---:|
| 1339 | `3464d644-f5ca-43cb-a4a0-b50411ba2f86` | canonical clean | `succeeded` | 566 | 2433 |
| 1340 | `6d40aa7b-e61d-49b4-9479-4ada8377fbf1` | semantic annotation；目录错误时退回 outline | `succeeded` | 565 | 2432 |
| 1341 | `c75e6a5f-712e-4833-9420-a16f126ae2af` | deterministic ElegantBook | `succeeded` | 564 | 2431 |
| 1342 | `3df58f4c-841e-4b6d-b217-58556c5a56b8` | outline reconstruction | `succeeded` | 567 | 2430 |
| 1343 | `1e257693-6539-4d99-bb09-b004bef12218` | outline reconstruction | `succeeded` | 563 | 2429 |

恢复遵循“最小失败阶段”原则；本轮没有重跑 MinerU/Popo。修订过程中后续门禁暴露的新通用缺陷，仅重试对应 Worker 阶段。五个作业当前活动 stage 数为 0。

## 5. P0 修复与回归覆盖

### 5.1 源码块守恒映射

- canonical clean 生成时直接输出稳定 source→clean 引用；
- 以 `page_idx + block_id + source_order + bbox` 区分重复短文本；
- 页码、重复页眉等仅在有结构证据时归为噪声移除；
- preserve 必须有确定 output reference，不能事后只靠标题/文本猜测；
- 回归覆盖重复短文本、重复列表、页码、章节页眉、同文本不同 bbox、拆分/合并。

代表性测试：

- `test_preserved_duplicate_short_text_has_stable_one_to_one_lineage`
- `test_structural_page_noise_is_removed_before_exact_clean_matching`
- `test_repeated_page_edge_text_is_allowlisted_noise`
- `test_generation_time_lineage_binds_duplicate_short_text_to_exact_clean_lines`
- `test_page_number_is_noise_only_at_a_real_page_edge`

### 5.2 目录重建

- `Default Title` 和单层目录不能成为可接受终态；
- 大候选池不能只选一个节点即结束；
- 父节点页码晚于子节点时阻断该关系；
- 重复局部标题按稳定源位置挂到最近有效章节；
- 确定性证据不足时只提交歧义候选给有边界裁决，不重跑全流程；
- 保留原 `outline_depth` 硬门禁，没有降级标准。

代表性测试：

- `test_placeholder_title_and_child_before_parent_are_hard_blockers`
- `test_large_candidate_pool_cannot_finish_with_single_selected_node`
- `test_repeated_local_exercise_labels_nest_below_source_chapters`
- `test_parent_is_repaired_to_nearest_preceding_level`
- `test_missing_parent_is_not_invented_without_preceding_parent`

### 5.3 语义章节绑定

- semantic section 直接携带 `outline_node_id` 和 `parent_node_id`；
- 身份与父子关系以稳定 ID、源区间和顺序为准；
- 重复 `Questions`、`Method` 不再以规范化标题充当身份；
- 标题签名只保留为诊断信息。

代表性测试：

- `test_repeated_titles_are_bound_by_stable_outline_ids`
- `test_title_match_cannot_hide_wrong_parent_id`
- `test_semantic_artifact_must_match_outline_and_cover_clean_once`

### 5.4 LaTeX Unicode 规范化

- 转换器通用处理 Unicode 上标/下标数字；
- `H₂O`、`CO₃` 等转换为模板原生 LaTeX；
- 本轮进一步修复通用角度符号 `°`，不修改锁定模板、不新增自定义命令；
- 缺字门禁仍生效，并对多遍编译同一缺字去重计数。

代表性测试：

- `test_semantic_converter_normalizes_unicode_subscript_and_superscript_digits`
- `test_parse_latex_diagnostics_accepts_xetex_quoted_hex_codepoints`
- `test_parse_latex_diagnostics_deduplicates_same_glyph_across_compile_passes`
- 锁定模板/无新增定义测试继续通过。

## 6. 页面语义、进度、资源与会话

已实现并经前端构建/契约测试覆盖：

- 精修列表固定显示“成功/阻断/运行”；
- `needs_review` 显示为“质量门禁阻断”，`handoff_ready` 才显示“可人工接手”；
- 错误码有中文解释，长编译日志放入详情；
- 显示最后成功阶段、最小恢复阶段、交接产物可用性；
- PDF 资产页使用“解析已完成（已冻结）”，不暗示 Worker 也完成；
- 父任务进度由逐本持久阶段聚合，区分远端完成、本地回拉与冻结、模型切换；
- 大 PDF 资源门禁同时使用总大小、页数、历史放大系数、真实磁盘和 Wrapper quota；
- 上传前检查登录；上传中断后保留文件名、最后浏览器进度和恢复说明，要求用户重新选择文件并由 SHA-256 去重复用已入库部分；
- 登录失效提示去重；
- SSH 转发恢复状态进入运行设置；
- 监控策略改为事故状态：首次阻断立即通知，未解除时每 15 分钟重报，UI 不可读时明确写“UI 层未验证”。

新增前端断点契约 `test:upload-resume` 已通过；`vue-tsc -b && vite build` 已通过。

## 7. ZIP 与图片守恒

所有 ZIP 顶层严格为：

- `images/`
- `figure/`
- `main.tex`
- `elegantbook.cls`

| material PK | Output | ZIP bytes | ZIP SHA-256 | 图片数 | 最大图片 bytes | main.tex SHA-256 |
|---:|---:|---:|---|---:|---:|---|
| 1339 | 566 | 24,923,045 | `70f96e163e16171cdf2729c293a32ca85cfad43da48febe6f452b3deaa626709` | 653 | 351,074 | `905246d8747cddde5d8a03b938e72734a3f85ed75d163358b6cc8ff25e52fb8e` |
| 1340 | 565 | 6,415,847 | `f0ea36f7999aa83f1c393d0d0e6ce28be3f232addea7981477e6aafa44ef3afa` | 211 | 290,969 | `9f196abeabc9d0746ef77da138fb21bce73da8d7b1294ca985c22d0ee9a02115` |
| 1341 | 564 | 36,866,987 | `03e9a013e0c926ccc8d1ae0fa4f683df16cc3ddc99f4decc80010ee176478bf3` | 626 | 646,213 | `cbe5766b29860c960b6986ac8be0ca8e5e7713fa5a906b6e2943ad6b10406aa9` |
| 1342 | 567 | 5,748,498 | `1b450a20fe5134612c07917122dcd3e8f3a40ba915d7239a156f7df4b86e49c6` | 263 | 160,111 | `cf75a5a5fd4eeb30b8345b73d54f14d87444585dbc56e72cdfc9b73b98386b9d` |
| 1343 | 563 | 7,723,460 | `f77c670f40277b802ea9905bc63d00f06a78c3829aa05234e33ee57c316b8bb4` | 282 | 302,281 | `5e0b6b39a6b3ed4f8002f73bb7bc7b45de169f48928c2604c7b7043d3d9c1739` |

共同结果：

- ZIP 均小于 50 MB；
- 单图均小于 1 MiB；
- 源 Popo 图片与 Worker 交付图片逐本文件名集合完全相等：
  - 1339：653/653，missing 0，added 0；
  - 1340：211/211，missing 0，added 0；
  - 1341：626/626，missing 0，added 0；
  - 1342：263/263，missing 0，added 0；
  - 1343：282/282，missing 0，added 0；
- `elegantbook.cls` 五本一致，SHA-256 均为 `048c3b90da41be64f4744da3bff6ae8c5ea7abd30a5f3e2a6ad1a98f3b0d71fe`。

## 8. 独立 XeLaTeX

| material PK | 结果 | 编译页数 | 缺字 | 未定义命令 | >10pt overfull |
|---:|---|---:|---:|---:|---:|
| 1339 | 两遍成功 | 311 | 0 | 0 | 0 |
| 1340 | 两遍成功 | 49 | 0 | 0 | 0 |
| 1341 | 两遍成功 | 311 | 0 | 0 | 0 |
| 1342 | 两遍成功 | 109 | 0 | 0 | 0 |
| 1343 | 两遍成功 | 111 | 0 | 0 | 0 |

#1342 在本机 Docker `sharelatex` 容器（TeX Live 2025）中重新建立第一遍辅助文件后执行真正第二遍；日志只出现允许的 underfull/font fallback 提示，没有 `Missing character`、`Undefined control sequence` 或 overfull。

## 9. 测试与镜像

- backend 正确挂载完整测试范围：673/673 通过；
- frontend TypeScript/Vite build 通过；
- frontend 主题、认证、nginx DNS/资产路由、Workflow V2 恢复、管理员 Popo 恢复、资源门禁、源追溯、上传恢复契约通过；
- `git diff --check` 通过；
- graphify 已更新到 4,805 nodes / 4,949 edges。

当前镜像：

- backend 及全部 backend worker：`sha256:f809d5779924e872ac5663a79224f7e86d28a737d04a080e1a4e5f5062aa25ca`
- frontend：`sha256:ea8be9fa2c9e370b31aae80ba429d5ddb85851c01cef330ce82b7bf2604ace0c`

当前容器：

- backend、frontend：healthy；
- workflow-v2-worker、material-task-worker、backup-task-worker、Redis：running；
- 全部 restart 0、OOMKilled=false；
- 本轮没有 Git commit 或 push。

## 10. 最终页面与复编证据

2026-07-25 使用已登录公网页面完成逐本复核：

| material PK | review asset | Output | 原 PDF 页数 | 编译 PDF 页数 | 截图 SHA-256 |
|---:|---:|---:|---:|---:|---|
| 1339 | 2433 | 566 | 344 | 311 | `97954182a6d1b88dd6acd5d7861a23f5383db011b6b983e7dfcd0e48755ec031` |
| 1340 | 2432 | 565 | 127 | 49 | `eac22a02605c673e21647900958db04cb2d5dec8dd410f814f9c5d5f03ad92e7` |
| 1341 | 2431 | 564 | 351 | 311 | `95d7d12bb23e9cdfc4984e6be3b61fe1265eab93c74bf3f4ad5efffc661d5b44` |
| 1342 | 2430 | 567 | 182 | 109 | `efa6a5e51f2bfedb46ea65da74da31cc639eed9f2c7001991753ce1ad9d12439` |
| 1343 | 2429 | 563 | 200 | 111 | `493d8bd049cfb02c5961b78ea0512fc2ed6f102fb542339e88de0b936c66618b` |

五页均显示正确文件名与 material_id、`Worker V2.3 核心产物`、ZIP/PDF 下载按钮和两侧实际首屏内容；浏览器控制台 error 为空。资产页同时显示五本“解析已完成（已冻结）”、AI 已编目、Output 563–567 可用和最新精修已完成。

精修任务页最终截图 `workflow-jobs-final.png` SHA-256：`2c79d57e312fdb869ac5e98110aca2d646f81472e0c368ea4cc10b130ed16cdb`。该页五本目标对象均显示“已完成”，当前阶段为 `bounded_deepseek_polish_qa / succeeded`，并分别显示正确的 Worker job、Popo 输入和 Output；页面同时保留历史阻断对象的“质量门禁阻断”语义，没有把失败或 `needs_review` 伪装成完成。

页面实际触发的下载目标逐本为：

- 1339：asset 2433 / Output 566；
- 1340：asset 2432 / Output 565；
- 1341：asset 2431 / Output 564；
- 1342：asset 2430 / Output 567；
- 1343：asset 2429 / Output 563。

每本均分别打开 `files/latex-project.zip` 与 `files/main.pdf`，没有对象串书。下载标签页核验完成后，清理旧的 Chromium 超时错误页时受浏览器 URL 安全策略阻止；这不影响上述页面渲染和下载结论。

#1342 独立复编证据：

- `1342-independent-main.log` SHA-256：`fdd19853744f5371849f16ffa710dd5b5f81594f01f8a091adfc4ce278d9f44d`；
- `1342-independent-main.pdf` SHA-256：`d504165517e5a236efbdcded266bf549075044fb239bb33b9357b8eb62e36a03`；
- 第二遍输出 109 页，缺字 0、未定义命令 0、overfull 0。

最终判定：**当前开发环境 UAT 通过**。
