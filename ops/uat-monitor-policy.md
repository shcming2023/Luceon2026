# Luceon UAT 事故状态监控策略

- 每次报告标题固定为：`成功 x/n / 阻断 y/n / 运行 z/n`。
- 任一本首次进入 `failed`、`needs_review` 或 `blocked` 时立即通知。
- 阻断未解除时切换为事故状态，每 15 分钟重复一次简报；不得因“无增量变化”静默。
- `DONT_NOTIFY` 只用于没有阻断、页面层可读且不需要用户操作的健康推进状态。
- 页面会话不可读时标题必须带 `UI 层未验证`，并明确数据库、MinIO、API 和运行时证据不能替代页面 UAT。
- `succeeded` 和具备完整可下载、可编译交接件的 `handoff_ready` 才计入成功；`needs_review` 不计成功。
- 自动化应采用 `decide_uat_notification` 返回的 `next_interval_seconds` 动态调整检查周期。
