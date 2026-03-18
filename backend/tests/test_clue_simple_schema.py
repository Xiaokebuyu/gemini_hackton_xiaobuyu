"""Tests for simplified clue schema (Group 2: 2a + 2b + 2c + 2j).

Covers:
- normalize_clue_definition: simple schema detection and normalization
- validate_clue_definition: simple schema validation branch
- _compute_investigate_simple: base_effects triggered, narrative injected to area_events
- _compute_apply_check_result: grade-based check_effects execution
- skill_check metadata: margin and grade fields
- grant_item effect type: mapped to pick_up command
"""

from __future__ import annotations

from typing import Any
from unittest import mock

from app.game_core.bootstrap import build_default_world
from app.game_core.clue_investigation import (
    CLUE_EFFECT_TYPES,
    _is_simple_clue,
    _normalize_check,
    normalize_clue_definition,
    validate_clue_definition,
)
from app.game_core.rules.handlers.clue import ClueHandler
from app.game_core.rules.handlers.skill_check import SkillCheckHandler
from app.game_core.rules.models import Command
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, FlagSlice, PlayerSlice, TimeSlice


# ---------------------------------------------------------------------------
# Shared helpers
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


def _build_state_with_simple_clue(
    *,
    base_effects: list | None = None,
    check: dict | None = None,
    check_effects: list | None = None,
    narrative: str = "",
) -> StateContainer:
    state = StateContainer()

    player = PlayerSlice()
    player.restore(
        {
            "current_area": "frontier_town",
            "current_location": "adventurer_guild",
            "current_room": "guild_counter",
            "stats": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 12, "cha": 10},
            "skill_proficiencies": ["investigation"],
        }
    )
    state.register(player)

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9, "absolute_tick": 12, "accumulated": 0.0})
    state.register(time_slice)

    flags = FlagSlice()
    flags.restore({})
    state.register(flags)

    areas = AreaSlice()
    areas.restore({"areas": {"frontier_town": {}}})

    clue_params: dict[str, Any] = {
        "clue_id": "herb_patch",
        "name": "药草丛",
        "description": "溪边的一片药草丛。",
        "base_effects": base_effects or [],
    }
    if check is not None:
        clue_params["check"] = check
    if check_effects is not None:
        clue_params["check_effects"] = check_effects
    if narrative:
        clue_params["narrative"] = narrative

    areas.set_scoped_interactable_overlays(
        "frontier_town",
        "adventurer_guild__guild_counter",
        [
            {
                "id": "herb_patch_clue",
                "name": "药草丛",
                "description": "溪边的一片药草丛。",
                "type": "inspect",
                "tags": ["clue"],
                "functional": {
                    "type": "investigate_clue",
                    "params": clue_params,
                },
            }
        ],
    )
    state.register(areas)
    return state


# ---------------------------------------------------------------------------
# 2a: clue_investigation.py — schema detection and normalization
# ---------------------------------------------------------------------------

def test_grant_item_in_clue_effect_types() -> None:
    """2j: grant_item must be in CLUE_EFFECT_TYPES."""
    assert "grant_item" in CLUE_EFFECT_TYPES


def test_is_simple_clue_detects_base_effects_without_options() -> None:
    assert _is_simple_clue({"base_effects": []}) is True
    assert _is_simple_clue({"base_effects": [], "check": {}}) is True


def test_is_simple_clue_returns_false_when_options_present() -> None:
    assert _is_simple_clue({"base_effects": [], "options": []}) is False


def test_is_simple_clue_returns_false_when_no_base_effects() -> None:
    assert _is_simple_clue({"options": []}) is False
    assert _is_simple_clue({}) is False


def test_normalize_check_valid() -> None:
    result = _normalize_check({"skill": "nature", "dc": 12})
    assert result == {"skill": "nature", "dc": 12}


def test_normalize_check_rejects_missing_skill() -> None:
    assert _normalize_check({"dc": 12}) is None


def test_normalize_check_rejects_negative_dc() -> None:
    assert _normalize_check({"skill": "nature", "dc": -1}) is None


