"""Tests for ContextAssembler — L0-L7 layer assembly."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry
from app.game_core.content.world import WorldInstance
from app.game_core.orchestration.context_assembler import ContextAssembler
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.shared_context import SharedContext
from app.game_core.rules import RulesEngine
from app.game_core.state.base import StateContainer, StateSlice
from app.game_core.state.slices import SceneSlice
from app.game_core.state.slices.area import AreaSlice
from app.game_core.state.slices.events import EventSlice
from app.game_core.state.slices.flags import FlagSlice
from app.game_core.state.slices.party import PartySlice
from app.game_core.state.slices.player import PlayerSlice
from app.game_core.state.slices.quests import QuestSlice
from app.game_core.state.slices.relations import RelationSlice
from app.game_core.state.slices.time import TimeSlice
from app.game_core.state.slices.narrative_plan import NarrativePlanSlice


# -- Helpers ------------------------------------------------------------------


class StubRegistry(ContentRegistry):
    """Minimal registry for testing."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._items: dict[str, dict[str, Any]] = {}

    def load(self, data: dict[str, Any]) -> None:
        self._items = self._coerce_dict_mapping(data)

    def get(self, content_id: str) -> Any | None:
        item = self._items.get(content_id)
        return dict(item) if isinstance(item, dict) else item

    def list_all(self) -> list[Any]:
        return [dict(v) for v in self._items.values()]


def _make_shared(
    *,
    slices: list[StateSlice] | None = None,
    registries: list[ContentRegistry] | None = None,
    world_id: str = "test_world",
) -> SharedContext:
    """Build a SharedContext with given slices and registries."""
    world = WorldInstance(world_id)
    for reg in (registries or []):
        world.register(reg)

    state = StateContainer()
    for sl in (slices or []):
        state.register(sl)

    scene_slice = SceneSlice()
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    return SharedContext(
        world=world,
        state=state,
        rules_engine=RulesEngine(),
        scene_bus=scene_bus,
    )


# -- Tests --------------------------------------------------------------------


class TestLayerKeys:
    """All 8 layer keys must always be present."""

    EXPECTED_KEYS = {
        "l0_world_constants",
        "l1_chapter_state",
        "l2_area_environment",
        "l3_location_details",
        "l4_dynamic_state",
        "l5_scene_bus",
        "l6_memory_recall",
        "l7_engine_result",
    }

    def test_empty_containers(self) -> None:
        shared = _make_shared()
        result = ContextAssembler().assemble(shared)
        assert set(result.keys()) == self.EXPECTED_KEYS

    def test_full_slices_and_registries(self) -> None:
        lore = StubRegistry("lore")
        lore.load({"entry1": {"id": "entry1", "text": "Ancient lore"}})
        factions = StubRegistry("factions")
        factions.load({"guild": {"id": "guild", "name": "Adventurer Guild"}})
        maps = StubRegistry("maps")
        maps.load({
            "town_square": {
                "id": "town_square",
                "name": "Town Square",
                "sub_locations": {
                    "tavern": {"id": "tavern", "name": "Rusty Dragon Tavern"},
                },
            },
        })

        slices: list[StateSlice] = [
            TimeSlice(),
            PlayerSlice(),
            AreaSlice(),
            QuestSlice(),
            RelationSlice(),
            FlagSlice(),
            PartySlice(),
            EventSlice(),
            NarrativePlanSlice(),
        ]

        shared = _make_shared(
            slices=slices,
            registries=[lore, factions, maps],
        )
        result = ContextAssembler().assemble(shared)
        assert set(result.keys()) == self.EXPECTED_KEYS
        # L0 has actual content
        assert result["l0_world_constants"]["world_id"] == "test_world"
        assert len(result["l0_world_constants"]["lore"]) == 1
        assert len(result["l0_world_constants"]["factions"]) == 1


class TestL0WorldConstants:
    def test_includes_lore_and_factions(self) -> None:
        lore = StubRegistry("lore")
        lore.load({"a": {"id": "a", "text": "lore A"}})
        factions = StubRegistry("factions")
        factions.load({"f1": {"id": "f1", "name": "The Order"}})
        shared = _make_shared(registries=[lore, factions])
        l0 = ContextAssembler().assemble(shared)["l0_world_constants"]
        assert l0["world_id"] == "test_world"
        assert l0["lore"] == [{"id": "a", "text": "lore A"}]
        assert l0["factions"] == [{"id": "f1", "name": "The Order"}]

    def test_missing_registries(self) -> None:
        shared = _make_shared()
        l0 = ContextAssembler().assemble(shared)["l0_world_constants"]
        assert l0 == {"world_id": "test_world", "lore": [], "factions": []}


