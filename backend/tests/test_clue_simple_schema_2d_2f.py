"""Tests for simplified clue schema steps 2d + 2e + 2f.

2d: WorldBuilderSubSystem._translate_environmental_clue_payload()
    - New schema (base_effects) produces simplified clue interactable
    - Legacy schema (options) still works unchanged
    - Missing area_id/clue_id falls through gracefully

2e: directive_contracts fill_location simplified clue passes validation
    - validate_planner_directive accepts fill_location with simple schema clue
    - validate_planner_directive correctly strips legacy clue with <2 options

2f: agent_orchestration._run_clue_investigation_round
    - Simple schema: no dialogue_options panel emitted, simple/has_check flags in payload
    - Legacy schema: dialogue_options panel emitted as before
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest import mock
from unittest.mock import MagicMock

from app.game_core.content import WorldInstance
from app.game_core.orchestration.models import SSEEvent
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning.directive_contracts import validate_planner_directive
from app.game_core.planning.world_builder import WorldBuilderSubSystem
from app.game_core.rules import RulesEngine, register_default_rules_handlers
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    AreaSlice,
    FlagSlice,
    NarrativePlanSlice,
    PlayerSlice,
    QuestSlice,
    SceneSlice,
    TimeSlice,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_context(
    *,
    player_area: str = "frontier_town",
    location: str = "north_gate",
) -> SettlementContext:
    world = WorldInstance("test_world")
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": player_area, "current_location": location})
    state.register(player)

    quests = QuestSlice()
    quests.restore({"milestone_states": {}, "dynamic_quests": {}})
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({})
    state.register(narrative_plan)

    areas = AreaSlice()
    areas.restore({"areas": {player_area: {}}})
    state.register(areas)

    flag_slice = FlagSlice()
    flag_slice.restore({})
    state.register(flag_slice)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    change_log: list[StateChange] = []
    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        change_log.extend(delta.changes)
        for change in delta.changes:
            scene_bus.record_state_change(change)

    return SettlementContext(
        change_log=change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
    )


# ---------------------------------------------------------------------------
# 2d: WorldBuilderSubSystem — _translate_environmental_clue_payload
# ---------------------------------------------------------------------------

class TestTranslateEnvironmentalCluePayloadNewSchema:
    """New schema (base_effects present, no options) → simplified clue interactable."""

    def _call(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        context = _make_context()
        builder = WorldBuilderSubSystem(sse_collector=[])
        return builder._translate_environmental_clue_payload(payload, context)

    def test_new_schema_returns_non_none(self) -> None:
        result = self._call({
            "area_id": "frontier_town",
            "location_id": "north_gate",
            "clue_id": "herb_patch",
            "label": "药草丛",
            "description": "溪边的一片药草丛。",
            "base_effects": [{"type": "set_flag", "params": {"key": "found_herbs", "value": True}}],
        })
        assert result is not None

    def test_new_schema_clue_interactable_has_base_effects(self) -> None:
        result = self._call({
            "area_id": "frontier_town",
            "location_id": "north_gate",
            "clue_id": "herb_patch",
            "base_effects": [{"type": "set_flag", "params": {"key": "found_herbs", "value": True}}],
        })
        assert result is not None
        interactable = result["interactables"][0]
        params = interactable["functional"]["params"]
        assert "base_effects" in params
        assert len(params["base_effects"]) == 1
        assert "options" not in params
        assert "outcomes" not in params

    def test_new_schema_clue_interactable_has_check_when_provided(self) -> None:
        result = self._call({
            "area_id": "frontier_town",
            "location_id": "north_gate",
            "clue_id": "herb_patch",
            "base_effects": [],
            "check": {"skill": "nature", "dc": 12},
            "check_effects": [{"type": "grant_item", "params": {"item_id": "rare_herb", "count": 1}}],
        })
        assert result is not None
        params = result["interactables"][0]["functional"]["params"]
        assert params["check"] == {"skill": "nature", "dc": 12}
        assert len(params["check_effects"]) == 1

    def test_new_schema_clue_interactable_has_narrative(self) -> None:
        result = self._call({
            "area_id": "frontier_town",
            "location_id": "north_gate",
            "clue_id": "herb_patch",
            "base_effects": [],
            "narrative": "这片药草丛生长在溪边阴凉处。",
        })
        assert result is not None
        params = result["interactables"][0]["functional"]["params"]
        assert params["narrative"] == "这片药草丛生长在溪边阴凉处。"

    def test_new_schema_has_no_options_field(self) -> None:
        result = self._call({
            "area_id": "frontier_town",
            "location_id": "north_gate",
            "clue_id": "herb_patch",
            "base_effects": [],
        })
        assert result is not None
        params = result["interactables"][0]["functional"]["params"]
        assert "options" not in params
        assert "outcomes" not in params

    def test_new_schema_tags_include_clue_and_party_discussion(self) -> None:
        result = self._call({
            "area_id": "frontier_town",
            "location_id": "north_gate",
            "clue_id": "herb_patch",
            "base_effects": [],
        })
        assert result is not None
        tags = result["interactables"][0]["tags"]
        assert "clue" in tags
        assert "party_discussion" in tags

    def test_new_schema_area_location_room_preserved(self) -> None:
        result = self._call({
            "area_id": "frontier_town",
            "location_id": "north_gate",
            "room_id": "gatehouse",
            "clue_id": "herb_patch",
            "base_effects": [],
        })
        assert result is not None
        assert result["area_id"] == "frontier_town"
        assert result["location_id"] == "north_gate"
        assert result["room_id"] == "gatehouse"

    def test_new_schema_clue_id_propagated(self) -> None:
        result = self._call({
            "area_id": "frontier_town",
            "location_id": "north_gate",
            "clue_id": "my_clue",
            "base_effects": [],
        })
        assert result is not None
        params = result["interactables"][0]["functional"]["params"]
        assert params["clue_id"] == "my_clue"

    def test_new_schema_hide_on_resolve_is_true(self) -> None:
        result = self._call({
            "area_id": "frontier_town",
            "location_id": "north_gate",
            "clue_id": "herb_patch",
            "base_effects": [],
        })
        assert result is not None
        params = result["interactables"][0]["functional"]["params"]
        assert params["hide_on_resolve"] is True


class TestTranslateEnvironmentalCluePayloadLegacySchema:
    """Legacy schema (options present) still works — no regressions."""

    def _call(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        context = _make_context()
        builder = WorldBuilderSubSystem(sse_collector=[])
        return builder._translate_environmental_clue_payload(payload, context)

    def test_legacy_schema_returns_options(self) -> None:
        result = self._call({
            "area_id": "frontier_town",
            "location_id": "north_gate",
            "clue_id": "blood_trail",
            "options": [
                {"id": "examine", "label": "仔细检查"},
                {"id": "follow", "label": "追踪"},
            ],
            "outcomes": {
                "examine": [],
                "follow": [],
            },
        })
        assert result is not None
        params = result["interactables"][0]["functional"]["params"]
        assert "options" in params
        assert len(params["options"]) == 2
        assert "base_effects" not in params

    def test_legacy_schema_missing_options_uses_defaults(self) -> None:
        """If payload has neither base_effects nor valid options, default options are used."""
        result = self._call({
            "area_id": "frontier_town",
            "location_id": "north_gate",
            "clue_id": "mystery",
        })
        assert result is not None
        params = result["interactables"][0]["functional"]["params"]
        assert "options" in params
        assert len(params["options"]) >= 2

    def test_missing_area_id_returns_none(self) -> None:
        result = self._call({
            "location_id": "north_gate",
            "clue_id": "test_clue",
            "base_effects": [],
        })
        assert result is None

    def test_missing_clue_id_returns_none(self) -> None:
        result = self._call({
            "area_id": "frontier_town",
            "location_id": "north_gate",
            "base_effects": [],
        })
        assert result is None


# ---------------------------------------------------------------------------
# 2e: directive_contracts — fill_location simple clue passes validation
# ---------------------------------------------------------------------------

class TestFillLocationSimpleClueContract:
    def test_simple_clue_schema_passes_validation(self) -> None:
        """fill_location with simplified schema clue (base_effects) passes contract."""
        result = validate_planner_directive({
            "kind": "fill_location",
            "payload": {
                "area_id": "frontier_town",
                "location_id": "north_gate",
                "interactables": [
                    {
                        "id": "herb_patch",
                        "name": "药草丛",
                        "description": "溪边的一片药草丛。",
                        "type": "inspect",
                        "tags": ["clue", "party_discussion"],
                        "functional": {
                            "type": "investigate_clue",
                            "params": {
                                "clue_id": "herb_patch",
                                "base_effects": [
                                    {"type": "set_flag", "params": {"key": "found_herbs", "value": True}},
                                    {"type": "grant_item", "params": {"item_id": "herb", "count": 3}},
                                ],
                                "check": {"skill": "nature", "dc": 12},
                                "check_effects": [
                                    {"type": "grant_item", "params": {"item_id": "rare_herb", "count": 1}},
                                ],
                                "narrative": "这片药草丛生长在溪边阴凉处。",
                                "hide_on_resolve": True,
                            },
                        },
                    }
                ],
            },
        })
        assert result.ok is True
        interactable = result.payload["interactables"][0]
        assert interactable["id"] == "herb_patch"

    def test_simple_clue_no_check_passes_validation(self) -> None:
        """Simple clue without check field passes contract."""
        result = validate_planner_directive({
            "kind": "fill_location",
            "payload": {
                "area_id": "frontier_town",
                "location_id": "north_gate",
                "interactables": [
                    {
                        "id": "item_clue",
                        "name": "散落物品",
                        "description": "地上散落着些什么。",
                        "type": "inspect",
                        "tags": ["clue"],
                        "functional": {
                            "type": "investigate_clue",
                            "params": {
                                "clue_id": "item_clue",
                                "base_effects": [
                                    {"type": "set_flag", "params": {"key": "found_item", "value": True}},
                                ],
                            },
                        },
                    }
                ],
            },
        })
        assert result.ok is True

    def test_simple_clue_invalid_effect_type_gets_stripped(self) -> None:
        """Simple clue with invalid effect type fails validation and is stripped."""
        result = validate_planner_directive({
            "kind": "fill_location",
            "payload": {
                "area_id": "frontier_town",
                "location_id": "north_gate",
                "interactables": [
                    {
                        "id": "bad_clue",
                        "name": "坏线索",
                        "description": "...",
                        "type": "inspect",
                        "tags": ["clue"],
                        "functional": {
                            "type": "investigate_clue",
                            "params": {
                                "clue_id": "bad_clue",
                                "base_effects": [
                                    {"type": "teleport_player", "params": {}},
                                ],
                            },
                        },
                    }
                ],
            },
        })
        # Invalid effect type → stripped → all_interactables_invalid
        assert result.ok is False
        assert result.reason_code == "all_interactables_invalid"

    def test_legacy_clue_still_passes_validation(self) -> None:
        """Existing legacy schema (options/outcomes) is not broken by 2e changes."""
        result = validate_planner_directive({
            "kind": "fill_location",
            "payload": {
                "area_id": "frontier_town",
                "location_id": "adventurer_guild",
                "interactables": [
                    {
                        "id": "blood_trail_clue",
                        "name": "拖拽血迹",
                        "description": "半干的血迹。",
                        "type": "inspect",
                        "tags": ["clue"],
                        "functional": {
                            "type": "investigate_clue",
                            "params": {
                                "clue_id": "blood_trail",
                                "options": [
                                    {"id": "examine", "label": "仔细检查"},
                                    {"id": "follow", "label": "顺着痕迹追过去"},
                                ],
                                "outcomes": {
                                    "examine": [],
                                    "follow": [],
                                },
                            },
                        },
                    }
                ],
            },
        })
        assert result.ok is True


# ---------------------------------------------------------------------------
# 2f: agent_orchestration._run_clue_investigation_round
# ---------------------------------------------------------------------------

def _make_service():
    """Create AgentOrchestrationService with mock executor."""
    from app.agent_orchestration import AgentOrchestrationService
    from app.game_core.narrative.executor import AgenticExecutor
    dummy_executor = mock.MagicMock(spec=AgenticExecutor)
    return AgentOrchestrationService(executor=dummy_executor)


def _make_pipeline_result(metadata: dict[str, Any]):
    """Build a minimal PipelineResult-like object."""
    from app.game_core.orchestration.models import PipelineResult
    return PipelineResult(
        executed=True,
        action_type="investigate_clue",
        time_cost=1.0 / 6.0,
        metadata=metadata,
    )


def _make_shared_context(
    *,
    player_area: str = "frontier_town",
) -> Any:
    """Build a SharedContext for agent orchestration tests."""
    from app.game_core.orchestration.shared_context import SharedContext
    from app.game_core.rules import RulesEngine
    world = WorldInstance("test_world")
    state = StateContainer()

    player = PlayerSlice()
    player.restore({"current_area": player_area})
    state.register(player)

    areas = AreaSlice()
    areas.restore({"areas": {player_area: {}}})
    state.register(areas)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    rules_engine = RulesEngine()

    return SharedContext(
        world=world,
        state=state,
        rules_engine=rules_engine,
        scene_bus=scene_bus,
    )


def test_run_clue_investigation_round_simple_no_dialogue_options() -> None:
    """Simple schema clue (simple=True) must NOT emit dialogue_options panel."""
    service = _make_service()
    shared = _make_shared_context()

    metadata = {
        "clue_id": "herb_patch",
        "interactable_id": "herb_patch",
        "clue_name": "药草丛",
        "description": "溪边的一片药草丛。",
        "simple": True,
        "has_check": False,
        "check": None,
        "options": [],  # empty — no legacy options
        "area_id": "frontier_town",
        "location_id": "north_gate",
        "room_id": "",
    }
    result = _make_pipeline_result(metadata)

    # Patch LLM-dependent methods to return empty so we only test the panel logic
    with (
        mock.patch.object(service, "_generate_clue_gm_events", return_value=[]) as _,
        mock.patch.object(service, "_generate_clue_teammate_events", return_value=[]) as _,
    ):
        def _run():
            return asyncio.run(service._run_clue_investigation_round(
                shared, result, execute_command=lambda cmd: None,
            ))
        events = _run()

    event_types = [e.event_type for e in events]
    assert "dialogue_options" not in event_types


def test_run_clue_investigation_round_simple_with_check_no_dialogue_options() -> None:
    """Simple schema clue with check (simple=True, has_check=True) must also NOT emit
    runtime dialogue_options — the GM should use suggest_options via its own prompt."""
    service = _make_service()
    shared = _make_shared_context()

    metadata = {
        "clue_id": "herb_patch",
        "interactable_id": "herb_patch",
        "clue_name": "药草丛",
        "description": "溪边的一片药草丛。",
        "simple": True,
        "has_check": True,
        "check": {"skill": "nature", "dc": 12},
        "options": [],
        "area_id": "frontier_town",
        "location_id": "north_gate",
        "room_id": "",
    }
    result = _make_pipeline_result(metadata)

    with (
        mock.patch.object(service, "_generate_clue_gm_events", return_value=[]) as _,
        mock.patch.object(service, "_generate_clue_teammate_events", return_value=[]) as _,
    ):
        def _run():
            return asyncio.run(service._run_clue_investigation_round(
                shared, result, execute_command=lambda cmd: None,
            ))
        events = _run()

    event_types = [e.event_type for e in events]
    assert "dialogue_options" not in event_types


def test_run_clue_investigation_round_legacy_emits_dialogue_options() -> None:
    """Legacy schema clue (simple not set) with options must emit dialogue_options panel."""
    service = _make_service()
    shared = _make_shared_context()

    metadata = {
        "clue_id": "blood_trail",
        "interactable_id": "blood_trail_clue",
        "clue_name": "拖拽血迹",
        "description": "半干的血迹。",
        # simple is False / absent — legacy path
        "has_check": False,
        "check": None,
        "options": [
            {"id": "examine", "label": "仔细检查"},
            {"id": "follow", "label": "追踪"},
        ],
        "area_id": "frontier_town",
        "location_id": "adventurer_guild",
        "room_id": "",
    }
    result = _make_pipeline_result(metadata)

    with (
        mock.patch.object(service, "_generate_clue_gm_events", return_value=[]) as _,
        mock.patch.object(service, "_generate_clue_teammate_events", return_value=[]) as _,
    ):
        def _run():
            return asyncio.run(service._run_clue_investigation_round(
                shared, result, execute_command=lambda cmd: None,
            ))
        events = _run()

    event_types = [e.event_type for e in events]
    assert "dialogue_options" in event_types


def test_clue_payload_has_simple_and_has_check_flags() -> None:
    """clue_payload built in _run_clue_investigation_round must have simple and has_check fields."""
    service = _make_service()
    shared = _make_shared_context()

    captured_payloads: list[dict] = []

    async def _fake_gm_events(shared, result, clue_payload):
        captured_payloads.append(dict(clue_payload))
        return []

    metadata = {
        "clue_id": "herb_patch",
        "interactable_id": "herb_patch",
        "clue_name": "药草丛",
        "description": "药草丛。",
        "simple": True,
        "has_check": True,
        "check": {"skill": "nature", "dc": 12},
        "options": [],
        "area_id": "frontier_town",
        "location_id": "north_gate",
        "room_id": "",
    }
    result = _make_pipeline_result(metadata)

    with (
        mock.patch.object(service, "_generate_clue_gm_events", side_effect=_fake_gm_events) as _,
        mock.patch.object(service, "_generate_clue_teammate_events", return_value=[]) as _,
    ):
        def _run():
            return asyncio.run(service._run_clue_investigation_round(
                shared, result, execute_command=lambda cmd: None,
            ))
        _run()

    assert len(captured_payloads) == 1
    payload = captured_payloads[0]
    assert payload["simple"] is True
    assert payload["has_check"] is True
    assert payload["check"] == {"skill": "nature", "dc": 12}
