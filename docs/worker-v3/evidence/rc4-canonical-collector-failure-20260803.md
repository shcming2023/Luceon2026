# Worker V3 UAT 证据报告

- 生成时间：`2026-08-03T08:43:11.134897Z`
- 总体结论：**failed**
- 任务数：5
- 缺陷阻断：5
- 证据缺口：0
- 警告：0

## 逐本状态

| Job | material_id | Machine | Spec | Readiness | Human | UAT |
|---|---|---|---|---|---|---|
| a96f48de-da64-4e7a-af42-e89ec47026ab | pdf-de6b27db13aeb80d | needs_review | needs_review | not_ready | pending | failed |
| a0e903d0-260e-4ca8-8a90-5df4517a05de | pdf-2e57426befa155be | needs_review | needs_review | not_ready | pending | failed |
| 524731fd-0791-4edb-bb24-b8c96856fc24 | pdf-ac357479938bb9b5 | needs_review | needs_review | not_ready | pending | failed |
| 2de1aa8c-60e5-4950-93d7-94b18b6bf0e4 | pdf-ffaf7e6e6dd3b32d | needs_review | needs_review | not_ready | pending | failed |
| 2c442f63-18b3-410e-b6dc-85272164c254 | pdf-8e5464971a3e1d6b | needs_review | needs_review | not_ready | pending | failed |

## 发现

- **blocker** `job_terminal_not_ready` (db; a96f48de-da64-4e7a-af42-e89ec47026ab)：Worker V3 ended without a technically ready result
- **blocker** `job_terminal_not_ready` (db; a0e903d0-260e-4ca8-8a90-5df4517a05de)：Worker V3 ended without a technically ready result
- **blocker** `job_terminal_not_ready` (db; 524731fd-0791-4edb-bb24-b8c96856fc24)：Worker V3 ended without a technically ready result
- **blocker** `job_terminal_not_ready` (db; 2de1aa8c-60e5-4950-93d7-94b18b6bf0e4)：Worker V3 ended without a technically ready result
- **blocker** `job_terminal_not_ready` (db; 2c442f63-18b3-410e-b6dc-85272164c254)：Worker V3 ended without a technically ready result

## 判定边界

- `machine/spec/readiness/human acceptance` 独立呈现，不互相替代。
- 本报告只读；未调用状态变更 API，也未写入 DB/MinIO。
- MinIO 对象以不可变 SHA 元数据+大小或流式 SHA-256 核验。
- UI 或容器证据缺失时结论为 `incomplete`，不会制造通过。
