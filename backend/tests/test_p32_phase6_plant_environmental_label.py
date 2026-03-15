"""Tests for P32 Phase 6 — plant_environmental label/description separation.

Covers:
- With explicit label: sub-area label == label, description unchanged
- Without label, short description: label == description
- Without label, long description: label is description[:20] + "…"
- directive_contracts: optional label field normalized and validated
- directive_contracts: label_too_long rejected
- world_builder path: _resolve_sub_area_label helper used in preview spec
"""

from __future__ import annotations

from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.planning.directive_contracts import validate_planner_directive
from app.game_core.planning.world_builder import WorldBuilderSubSystem, _resolve_sub_area_label
from app.game_core.rules import RulesEngine
from app.game_core.rules.defaults import register_default_rules_handlers
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    AreaSlice,
    EventSlice,
    FlagSlice,
    NarrativePlanSlice,
    PlayerSlice,
    QuestSlice,
    SceneSlice,
    TimeSlice,
)


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------


def _make_context(area_id: str = "test_area") -> SettlementContext:
    world = WorldInstance("test_world")
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": area_id, "current_location": None})
    state.register(player)

    quests = QuestSlice()
    quests.restore({"milestone_states": {}, "dynamic_quests": {}})
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({
        "current_chapter": "chapter_1",
        "escalation_level": 0,
        "last_run_tick": 0,
        "ticks_since_milestone_progress": 0,
    })
    state.register(narrative_plan)

    areas = AreaSlice()
    areas.restore({"areas": {area_id: {}}})
    state.register(areas)

    flags = FlagSlice()
    flags.restore({})
    state.register(flags)

    events = EventSlice()
    events.restore({})
    state.register(events)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    change_log: list[StateChange] = []

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        change_log.extend(delta.changes)
        for change in delta.changes:
            scene_bus.record_state_change(change)

    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)

    return SettlementContext(
        change_log=change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
        knowledge_graph=None,
    )


def _run_plant_environmental(
    payload: dict[str, Any],
    *,
    area_id: str = "test_area",
    current_tick: int = 5,
) -> dict[str, Any] | None:
    """Apply a plant_environmental directive and return the created sub-area dict (or None)."""
    ctx = _make_context(area_id=area_id)
    pending_sse: list[Any] = []
    world_builder = WorldBuilderSubSystem(sse_collector=pending_sse)
    world_builder.apply_directive(
        "plant_environmental",
        payload,
        ctx,
        current_tick=current_tick,
    )
    sub_areas = ctx.state.areas.list_temporary_sub_areas(area_id)
    return sub_areas[0] if sub_areas else None


# ------------------------------------------------------------------
# _resolve_sub_area_label unit tests (helper in world_builder.py)
# ------------------------------------------------------------------


def test_resolve_label_uses_explicit_label_when_provided() -> None:
    result = _resolve_sub_area_label({
        "label": "林道入口",
        "description": "恶臭的遗迹入口林道，充满腐败的气味。",
    })
    assert result == "林道入口"


def test_resolve_label_falls_back_to_description_when_short() -> None:
    result = _resolve_sub_area_label({
        "description": "短描述"
    })
    assert result == "短描述"


def test_resolve_label_truncates_long_description() -> None:
    description = "这是一段超过二十字符的详细描述文字，应该被截断。"
    result = _resolve_sub_area_label({"description": description})
    assert result == description[:20] + "…"
    assert len(result) == 21  # 20 chars + ellipsis


def test_resolve_label_empty_when_no_description_no_label() -> None:
    result = _resolve_sub_area_label({})
    assert result == ""


def test_resolve_label_prefers_label_over_description_when_both_present() -> None:
    result = _resolve_sub_area_label({
        "label": "明月楼",
        "description": "月光洒在楼阁之上，此地充满神秘气息，据说是古代魔法师的聚集地。",
    })
    assert result == "明月楼"
    assert result != "月光洒在楼阁之上，此地充满神秘气息，据说是古代魔法师的聚集地。"


# ------------------------------------------------------------------
# plant_environmental directive: sub-area stored with correct label/description
# ------------------------------------------------------------------