def test_normalize_check_rejects_non_mapping() -> None:
    assert _normalize_check("invalid") is None
    assert _normalize_check(None) is None


def test_normalize_clue_definition_simple_schema_sets_simple_flag() -> None:
    raw = {
        "clue_id": "herb_patch",
        "base_effects": [{"type": "set_flag", "params": {"key": "found_herbs", "value": True}}],
        "check": {"skill": "nature", "dc": 12},
        "check_effects": [{"type": "set_flag", "params": {"key": "found_rare_herb", "value": True}}],
        "narrative": "这片药草丛生长在溪边。",
    }
    clue = normalize_clue_definition(raw)
    assert clue["simple"] is True
    assert clue["clue_id"] == "herb_patch"
    assert len(clue["base_effects"]) == 1
    assert clue["check"] == {"skill": "nature", "dc": 12}
    assert len(clue["check_effects"]) == 1
    assert clue["narrative"] == "这片药草丛生长在溪边。"


def test_normalize_clue_definition_simple_no_check() -> None:
    raw = {
        "clue_id": "herb_patch",
        "base_effects": [],
    }
    clue = normalize_clue_definition(raw)
    assert clue["simple"] is True
    assert "check" not in clue  # check not injected when None
    assert clue["check_effects"] == []


def test_normalize_clue_definition_simple_schema_has_no_options() -> None:
    raw = {
        "clue_id": "herb_patch",
        "base_effects": [],
    }
    clue = normalize_clue_definition(raw)
    assert "options" not in clue
    assert "outcomes" not in clue


def test_normalize_clue_definition_legacy_schema_unaffected() -> None:
    """Old schema (with options) must still normalize correctly."""
    raw = {
        "clue_id": "blood_trail",
        "options": [
            {"id": "examine", "label": "仔细检查"},
            {"id": "follow", "label": "追踪"},
        ],
        "outcomes": {
            "examine": [],
            "follow": [],
        },
    }
    clue = normalize_clue_definition(raw)
    assert clue.get("simple") is not True
    assert "options" in clue
    assert len(clue["options"]) == 2


def test_validate_clue_definition_simple_schema_valid() -> None:
    clue = {
        "clue_id": "herb_patch",
        "simple": True,
        "base_effects": [{"type": "set_flag", "params": {"key": "x", "value": True}}],
        "check_effects": [{"type": "grant_item", "params": {"item_id": "rare_herb", "count": 1}}],
    }
    assert validate_clue_definition(clue) is None


def test_validate_clue_definition_simple_schema_empty_effects_valid() -> None:
    clue = {
        "clue_id": "herb_patch",
        "simple": True,
        "base_effects": [],
        "check_effects": [],
    }
    assert validate_clue_definition(clue) is None


def test_validate_clue_definition_simple_schema_invalid_effect_type() -> None:
    clue = {
        "clue_id": "herb_patch",
        "simple": True,
        "base_effects": [{"type": "teleport_player", "params": {}}],
        "check_effects": [],
    }
    assert validate_clue_definition(clue) == "invalid_effect_type"


def test_validate_clue_definition_simple_schema_invalid_check_effect() -> None:
    clue = {
        "clue_id": "herb_patch",
        "simple": True,
        "base_effects": [],
        "check_effects": [{"type": "explode_everything", "params": {}}],
    }
    assert validate_clue_definition(clue) == "invalid_effect_type"


def test_validate_clue_definition_simple_schema_no_clue_id() -> None:
    clue = {
        "simple": True,
        "base_effects": [],
        "check_effects": [],
    }
    assert validate_clue_definition(clue) == "missing_clue_id"


# ---------------------------------------------------------------------------
# 2b: ClueHandler — _compute_investigate_simple
# ---------------------------------------------------------------------------

def test_investigate_simple_base_effects_trigger() -> None:
    """base_effects must be applied immediately on investigate_clue."""
    world = _build_world()
    state = _build_state_with_simple_clue(
        base_effects=[{"type": "set_flag", "params": {"key": "found_herbs", "value": True}}],
    )
    handler = ClueHandler()
    result = handler.compute(
        Command(type="investigate_clue", params={"interactable_id": "herb_patch_clue"}, source="player"),
        state,
        world,
    )
    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)
    assert state.flags.get("found_herbs") is True


