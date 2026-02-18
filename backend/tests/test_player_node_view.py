"""Tests for PlayerNodeView adapter and translate_character_to_node().

Covers:
  - Property read/write + dirty marking
  - add_item / remove_item / has_item
  - to_summary_text / to_combat_player_state
  - model_dump compatibility with PlayerCharacter format
  - spell_slots int/str key conversion
  - translate_character_to_node round-trip
"""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# Stub out 'mcp' package if not installed
if "mcp" not in sys.modules:
    _mcp_stub = ModuleType("mcp")
    _mcp_client = ModuleType("mcp.client")
    _mcp_session = ModuleType("mcp.client.session")
    _mcp_session.ClientSession = MagicMock
    _mcp_stdio = ModuleType("mcp.client.stdio")
    _mcp_stdio.stdio_client = MagicMock
    _mcp_stdio.StdioServerParameters = MagicMock
    _mcp_sse = ModuleType("mcp.client.sse")
    _mcp_sse.sse_client = MagicMock
    _mcp_http = ModuleType("mcp.client.streamable_http")
    _mcp_http.streamable_http_client = MagicMock
    _mcp_types = ModuleType("mcp.types")
    _mcp_types.Tool = MagicMock

    sys.modules["mcp"] = _mcp_stub
    sys.modules["mcp.client"] = _mcp_client
    sys.modules["mcp.client.session"] = _mcp_session
    sys.modules["mcp.client.stdio"] = _mcp_stdio
    sys.modules["mcp.client.sse"] = _mcp_sse
    sys.modules["mcp.client.streamable_http"] = _mcp_http
    sys.modules["mcp.types"] = _mcp_types

from app.world.constants import default_player_state
from app.world.models import WorldNode, WorldNodeType
from app.world.player_node import PlayerNodeView, translate_character_to_node
from app.world.world_graph import WorldGraph


# =============================================================================
# Fixtures
# =============================================================================


def _make_player_node_and_graph() -> tuple[WorldNode, WorldGraph]:
    """Create a minimal WorldGraph with a player node."""
    wg = WorldGraph()
    state = default_player_state()
    state.update({
        "hp": 25,
        "max_hp": 30,
        "level": 3,
        "xp": 500,
        "ac": 15,
        "gold": 42,
        "abilities": {"str": 16, "dex": 14, "con": 12, "int": 10, "wis": 8, "cha": 13},
        "inventory": [
            {"item_id": "sword", "name": "Iron Sword", "quantity": 1},
        ],
        "spell_slots_max": {"1": 3, "2": 1},
        "spell_slots_used": {"1": 1},
        "equipment": {"main_hand": "sword", "armor": None},
    })
    node = WorldNode(
        id="player",
        type=WorldNodeType.PLAYER,
        name="TestHero",
        properties={
            "race": "human",
            "character_class": "fighter",
            "background": "soldier",
            "backstory": "A brave warrior.",
        },
        state=state,
    )
    wg.add_node(node)
    return node, wg


def _make_player_character():
    """Create a PlayerCharacter instance for translate tests."""
    from app.models.player_character import PlayerCharacter, CharacterRace, CharacterClass
    return PlayerCharacter(
        character_id="player",
        name="TranslateHero",
        race=CharacterRace.ELF,
        character_class=CharacterClass.MAGE,
        background="sage",
        backstory="A wise elf.",
        level=5,
        xp=2000,
        xp_to_next_level=6500,
        abilities={"str": 8, "dex": 14, "con": 10, "int": 18, "wis": 12, "cha": 10},
        max_hp=28,
        current_hp=20,
        ac=12,
        initiative_bonus=2,
        proficiency_bonus=3,
        speed=30,
        gold=100,
        spell_slots={1: 4, 2: 3},
        spell_slots_used={1: 2},
        inventory=[{"item_id": "staff", "name": "Oak Staff", "quantity": 1}],
        weapon_proficiencies=["dagger"],
        feats=["war_caster"],
    )


# =============================================================================
# Tests: Property read/write + dirty
# =============================================================================


class TestPlayerNodeViewProperties:

    def test_read_identity(self):
        node, wg = _make_player_node_and_graph()
        view = PlayerNodeView(node, wg)
        assert view.character_id == "player"
        assert view.name == "TestHero"
        assert view.race == "human"
        assert view.character_class == "fighter"
        assert view.background == "soldier"

    def test_read_state_values(self):
        node, wg = _make_player_node_and_graph()
        view = PlayerNodeView(node, wg)
        assert view.current_hp == 25
        assert view.max_hp == 30
        assert view.level == 3
        assert view.xp == 500
        assert view.ac == 15
        assert view.gold == 42

    def test_write_marks_dirty(self):
        node, wg = _make_player_node_and_graph()
        view = PlayerNodeView(node, wg)
        assert "player" not in wg._dirty_nodes

        view.current_hp = 10
        assert node.state["hp"] == 10
        assert "player" in wg._dirty_nodes

    def test_write_multiple_fields(self):
        node, wg = _make_player_node_and_graph()
        view = PlayerNodeView(node, wg)

        view.xp = 999
        view.gold = 200
        view.level = 5

        assert node.state["xp"] == 999
        assert node.state["gold"] == 200
        assert node.state["level"] == 5

    def test_spell_slots_int_str_conversion(self):
        node, wg = _make_player_node_and_graph()
        view = PlayerNodeView(node, wg)

        # Read: str keys → int keys
        slots = view.spell_slots
        assert slots == {1: 3, 2: 1}
        assert all(isinstance(k, int) for k in slots)

        # Write: int keys → str keys in state
        view.spell_slots = {1: 4, 2: 2, 3: 1}
        assert node.state["spell_slots_max"] == {"1": 4, "2": 2, "3": 1}


