"""Tests for Phase 4: content-layer service bootstrap + context_builder merging.

Coverage:
- _bootstrap_content_services(): content services injected into NarrativePlanSlice
- donation entries skipped
- unknown service_id → empty effects list
- idempotency: second call does NOT duplicate services
- planner services override content services with same service_id in role_data
- context_builder _extract_temple_keeper_data() includes effects field
- context_builder merges planner services, planner overrides content

Decision record: D-Svc04 (narrative.md)
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.content.registries.characters import CharacterRegistry
from app.game_core.orchestration.hooks.narrative_planner import (
    NarrativePlannerHook,
    _CONTENT_SERVICE_EFFECTS,
)
from app.game_core.orchestration.models import SSEEvent
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules.defaults import register_default_rules_handlers
from app.game_core.rules.engine import RulesEngine
from app.game_core.state import StateDelta, StateChange, StateContainer
from app.game_core.state.slices import (
    NarrativePlanSlice,
    PlayerSlice,
    SceneSlice,
    TimeSlice,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRIESTESS_DATA = {
    "priestess": {
        "id": "priestess",
        "name": "Test Priestess",
        "tags": ["temple_keeper"],
        "shop": {
            "services": [
                {
                    "service_id": "heal",
                    "label": "治疗祈祷",
                    "price": 25,
                    "notes": "治疗一般伤势。",
                },
                {
                    "service_id": "blessing",
                    "label": "旅途祝福",
                    "price": 15,
                    "notes": "出发前祝福。",
                },
                {
                    "service_id": "donation",
                    "label": "奉献捐赠",
                    "price": 5,
                    "notes": "象征性奉献。",
                },
                {
                    "service_id": "mystery_rite",
                    "label": "神秘仪式",
                    "price": 50,
                    "notes": "未知服务。",
                },
            ]
        },
    }
}


def _make_state_with_narrative() -> StateContainer:
    state = StateContainer()
    np_slice = NarrativePlanSlice()
    np_slice.restore({})
    state.register(np_slice)

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({})
    state.register(player)

    scene = SceneSlice()
    scene.restore({})
    state.register(scene)

    return state


def _make_world_with_characters(char_data: dict[str, Any]) -> WorldInstance:
    world = WorldInstance("test")
    registry = CharacterRegistry()
    registry.load(char_data)
    world.register(registry)
    return world


def _make_context(state: StateContainer, world: WorldInstance) -> SettlementContext:
    engine = RulesEngine()
    register_default_rules_handlers(engine)
    scene_bus = SceneBus(state.scene)
    change_log: list[StateChange] = []

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        change_log.extend(delta.changes)

    return SettlementContext(
        change_log=change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=engine,
        _apply_delta=_apply_delta,
    )


# ---------------------------------------------------------------------------
# Tests: _CONTENT_SERVICE_EFFECTS mapping
# ---------------------------------------------------------------------------

class TestContentServiceEffectsMapping:
    def test_known_service_ids_present(self) -> None:
        expected = {"heal", "blessing", "field_dressing", "trail_prayer"}
        assert expected == set(_CONTENT_SERVICE_EFFECTS.keys())

    def test_heal_restores_hp(self) -> None:
        effects = _CONTENT_SERVICE_EFFECTS["heal"]
        assert len(effects) == 1
        assert effects[0]["type"] == "restore_hp"
        assert effects[0]["amount"] == 30

    def test_blessing_applies_effect(self) -> None:
        effects = _CONTENT_SERVICE_EFFECTS["blessing"]
        assert len(effects) == 1
        assert effects[0]["type"] == "apply_effect"
        assert effects[0]["effect_id"] == "blessed"
        assert effects[0]["duration_ticks"] == 6

    def test_field_dressing_restores_hp(self) -> None:
        effects = _CONTENT_SERVICE_EFFECTS["field_dressing"]
        assert len(effects) == 1
        assert effects[0]["type"] == "restore_hp"
        assert effects[0]["amount"] == 15

    def test_trail_prayer_applies_effect(self) -> None:
        effects = _CONTENT_SERVICE_EFFECTS["trail_prayer"]
        assert len(effects) == 1
        assert effects[0]["type"] == "apply_effect"
        assert effects[0]["effect_id"] == "trail_protection"
        assert effects[0]["duration_ticks"] == 4


# ---------------------------------------------------------------------------
# Tests: _bootstrap_content_services()
# ---------------------------------------------------------------------------

class TestBootstrapContentServices:
    def test_known_services_injected(self) -> None:
        state = _make_state_with_narrative()
        world = _make_world_with_characters(_PRIESTESS_DATA)
        hook = NarrativePlannerHook(planner=None)
        context = _make_context(state, world)

        hook._bootstrap_content_services(context)

        services = state.narrative_plan.get_services("priestess")
        service_ids = {s["service_id"] for s in services}
        # heal, blessing, mystery_rite should be injected; donation skipped
        assert "heal" in service_ids
        assert "blessing" in service_ids
        assert "mystery_rite" in service_ids

    def test_donation_is_skipped(self) -> None:
        state = _make_state_with_narrative()
        world = _make_world_with_characters(_PRIESTESS_DATA)
        hook = NarrativePlannerHook(planner=None)
        context = _make_context(state, world)

        hook._bootstrap_content_services(context)

        services = state.narrative_plan.get_services("priestess")
        service_ids = {s["service_id"] for s in services}
        assert "donation" not in service_ids

    def test_known_service_has_effects(self) -> None:
        state = _make_state_with_narrative()
        world = _make_world_with_characters(_PRIESTESS_DATA)
        hook = NarrativePlannerHook(planner=None)
        context = _make_context(state, world)

        hook._bootstrap_content_services(context)

        services = state.narrative_plan.get_services("priestess")
        heal = next(s for s in services if s["service_id"] == "heal")
        assert heal["effects"] == [{"type": "restore_hp", "amount": 30}]
        assert heal["source"] == "content"

    def test_unknown_service_id_gets_empty_effects(self) -> None:
        state = _make_state_with_narrative()
        world = _make_world_with_characters(_PRIESTESS_DATA)
        hook = NarrativePlannerHook(planner=None)
        context = _make_context(state, world)

        hook._bootstrap_content_services(context)

        services = state.narrative_plan.get_services("priestess")
        mystery = next(s for s in services if s["service_id"] == "mystery_rite")
        assert mystery["effects"] == []
        assert mystery["source"] == "content"

    def test_idempotent_second_call_no_duplicate(self) -> None:
        state = _make_state_with_narrative()
        world = _make_world_with_characters(_PRIESTESS_DATA)
        hook = NarrativePlannerHook(planner=None)
        context = _make_context(state, world)

        hook._bootstrap_content_services(context)
        hook._bootstrap_content_services(context)  # second call

        services = state.narrative_plan.get_services("priestess")
        heal_entries = [s for s in services if s["service_id"] == "heal"]
        assert len(heal_entries) == 1, "heal should appear only once"

    def test_service_price_preserved(self) -> None:
        state = _make_state_with_narrative()
        world = _make_world_with_characters(_PRIESTESS_DATA)
        hook = NarrativePlannerHook(planner=None)
        context = _make_context(state, world)

        hook._bootstrap_content_services(context)

        services = state.narrative_plan.get_services("priestess")
        heal = next(s for s in services if s["service_id"] == "heal")
        assert heal["price"] == 25
        blessing = next(s for s in services if s["service_id"] == "blessing")
        assert blessing["price"] == 15

    def test_no_characters_registry_no_crash(self) -> None:
        """When world has no characters registry, method should no-op gracefully."""
        state = _make_state_with_narrative()
        world = WorldInstance("test")  # no characters registry
        hook = NarrativePlannerHook(planner=None)
        context = _make_context(state, world)

        hook._bootstrap_content_services(context)  # should not raise

    def test_no_narrative_plan_slice_no_crash(self) -> None:
        """When state has no narrative_plan slice, method should no-op gracefully."""
        state = StateContainer()  # no narrative_plan slice
        scene = SceneSlice()
        scene.restore({})
        state.register(scene)
        world = _make_world_with_characters(_PRIESTESS_DATA)
        hook = NarrativePlannerHook(planner=None)
        engine = RulesEngine()
        register_default_rules_handlers(engine)
        scene_bus = SceneBus(scene)

        def _apply_delta(delta: StateDelta | None) -> None:
            if delta:
                state.apply(delta)

        context = SettlementContext(
            change_log=[],
            state=state,
            world=world,
            scene_bus=scene_bus,
            _rules_engine=engine,
            _apply_delta=_apply_delta,
        )
        hook._bootstrap_content_services(context)  # should not raise

    def test_npc_without_shop_skipped(self) -> None:
        """NPCs with no shop.services should produce no entries."""
        char_data = {
            "goblin": {
                "id": "goblin",
                "name": "Goblin",
                "tags": [],
                # no shop key at all
            }
        }
        state = _make_state_with_narrative()
        world = _make_world_with_characters(char_data)
        hook = NarrativePlannerHook(planner=None)
        context = _make_context(state, world)

        hook._bootstrap_content_services(context)

        services = state.narrative_plan.get_services("goblin")
        assert services == []


# ---------------------------------------------------------------------------
# Tests: context_builder _extract_temple_keeper_data() merging
# ---------------------------------------------------------------------------

class TestContextBuilderServiceMerging:
    """Test _extract_temple_keeper_data() merging via public entry point.

    We test through the module-level function directly rather than through
    the full AgentContextBuilder stack to keep tests focused and fast.
    """

    def test_content_services_include_effects_field(self) -> None:
        """Services from content layer should carry effects after bootstrap."""
        from app.game_core.narrative.context_builder import _extract_temple_keeper_data

        state = _make_state_with_narrative()
        world = _make_world_with_characters(_PRIESTESS_DATA)

        # Bootstrap first so planner slice has content services
        hook = NarrativePlannerHook(planner=None)
        context = _make_context(state, world)
        hook._bootstrap_content_services(context)

        role_data = _extract_temple_keeper_data("priestess", state, world)
        services = role_data["services"]
        # heal should be present with an effects field
        heal = next((s for s in services if s["service_id"] == "heal"), None)
        assert heal is not None, "heal service should be in role_data"
        assert "effects" in heal
        assert heal["effects"] == [{"type": "restore_hp", "amount": 30}]

    def test_content_services_present_without_bootstrap(self) -> None:
        """Even without bootstrap, content-layer services should appear (from shop data)."""
        from app.game_core.narrative.context_builder import _extract_temple_keeper_data

        state = _make_state_with_narrative()
        world = _make_world_with_characters(_PRIESTESS_DATA)

        role_data = _extract_temple_keeper_data("priestess", state, world)
        services = role_data["services"]
        service_ids = {s["service_id"] for s in services}
        # heal, blessing, donation, mystery_rite all from content layer
        assert "heal" in service_ids

    def test_planner_service_overrides_content_service(self) -> None:
        """A planner-assigned service with same service_id should override content entry."""
        from app.game_core.narrative.context_builder import _extract_temple_keeper_data

        state = _make_state_with_narrative()
        world = _make_world_with_characters(_PRIESTESS_DATA)

        # Assign a planner override for 'heal' with custom effects
        custom_effects = [{"type": "restore_hp", "amount": 100}]
        state.narrative_plan.assign_service("priestess", {
            "service_id": "heal",
            "npc_id": "priestess",
            "label": "奇迹治愈",
            "price": 50,
            "effects": custom_effects,
            "preconditions": {},
            "notes": "增强版治疗",
            "assigned_tick": 5,
            "expiry_tick": 0,
            "source": "planner",
            "one_shot": False,
        })

        role_data = _extract_temple_keeper_data("priestess", state, world)
        services = role_data["services"]
        heal = next((s for s in services if s["service_id"] == "heal"), None)
        assert heal is not None
        # planner entry should win
        assert heal["effects"] == custom_effects
        assert heal["price"] == 50
        assert heal["source"] == "planner"

    def test_planner_service_added_to_content_services(self) -> None:
        """A planner-assigned new service should appear alongside content services."""
        from app.game_core.narrative.context_builder import _extract_temple_keeper_data

        state = _make_state_with_narrative()
        world = _make_world_with_characters(_PRIESTESS_DATA)

        # Planner adds a brand-new service
        state.narrative_plan.assign_service("priestess", {
            "service_id": "resurrection",
            "npc_id": "priestess",
            "label": "复活",
            "price": 200,
            "effects": [{"type": "restore_hp", "amount": 999}],
            "preconditions": {},
            "notes": "仅在极端情况下",
            "assigned_tick": 1,
            "expiry_tick": 0,
            "source": "planner",
            "one_shot": True,
        })

        role_data = _extract_temple_keeper_data("priestess", state, world)
        services = role_data["services"]
        service_ids = {s["service_id"] for s in services}
        assert "resurrection" in service_ids
        # Content services also still present
        assert "heal" in service_ids

    def test_role_data_role_field(self) -> None:
        """role_data should identify role as temple_keeper."""
        from app.game_core.narrative.context_builder import _extract_temple_keeper_data

        state = _make_state_with_narrative()
        world = _make_world_with_characters(_PRIESTESS_DATA)

        role_data = _extract_temple_keeper_data("priestess", state, world)
        assert role_data["role"] == "temple_keeper"
