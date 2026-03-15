"""Tests for KI-01 + KI-02 Clue system fixes.

KI-02: resolve_clue_option now persists outcome_text/check_passed/effects_applied
       to interactable_states and writes an area_event entry.
KI-01: agent_orchestration fallback methods use outcome_text from metadata/payload
       instead of static template text.
"""

from __future__ import annotations

from typing import Any, Mapping
from unittest import mock

from app.game_core.bootstrap import build_default_world
from app.game_core.rules.handlers.clue import ClueHandler, _derive_outcome_text
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, FlagSlice, PlayerSlice, TimeSlice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_world():
    return build_default_world(
        "test_world",
        world_data={
            "maps": {
                "frontier_town": {
                    "id": "frontier_town",
                    "name": "边境小镇",
                    "sub_locations": {
                        "adventurer_guild": {
                            "id": "adventurer_guild",
                            "name": "冒险者公会",
                            "default_room": "guild_counter",
                            "rooms": {
                                "guild_counter": {
                                    "id": "guild_counter",
                                    "name": "受付柜台",
                                }
                            },
                        }
                    },
                }
            }
        },
    )


def _build_state_with_clue(
    *,
    options: list[dict],
    outcomes: dict,
    on_first_inspect: list | None = None,
) -> StateContainer:
    state = StateContainer()

    player = PlayerSlice()
    player.restore(
        {
            "current_area": "frontier_town",
            "current_location": "adventurer_guild",
            "current_room": "guild_counter",
            "stats": {"str": 10, "dex": 10, "con": 10, "int": 14, "wis": 12, "cha": 10},
            "skill_proficiencies": ["investigation"],
        }
    )
    state.register(player)

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9, "absolute_tick": 7, "accumulated": 0.0})
    state.register(time_slice)

    flags = FlagSlice()
    flags.restore({})
    state.register(flags)

    areas = AreaSlice()
    areas.restore({"areas": {"frontier_town": {}}})
    areas.set_scoped_interactable_overlays(
        "frontier_town",
        "adventurer_guild__guild_counter",
        [
            {
                "id": "blood_trail_clue",
                "name": "拖拽血迹",
                "description": "半干的血迹断断续续地拖向后门。",
                "type": "inspect",
                "tags": ["clue"],
                "functional": {
                    "type": "investigate_clue",
                    "params": {
                        "clue_id": "blood_trail",
                        "on_first_inspect": on_first_inspect or [],
                        "options": options,
                        "outcomes": outcomes,
                    },
                },
            }
        ],
    )
    state.register(areas)
    return state


def _resolve_clue(state: StateContainer, world, *, option_id: str = "examine") -> ExecuteResult:
    """Investigate then resolve a clue, returning the resolve result."""
    handler = ClueHandler()
    investigate = handler.compute(
        Command(type="investigate_clue", params={"interactable_id": "blood_trail_clue"}, source="player"),
        state,
        world,
    )
    assert investigate.delta is not None, "investigate_clue failed"
    state.apply(investigate.delta)

    resolve = handler.compute(
        Command(
            type="resolve_clue_option",
            params={"interactable_id": "blood_trail_clue", "option_id": option_id},
            source="player",
        ),
        state,
        world,
    )
    return resolve


# ---------------------------------------------------------------------------
# KI-02: Handler now persists outcome_text, check_passed, effects_applied
# ---------------------------------------------------------------------------

def test_resolve_stores_outcome_text_in_interactable_state() -> None:
    world = _build_world()
    state = _build_state_with_clue(
        options=[{"id": "examine", "label": "仔细检查"}, {"id": "think", "label": "推理"}],
        outcomes={"examine": [], "think": []},
    )
    resolve = _resolve_clue(state, world, option_id="examine")

    assert resolve.executed is True
    assert resolve.delta is not None
    state.apply(resolve.delta)

    clue_state = state.areas.get_area("frontier_town").interactable_states["blood_trail_clue"]
    assert "outcome_text" in clue_state
    assert isinstance(clue_state["outcome_text"], str)
    assert len(clue_state["outcome_text"]) > 0


def test_resolve_stores_check_passed_in_interactable_state() -> None:
    """When option has no check, check_passed should be None."""
    world = _build_world()
    state = _build_state_with_clue(
        options=[{"id": "examine", "label": "仔细检查"}, {"id": "think", "label": "推理"}],
        outcomes={"examine": [], "think": []},
    )
    resolve = _resolve_clue(state, world, option_id="examine")
    assert resolve.delta is not None
    state.apply(resolve.delta)

    clue_state = state.areas.get_area("frontier_town").interactable_states["blood_trail_clue"]
    # No check on this option → check_passed should be None
    assert clue_state["check_passed"] is None


def test_resolve_stores_effects_applied_in_interactable_state() -> None:
    world = _build_world()
    state = _build_state_with_clue(
        options=[
            {"id": "examine", "label": "仔细检查"},
            {"id": "search", "label": "搜寻"},
        ],
        outcomes={
            "examine": [{"type": "set_flag", "params": {"key": "trail_examined", "value": True}}],
            "search": [],
        },
    )
    resolve = _resolve_clue(state, world, option_id="examine")
    assert resolve.delta is not None
    state.apply(resolve.delta)

    clue_state = state.areas.get_area("frontier_town").interactable_states["blood_trail_clue"]
    assert "effects_applied" in clue_state
    assert isinstance(clue_state["effects_applied"], list)
    assert "set_flag" in clue_state["effects_applied"]


