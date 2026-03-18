"""Tests for Group 3: NPC 生命感改动.

Covers:
- #24a: CharacterTemplate.initial_blackboard loading
- #24b: _initial_relation_payload includes npc_blackboards
- #25:  NpcAutonomyHook mood derivation + pinned_memories overflow protection
- S8:  context_builder pinned_memories rendering
- #29: area_situation NPC location + time period atmosphere
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.content.registries.characters import CharacterRegistry
from app.game_core.orchestration.hooks.narrative_planner import NarrativePlannerHook
from app.game_core.orchestration.hooks.npc_autonomy import NpcAutonomyHook
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.state import StateContainer
from app.game_core.state.base import StateContainer as SC
from app.game_core.state.slices import AreaSlice, SceneSlice, TimeSlice
from app.game_core.state.slices.narrative_plan import NarrativePlanSlice
from app.game_core.state.slices.party import PartySlice
from app.game_core.state.slices.player import PlayerSlice
from app.game_core.state.slices.relations import RelationSlice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_autonomy_context(
    *,
    player_area: str = "frontier_town",
    player_location: str | None = "north_gate",
    npc_locations: dict[str, str | None] | None = None,
    companions: list[str] | None = None,
    npc_blackboards: dict[str, dict[str, Any]] | None = None,
    period: str = "day",
    area_events: list[dict[str, Any]] | None = None,
    slot: int = 10,
) -> SettlementContext:
    """Build a minimal SettlementContext for NpcAutonomyHook tests."""
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": slot, "period": period})
    state.register(time_slice)

    player_slice = PlayerSlice()
    player_slice.restore({
        "current_area": player_area,
        "current_location": player_location,
    })
    state.register(player_slice)

    area_slice = AreaSlice()
    area_slice.restore({
        "areas": {
            player_area: {
                "npc_locations": npc_locations or {},
                "npc_rooms": {},
                "area_events": area_events or [],
            }
        }
    })
    state.register(area_slice)

    rel_slice = RelationSlice()
    payload: dict[str, Any] = {}
    if npc_blackboards:
        payload["npc_blackboards"] = npc_blackboards
    rel_slice.restore(payload)
    state.register(rel_slice)

    party_slice = PartySlice()
    members: dict[str, dict[str, Any]] = {}
    if companions:
        for c_id in companions:
            members[c_id] = {"id": c_id}
    party_slice.restore({"members": members})
    state.register(party_slice)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)

    world = WorldInstance("test_world")
    engine = RulesEngine()

    return SettlementContext(
        change_log=[],
        state=state,
        world=world,
        scene_bus=SceneBus(scene_slice),
        _rules_engine=engine,
        _apply_delta=lambda d: state.apply(d) if d else None,
    )


def _make_situation_context(
    *,
    area_id: str = "town",
    danger_level: float = 1.0,
    npc_locations: dict[str, str | None] | None = None,
    period: str = "day",
    events: list | None = None,
    hostile_tracking: dict | None = None,
    characters_data: dict | None = None,
) -> SettlementContext:
    """Build a minimal SettlementContext for _update_area_situation tests."""
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 8, "period": period})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": area_id, "current_location": "main_square"})
    state.register(player)

    areas = AreaSlice()
    area_data: dict = {
        "danger_level": danger_level,
        "tags": [],
        "area_events": events or [],
        "hostile_tracking": hostile_tracking or {},
        "npc_locations": npc_locations or {},
        "npc_rooms": {},
    }
    areas.restore({"areas": {area_id: area_data}})
    state.register(areas)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)

    world = WorldInstance("test_world")
    if characters_data:
        reg = CharacterRegistry()
        reg.load(characters_data)
        world.register(reg)

    return SettlementContext(
        change_log=[],
        state=state,
        world=world,
        scene_bus=SceneBus(scene_slice),
        _rules_engine=RulesEngine(),
        _apply_delta=lambda delta: None,
    )


# ---------------------------------------------------------------------------
# #24a: CharacterTemplate.initial_blackboard loading
# ---------------------------------------------------------------------------

def test_initial_blackboard_loaded_from_raw():
    """CharacterRegistry._build_template loads initial_blackboard dict."""
    reg = CharacterRegistry()
    reg.load({
        "npc_test": {
            "id": "npc_test",
            "name": "Test NPC",
            "initial_blackboard": {
                "mood": "高兴",
                "thoughts": "今天天气不错",
                "goals": ["完成今天的任务"],
            }
        }
    })
    tmpl = reg.get("npc_test")
    assert tmpl is not None
    assert tmpl.initial_blackboard is not None
    assert tmpl.initial_blackboard["mood"] == "高兴"
    assert tmpl.initial_blackboard["thoughts"] == "今天天气不错"
    assert tmpl.initial_blackboard["goals"] == ["完成今天的任务"]


def test_initial_blackboard_none_when_absent():
    """initial_blackboard is None when not specified in raw data."""
    reg = CharacterRegistry()
    reg.load({
        "npc_plain": {
            "id": "npc_plain",
            "name": "Plain NPC",
        }
    })
    tmpl = reg.get("npc_plain")
    assert tmpl is not None
    assert tmpl.initial_blackboard is None


def test_initial_blackboard_ignored_if_not_dict():
    """initial_blackboard is None when raw value is not a dict."""
    reg = CharacterRegistry()
    reg.load({
        "npc_bad": {
            "id": "npc_bad",
            "name": "Bad NPC",
            "initial_blackboard": "not a dict",
        }
    })
    tmpl = reg.get("npc_bad")
    assert tmpl is not None
    assert tmpl.initial_blackboard is None


def test_initial_blackboard_is_defensive_copy():
    """initial_blackboard stored in template is a copy, not the original dict."""
    original = {"mood": "neutral"}
    reg = CharacterRegistry()
    reg.load({
        "npc_copy": {
            "id": "npc_copy",
            "name": "Copy NPC",
            "initial_blackboard": original,
        }
    })
    tmpl = reg.get("npc_copy")
    assert tmpl is not None
    assert tmpl.initial_blackboard is not None
    # Mutating original should not affect stored template
    original["mood"] = "angry"
    assert tmpl.initial_blackboard["mood"] == "neutral"


# ---------------------------------------------------------------------------
# #24b: _initial_relation_payload includes npc_blackboards
# ---------------------------------------------------------------------------

def test_initial_relation_payload_includes_npc_blackboards():
    """StateContainer._initial_relation_payload populates npc_blackboards from initial_blackboard."""
    world = WorldInstance("test_world")
    reg = CharacterRegistry()
    reg.load({
        "innkeeper": {
            "id": "innkeeper",
            "name": "Innkeeper",
            "initial_blackboard": {"mood": "疲惫", "thoughts": "又到了忙碌的夜晚"},
        },
        "guard": {
            "id": "guard",
            "name": "Guard",
            # No initial_blackboard
        }
    })
    world.register(reg)

    payload = SC._initial_relation_payload(world)
    npc_blackboards = payload.get("npc_blackboards", {})
    assert "innkeeper" in npc_blackboards
    assert npc_blackboards["innkeeper"]["mood"] == "疲惫"
    assert npc_blackboards["innkeeper"]["thoughts"] == "又到了忙碌的夜晚"
    # NPC without initial_blackboard should NOT appear in npc_blackboards
    assert "guard" not in npc_blackboards


def test_initial_relation_payload_npc_blackboards_empty_when_no_registry():
    """When no characters registry, npc_blackboards is empty dict."""
    world = WorldInstance("empty_world")
    payload = SC._initial_relation_payload(world)
    assert payload.get("npc_blackboards", {}) == {}


def test_npc_blackboards_written_to_relation_slice_on_restore():
    """RelationSlice.restore() correctly loads npc_blackboards from initial payload."""
    world = WorldInstance("test_world")
    reg = CharacterRegistry()
    reg.load({
        "tavernkeeper": {
            "id": "tavernkeeper",
            "name": "Tavernkeeper",
            "initial_blackboard": {"mood": "平静", "goals": ["招待顾客"]},
        }
    })
    world.register(reg)

    rel_slice = RelationSlice()
    payload = SC._initial_relation_payload(world)
    rel_slice.restore(payload)

    board = rel_slice.get_blackboard("tavernkeeper")
    assert board.get("mood") == "平静"
    assert board.get("goals") == ["招待顾客"]


# ---------------------------------------------------------------------------
# #25: NpcAutonomyHook mood derivation
# ---------------------------------------------------------------------------

def test_mood_derived_dawn():
    """NPC gets mood '精神' during dawn period."""
    ctx = _make_autonomy_context(
        npc_locations={"villager": "north_gate"},
        period="dawn",
    )
    asyncio.run(NpcAutonomyHook().execute(ctx))
    board = ctx.state.relations.get_blackboard("villager")
    assert board.get("mood") == "精神"


def test_mood_derived_night():
    """NPC gets mood '困倦' during night period."""
    ctx = _make_autonomy_context(
        npc_locations={"guard": "north_gate"},
        period="night",
    )
    asyncio.run(NpcAutonomyHook().execute(ctx))
    board = ctx.state.relations.get_blackboard("guard")
    assert board.get("mood") == "困倦"


def test_mood_derived_day():
    """NPC gets mood '平静' during day period."""
    ctx = _make_autonomy_context(
        npc_locations={"npc": "north_gate"},
        period="day",
    )
    asyncio.run(NpcAutonomyHook().execute(ctx))
    board = ctx.state.relations.get_blackboard("npc")
    assert board.get("mood") == "平静"


def test_mood_derived_dusk():
    """NPC gets mood '疲惫' during dusk period."""
    ctx = _make_autonomy_context(
        npc_locations={"farmer": "north_gate"},
        period="dusk",
    )
    asyncio.run(NpcAutonomyHook().execute(ctx))
    board = ctx.state.relations.get_blackboard("farmer")
    assert board.get("mood") == "疲惫"


def test_mood_not_overwritten_when_already_set():
    """NpcAutonomyHook does NOT overwrite mood if already set in blackboard."""
    ctx = _make_autonomy_context(
        npc_locations={"npc": "north_gate"},
        period="dawn",  # would derive '精神'
        npc_blackboards={"npc": {"mood": "忧郁"}},  # pre-set by Planner
    )
    asyncio.run(NpcAutonomyHook().execute(ctx))
    board = ctx.state.relations.get_blackboard("npc")
    # '忧郁' must be preserved — not overwritten with '精神'
    assert board.get("mood") == "忧郁"


def test_mood_derived_for_companion():
    """Party companion also gets mood derived from time period."""
    ctx = _make_autonomy_context(
        companions=["aria"],
        period="night",
    )
    asyncio.run(NpcAutonomyHook().execute(ctx))
    board = ctx.state.relations.get_blackboard("aria")
    assert board.get("mood") == "困倦"


def test_companion_mood_not_overwritten_when_already_set():
    """Companion's pre-set mood is not overwritten by time-derived mood."""
    ctx = _make_autonomy_context(
        companions=["aria"],
        period="day",  # would derive '平静'
        npc_blackboards={"aria": {"mood": "兴奋", "updated_tick": 1}},
    )
    asyncio.run(NpcAutonomyHook().execute(ctx))
    board = ctx.state.relations.get_blackboard("aria")
    assert board.get("mood") == "兴奋"


