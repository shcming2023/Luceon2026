from app.services.uat_incident_policy import decide_uat_notification


def test_first_blocker_notifies_immediately_with_fixed_counts_title():
    decision = decide_uat_notification(
        ["running", "blocked", "running", "running", "running"],
        ui_verified=True,
        previous_counts={"passed": 0, "blocked": 0, "running": 5},
    )

    assert decision.decision == "NOTIFY"
    assert decision.title == "成功 0/5 / 阻断 1/5 / 运行 4/5"
    assert decision.next_interval_seconds == 900
    assert "立即通知" in decision.message


def test_unresolved_incident_repeats_every_fifteen_minutes():
    decision = decide_uat_notification(
        ["blocked"] * 5,
        ui_verified=True,
        previous_counts={"passed": 0, "blocked": 5, "running": 0},
    )

    assert decision.decision == "NOTIFY"
    assert decision.next_interval_seconds == 900
    assert "每 15 分钟" in decision.message


def test_ui_unavailable_is_explicit_and_cannot_be_silent():
    decision = decide_uat_notification(
        ["running"] * 5,
        ui_verified=False,
        previous_counts={"passed": 0, "blocked": 0, "running": 5},
    )

    assert decision.decision == "NOTIFY"
    assert "UI 层未验证" in decision.title
    assert "不能替代" not in decision.message
    assert "不得声称页面 UAT 完成" in decision.message


def test_unchanged_healthy_progress_can_be_silent():
    decision = decide_uat_notification(
        ["running"] * 5,
        ui_verified=True,
        previous_counts={"passed": 0, "blocked": 0, "running": 5},
    )

    assert decision.decision == "DONT_NOTIFY"
    assert decision.next_interval_seconds == 300
    assert "健康推进" in decision.message
