"""Tests for 3-C (NPC prompt blackboard), 3-D (NPC directive movement), 3-E (retire_quest reward).

Decision record: D-WE03c, D-WE03d, D-WE03e
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

from app.game_core.narrative.context_builder import _build_npc_prompt_text
from app.game_core.orchestration.hooks.npc_schedule import BasicNpcScheduleProvider


# ---------------------------------------------------------------------------
# 3-C: Blackboard block in NPC prompt
# ---------------------------------------------------------------------------


def test_blackboard_block_appears_when_provided() -> None:
    """NPC prompt includes 思绪/目标/情绪 from blackboard."""
    bb = {
        "thoughts": "城里最近不太平",
        "goals": ["卖出所有库存", "打听哥布林消息"],
        "observations": ["北门骑兵增加了"],
        "mood": "警惕",
        "attitude_towards_player": "这个冒险者看起来靠谱",
    }
    prompt = _build_npc_prompt_text(
        npc_profile={"name": "Tom", "personality": "A shrewd merchant."},
        disposition={"approval": 20, "trust": 30, "fear": 0, "romance": 0},
        stage="acquaintance",
        blackboard=bb,
    )
    assert "## 你当前的想法" in prompt
    assert "城里最近不太平" in prompt
    assert "卖出所有库存" in prompt
    assert "北门骑兵增加了" in prompt
    assert "警惕" in prompt
    assert "靠谱" in prompt


def test_blackboard_block_absent_when_empty() -> None:
    """NPC prompt omits 当前想法 block when blackboard is empty."""
    prompt = _build_npc_prompt_text(
        npc_profile={"name": "Tom", "personality": "A shrewd merchant."},
        disposition={"approval": 0, "trust": 0, "fear": 0, "romance": 0},
        stage="stranger",
        blackboard=None,
    )
    assert "## 你当前的想法" not in prompt


def test_blackboard_block_absent_when_all_values_empty() -> None:
    """NPC prompt omits 当前想法 block when blackboard has only empty values."""
    bb = {"thoughts": "", "goals": [], "observations": [], "mood": "", "attitude_towards_player": ""}
    prompt = _build_npc_prompt_text(
        npc_profile={"name": "Tom", "personality": "A shrewd merchant."},
        disposition={"approval": 0, "trust": 0, "fear": 0, "romance": 0},
        stage="stranger",
        blackboard=bb,
    )
    assert "## 你当前的想法" not in prompt


def test_relationship_compact_format_contains_all_dimensions() -> None:
    """Relationship section shows stage, approval, trust, fear, romance in one line."""
    prompt = _build_npc_prompt_text(
        npc_profile={"name": "Alice", "personality": "Brave warrior."},
        disposition={"approval": 40, "trust": 55, "fear": 10, "romance": 20},
        stage="friend",
    )
    assert "好感：40" in prompt
    assert "信任：55" in prompt
    assert "恐惧：10" in prompt
    assert "浪漫：20" in prompt
    assert "friend" in prompt


def test_impressions_not_in_prompt() -> None:
    """Impressions are no longer injected into the NPC prompt (3-C)."""
    prompt = _build_npc_prompt_text(
        npc_profile={"name": "Bob", "personality": "Grumpy gatekeeper."},
        disposition={"approval": 10, "trust": 5, "fear": 0, "romance": 0},
        stage="acquaintance",
    )
    assert "bribe" not in prompt
    assert "Talks too much" not in prompt
    assert "No previous memories" not in prompt


def test_behavior_guides_not_in_prompt() -> None:
    """Stage guides and trust/fear/romance hints are no longer in the NPC prompt (3-C)."""
    prompt = _build_npc_prompt_text(
        npc_profile={"name": "Enemy", "personality": "Hostile landlord."},
        disposition={"approval": -80, "trust": -50, "fear": 70, "romance": 0},
        stage="hostile",
    )
    assert "## How to behave" not in prompt
    # The numbers are still present in the compact relationship line
    assert "好感：-80" in prompt
    assert "hostile" in prompt


# ---------------------------------------------------------------------------
# 3-D: NPC directive-driven movement
# ---------------------------------------------------------------------------


def test_directive_destination_overrides_schedule() -> None:
    """Active npc_directive with destination takes priority over schedule."""
    provider = BasicNpcScheduleProvider()
    char_data = {
        "id": "goblin_slayer",
        "schedule": {"dusk": "tavern"},
    }
    directives = [
        {
            "npc_id": "goblin_slayer",
            "consumed": False,
            "directive": {
                "destination": {
                    "area_id": "ancient_ruins",
                    "location_id": "main_gate",
                }
            },
        }
    ]
    area, loc, room = provider._scheduled_destination(
        char_data, "dusk", {"frontier_town", "ancient_ruins"}, npc_directives=directives
    )
    assert area == "ancient_ruins"
    assert loc == "main_gate"
    assert room is None


def test_no_directive_falls_back_to_schedule() -> None:
    """When no active directive, schedule is used as before."""
    provider = BasicNpcScheduleProvider()
    char_data = {
        "id": "goblin_slayer",
        "schedule": {"dusk": "tavern"},
    }
    area, loc, room = provider._scheduled_destination(
        char_data, "dusk", {"frontier_town"}, npc_directives=[]
    )
    assert area is None
    assert loc == "tavern"
    assert room is None


def test_consumed_directive_ignored() -> None:
    """Consumed directive is skipped; schedule used as fallback."""
    provider = BasicNpcScheduleProvider()
    char_data = {
        "id": "goblin_slayer",
        "schedule": {"dusk": "tavern"},
    }
    directives = [
        {
            "npc_id": "goblin_slayer",
            "consumed": True,
            "directive": {
                "destination": {"area_id": "ancient_ruins"}
            },
        }
    ]
    area, loc, room = provider._scheduled_destination(
        char_data, "dusk", set(), npc_directives=directives
    )
    # consumed → fallback to schedule
    assert area is None
    assert loc == "tavern"


def test_directive_for_different_npc_ignored() -> None:
    """Directive for a different NPC does not affect current NPC's destination."""
    provider = BasicNpcScheduleProvider()
    char_data = {
        "id": "merchant_tom",
        "schedule": {"dusk": "market"},
    }
    directives = [
        {
            "npc_id": "someone_else",
            "consumed": False,
            "directive": {"destination": {"area_id": "ancient_ruins"}},
        }
    ]
    area, loc, room = provider._scheduled_destination(
        char_data, "dusk", set(), npc_directives=directives
    )
    # Directive is for a different NPC → fallback to schedule
    assert area is None
    assert loc == "market"