# ---------------------------------------------------------------------------
# S8: pinned_memories overflow protection
# ---------------------------------------------------------------------------

def test_pinned_memories_not_displaced_on_overflow():
    """pinned_memories entries are preserved when observations overflow _OBS_CAP."""
    # Start with 19 regular observations + 1 pinned = 20 total (at cap)
    pinned = ["重要记忆：师傅的临终教诲"]
    existing_obs = [f"普通观察{i}" for i in range(19)]
    pre_board = {
        "pinned_memories": pinned,
        "observations": existing_obs + pinned,
        "updated_tick": 5,
    }
    # Add 3 new events → would push to 13, triggering overflow
    area_events = [
        {"tick": 10, "event": f"新事件{i}", "source": "engine"}
        for i in range(3)
    ]
    ctx = _make_autonomy_context(
        npc_locations={"npc": "north_gate"},
        npc_blackboards={"npc": pre_board},
        area_events=area_events,
    )
    asyncio.run(NpcAutonomyHook().execute(ctx))

    board = ctx.state.relations.get_blackboard("npc")
    obs = board.get("observations", [])
    # Pinned memory must survive
    assert any("师傅的临终教诲" in o for o in obs), f"Pinned memory lost: {obs}"


def test_non_pinned_observations_can_be_displaced():
    """Regular (non-pinned) observations ARE displaced when overflow occurs."""
    existing_obs = [f"普通观察{i}" for i in range(20)]  # Full cap already
    pre_board = {
        "observations": existing_obs,
        "updated_tick": 5,
    }
    area_events = [
        {"tick": 11, "event": "新到的事件", "source": "engine"},
    ]
    ctx = _make_autonomy_context(
        npc_locations={"npc": "north_gate"},
        npc_blackboards={"npc": pre_board},
        area_events=area_events,
    )
    asyncio.run(NpcAutonomyHook().execute(ctx))

    board = ctx.state.relations.get_blackboard("npc")
    obs = board.get("observations", [])
    # Total should not exceed cap
    from app.game_core.orchestration.hooks.npc_autonomy import _OBS_CAP
    assert len(obs) <= _OBS_CAP
    # New event should be present (wasn't in original)
    assert any("新到的事件" in o for o in obs), f"New event missing: {obs}"
    # Overflow should go to pending_graphize
    pending = board.get("pending_graphize", [])
    assert len(pending) > 0


