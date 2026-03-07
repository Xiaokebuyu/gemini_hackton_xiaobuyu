"""Tests for board bulletin storage in AreaSlice."""

from __future__ import annotations

from app.game_core.state import StateChange
from app.game_core.state.slices.area import AreaSlice


def _make_area_slice() -> AreaSlice:
    area_slice = AreaSlice()
    area_slice.restore(
        {
            "areas": {
                "forest": {
                    "board_bulletins": {
                        "quest_board": [
                            {"board_id": "quest_board", "quest_id": "q_open", "title": "旧任务"},
                        ],
                    }
                }
            }
        }
    )
    return area_slice


def test_area_slice_snapshot_restore_preserves_board_bulletins() -> None:
    area_slice = _make_area_slice()
    snapshot = area_slice.snapshot()
    restored = AreaSlice()
    restored.restore(snapshot)

    assert restored.areas["forest"].board_bulletins == {
        "quest_board": [
            {"board_id": "quest_board", "quest_id": "q_open", "title": "旧任务"},
        ]
    }


def test_area_slice_validate_reports_board_bulletins_type_issue() -> None:
    area_slice = AreaSlice()
    area_slice.restore({"areas": {"forest": {}}})
    area_slice.areas["forest"].board_bulletins["quest_board"] = "not_a_list"  # type: ignore[assignment]
    issues = area_slice.validate()
    assert any("board_bulletins 'quest_board' must be a list" in issue for issue in issues)


def test_area_slice_board_bulletin_crud() -> None:
    area_slice = AreaSlice()
    area_slice.restore({"areas": {"forest": {}}})

    area_slice.add_board_bulletin(
        "forest",
        "quest_board",
        {
            "board_id": "quest_board",
            "quest_id": "q_1",
            "title": "A Quest",
        },
    )
    area_slice.add_board_bulletin(
        "forest",
        "quest_board",
        {
            "board_id": "quest_board",
            "quest_id": "q_2",
            "title": "B Quest",
        },
    )

    assert area_slice.get_board_bulletins("forest", "quest_board")[0]["quest_id"] == "q_1"
    assert area_slice.remove_board_bulletin("forest", "quest_board", "q_1")
    assert area_slice.remove_board_bulletin("forest", "quest_board", "q_1") is False


def test_area_slice_apply_state_change_append_bulletin() -> None:
    area_slice = AreaSlice()
    area_slice.restore({"areas": {"forest": {}}})
    area_slice.apply_state_change(
        StateChange(
            slice="areas",
            operation="append",
            path="board_bulletins.quest_board",
            value={
                "area_id": "forest",
                "board_id": "quest_board",
                "quest_id": "q_1",
                "title": "A Quest",
                "content": "内容",
                "published_at_tick": 0,
                "source": "narrative_planner",
            },
        )
    )

    entries = area_slice.get_board_bulletins("forest", "quest_board")
    assert len(entries) == 1
    assert entries[0]["quest_id"] == "q_1"