def test_investigate_simple_sets_resolved_option_id_to_sentinel() -> None:
    """resolved_option_id must be '__simple__' to satisfy clue_investigated condition."""
    world = _build_world()
    state = _build_state_with_simple_clue()
    handler = ClueHandler()
    result = handler.compute(
        Command(type="investigate_clue", params={"interactable_id": "herb_patch_clue"}, source="player"),
        state,
        world,
    )
    assert result.delta is not None
    state.apply(result.delta)
    clue_state = state.areas.get_area("frontier_town").interactable_states["herb_patch_clue"]
    assert clue_state["resolved_option_id"] == "__simple__"


def test_investigate_simple_stores_pending_check_when_check_present() -> None:
    world = _build_world()
    state = _build_state_with_simple_clue(
        check={"skill": "nature", "dc": 12},
        check_effects=[{"type": "set_flag", "params": {"key": "found_rare_herb", "value": True}}],
    )
    handler = ClueHandler()
    result = handler.compute(
        Command(type="investigate_clue", params={"interactable_id": "herb_patch_clue"}, source="player"),
        state,
        world,
    )
    assert result.delta is not None
    state.apply(result.delta)
    clue_state = state.areas.get_area("frontier_town").interactable_states["herb_patch_clue"]
    assert "pending_check" in clue_state
    pending = clue_state["pending_check"]
    assert pending["skill"] == "nature"
    assert pending["dc"] == 12
    assert len(pending["check_effects"]) == 1


def test_investigate_simple_no_pending_check_when_no_check() -> None:
    world = _build_world()
    state = _build_state_with_simple_clue()
    handler = ClueHandler()
    result = handler.compute(
        Command(type="investigate_clue", params={"interactable_id": "herb_patch_clue"}, source="player"),
        state,
        world,
    )
    assert result.delta is not None
    state.apply(result.delta)
    clue_state = state.areas.get_area("frontier_town").interactable_states["herb_patch_clue"]
    assert "pending_check" not in clue_state


def test_investigate_simple_narrative_injected_to_area_events() -> None:
    """narrative text must be written to area_events when non-empty."""
    world = _build_world()
    state = _build_state_with_simple_clue(
        narrative="这片药草丛生长在溪边阴凉处，其中夹杂着一些不常见的品种。",
    )
    handler = ClueHandler()
    result = handler.compute(
        Command(type="investigate_clue", params={"interactable_id": "herb_patch_clue"}, source="player"),
        state,
        world,
    )
    assert result.delta is not None
    state.apply(result.delta)
    events = state.areas.get_area_events("frontier_town")
    assert any(
        "药草丛" in e.get("event", "") and e.get("source") == "clue_narrative"
        for e in events
    )


def test_investigate_simple_no_area_event_when_no_narrative() -> None:
    world = _build_world()
    state = _build_state_with_simple_clue(narrative="")
    handler = ClueHandler()
    result = handler.compute(
        Command(type="investigate_clue", params={"interactable_id": "herb_patch_clue"}, source="player"),
        state,
        world,
    )
    assert result.delta is not None
    state.apply(result.delta)
    events = state.areas.get_area_events("frontier_town")
    # No clue_narrative events
    assert not any(e.get("source") == "clue_narrative" for e in events)


def test_investigate_simple_metadata_has_check() -> None:
    world = _build_world()
    state = _build_state_with_simple_clue(
        check={"skill": "nature", "dc": 12},
    )
    handler = ClueHandler()
    result = handler.compute(
        Command(type="investigate_clue", params={"interactable_id": "herb_patch_clue"}, source="player"),
        state,
        world,
    )
    assert result.executed is True
    assert result.metadata["has_check"] is True
    assert result.metadata["check"]["skill"] == "nature"
    assert result.metadata["simple"] is True


