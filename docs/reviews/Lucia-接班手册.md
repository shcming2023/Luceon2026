# Lucia 接班手册

> 本文为 Lucia 职责的强制交接入口。任何临时代班、并行协作、Lucia 额度恢复或 Lucia 重新接手前，必须先阅读本文。

最后更新：2026-04-24
适用范围：Luceon2026 仓库中由 Lucia 负责的 PRD、验收、部署、派单、收敛控制工作

## 1. 这份文档是做什么的

这是一份给 Lucia 或 Lucia 临时代班同事使用的交接文档。

它不是参考材料，而是默认执行入口。

目标只有三个：

1. 保证任何接手者先按同一套节奏工作，而不是各自发挥。
2. 保证派单、验收、复盘都以同一份基线为准。
3. 保证 Lucia 额度恢复后先读手册、再重新接管，不需要重新考古。

## 2. 当前总策略

当前策略已经明确：

- 暂停泛化开发。
- 不进入 Wave3。
- 不再以 Wave 名义发起中风险功能扩展。
- 只允许收敛型、小批量、低风险、可验收任务。

默认优先级顺序：

1. 基线复验
2. 回归修复
3. UAT 防线补强
4. 只读可观测性增强
5. 文档契约对齐

以下事项默认禁止直接推进，除非用户明确批准且 Lucia 单独立项：

- 审核流扩展
- 状态推进逻辑改写
- 下载、发布、删除、清理等新操作入口
- 大页面重构
- 任何新的 Wave 式泛化开发

## 3. 角色分工

### Lucia

负责：

- 维护和修订 PRD
- 拉取和部署代码
- 执行 smoke、UAT、专项验证
- 输出基线报告、验收报告、复盘文档
- 给 lucode 下达小批量任务书
- 判断某一批任务是否关闭

### lucode

负责：

- 按 Lucia 任务书改代码
- 提交并推送 GitHub
- 汇报修改文件、commit hash、测试结果

### 临时代班同事

必须先读完本文件，再开始接手。

代班期间默认继承 Lucia 当前策略，不得自行切换节奏。如果认为策略需要改变，必须先形成书面判断，再由用户决定是否接受。

未阅读本文，不应直接派单、验收、复盘或调整项目节奏。

Lucia 本人额度恢复、上下文压缩后恢复工作，或间隔较久重新接管时，也必须先阅读本文，再继续履行职责。

## 4. 接手时必须先读的材料

按这个顺序读：

1. [Luceon2026-PRD-v0.4.md](../prd/Luceon2026-PRD-v0.4.md)
2. [说明文档.md](/Users/concm/prod_workspace/Luceon2026/说明文档.md)
3. 本文：[Lucia-接班手册.md](/Users/concm/prod_workspace/Luceon2026/docs/reviews/Lucia-接班手册.md)
4. [UAT 测试指南](../../uat/README.md)
5. [一致性清理操作说明.md](/Users/concm/prod_workspace/Luceon2026/docs/reviews/一致性清理操作说明.md)
6. [任务状态诊断说明.md](/Users/concm/prod_workspace/Luceon2026/docs/reviews/任务状态诊断说明.md)

## 5. 当前已确认的稳定基线

当前最新仓库提交（Lucia 交班时本地已拉取）：

- Git commit：`b1c1ab5`
- 提交说明：`fix(P0): Patch 1 - finalize AssetDetailPage stability and optional chaining`

最近一次 Lucia 确认的文档契约提交：

- Git commit：`a1f50bb`
- 提交说明：`docs: add PRD v0.4 state tracking revision proposal`
- 文件：`docs/reviews/PRD-v0.4-状态与追踪模型收口修订建议.md`

最后一个已通过 Lucia 验收的功能/验收防线基线提交：

- Git commit：`3c9f793`
- 提交说明：`test(uat): add real asset detail page smoke test`

该基线的结论：

- `docker compose up -d --build` 通过
- `uat/smoke-test.sh` 通过
- `uat/tests/pages-smoke.spec.ts` 连续 2 次通过
- `uat/tests/pipeline-consistency.spec.ts` 通过
- `/__proxy/upload/audit/consistency` 返回 `ok: true`
- 不进入 Wave3

## 6. 最近一次稳定基线数字

以 2026-04-23 最新一次 Lucia 接班后复验为准：

- `materials`: 39
- `tasks`: 44
- `aiJobs`: 41
- `findings`: 46

Finding 分布：

- `orphan-object`: 37
- `orphan-task`: 4
- `parsed-file-missing`: 3
- `bad-parsed-prefix`: 2

说明：