def test_plant_environmental_with_label_uses_label_as_sub_area_label() -> None:
    sub_area = _run_plant_environmental({
        "area_id": "test_area",
        "clue_id": "clue_p32_1",
        "label": "林道入口",
        "description": "恶臭的遗迹入口林道，充满腐败的气味，树木枯死，地面满是污渍。",
        "dc": 12,
    })

    assert sub_area is not None
    assert sub_area["label"] == "林道入口"


def test_plant_environmental_with_label_keeps_description_intact() -> None:
    long_description = "恶臭的遗迹入口林道，充满腐败的气味，树木枯死，地面满是污渍。"
    sub_area = _run_plant_environmental({
        "area_id": "test_area",
        "clue_id": "clue_p32_2",
        "label": "林道入口",
        "description": long_description,
        "dc": 12,
    })

    assert sub_area is not None
    assert sub_area["description"] == long_description


def test_plant_environmental_label_differs_from_description() -> None:
    long_description = "这是一段很长的描述文字，不应该被当作名称使用。"
    sub_area = _run_plant_environmental({
        "area_id": "test_area",
        "clue_id": "clue_p32_3",
        "label": "简短名称",
        "description": long_description,
        "dc": 10,
    })

    assert sub_area is not None
    assert sub_area["label"] != sub_area["description"]
    assert sub_area["label"] == "简短名称"
    assert sub_area["description"] == long_description


def test_plant_environmental_without_label_short_description_uses_description_as_label() -> None:
    short_description = "篝火痕迹"
    sub_area = _run_plant_environmental({
        "area_id": "test_area",
        "clue_id": "clue_p32_4",
        "description": short_description,
        "dc": 10,
    })

    assert sub_area is not None
    assert sub_area["label"] == short_description
    assert sub_area["description"] == short_description


def test_plant_environmental_without_label_long_description_truncates_label() -> None:
    # Use a description longer than 20 chars without punctuation in the first 20 chars
    # (Chinese commas/periods in the label would fail the punctuation check).
    long_description = "远处传来狼嚎声 让人心生不安 仿佛有什么危险正在接近已知区域附近"
    assert len(long_description) > 20
    sub_area = _run_plant_environmental({
        "area_id": "test_area",
        "clue_id": "clue_p32_5",
        "description": long_description,
        "dc": 14,
    })

    assert sub_area is not None
    # Label should be truncated to 20 chars + "…"
    assert sub_area["label"] == long_description[:20] + "…"
    # Description must remain the full original text
    assert sub_area["description"] == long_description
    # Confirm they differ
    assert sub_area["label"] != sub_area["description"]


# ------------------------------------------------------------------
# directive_contracts: optional label field validation
# ------------------------------------------------------------------


def test_validate_plant_environmental_accepts_optional_label() -> None:
    result = validate_planner_directive({
        "kind": "plant_environmental",
        "payload": {
            "area_id": "frontier_wilderness",
            "description": "Suspicious tracks in the mud.",
            "label": "泥中足迹",
        },
    })

    assert result.ok is True


def test_validate_plant_environmental_without_label_still_valid() -> None:
    result = validate_planner_directive({
        "kind": "plant_environmental",
        "payload": {
            "area_id": "frontier_wilderness",
            "description": "Thin mist over the farmland.",
        },
    })

    assert result.ok is True


def test_validate_plant_environmental_rejects_label_too_long() -> None:
    # Use ASCII characters to ensure len() accurately reflects character count
    long_label = "This is a label that is definitely over thirty characters long"
    assert len(long_label) > 30
    result = validate_planner_directive({
        "kind": "plant_environmental",
        "payload": {
            "area_id": "frontier_wilderness",
            "description": "Some description.",
            "label": long_label,
        },
    })

    assert result.ok is False
    assert result.reason_code == "label_too_long"


def test_validate_plant_environmental_normalizes_label_in_payload() -> None:
    """validate_planner_directive should normalize and retain the label in the payload."""
    result = validate_planner_directive({
        "kind": "plant_environmental",
        "payload": {
            "area_id": "frontier_wilderness",
            "description": "A clue about something.",
            "label": "  林中迷雾  ",  # leading/trailing spaces
        },
    })

    assert result.ok is True
    # The normalized payload should have the label stripped
    assert result.payload["label"] == "林中迷雾"
