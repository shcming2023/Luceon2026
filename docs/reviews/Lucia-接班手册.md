# Lucia 接班手册

> 本文为 Lucia 职责的强制交接入口。任何临时代班、并行协作、Lucia 额度恢复或 Lucia 重新接手前，必须先阅读本文。

最后更新：2026-04-23  
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

最后一个已通过 Lucia 验收的收敛任务提交：

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

以 2026-04-23 最后一次 Lucia 复验为准：

- `materials`: 37
- `tasks`: 42
- `aiJobs`: 39
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
9. 只有基线过关后，才允许给 lucode 派下一单

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

## 14. 当前一句话口径

如果代班同事只记一句话，就记这个：

**现在的 Luceon2026 不缺新功能，缺的是按 Lucia 节奏做收敛、复验、补防线。**