- `materials/tasks/aiJobs` 会随着 UAT 新增测试数据增长，这不是阻塞问题。
- `findings = 46` 是当前稳定历史脏数据基线。
- 这 46 项当前不构成主链路阻塞，但也不应被忽略。

## 6.1 最新复验结论

截至 `b1c1ab5` 的 Lucia 交班复核，当前结论为：

- `npx tsc --noEmit` 通过
- `npm run build` 通过
- `BASE_URL=http://127.0.0.1:8081 bash uat/smoke-test.sh` 12/12 通过
- `pages-smoke.spec.ts` 当前部署上 7/8 通过，真实资产详情断言失败
- `cross-page-consistency.spec.ts` 未通过，测试数据创建拿到 `Material undefined, Task undefined`
- `docker compose up -d --build` 未完成：本机缺少 `nginx:1.27-alpine`，拉取镜像元数据阶段长时间无输出后中止
- `/__proxy/upload/audit/consistency` 返回 `ok: true`
- P0 Patch 1 不能完全关闭：代码静态可构建，但 UAT 防线尚未闭环
- P0 Patch 2 已下达给 lucode，下一位 Lucia 的第一任务是评审 lucode 的 P0 Patch 2 执行结果
- 继续暂停泛化开发
- 不进入 Wave3

P0 Patch 1 已确认完成的部分：

- 前端直接 `POST /__proxy/db/tasks` 创建占位任务的绕路逻辑已移除
- `AssetDetailPage` 已从 `if (!detail)` 改为 `if (!material && !detail)`
- `cross-page-consistency.spec.ts` 页面路径已改为 `/cms/...`

P0 Patch 1 未关闭的阻塞点：

- `cross-page-consistency.spec.ts` 仍使用错误测试数据创建方式，未通过当前主链路契约
- `pages-smoke.spec.ts` 真实资产详情断言仍会被历史 `Material / assetDetails` 标题不一致影响
- 最新前端代码尚未完成 Docker 重建部署复验

## 7. 当前页面级 UAT 覆盖

`uat/tests/pages-smoke.spec.ts` 当前至少覆盖：

- `/cms/tasks`
- `/cms/audit`
- `/cms/ops/health`
- `/cms/workspace`
- `/cms/library`
- `/cms/settings`
- `/cms/tasks/non-existent-id`
- `/cms/tasks/{真实任务ID}`
- `/cms/asset/999999999`
- `/cms/asset/{真实可用 materialId}`

当前要求：

- 不只看 HTTP 200。
- 必须防止出现“HTTP 200 但 React 崩溃”。
- 必须捕获 `ReferenceError`、`is not defined`、`ErrorBoundary`。
- 涉及详情页的页面测试，必须尽量使用真实数据而不是只测空态。

## 8. 派单红线

给 lucode 派单时，默认遵守下面的边界：

- 单批任务最多 1 到 3 个小目标。
- 每个目标都必须可独立验收。
- 默认不改上传、解析、AI 主链路。
- 默认不新增写操作入口。
- 默认不做自动清理。
- 默认不改 MinIO、MinerU、Ollama 配置。
- 如果任务会改变状态流转、审核逻辑、下载逻辑、元数据写回，必须先得到用户明确批准。

任何任务书都应明确写出：

- 任务目标
- 修改范围
- 禁止事项
- 验收要求
- 回报格式

## 9. 验收硬门槛

只要涉及前端页面改动，默认至少跑：

```bash
docker compose up -d --build
node server/tests/worker-smoke.mjs
BASE_URL=http://127.0.0.1:8081 bash uat/smoke-test.sh
cd uat && BASE_URL=http://127.0.0.1:8081 npx playwright test tests/pages-smoke.spec.ts
cd uat && BASE_URL=http://127.0.0.1:8081 npx playwright test tests/pipeline-consistency.spec.ts
curl -sS http://127.0.0.1:8081/__proxy/upload/audit/consistency
```

验收口径：

- `pages-smoke.spec.ts` 建议连续跑 2 次
- `pipeline-consistency.spec.ts` 不允许 flaky/retry 后才算过
- `/audit/consistency` 只做 dry-run，不做 apply
- 若改动详情页或真实数据页面，必须做浏览器级人工抽查

## 10. 接手后的标准动作

临时代班、Lucia 额度恢复或 Lucia 重新接手时，默认按这个顺序：

1. 先阅读本文
2. `git pull origin main`
3. `docker compose up -d --build`
4. 跑一轮 smoke
5. 跑 `pages-smoke`
6. 跑 `pipeline-consistency`
7. 跑 `/audit/consistency` dry-run
8. 输出《当前部署基线报告》或《复验报告》
9. 根据复验结果决定是否需要继续下达收敛补丁任务