class TestL1ChapterState:
    def test_includes_quests_and_narrative_plan(self) -> None:
        shared = _make_shared(slices=[QuestSlice(), NarrativePlanSlice()])
        l1 = ContextAssembler().assemble(shared)["l1_chapter_state"]
        assert set(l1.keys()) == {
            "chapter_completion",
            "available_milestones",
            "milestone_states",
            "active_dynamic_quests",
            "current_chapter",
            "current_target_milestone",
            "escalation_level",
            "strategy_notes",
        }

    def test_empty_when_no_slices(self) -> None:
        shared = _make_shared()
        l1 = ContextAssembler().assemble(shared)["l1_chapter_state"]
        assert l1 == {
            "chapter_completion": {},
            "available_milestones": [],
            "milestone_states": {},
            "active_dynamic_quests": [],
            "current_chapter": "",
            "current_target_milestone": None,
            "escalation_level": 0,
            "strategy_notes": "",
        }


class TestL2AreaEnvironment:
    def test_current_area_with_template_and_state(self) -> None:
        maps = StubRegistry("maps")
        maps.load({
            "forest": {"id": "forest", "name": "Dark Forest", "danger": "high"},
        })
        player = PlayerSlice()
        player.restore({"current_area": "forest"})
        area = AreaSlice()
        area.set_exploration("forest", "discovered")

        shared = _make_shared(slices=[player, area], registries=[maps])
        l2 = ContextAssembler().assemble(shared)["l2_area_environment"]
        assert l2["area_id"] == "forest"
        assert l2["template"]["name"] == "Dark Forest"
        assert l2["state"]["exploration"] == "discovered"

    def test_empty_when_no_current_area(self) -> None:
        player = PlayerSlice()
        player.restore({"current_area": ""})
        shared = _make_shared(slices=[player])
        l2 = ContextAssembler().assemble(shared)["l2_area_environment"]
        assert l2 == {
            "area_id": "",
            "template": None,
            "state": None,
            "dynamic_sub_area_counts": None,
        }

    def test_no_player_slice(self) -> None:
        shared = _make_shared()
        l2 = ContextAssembler().assemble(shared)["l2_area_environment"]
        assert l2 == {
            "area_id": "",
            "template": None,
            "state": None,
            "dynamic_sub_area_counts": None,
        }


class TestL3LocationDetails:
    def test_sub_location_from_template(self) -> None:
        maps = StubRegistry("maps")
        maps.load({
            "town": {
                "id": "town",
                "name": "Town",
                "sub_locations": {
                    "inn": {"id": "inn", "name": "Sleeping Giant Inn"},
                },
            },
        })
        player = PlayerSlice()
        player.restore({"current_area": "town", "current_location": "inn"})

        shared = _make_shared(slices=[player], registries=[maps])
        l3 = ContextAssembler().assemble(shared)["l3_location_details"]
        assert l3["location_id"] == "inn"
        assert l3["template"]["name"] == "Sleeping Giant Inn"

    def test_no_current_location(self) -> None:
        player = PlayerSlice()
        player.restore({"current_area": "town"})
        shared = _make_shared(slices=[player])
        l3 = ContextAssembler().assemble(shared)["l3_location_details"]
        assert l3 == {
            "location_id": None,
            "template": None,
            "is_dynamic": False,
            "area_exploration": None,
            "discovered_items": [],
            "dynamic_sub_areas": [],
        }


class TestL4DynamicState:
    def test_includes_all_dynamic_slices(self) -> None:
        slices: list[StateSlice] = [
            TimeSlice(),
            PlayerSlice(),
            RelationSlice(),
            FlagSlice(),
            PartySlice(),
            EventSlice(),
        ]
        shared = _make_shared(slices=slices)
        l4 = ContextAssembler().assemble(shared)["l4_dynamic_state"]
        assert set(l4.keys()) == {"time", "player", "relations", "flags", "party"}

    def test_partial_slices(self) -> None:
        shared = _make_shared(slices=[TimeSlice()])
        l4 = ContextAssembler().assemble(shared)["l4_dynamic_state"]
        assert set(l4.keys()) == {"time", "player", "relations", "flags", "party"}
        assert l4["time"] is not None
        assert l4["player"] is None
        assert l4["relations"] is None
        assert l4["flags"] is None
        assert l4["party"] is None


class TestL5SceneBus:
    def test_returns_scene_snapshot(self) -> None:
        shared = _make_shared()
        l5 = ContextAssembler().assemble(shared)["l5_scene_bus"]
        assert "entries" in l5
        assert "state_changes" in l5


class TestL6MemoryRecall:
    def test_always_empty_stub(self) -> None:
        shared = _make_shared()
        l6 = ContextAssembler().assemble(shared)["l6_memory_recall"]
        assert l6 == {"hits": [], "source": "stub"}


class TestL7EngineResult:
    def test_stable_empty_shape_before_pipeline_fills(self) -> None:
        shared = _make_shared()
        l7 = ContextAssembler().assemble(shared)["l7_engine_result"]
        assert l7 == {
            "executed": None,
            "narrative_hints": [],
            "rolls": [],
            "time_cost": 0.0,
        }