# =============================================================================
# Tests: Inventory methods
# =============================================================================


class TestPlayerNodeViewInventory:

    def test_add_item_new(self):
        node, wg = _make_player_node_and_graph()
        view = PlayerNodeView(node, wg)

        result = view.add_item("potion", "Healing Potion", 3)
        assert result["item_id"] == "potion"
        assert result["quantity"] == 3
        assert len(view.inventory) == 2
        assert "player" in wg._dirty_nodes

    def test_add_item_stack(self):
        node, wg = _make_player_node_and_graph()
        view = PlayerNodeView(node, wg)

        result = view.add_item("sword", "Iron Sword", 2)
        assert result["quantity"] == 3  # 1 + 2

    def test_remove_item_success(self):
        node, wg = _make_player_node_and_graph()
        view = PlayerNodeView(node, wg)

        assert view.remove_item("sword") is True
        assert not view.has_item("sword")

    def test_remove_item_not_found(self):
        node, wg = _make_player_node_and_graph()
        view = PlayerNodeView(node, wg)

        assert view.remove_item("nonexistent") is False

    def test_has_item(self):
        node, wg = _make_player_node_and_graph()
        view = PlayerNodeView(node, wg)

        assert view.has_item("sword") is True
        assert view.has_item("shield") is False


# =============================================================================
# Tests: to_summary_text / to_combat_player_state
# =============================================================================


class TestPlayerNodeViewMethods:

    def test_to_summary_text(self):
        node, wg = _make_player_node_and_graph()
        view = PlayerNodeView(node, wg)

        text = view.to_summary_text()
        assert "TestHero" in text
        assert "human" in text
        assert "fighter" in text
        assert "Lv3" in text
        assert "HP:25/30" in text
        assert "AC:15" in text

    def test_to_combat_player_state(self):
        node, wg = _make_player_node_and_graph()
        view = PlayerNodeView(node, wg)

        combat = view.to_combat_player_state()
        assert combat["name"] == "TestHero"
        assert combat["hp"] == 25
        assert combat["max_hp"] == 30
        assert combat["ac"] == 15
        assert combat["level"] == 3
        assert combat["class"] == "fighter"
        assert combat["race"] == "human"
        assert isinstance(combat["abilities"], dict)

    def test_ability_modifier(self):
        node, wg = _make_player_node_and_graph()
        view = PlayerNodeView(node, wg)

        assert view.ability_modifier("str") == 3   # (16 - 10) // 2
        assert view.ability_modifier("dex") == 2   # (14 - 10) // 2
        assert view.ability_modifier("wis") == -1   # (8 - 10) // 2


# =============================================================================
# Tests: model_dump compatibility
# =============================================================================


class TestPlayerNodeViewModelDump:

    def test_model_dump_fields(self):
        node, wg = _make_player_node_and_graph()
        view = PlayerNodeView(node, wg)

        dump = view.model_dump()
        assert dump["character_id"] == "player"
        assert dump["name"] == "TestHero"
        assert dump["race"] == "human"
        assert dump["character_class"] == "fighter"
        assert dump["current_hp"] == 25
        assert dump["max_hp"] == 30
        assert dump["gold"] == 42
        # spell_slots should have int keys
        assert dump["spell_slots"] == {1: 3, 2: 1}

    def test_model_dump_round_trip(self):
        """translate → build node → PlayerNodeView.model_dump() ≈ original."""
        pc = _make_player_character()
        state, props = translate_character_to_node(pc)

        wg = WorldGraph()
        node = WorldNode(
            id="player",
            type=WorldNodeType.PLAYER,
            name=pc.name,
            properties=props,
            state=state,
        )
        wg.add_node(node)

        view = PlayerNodeView(node, wg)
        dump = view.model_dump()

        assert dump["name"] == "TranslateHero"
        assert dump["race"] == "elf"
        assert dump["character_class"] == "mage"
        assert dump["current_hp"] == 20
        assert dump["max_hp"] == 28
        assert dump["level"] == 5
        assert dump["xp"] == 2000
        assert dump["gold"] == 100
        assert dump["spell_slots"] == {1: 4, 2: 3}
        assert dump["spell_slots_used"] == {1: 2}
        assert len(dump["inventory"]) == 1
        assert dump["weapon_proficiencies"] == ["dagger"]
        assert dump["feats"] == ["war_caster"]


# =============================================================================
# Tests: translate_character_to_node
# =============================================================================


class TestTranslateCharacterToNode:

    def test_state_fields(self):
        pc = _make_player_character()
        state, props = translate_character_to_node(pc)

        assert state["hp"] == 20  # current_hp → hp
        assert state["max_hp"] == 28
        assert state["level"] == 5
        assert state["xp"] == 2000
        assert state["gold"] == 100
        assert state["ac"] == 12

    def test_spell_slots_conversion(self):
        pc = _make_player_character()
        state, _ = translate_character_to_node(pc)

        # int keys → str keys
        assert state["spell_slots_max"] == {"1": 4, "2": 3}
        assert state["spell_slots_used"] == {"1": 2}

    def test_properties(self):
        pc = _make_player_character()
        _, props = translate_character_to_node(pc)

        assert props["race"] == "elf"
        assert props["character_class"] == "mage"
        assert props["background"] == "sage"
        assert props["backstory"] == "A wise elf."

    def test_default_values_preserved(self):
        """Fields not in PlayerCharacter should have defaults from default_player_state."""
        pc = _make_player_character()
        state, _ = translate_character_to_node(pc)

        # These come from default_player_state
        assert "active_quests" in state
        assert "rest_state" in state
        assert state["inspiration"] is False