# ---------------------------------------------------------------------------
# #29: area_situation NPC location descriptions + time period atmosphere
# ---------------------------------------------------------------------------

def test_area_situation_includes_npc_locations():
    """_update_area_situation lists NPC names and their locations."""
    ctx = _make_situation_context(
        npc_locations={"guard_captain": "north_gate", "merchant": "market"},
        characters_data={
            "guard_captain": {"id": "guard_captain", "name": "卫队长"},
            "merchant": {"id": "merchant", "name": "商人"},
        },
    )
    NarrativePlannerHook()._update_area_situation(ctx)
    situation = ctx.state.areas.get_area_situation("town")
    assert "当前区域NPC" in situation
    assert "卫队长" in situation
    assert "商人" in situation


def test_area_situation_npc_location_shows_fallback_for_none():
    """NPC with no location_id gets '附近' as fallback."""
    ctx = _make_situation_context(
        npc_locations={"wanderer": None},
        characters_data={
            "wanderer": {"id": "wanderer", "name": "流浪者"},
        },
    )
    NarrativePlannerHook()._update_area_situation(ctx)
    situation = ctx.state.areas.get_area_situation("town")
    assert "流浪者在附近" in situation


def test_area_situation_includes_time_period_day():
    """Day period produces '白天' atmosphere in situation text."""
    ctx = _make_situation_context(period="day")
    NarrativePlannerHook()._update_area_situation(ctx)
    situation = ctx.state.areas.get_area_situation("town")
    assert "白天" in situation


