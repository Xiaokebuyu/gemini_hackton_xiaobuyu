"""Tests for P32 patch: plant_environmental parent_location_id/parent_room_id.

Covers:
- C1: DynamicSubAreaManager.create() output includes parent_location_id/parent_room_id
- C2: PlannerWorldHandler._compute_plant_environmental passes parent location from
      cmd.params or player state into the created sub-area dict
- C3: WorldBuilderSubSystem._apply_plant_environmental (Path B) injects parent
      location from payload or player state before calling execute_command
- C4: directive_contracts validates plant_environmental with optional location_id/room_id
"""
from __future__ import annotations

from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.orchestration.models import SSEEvent
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning import DynamicSubAreaManager
from app.game_core.planning.directive_contracts import validate_planner_directive
from app.game_core.planning.world_builder import WorldBuilderSubSystem
from app.game_core.rules import RulesEngine, register_default_rules_handlers
from app.game_core.rules.handlers.planner import PlannerWorldHandler
from app.game_core.rules.models import Command
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
# Helpers
# ---------------------------------------------------------------------------


def _make_context(
    *,
    player_area: str = "frontier_town",
    player_location: str | None = "north_gate",
    player_room: str | None = "gate_plaza",
    area_payload: dict[str, Any] | None = None,
) -> SettlementContext:
    """Build a minimal SettlementContext for WorldBuilder directive testing."""
    world = WorldInstance("test_world")
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({
        "current_area": player_area,
        "current_location": player_location,
        "current_room": player_room,
    })
    state.register(player)

    quests = QuestSlice()
    quests.restore({"milestone_states": {}, "dynamic_quests": {}})
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({})
    state.register(narrative_plan)

    if area_payload is None:
        area_payload = {"areas": {player_area: {}}}
    areas = AreaSlice()
    areas.restore(area_payload)
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


def _make_state(
    *,
    player_location: str | None = "north_gate",
    player_room: str | None = None,
) -> StateContainer:
    state = StateContainer()

    quests = QuestSlice()
    quests.restore({"milestone_states": {}, "dynamic_quests": {}, "chapter_completion": {}})
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({})
    state.register(narrative_plan)

    areas = AreaSlice()
    areas.restore({"areas": {"frontier_town": {}}})
    state.register(areas)

    flags = FlagSlice()
    flags.restore({})
    state.register(flags)

    player = PlayerSlice()
    player.restore({
        "current_area": "frontier_town",
        "current_location": player_location,
        "current_room": player_room,
    })
    state.register(player)

    return state


# ---------------------------------------------------------------------------
# C1: DynamicSubAreaManager.create() includes parent_location_id / parent_room_id
# ---------------------------------------------------------------------------


class TestC1DynamicSubAreaManagerParentLocation:
    def test_create_with_parent_location_id_included(self) -> None:
        """create() output must include parent_location_id when spec provides it."""
        areas = AreaSlice()
        areas.restore({"areas": {"frontier_town": {}}})
        manager = DynamicSubAreaManager(areas)

        result = manager.create(
            "frontier_town",
            {
                "id": "incense_burner",
                "label": "香炉",
                "description": "神殿香炉",
                "parent_location_id": "temple",
                "parent_room_id": "inner_sanctum",
            },
        )

        assert result is not None
        assert result["parent_location_id"] == "temple"
        assert result["parent_room_id"] == "inner_sanctum"

    def test_create_without_parent_location_defaults_to_none(self) -> None:
        """create() without parent location fields defaults to None (backward compat)."""
        areas = AreaSlice()
        areas.restore({"areas": {"frontier_town": {}}})
        manager = DynamicSubAreaManager(areas)

        result = manager.create(
            "frontier_town",
            {"id": "standalone", "label": "standalone area"},
        )

        assert result is not None
        assert result["parent_location_id"] is None
        assert result["parent_room_id"] is None

    def test_create_with_parent_location_id_only(self) -> None:
        """parent_room_id is independently optional."""
        areas = AreaSlice()
        areas.restore({"areas": {"frontier_town": {}}})
        manager = DynamicSubAreaManager(areas)

        result = manager.create(
            "frontier_town",
            {"id": "courtyard", "label": "内院", "parent_location_id": "temple"},
        )

        assert result is not None
        assert result["parent_location_id"] == "temple"
        assert result["parent_room_id"] is None


