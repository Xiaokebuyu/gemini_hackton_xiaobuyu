from __future__ import annotations

from app.game_core.state.quest_runtime import (
    normalize_runtime_dynamic_quest,
    quest_can_report,
)


def test_runtime_projection_prefers_top_level_report_flags_over_metadata() -> None:
    quest = normalize_runtime_dynamic_quest(
        "dq_report_in",
        {
            "status": "completed",
            "requires_report": False,
            "reported": True,
            "metadata": {
                "requires_report": True,
                "reported": False,
            },
        },
    )

    assert quest["requires_report"] is False
    assert quest["reported"] is True
    assert quest["can_report"] is False


def test_runtime_projection_falls_back_to_metadata_when_top_level_flags_missing() -> None:
    quest = normalize_runtime_dynamic_quest(
        "dq_report_in",
        {
            "status": "completed",
            "metadata": {
                "requires_report": True,
                "reported": False,
            },
        },
    )

    assert quest["requires_report"] is True
    assert quest["reported"] is False
    assert quest["can_report"] is True


def test_quest_can_report_uses_completed_requires_report_and_reported_formula() -> None:
    assert quest_can_report(
        {
            "status": "completed",
            "requires_report": True,
            "reported": False,
        }
    ) is True
    assert quest_can_report(
        {
            "status": "active",
            "requires_report": True,
            "reported": False,
        }
    ) is False
    assert quest_can_report(
        {
            "status": "completed",
            "requires_report": True,
            "reported": True,
        }
    ) is False