def test_area_situation_includes_time_period_night():
    """Night period produces '夜晚' atmosphere in situation text."""
    ctx = _make_situation_context(period="night")
    NarrativePlannerHook()._update_area_situation(ctx)
    situation = ctx.state.areas.get_area_situation("town")
    assert "夜晚" in situation


def test_area_situation_includes_time_period_dawn():
    """Dawn period produces '清晨' atmosphere in situation text."""
    ctx = _make_situation_context(period="dawn")
    NarrativePlannerHook()._update_area_situation(ctx)
    situation = ctx.state.areas.get_area_situation("town")
    assert "清晨" in situation


def test_area_situation_includes_time_period_dusk():
    """Dusk period produces '傍晚' atmosphere in situation text."""
    ctx = _make_situation_context(period="dusk")
    NarrativePlannerHook()._update_area_situation(ctx)
    situation = ctx.state.areas.get_area_situation("town")
    assert "傍晚" in situation


def test_area_situation_npc_capped_at_five():
    """At most 5 NPCs are listed in area_situation."""
    npc_locations = {f"npc_{i}": "market" for i in range(8)}
    characters_data = {
        f"npc_{i}": {"id": f"npc_{i}", "name": f"村民{i}"}
        for i in range(8)
    }
    ctx = _make_situation_context(
        npc_locations=npc_locations,
        characters_data=characters_data,
    )
    NarrativePlannerHook()._update_area_situation(ctx)
    situation = ctx.state.areas.get_area_situation("town")
    # Only 5 NPC entries should appear; count "村民" occurrences as proxy
    assert situation.count("村民") <= 5