def test_resolve_writes_area_event() -> None:
    world = _build_world()
    state = _build_state_with_clue(
        options=[{"id": "examine", "label": "仔细检查"}, {"id": "think", "label": "推理"}],
        outcomes={"examine": [], "think": []},
    )
    resolve = _resolve_clue(state, world, option_id="examine")
    assert resolve.delta is not None
    state.apply(resolve.delta)

    events = state.areas.get_area_events("frontier_town")
    assert len(events) >= 1
    latest = events[-1]
    assert latest["source"] == "clue_investigation"
    assert latest["severity"] == "minor"
    assert isinstance(latest["tick"], int) and latest["tick"] >= 0
    assert "拖拽血迹" in latest["event"] or "blood_trail" in latest["event"]


def test_resolve_metadata_contains_outcome_text() -> None:
    world = _build_world()
    state = _build_state_with_clue(
        options=[{"id": "examine", "label": "仔细检查"}, {"id": "think", "label": "推理"}],
        outcomes={"examine": [], "think": []},
    )
    resolve = _resolve_clue(state, world, option_id="examine")

    assert "outcome_text" in resolve.metadata
    assert isinstance(resolve.metadata["outcome_text"], str)
    assert len(resolve.metadata["outcome_text"]) > 0


# ---------------------------------------------------------------------------
# KI-02: _derive_outcome_text helper
# ---------------------------------------------------------------------------

def test_derive_outcome_text_unlock_sub_location() -> None:
    text = _derive_outcome_text(
        clue_name="拖拽血迹",
        option_label="顺着痕迹追过去",
        effect_types=["unlock_sub_location"],
        passed=None,
    )
    assert "追下去" in text or "路" in text


def test_derive_outcome_text_advance_quest() -> None:
    text = _derive_outcome_text(
        clue_name="古老符文",
        option_label="翻译符文",
        effect_types=["advance_quest"],
        passed=True,
    )
    assert "方向" in text or "前" in text


def test_derive_outcome_text_check_failed() -> None:
    text = _derive_outcome_text(
        clue_name="密码箱",
        option_label="暴力破解",
        effect_types=[],
        passed=False,
    )
    assert "没能" in text or "岔路" in text


def test_derive_outcome_text_default() -> None:
    text = _derive_outcome_text(
        clue_name="神秘印记",
        option_label="仔细检查",
        effect_types=[],
        passed=None,
    )
    assert "方向" in text


# ---------------------------------------------------------------------------
# KI-01: agent_orchestration fallback methods use outcome_text
# ---------------------------------------------------------------------------

def _make_service():
    """Create an AgentOrchestrationService with a minimal mock executor."""
    from app.agent_orchestration import AgentOrchestrationService
    from app.game_core.narrative.executor import AgenticExecutor

    dummy_executor = mock.MagicMock(spec=AgenticExecutor)
    return AgentOrchestrationService(executor=dummy_executor)


def test_build_fallback_clue_comment_uses_outcome_text_when_present() -> None:
    """_build_fallback_clue_comment_event should use outcome_text from payload."""
    service = _make_service()
    clue_payload = {
        "clue_id": "blood_trail",
        "clue_name": "拖拽血迹",
        "outcome_text": "顺着痕迹追过去终于露出了一条能追下去的路。",
    }
    event = service._build_fallback_clue_comment_event(clue_payload)
    assert event.payload["content"] == "顺着痕迹追过去终于露出了一条能追下去的路。"


def test_build_fallback_clue_comment_falls_back_to_template_when_no_outcome_text() -> None:
    """Without outcome_text, _build_fallback_clue_comment_event uses the default template."""
    service = _make_service()
    clue_payload = {
        "clue_id": "blood_trail",
        "clue_name": "拖拽血迹",
    }
    event = service._build_fallback_clue_comment_event(clue_payload)
    assert "拖拽血迹" in event.payload["content"]
    assert "先把方向" in event.payload["content"]


def test_build_clue_resolution_comment_uses_outcome_text_from_metadata() -> None:
    """_build_clue_resolution_comment_event should prefer outcome_text from metadata."""
    service = _make_service()

    # Simulate a PipelineResult-like object with metadata containing outcome_text.
    class FakePipelineResult:
        metadata: dict = {
            "clue_name": "古老符文",
            "option_label": "翻译符文",
            "passed": True,
            "effect_types": ["advance_quest"],
            "outcome_text": "这段话终于指向了正确方向。",
        }

    event = service._build_clue_resolution_comment_event(FakePipelineResult())
    assert event is not None
    assert event.payload["content"] == "这段话终于指向了正确方向。"


def test_build_clue_resolution_comment_falls_back_when_no_outcome_text() -> None:
    """Without outcome_text, _build_clue_resolution_comment_event derives text from metadata."""
    service = _make_service()

    class FakePipelineResult:
        metadata: dict = {
            "clue_name": "密码箱",
            "option_label": "暴力破解",
            "passed": False,
            "effect_types": [],
        }

    event = service._build_clue_resolution_comment_event(FakePipelineResult())
    assert event is not None
    assert "密码箱" in event.payload["content"] or "暴力破解" in event.payload["content"]