# ---------------------------------------------------------------------------
# C2: PlannerWorldHandler._compute_plant_environmental passes parent location
# ---------------------------------------------------------------------------


class TestC2PlannerHandlerParentLocation:
    def test_explicit_location_id_becomes_parent_location_id(self) -> None:
        """Explicit location_id in params is stored as parent_location_id in sub-area."""
        world = WorldInstance("test_world")
        state = _make_state()
        handler = PlannerWorldHandler()

        result = handler.compute(
            Command(
                type="planner_plant_environmental",
                params={
                    "area_id": "frontier_town",
                    "clue_id": "brazier_env",
                    "label": "神殿香炉",
                    "description": "香炉",
                    "location_id": "temple",
                    "room_id": "inner_sanctum",
                    "current_tick": 5,
                },
                source="narrative_planner",
            ),
            state,
            world,
        )

        assert result.executed
        sub_area = result.metadata.get("sub_area") if isinstance(result.metadata, dict) else {}
        assert isinstance(sub_area, dict)
        assert sub_area.get("parent_location_id") == "temple"
        assert sub_area.get("parent_room_id") == "inner_sanctum"

    def test_player_location_falls_back_to_parent_location_id(self) -> None:
        """When no explicit location_id, player's current_location is used."""
        world = WorldInstance("test_world")
        state = _make_state(player_location="north_gate", player_room="gate_plaza")
        handler = PlannerWorldHandler()

        result = handler.compute(
            Command(
                type="planner_plant_environmental",
                params={
                    "area_id": "frontier_town",
                    "clue_id": "env_marker",
                    "label": "路标",
                    "description": "路标",
                    "current_tick": 7,
                },
                source="narrative_planner",
            ),
            state,
            world,
        )

        assert result.executed
        sub_area = result.metadata.get("sub_area") if isinstance(result.metadata, dict) else {}
        assert isinstance(sub_area, dict)
        assert sub_area.get("parent_location_id") == "north_gate"
        assert sub_area.get("parent_room_id") == "gate_plaza"

    def test_no_player_slice_parent_location_is_none(self) -> None:
        """Without player slice, parent_location_id defaults to None."""
        world = WorldInstance("test_world")
        # Build state without player slice
        state = StateContainer()
        quests = QuestSlice()
        quests.restore({"milestone_states": {}, "dynamic_quests": {}, "chapter_completion": {}})
        state.register(quests)
        narrative_plan = NarrativePlanSlice()
        narrative_plan.restore({})
        state.register(narrative_plan)
        areas = AreaSlice()
        areas.restore({"areas": {"frontier_town": {}}})
        state.register(areas)
        flags = FlagSlice()
        flags.restore({})
        state.register(flags)

        handler = PlannerWorldHandler()
        result = handler.compute(
            Command(
                type="planner_plant_environmental",
                params={
                    "area_id": "frontier_town",
                    "clue_id": "env_no_player",
                    "label": "孤岛",
                    "description": "孤岛",
                    "current_tick": 3,
                },
                source="narrative_planner",
            ),
            state,
            world,
        )

        assert result.executed
        sub_area = result.metadata.get("sub_area") if isinstance(result.metadata, dict) else {}
        assert sub_area.get("parent_location_id") is None


# ---------------------------------------------------------------------------
# C3: WorldBuilderSubSystem Path B injects parent_location from state
# ---------------------------------------------------------------------------