def test_investigate_simple_metadata_no_check() -> None:
    world = _build_world()
    state = _build_state_with_simple_clue()
    handler = ClueHandler()
    result = handler.compute(
        Command(type="investigate_clue", params={"interactable_id": "herb_patch_clue"}, source="player"),
        state,
        world,
    )
    assert result.executed is True
    assert result.metadata["has_check"] is False
    assert result.metadata["check"] is None


def test_investigate_simple_already_resolved_is_blocked() -> None:
    """Re-investigating an already-resolved simple clue must return error."""
    world = _build_world()
    state = _build_state_with_simple_clue()
    handler = ClueHandler()
    result1 = handler.compute(
        Command(type="investigate_clue", params={"interactable_id": "herb_patch_clue"}, source="player"),
        state,
        world,
    )
    assert result1.delta is not None
    state.apply(result1.delta)
    result2 = handler.compute(
        Command(type="investigate_clue", params={"interactable_id": "herb_patch_clue"}, source="player"),
        state,
        world,
    )
    assert result2.executed is False
    assert "already_resolved" in (result2.errors[0] if result2.errors else "")


# ---------------------------------------------------------------------------
# 2b: ClueHandler — _compute_apply_check_result
# ---------------------------------------------------------------------------

def _investigate_simple_clue_with_check(
    state: StateContainer,
    world,
    *,
    check_effects: list | None = None,
) -> None:
    """Helper: investigate the herb_patch_clue and store pending_check."""
    handler = ClueHandler()
    result = handler.compute(
        Command(type="investigate_clue", params={"interactable_id": "herb_patch_clue"}, source="player"),
        state,
        world,
    )
    assert result.executed is True and result.delta is not None
    state.apply(result.delta)


def test_apply_check_result_excellent_executes_check_effects() -> None:
    world = _build_world()
    state = _build_state_with_simple_clue(
        check={"skill": "nature", "dc": 12},
        check_effects=[{"type": "set_flag", "params": {"key": "found_rare_herb", "value": True}}],
    )
    _investigate_simple_clue_with_check(state, world)

    handler = ClueHandler()
    result = handler.compute(
        Command(
            type="apply_clue_check_result",
            params={"interactable_id": "herb_patch_clue", "grade": "excellent"},
            source="system",
        ),
        state,
        world,
    )
    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)
    assert state.flags.get("found_rare_herb") is True


def test_apply_check_result_good_executes_check_effects() -> None:
    world = _build_world()
    state = _build_state_with_simple_clue(
        check={"skill": "nature", "dc": 12},
        check_effects=[{"type": "set_flag", "params": {"key": "found_herbs_good", "value": True}}],
    )
    _investigate_simple_clue_with_check(state, world)

    handler = ClueHandler()
    result = handler.compute(
        Command(
            type="apply_clue_check_result",
            params={"interactable_id": "herb_patch_clue", "grade": "good"},
            source="system",
        ),
        state,
        world,
    )
    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)
    assert state.flags.get("found_herbs_good") is True


def test_apply_check_result_poor_does_not_execute_check_effects() -> None:
    world = _build_world()
    state = _build_state_with_simple_clue(
        check={"skill": "nature", "dc": 12},
        check_effects=[{"type": "set_flag", "params": {"key": "should_not_be_set", "value": True}}],
    )
    _investigate_simple_clue_with_check(state, world)

    handler = ClueHandler()
    result = handler.compute(
        Command(
            type="apply_clue_check_result",
            params={"interactable_id": "herb_patch_clue", "grade": "poor"},
            source="system",
        ),
        state,
        world,
    )
    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)
    assert state.flags.get("should_not_be_set") is not True


def test_apply_check_result_bad_does_not_execute_check_effects() -> None:
    world = _build_world()
    state = _build_state_with_simple_clue(
        check={"skill": "nature", "dc": 12},
        check_effects=[{"type": "set_flag", "params": {"key": "should_not_be_set_bad", "value": True}}],
    )
    _investigate_simple_clue_with_check(state, world)

    handler = ClueHandler()
    result = handler.compute(
        Command(
            type="apply_clue_check_result",
            params={"interactable_id": "herb_patch_clue", "grade": "bad"},
            source="system",
        ),
        state,
        world,
    )
    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)
    assert state.flags.get("should_not_be_set_bad") is not True