def test_directive_with_room_destination() -> None:
    """Directive can specify area_id, location_id, and room_id."""
    provider = BasicNpcScheduleProvider()
    char_data = {"id": "npc_x", "schedule": {}}
    directives = [
        {
            "npc_id": "npc_x",
            "consumed": False,
            "directive": {
                "destination": {
                    "area_id": "dungeon",
                    "location_id": "level1",
                    "room_id": "boss_chamber",
                }
            },
        }
    ]
    area, loc, room = provider._scheduled_destination(
        char_data, "night", set(), npc_directives=directives
    )
    assert area == "dungeon"
    assert loc == "level1"
    assert room == "boss_chamber"


def test_directive_without_area_id_is_skipped() -> None:
    """Directive without area_id is not a valid destination; schedule used."""
    provider = BasicNpcScheduleProvider()
    char_data = {
        "id": "npc_y",
        "schedule": {"dusk": "market"},
    }
    directives = [
        {
            "npc_id": "npc_y",
            "consumed": False,
            "directive": {
                "destination": {
                    "location_id": "square",
                    # no area_id!
                }
            },
        }
    ]
    area, loc, room = provider._scheduled_destination(
        char_data, "dusk", set(), npc_directives=directives
    )
    # No area_id → skip directive, fallback to schedule
    assert area is None
    assert loc == "market"


# ---------------------------------------------------------------------------
# 3-E: retire_quest assigns reward service before retiring
# ---------------------------------------------------------------------------


