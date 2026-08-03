# Worker V3 UAT 证据报告

- 生成时间：`2026-08-03T10:38:53.261422Z`
- 总体结论：**passed**
- 任务数：5
- 缺陷阻断：0
- 证据缺口：0
- 警告：0

## 逐本状态

| Job | material_id | Machine | Spec | Readiness | Human | UAT |
|---|---|---|---|---|---|---|
| c6f01dd4-63de-4dae-acae-a6f7aa42bcb5 | pdf-de6b27db13aeb80d | needs_review | needs_review | not_ready | pending | passed |
| 4016c3b9-b18f-4cc7-9bad-fac7e788bc08 | pdf-2e57426befa155be | needs_review | needs_review | not_ready | pending | passed |
| 33c99617-5a86-4ad4-92ad-4c09612d3c67 | pdf-ac357479938bb9b5 | needs_review | needs_review | not_ready | pending | passed |
| fc306a4f-dcd9-494d-a386-58106fcfbdad | pdf-ffaf7e6e6dd3b32d | needs_review | needs_review | not_ready | pending | passed |
| 78d680b3-1bbb-4a1d-8555-74982f6043c5 | pdf-8e5464971a3e1d6b | needs_review | needs_review | not_ready | pending | passed |

## 发现

- 无。

## 判定边界

- `machine/spec/readiness/human acceptance` 独立呈现，不互相替代。
- 本报告只读；未调用状态变更 API，也未写入 DB/MinIO。
- MinIO 对象以不可变 SHA 元数据+大小或流式 SHA-256 核验。
- UI 或容器证据缺失时结论为 `incomplete`，不会制造通过。