def test_apply_check_result_clears_pending_check() -> None:
    world = _build_world()
    state = _build_state_with_simple_clue(
        check={"skill": "nature", "dc": 12},
        check_effects=[],
    )
    _investigate_simple_clue_with_check(state, world)

    handler = ClueHandler()
    result = handler.compute(
        Command(
            type="apply_clue_check_result",
            params={"interactable_id": "herb_patch_clue", "grade": "good"},
            source="system",
        ),
        state,
        world,
    )
    assert result.delta is not None
    state.apply(result.delta)
    clue_state = state.areas.get_area("frontier_town").interactable_states["herb_patch_clue"]
    assert "pending_check" not in clue_state


def test_apply_check_result_stores_grade_and_check_passed() -> None:
    world = _build_world()
    state = _build_state_with_simple_clue(
        check={"skill": "nature", "dc": 12},
        check_effects=[],
    )
    _investigate_simple_clue_with_check(state, world)

    handler = ClueHandler()
    result = handler.compute(
        Command(
            type="apply_clue_check_result",
            params={"interactable_id": "herb_patch_clue", "grade": "excellent"},
            source="system",
        ),
        state,
        world,
    )
    assert result.delta is not None
    state.apply(result.delta)
    clue_state = state.areas.get_area("frontier_town").interactable_states["herb_patch_clue"]
    assert clue_state["check_grade"] == "excellent"
    assert clue_state["check_passed"] is True


def test_apply_check_result_validates_missing_pending_check() -> None:
    """apply_clue_check_result must fail if no pending_check in interactable state."""
    world = _build_world()
    # Build state with simple clue but NO check — so no pending_check is stored
    state = _build_state_with_simple_clue()
    _investigate_simple_clue_with_check(state, world)

    handler = ClueHandler()
    result = handler.compute(
        Command(
            type="apply_clue_check_result",
            params={"interactable_id": "herb_patch_clue", "grade": "good"},
            source="system",
        ),
        state,
        world,
    )
    assert result.executed is False
    assert any("pending_check" in e for e in result.errors)


def test_apply_check_result_validates_invalid_grade() -> None:
    world = _build_world()
    state = _build_state_with_simple_clue(
        check={"skill": "nature", "dc": 12},
        check_effects=[],
    )
    _investigate_simple_clue_with_check(state, world)

    handler = ClueHandler()
    result = handler.compute(
        Command(
            type="apply_clue_check_result",
            params={"interactable_id": "herb_patch_clue", "grade": "legendary"},
            source="system",
        ),
        state,
        world,
    )
    assert result.executed is False


def test_apply_check_result_metadata_contains_grade_and_passed() -> None:
    world = _build_world()
    state = _build_state_with_simple_clue(
        check={"skill": "nature", "dc": 12},
        check_effects=[],
    )
    _investigate_simple_clue_with_check(state, world)

    handler = ClueHandler()
    result = handler.compute(
        Command(
            type="apply_clue_check_result",
            params={"interactable_id": "herb_patch_clue", "grade": "poor"},
            source="system",
        ),
        state,
        world,
    )
    assert result.executed is True
    assert result.metadata["grade"] == "poor"
    assert result.metadata["passed"] is False


# ---------------------------------------------------------------------------
# 2c: skill_check.py — margin and grade in metadata
# ---------------------------------------------------------------------------

def _make_player_state(*, skill: str = "nature", proficiency: bool = False) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    # proficiency_bonus=0 so modifier is purely from stats (all 10 → 0)
    # This makes margins predictable: margin = roll - dc
    player.restore(
        {
            "stats": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
            "proficiency_bonus": 0,
        }
    )
    state.register(player)
    return state


