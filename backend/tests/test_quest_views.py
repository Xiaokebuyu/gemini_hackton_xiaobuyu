from __future__ import annotations

from app.quest_views import normalize_dynamic_quest_panel, normalize_dynamic_quest_view


def test_normalize_dynamic_quest_view_adds_stable_defaults() -> None:
    quest = normalize_dynamic_quest_view(
        "dq_intro",
        {
            "status": "active",
            "title": "Intro Lead",
            "summary": "Follow the first clue.",
        },
    )

    assert quest["quest_id"] == "dq_intro"
    assert quest["status"] == "active"
    assert quest["current_step"] is None
    assert quest["next_steps"] == []
    assert quest["hints"] == []
    assert quest["requires_report"] is False
    assert quest["reported"] is False
    assert quest["can_report"] is False
    assert quest["ui_state"] == "active"
    assert quest["badge"] == {"key": "active", "label": "进行中"}


def test_normalize_dynamic_quest_view_marks_reportable_completed_quest() -> None:
    quest = normalize_dynamic_quest_view(
        "dq_report_in",
        {
            "status": "completed",
            "title": "Lead: Report In",
            "summary": "Follow the new lead tied to report_in.",
            "requires_report": True,
        },
    )

    assert quest["can_report"] is True
    assert quest["reported"] is False
    assert "report" in str(quest["current_step"]).lower()
    assert quest["next_steps"]
    assert quest["hints"]
    assert quest["ui_state"] == "ready_to_report"
    assert quest["badge"] == {"key": "ready_to_report", "label": "待汇报"}


def test_normalize_dynamic_quest_panel_normalizes_each_entry() -> None:
    payload = normalize_dynamic_quest_panel(
        {
            "dq_a": {"status": "available", "title": "A"},
            "dq_b": {"status": "completed", "title": "B", "requires_report": True},
        }
    )

    assert set(payload) == {"dq_a", "dq_b"}
    assert payload["dq_a"]["quest_id"] == "dq_a"
    assert payload["dq_b"]["can_report"] is True


def test_normalize_dynamic_quest_view_maps_badges_for_all_supported_states() -> None:
    assert normalize_dynamic_quest_view("dq_active", {"status": "active"})["badge"] == {
        "key": "active",
        "label": "进行中",
    }
    assert normalize_dynamic_quest_view("dq_available", {"status": "available"})["badge"] == {
        "key": "available",
        "label": "可接取",
    }
    assert normalize_dynamic_quest_view(
        "dq_completed",
        {"status": "completed", "reported": True},
    )["badge"] == {
        "key": "completed",
        "label": "已完成",
    }
    assert normalize_dynamic_quest_view("dq_failed", {"status": "failed"})["badge"] == {
        "key": "failed",
        "label": "失败",
    }
    assert normalize_dynamic_quest_view("dq_retired", {"status": "retired"})["badge"] == {
        "key": "retired",
        "label": "已撤回",
    }
    assert normalize_dynamic_quest_view("dq_expired", {"status": "expired"})["badge"] == {
        "key": "expired",
        "label": "已过期",
    }
    assert normalize_dynamic_quest_view("dq_unknown", {})["badge"] == {
        "key": "unknown",
        "label": "未知",
    }


def test_normalize_dynamic_quest_panel_sorts_by_status_buckets() -> None:
    payload = normalize_dynamic_quest_panel(
        {
            "dq_unknown": {"title": "Unknown"},
            "dq_retired": {"status": "retired", "title": "Retired", "created_at_tick": 7},
            "dq_available": {"status": "available", "title": "Available", "created_at_tick": 4},
            "dq_failed": {"status": "failed", "title": "Failed", "created_at_tick": 8},
            "dq_completed_done": {
                "status": "completed",
                "title": "Reported",
                "requires_report": True,
                "reported": True,
                "created_at_tick": 5,
            },
            "dq_report_in": {
                "status": "completed",
                "title": "Report In",
                "requires_report": True,
                "created_at_tick": 6,
            },
            "dq_active": {"status": "active", "title": "Active", "created_at_tick": 2},
            "dq_expired": {"status": "expired", "title": "Expired", "created_at_tick": 3},
        }
    )

    assert list(payload) == [
        "dq_active",
        "dq_report_in",
        "dq_available",
        "dq_completed_done",
        "dq_failed",
        "dq_retired",
        "dq_expired",
        "dq_unknown",
    ]


def test_normalize_dynamic_quest_panel_sorts_by_created_tick_then_quest_id() -> None:
    payload = normalize_dynamic_quest_panel(
        {
            "dq_z": {"status": "available", "title": "Z"},
            "dq_b": {"status": "available", "title": "B", "created_at_tick": 3},
            "dq_a": {"status": "available", "title": "A", "created_at_tick": 3},
            "dq_y": {"status": "available", "title": "Y"},
        }
    )

    assert list(payload) == ["dq_a", "dq_b", "dq_y", "dq_z"]