def test_area_situation_no_npc_section_when_empty():
    """No 'NPC' section when there are no NPCs in the area."""
    ctx = _make_situation_context(npc_locations={})
    NarrativePlannerHook()._update_area_situation(ctx)
    situation = ctx.state.areas.get_area_situation("town")
    assert "当前区域NPC" not in situation


# ---------------------------------------------------------------------------
# S8: context_builder pinned_memories rendering
# ---------------------------------------------------------------------------

def test_pinned_memories_rendered_in_blackboard_block():
    """_build_npc_prompt_text renders pinned_memories before observations."""
    from app.game_core.narrative.context_builder import _build_npc_prompt_text

    npc_profile: dict[str, Any] = {
        "id": "npc",
        "name": "小红",
        "personality": "活泼",
    }
    disposition: dict[str, Any] = {"approval": 10, "trust": 20, "fear": 0, "romance": 5}
    blackboard: dict[str, Any] = {
        "mood": "开心",
        "pinned_memories": ["初次相遇那晚的烛光", "约好一起看星星"],
        "observations": ["今天客人不多"],
    }

    result = _build_npc_prompt_text(
        npc_profile=npc_profile,
        disposition=disposition,
        stage="acquaintance",
        blackboard=blackboard,
        recent_area_events=[],
        area_situation="",
        time_info=None,
    )

    assert "重要记忆" in result
    assert "初次相遇那晚的烛光" in result
    assert "约好一起看星星" in result


def test_pinned_memories_empty_list_not_rendered():
    """Empty pinned_memories list produces no '重要记忆' block."""
    from app.game_core.narrative.context_builder import _build_npc_prompt_text

    npc_profile: dict[str, Any] = {"id": "npc", "name": "小明"}
    disposition: dict[str, Any] = {"approval": 0, "trust": 0, "fear": 0, "romance": 0}
    blackboard: dict[str, Any] = {
        "pinned_memories": [],
        "observations": ["saw a bird"],
    }

    result = _build_npc_prompt_text(
        npc_profile=npc_profile,
        disposition=disposition,
        stage="stranger",
        blackboard=blackboard,
        recent_area_events=[],
        area_situation="",
        time_info=None,
    )

    assert "重要记忆" not in result


def test_pinned_memories_rendered_before_observations():
    """pinned_memories appear before observations in rendered text."""
    from app.game_core.narrative.context_builder import _build_npc_prompt_text

    npc_profile: dict[str, Any] = {"id": "npc", "name": "小华"}
    disposition: dict[str, Any] = {"approval": 0, "trust": 0, "fear": 0, "romance": 0}
    blackboard: dict[str, Any] = {
        "pinned_memories": ["珍贵的记忆"],
        "observations": ["今天的事"],
    }

    result = _build_npc_prompt_text(
        npc_profile=npc_profile,
        disposition=disposition,
        stage="stranger",
        blackboard=blackboard,
        recent_area_events=[],
        area_situation="",
        time_info=None,
    )

    idx_pinned = result.find("重要记忆")
    idx_obs = result.find("近期观察")
    assert idx_pinned != -1
    assert idx_obs != -1
    assert idx_pinned < idx_obs, "pinned_memories should appear before observations"