def test_skill_check_metadata_has_margin_and_grade() -> None:
    """_compute_skill_check must include margin and grade in metadata."""
    state = _make_player_state()
    handler = SkillCheckHandler()

    with mock.patch("app.game_core.rules.handler_utils.roll_d20", return_value=15):
        result = handler.compute(
            Command(type="skill_check", params={"skill": "nature", "dc": 10}, source="player"),
            state,
            None,
        )

    assert result.executed is True
    assert "margin" in result.metadata
    assert "grade" in result.metadata


def test_skill_check_grade_excellent_when_margin_ge_5() -> None:
    state = _make_player_state()
    handler = SkillCheckHandler()

    # roll=15, no modifier, dc=10 → total=15, margin=5 → excellent
    with mock.patch("app.game_core.rules.handler_utils.roll_d20", return_value=15):
        result = handler.compute(
            Command(type="skill_check", params={"skill": "nature", "dc": 10}, source="player"),
            state,
            None,
        )

    assert result.metadata["margin"] == 5
    assert result.metadata["grade"] == "excellent"


def test_skill_check_grade_good_when_margin_0_to_4() -> None:
    state = _make_player_state()
    handler = SkillCheckHandler()

    # roll=12, no modifier, dc=10 → total=12, margin=2 → good
    with mock.patch("app.game_core.rules.handler_utils.roll_d20", return_value=12):
        result = handler.compute(
            Command(type="skill_check", params={"skill": "nature", "dc": 10}, source="player"),
            state,
            None,
        )

    assert result.metadata["margin"] == 2
    assert result.metadata["grade"] == "good"


def test_skill_check_grade_poor_when_margin_minus5_to_minus1() -> None:
    state = _make_player_state()
    handler = SkillCheckHandler()

    # roll=8, no modifier, dc=10 → total=8, margin=-2 → poor
    with mock.patch("app.game_core.rules.handler_utils.roll_d20", return_value=8):
        result = handler.compute(
            Command(type="skill_check", params={"skill": "nature", "dc": 10}, source="player"),
            state,
            None,
        )

    assert result.metadata["margin"] == -2
    assert result.metadata["grade"] == "poor"


def test_skill_check_grade_bad_when_margin_lt_minus5() -> None:
    state = _make_player_state()
    handler = SkillCheckHandler()

    # roll=3, no modifier, dc=10 → total=3, margin=-7 → bad
    with mock.patch("app.game_core.rules.handler_utils.roll_d20", return_value=3):
        result = handler.compute(
            Command(type="skill_check", params={"skill": "nature", "dc": 10}, source="player"),
            state,
            None,
        )

    assert result.metadata["margin"] == -7
    assert result.metadata["grade"] == "bad"


def test_skill_check_grade_boundary_exactly_5() -> None:
    """margin == 5 should be excellent (>= 5)."""
    state = _make_player_state()
    handler = SkillCheckHandler()

    with mock.patch("app.game_core.rules.handler_utils.roll_d20", return_value=15):
        result = handler.compute(
            Command(type="skill_check", params={"skill": "nature", "dc": 10}, source="player"),
            state,
            None,
        )

    assert result.metadata["grade"] == "excellent"


def test_skill_check_grade_boundary_exactly_minus5() -> None:
    """margin == -5 should be poor (>= -5)."""
    state = _make_player_state()
    handler = SkillCheckHandler()

    # roll=5, dc=10 → margin=-5 → poor
    with mock.patch("app.game_core.rules.handler_utils.roll_d20", return_value=5):
        result = handler.compute(
            Command(type="skill_check", params={"skill": "nature", "dc": 10}, source="player"),
            state,
            None,
        )

    assert result.metadata["margin"] == -5
    assert result.metadata["grade"] == "poor"


def test_skill_check_grade_boundary_minus6_is_bad() -> None:
    """margin == -6 should be bad (< -5)."""
    state = _make_player_state()
    handler = SkillCheckHandler()

    # roll=4, dc=10 → margin=-6 → bad
    with mock.patch("app.game_core.rules.handler_utils.roll_d20", return_value=4):
        result = handler.compute(
            Command(type="skill_check", params={"skill": "nature", "dc": 10}, source="player"),
            state,
            None,
        )

    assert result.metadata["margin"] == -6
    assert result.metadata["grade"] == "bad"
