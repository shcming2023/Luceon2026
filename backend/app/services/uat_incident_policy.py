from __future__ import annotations

from dataclasses import asdict, dataclass


PASSED_STATUSES = frozenset({"succeeded", "handoff_ready"})
BLOCKED_STATUSES = frozenset({"failed", "needs_review", "blocked"})
RUNNING_STATUSES = frozenset({"queued", "pending", "running"})


@dataclass(frozen=True)
class UatIncidentDecision:
    decision: str
    title: str
    message: str
    next_interval_seconds: int
    counts: dict[str, int]
    ui_verified: bool

    def to_dict(self) -> dict:
        return asdict(self)


def decide_uat_notification(
    statuses: list[str],
    *,
    ui_verified: bool,
    previous_counts: dict[str, int] | None = None,
) -> UatIncidentDecision:
    counts = {"passed": 0, "blocked": 0, "running": 0}
    for status in statuses:
        if status in PASSED_STATUSES:
            counts["passed"] += 1
        elif status in BLOCKED_STATUSES:
            counts["blocked"] += 1
        elif status in RUNNING_STATUSES:
            counts["running"] += 1

    total = len(statuses)
    title = f"成功 {counts['passed']}/{total} / 阻断 {counts['blocked']}/{total} / 运行 {counts['running']}/{total}"
    if not ui_verified:
        title = f"{title} / UI 层未验证"

    previous_counts = previous_counts or {}
    newly_blocked = counts["blocked"] > int(previous_counts.get("blocked") or 0)
    changed = any(counts[key] != int(previous_counts.get(key) or 0) for key in counts)

    if counts["blocked"]:
        detail = "首次出现质量阻断，立即通知。" if newly_blocked else "质量阻断尚未解除；事故状态每 15 分钟重复简报。"
        if not ui_verified:
            detail += " 页面会话不可读，数据库或 API 证据不能替代页面 UAT。"
        return UatIncidentDecision("NOTIFY", title, detail, 900, counts, ui_verified)

    if not ui_verified:
        return UatIncidentDecision(
            "NOTIFY",
            title,
            "页面会话不可读，UI 层未验证；不得声称页面 UAT 完成。",
            900,
            counts,
            ui_verified,
        )

    if total and counts["passed"] == total:
        return UatIncidentDecision(
            "NOTIFY" if changed else "DONT_NOTIFY",
            title,
            "全部任务到达可解释终态；进入交付物、审阅和复编核验。",
            300,
            counts,
            ui_verified,
        )

    return UatIncidentDecision(
        "NOTIFY" if changed else "DONT_NOTIFY",
        title,
        "状态发生里程碑变化。" if changed else "任务健康推进且当前不需要用户操作。",
        300,
        counts,
        ui_verified,
    )