## 10.1 本次交班后的第一动作

当前不是自由派单阶段。下一位 Lucia 接手后，第一件事必须是评审 lucode 对《P0 Patch 2：修复状态收口 UAT 防线，完成可验收闭环》的执行结果。

代班 Lucia 可以完整接替 Lucia 的职责，包括继续给 lucode 下达必要任务；但任务必须沿着当前 P0/P1 收敛计划延续，不能绕过当前阻塞项另起 Wave、泛化开发或中风险产品扩展。

执行边界：

- 若 P0 Patch 2 未达到关闭标准，代班 Lucia 可以继续下达必要的 P0/P1 收敛补丁任务。
- 若 P0 Patch 2 达到关闭标准，代班 Lucia 应输出复验结论，并围绕生产交付目标处理下一项生产准入阻塞项。
- 任何新任务仍必须小批量、低风险、可验收，并要求 lucode 完成后主动同步 GitHub。
- 禁止跳过 P0 Patch 2 复核直接进入 Wave3 或泛化开发。

P0 Patch 2 验收重点：

- 确认 lucode 已 `git pull --rebase origin main`、提交、推送，并证明本地 `HEAD` 与 `origin/main` 一致
- `uat/tests/cross-page-consistency.spec.ts` 必须直接通过 `POST /__proxy/upload/tasks` 上传真实小 PDF 建任务，并断言 `taskId/materialId/objectName` 非空
- 所有 UAT API 请求失败时必须输出响应 body，禁止拿 `undefined` 继续做页面断言
- 所有页面访问路径必须是 `/cms/workspace`、`/cms/asset/:id`、`/cms/tasks`
- `uat/tests/pages-smoke.spec.ts` 的真实资产详情断言必须适配当前收口目标，不能因历史 `assetDetails` 标题不一致误判页面不可用
- 禁止改业务主链路、Worker、MinerU、AI 状态推进逻辑，禁止为了通过测试而无条件 `skip`

P0 Patch 2 的关闭标准：

- `npx tsc --noEmit` 通过
- `npm run build` 通过
- `BASE_URL=http://127.0.0.1:8081 bash uat/smoke-test.sh` 通过
- `cd uat && BASE_URL=http://127.0.0.1:8081 npx playwright test tests/pages-smoke.spec.ts` 通过
- `cd uat && BASE_URL=http://127.0.0.1:8081 npx playwright test tests/cross-page-consistency.spec.ts` 通过
- GitHub `main` 同步一致

## 11. 当前已知非阻塞事实

这些事实要记住，但不要误判成主链路阻塞：

- 历史 findings 当前稳定为 46
- `orphan-object` 涉及 MinIO 物理删除，默认不能全量 apply
- `parsed-file-missing` 和 `bad-parsed-prefix` 当前属于审计项，不代表页面或主链路一定不可用
- 多轮 UAT 会继续产生测试素材和任务，这本身不是问题

## 12. 当前工作区注意事项

当前仓库里有一些并非 Lucia 当前任务产生的本地变化。接手者默认不要碰：

- `.agents/workflows/luceon2026rules.md`
- `.codebuddy/`
- `.lucia-e2e/`

原则：

- 不清理、不回滚、不顺手整理
- 只要与当前任务无关，就视为外部痕迹

## 13. 什么时候要更新这份文档

出现以下任一情况，就应同步更新本文：

- Lucia 明确切换总策略
- 最新稳定基线 commit 改变
- 验收必跑命令改变
- 页面级 UAT 覆盖范围改变
- dry-run findings 基线被确认发生结构性变化
- 对 lucode 的派单边界发生变化

## 14. 当前代班期间唯一允许推进的下一步

当前用户的目标已经明确为“生产交付”，但 Lucia 现阶段判断仍是：

- 当前基线稳定
- 仍不建议直接宣布正式生产交付完成
- 下一步不应继续开发，而应先做生产准入判断

因此，代班期间默认唯一允许推进的下一步是：

**输出《生产交付前准入评估报告》**

该报告至少应回答：

1. 当前是否可正式生产交付
2. 哪些项已满足
3. 哪些项仍是上线阻塞项
4. 哪些项可带风险上线
5. 是否只允许小范围试运行/UAT 观察

除非用户再次明确授权，否则代班期间不要直接给 lucode 下达新的功能开发任务。

## 15. 当前一句话口径

如果代班同事只记一句话，就记这个：

**现在的 Luceon2026 不缺新功能，缺的是按 Lucia 节奏做收敛、复验、补防线。**