def _make_quest_manager_context(
    *,
    quest_id: str,
    objectives_completed: bool,
    rewards_claimed: bool,
    rewards: dict,
    has_receptionist: bool = True,
) -> tuple[Any, Any]:
    """Build a fake context with the necessary state for retire_quest testing."""
    from app.game_core.planning.quest_manager import QuestManagerSubSystem
    from app.game_core.planning.subsystem import PlannerDispatcher

    quest_data = {
        "status": "completed",
        "objectives": [{"description": "Kill 5 goblins", "completed": objectives_completed}],
        "rewards": rewards,
        "rewards_claimed": rewards_claimed,
    }

    class FakeQuestState:
        def get_dynamic_quest(self, qid: str) -> dict | None:
            return dict(quest_data) if qid == quest_id else None

    class FakeNarrativePlan:
        def get_services(self, npc_id: str) -> list:
            return []

    class FakeState:
        quests = FakeQuestState()
        narrative_plan = FakeNarrativePlan()

        def has_slice(self, name: str) -> bool:
            return name in {"quests", "narrative_plan"}

    class FakeCharTemplate:
        def __init__(self, npc_id: str, tags: list[str]) -> None:
            self.id = npc_id
            self.tags = tags

    class FakeCharRegistry:
        def list_all(self) -> list:
            if has_receptionist:
                return [FakeCharTemplate("receptionist_npc", ["receptionist"])]
            return []

    class FakeWorld:
        characters = FakeCharRegistry()

        def has_registry(self, name: str) -> bool:
            return name == "characters"

    applied_directives: list[dict] = []

    class FakeDispatcher:
        def apply_directive(self, kind: str, payload: dict, ctx: Any, *, current_tick: int) -> None:
            applied_directives.append({"kind": kind, "payload": payload})

    class FakeContext:
        state = FakeState()
        world = FakeWorld()

        def execute_command(self, cmd: Any) -> Any:
            class R:
                executed = True
                errors: list = []
                metadata: dict = {}
            return R()

    dispatcher = FakeDispatcher()
    context = FakeContext()
    qs = QuestManagerSubSystem(dispatcher=dispatcher)
    return qs, context, applied_directives


def test_retire_quest_assigns_reward_when_all_objectives_completed() -> None:
    """retire_quest should dispatch assign_service if all objectives complete, rewards unclaimed."""
    from app.game_core.planning.quest_manager import QuestManagerSubSystem

    applied: list[dict] = []

    class FakeQuestState:
        def get_dynamic_quest(self, qid: str) -> dict | None:
            return {
                "status": "completed",
                "objectives": [{"description": "Kill goblins", "completed": True}],
                "rewards": {"gold": 100, "xp": 50},
                "rewards_claimed": False,
            }

    class FakeNarrativePlan:
        def get_services(self, npc_id: str) -> list:
            return []

    class FakeState:
        quests = FakeQuestState()
        narrative_plan = FakeNarrativePlan()

        def has_slice(self, name: str) -> bool:
            return name in {"quests", "narrative_plan"}

    class FakeCharTemplate:
        id = "receptionist_npc"
        tags = ["receptionist"]

    class FakeCharRegistry:
        def list_all(self) -> list:
            return [FakeCharTemplate()]

    class FakeWorld:
        characters = FakeCharRegistry()

        def has_registry(self, name: str) -> bool:
            return name == "characters"

    class FakeDispatcher:
        def apply_directive(self, kind: str, payload: dict, ctx: Any, *, current_tick: int) -> None:
            applied.append({"kind": kind, "payload": payload})

    class FakeContext:
        state = FakeState()
        world = FakeWorld()

        def execute_command(self, cmd: Any) -> Any:
            class R:
                executed = True
                errors: list = []
                metadata: dict = {}
            return R()

    qs = QuestManagerSubSystem(dispatcher=FakeDispatcher())
    result = qs._apply_retire_quest(
        {"quest_id": "q_goblins"},
        FakeContext(),
        current_tick=10,
    )

    assert result is True
    assert len(applied) == 1
    assert applied[0]["kind"] == "assign_service"
    svc = applied[0]["payload"]
    assert svc["npc_id"] == "receptionist_npc"
    assert svc["service_id"] == "reward_q_goblins"
    assert any(e["type"] == "modify_gold" for e in svc["effects"])
    assert svc["one_shot"] is True


def test_retire_quest_skips_reward_when_already_claimed() -> None:
    """retire_quest must not re-assign reward if rewards_claimed is True."""
    applied: list[dict] = []

    class FakeQuestState:
        def get_dynamic_quest(self, qid: str) -> dict | None:
            return {
                "status": "completed",
                "objectives": [{"description": "Kill goblins", "completed": True}],
                "rewards": {"gold": 100},
                "rewards_claimed": True,  # already claimed
            }

    class FakeState:
        quests = FakeQuestState()

        def has_slice(self, name: str) -> bool:
            return name == "quests"

    class FakeDispatcher:
        def apply_directive(self, kind: str, payload: dict, ctx: Any, *, current_tick: int) -> None:
            applied.append({"kind": kind, "payload": payload})

    class FakeContext:
        state = FakeState()

        def execute_command(self, cmd: Any) -> Any:
            class R:
                executed = True
                errors: list = []
                metadata: dict = {}
            return R()

    from app.game_core.planning.quest_manager import QuestManagerSubSystem
    qs = QuestManagerSubSystem(dispatcher=FakeDispatcher())
    result = qs._apply_retire_quest(
        {"quest_id": "q_goblins"},
        FakeContext(),
        current_tick=10,
    )

    assert result is True
    assert len(applied) == 0  # no assign_service dispatched