class TestC3WorldBuilderPathBParentLocation:
    def test_path_b_injects_parent_location_from_player_state(self) -> None:
        """Path B (sub-area creation) injects parent_location_id from player position.

        Path B is triggered when there's no clue_id — the directive is a pure
        atmospheric element (label/description only), not a clue interactable.
        _translate_environmental_clue_payload returns None, and we fall through
        to the planner_plant_environmental command.
        """
        sse: list[SSEEvent] = []
        builder = WorldBuilderSubSystem(sse_collector=sse)
        context = _make_context(
            player_location="temple",
            player_room="inner_sanctum",
        )

        ok = builder.apply_directive(
            "plant_environmental",
            {
                "area_id": "frontier_town",
                # No clue_id: this triggers Path B (sub-area creation)
                "label": "神殿香炉",
                "description": "香炉",
                "dc": 10,
            },
            context,
            current_tick=4,
        )

        assert ok is True
        sub_areas = context.state.areas.list_temporary_sub_areas("frontier_town")
        assert len(sub_areas) == 1
        assert sub_areas[0].get("parent_location_id") == "temple"
        assert sub_areas[0].get("parent_room_id") == "inner_sanctum"

    def test_path_b_explicit_location_in_payload_takes_precedence(self) -> None:
        """Explicit location_id in directive payload overrides player position.

        Path B is triggered when no clue_id. The location_id in payload is
        passed through and stored as parent_location_id, overriding player's
        current location ("tavern").
        """
        sse: list[SSEEvent] = []
        builder = WorldBuilderSubSystem(sse_collector=sse)
        # Player is at "tavern" but directive specifies "temple"
        context = _make_context(player_location="tavern", player_room=None)

        ok = builder.apply_directive(
            "plant_environmental",
            {
                "area_id": "frontier_town",
                # No clue_id: triggers Path B
                "label": "神殿遗物",
                "description": "古老遗物",
                "location_id": "temple",
                "room_id": "altar_room",
                "dc": 12,
            },
            context,
            current_tick=6,
        )

        assert ok is True
        sub_areas = context.state.areas.list_temporary_sub_areas("frontier_town")
        assert len(sub_areas) == 1
        assert sub_areas[0].get("parent_location_id") == "temple"
        assert sub_areas[0].get("parent_room_id") == "altar_room"


# ---------------------------------------------------------------------------
# C4: directive_contracts allows optional location_id / room_id in plant_environmental
# ---------------------------------------------------------------------------


class TestC4DirectiveContractsLocationId:
    def test_plant_environmental_with_location_id_passes_validation(self) -> None:
        """plant_environmental with location_id should pass contract validation."""
        result = validate_planner_directive({
            "kind": "plant_environmental",
            "payload": {
                "area_id": "frontier_town",
                "clue_id": "brazier_env",
                "description": "香炉",
                "location_id": "temple",
                "room_id": "inner_sanctum",
            },
        })
        assert result.ok is True
        assert result.payload.get("location_id") == "temple"
        assert result.payload.get("room_id") == "inner_sanctum"

    def test_plant_environmental_with_location_id_only_passes(self) -> None:
        """plant_environmental with location_id but no room_id should pass."""
        result = validate_planner_directive({
            "kind": "plant_environmental",
            "payload": {
                "area_id": "frontier_town",
                "description": "环境描述",
                "location_id": "north_gate",
            },
        })
        assert result.ok is True
        assert result.payload.get("location_id") == "north_gate"
        assert "room_id" not in result.payload

    def test_plant_environmental_without_location_id_is_still_valid(self) -> None:
        """plant_environmental without location_id passes as before (backward compat)."""
        result = validate_planner_directive({
            "kind": "plant_environmental",
            "payload": {
                "area_id": "frontier_town",
                "description": "神秘痕迹",
            },
        })
        assert result.ok is True
        assert "location_id" not in result.payload
        assert "room_id" not in result.payload

    def test_plant_environmental_empty_location_id_stripped(self) -> None:
        """Empty string location_id is treated as absent (stripped from payload)."""
        result = validate_planner_directive({
            "kind": "plant_environmental",
            "payload": {
                "area_id": "frontier_town",
                "description": "神秘痕迹",
                "location_id": "   ",
            },
        })
        assert result.ok is True
        assert "location_id" not in result.payload
