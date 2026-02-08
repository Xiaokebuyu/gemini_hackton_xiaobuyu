"""ConditionEngine 单元测试。"""
import pytest

from app.models.narrative import Condition, ConditionGroup, ConditionType
from app.services.admin.condition_engine import ConditionEngine, ConditionResult, GameContext


@pytest.fixture
def engine():
    return ConditionEngine()


@pytest.fixture
def base_ctx():
    return GameContext(
        session_id="sess_1",
        area_id="guild_hall",
        sub_location=None,
        game_day=1,
        game_hour=14,
        game_minute=30,
        game_state="exploring",
        active_npc=None,
        party_member_ids=["elf", "dwarf"],
        events_triggered=["event_intro", "event_guild_visit"],
        objectives_completed=["obj_arrive"],
        rounds_in_chapter=5,
        npc_interactions={"guild_girl": 3, "goblin_slayer": 1},
        player_input="",
    )


# ---- 单条件测试 ----


class TestLocationCondition:
    def test_area_match(self, engine, base_ctx):
        cond = Condition(type=ConditionType.LOCATION, params={"area_id": "guild_hall"})
        result = engine.evaluate(ConditionGroup(conditions=[cond]), base_ctx)
        assert result.satisfied is True

    def test_area_mismatch(self, engine, base_ctx):
        cond = Condition(type=ConditionType.LOCATION, params={"area_id": "forest"})
        result = engine.evaluate(ConditionGroup(conditions=[cond]), base_ctx)
        assert result.satisfied is False

    def test_sub_location(self, engine, base_ctx):
        base_ctx.sub_location = "bar_counter"
        cond = Condition(type=ConditionType.LOCATION, params={
            "area_id": "guild_hall", "sub_location": "bar_counter"
        })
        result = engine.evaluate(ConditionGroup(conditions=[cond]), base_ctx)
        assert result.satisfied is True

    def test_sub_location_mismatch(self, engine, base_ctx):
        base_ctx.sub_location = "bar_counter"
        cond = Condition(type=ConditionType.LOCATION, params={
            "area_id": "guild_hall", "sub_location": "quest_board"
        })
        result = engine.evaluate(ConditionGroup(conditions=[cond]), base_ctx)
        assert result.satisfied is False


class TestNPCInteracted:
    def test_enough_interactions(self, engine, base_ctx):
        cond = Condition(type=ConditionType.NPC_INTERACTED, params={
            "npc_id": "guild_girl", "min_interactions": 2
        })
        result = engine.evaluate(ConditionGroup(conditions=[cond]), base_ctx)
        assert result.satisfied is True

    def test_not_enough_interactions(self, engine, base_ctx):
        cond = Condition(type=ConditionType.NPC_INTERACTED, params={
            "npc_id": "guild_girl", "min_interactions": 5
        })
        result = engine.evaluate(ConditionGroup(conditions=[cond]), base_ctx)
        assert result.satisfied is False

    def test_unknown_npc(self, engine, base_ctx):
        cond = Condition(type=ConditionType.NPC_INTERACTED, params={
            "npc_id": "unknown_npc", "min_interactions": 1
        })
        result = engine.evaluate(ConditionGroup(conditions=[cond]), base_ctx)
        assert result.satisfied is False


class TestTimePassed:
    def test_day_passed(self, engine, base_ctx):
        cond = Condition(type=ConditionType.TIME_PASSED, params={"min_day": 0, "min_hour": 12})
        result = engine.evaluate(ConditionGroup(conditions=[cond]), base_ctx)
        assert result.satisfied is True

    def test_time_not_reached(self, engine, base_ctx):
        cond = Condition(type=ConditionType.TIME_PASSED, params={"min_day": 2, "min_hour": 0})
        result = engine.evaluate(ConditionGroup(conditions=[cond]), base_ctx)
        assert result.satisfied is False


class TestRoundsElapsed:
    def test_within_range(self, engine, base_ctx):
        cond = Condition(type=ConditionType.ROUNDS_ELAPSED, params={"min_rounds": 3, "max_rounds": 10})
        result = engine.evaluate(ConditionGroup(conditions=[cond]), base_ctx)
        assert result.satisfied is True

    def test_below_min(self, engine, base_ctx):
        cond = Condition(type=ConditionType.ROUNDS_ELAPSED, params={"min_rounds": 6})
        result = engine.evaluate(ConditionGroup(conditions=[cond]), base_ctx)
        assert result.satisfied is False

    def test_above_max(self, engine, base_ctx):
        base_ctx.rounds_in_chapter = 20
        cond = Condition(type=ConditionType.ROUNDS_ELAPSED, params={"min_rounds": 0, "max_rounds": 10})
        result = engine.evaluate(ConditionGroup(conditions=[cond]), base_ctx)
        assert result.satisfied is False


class TestPartyContains:
    def test_member_present(self, engine, base_ctx):
        cond = Condition(type=ConditionType.PARTY_CONTAINS, params={"character_id": "elf"})
        result = engine.evaluate(ConditionGroup(conditions=[cond]), base_ctx)
        assert result.satisfied is True

    def test_member_absent(self, engine, base_ctx):
        cond = Condition(type=ConditionType.PARTY_CONTAINS, params={"character_id": "wizard"})
        result = engine.evaluate(ConditionGroup(conditions=[cond]), base_ctx)
        assert result.satisfied is False


class TestEventTriggered:
    def test_event_exists(self, engine, base_ctx):
        cond = Condition(type=ConditionType.EVENT_TRIGGERED, params={"event_id": "event_intro"})
        result = engine.evaluate(ConditionGroup(conditions=[cond]), base_ctx)
        assert result.satisfied is True

    def test_event_missing(self, engine, base_ctx):
        cond = Condition(type=ConditionType.EVENT_TRIGGERED, params={"event_id": "event_boss_fight"})
        result = engine.evaluate(ConditionGroup(conditions=[cond]), base_ctx)
        assert result.satisfied is False


class TestObjectiveCompleted:
    def test_completed(self, engine, base_ctx):
        cond = Condition(type=ConditionType.OBJECTIVE_COMPLETED, params={"objective_id": "obj_arrive"})
        result = engine.evaluate(ConditionGroup(conditions=[cond]), base_ctx)
        assert result.satisfied is True

    def test_not_completed(self, engine, base_ctx):
        cond = Condition(type=ConditionType.OBJECTIVE_COMPLETED, params={"objective_id": "obj_kill_boss"})
        result = engine.evaluate(ConditionGroup(conditions=[cond]), base_ctx)
        assert result.satisfied is False


class TestGameState:
    def test_match(self, engine, base_ctx):
        cond = Condition(type=ConditionType.GAME_STATE, params={"state": "exploring"})
        result = engine.evaluate(ConditionGroup(conditions=[cond]), base_ctx)
        assert result.satisfied is True

    def test_mismatch(self, engine, base_ctx):
        cond = Condition(type=ConditionType.GAME_STATE, params={"state": "combat"})
        result = engine.evaluate(ConditionGroup(conditions=[cond]), base_ctx)
        assert result.satisfied is False


class TestFlashEvaluate:
    def test_marks_as_pending(self, engine, base_ctx):
        cond = Condition(type=ConditionType.FLASH_EVALUATE, params={
            "prompt": "玩家是否表达了愿意接受任务？"
        })
        result = engine.evaluate(ConditionGroup(conditions=[cond]), base_ctx)
        assert result.satisfied is True  # 结构化部分视为满足
        assert len(result.pending_flash) == 1
        assert result.pending_flash[0].params["prompt"] == "玩家是否表达了愿意接受任务？"


# ---- 组合条件测试 ----


class TestConditionGroups:
    def test_and_all_true(self, engine, base_ctx):
        group = ConditionGroup(operator="and", conditions=[
            Condition(type=ConditionType.LOCATION, params={"area_id": "guild_hall"}),
            Condition(type=ConditionType.GAME_STATE, params={"state": "exploring"}),
        ])
        result = engine.evaluate(group, base_ctx)
        assert result.satisfied is True

    def test_and_one_false(self, engine, base_ctx):
        group = ConditionGroup(operator="and", conditions=[
            Condition(type=ConditionType.LOCATION, params={"area_id": "guild_hall"}),
            Condition(type=ConditionType.LOCATION, params={"area_id": "forest"}),
        ])
        result = engine.evaluate(group, base_ctx)
        assert result.satisfied is False

    def test_or_one_true(self, engine, base_ctx):
        group = ConditionGroup(operator="or", conditions=[
            Condition(type=ConditionType.LOCATION, params={"area_id": "forest"}),
            Condition(type=ConditionType.GAME_STATE, params={"state": "exploring"}),
        ])
        result = engine.evaluate(group, base_ctx)
        assert result.satisfied is True

    def test_or_all_false(self, engine, base_ctx):
        group = ConditionGroup(operator="or", conditions=[
            Condition(type=ConditionType.LOCATION, params={"area_id": "forest"}),
            Condition(type=ConditionType.GAME_STATE, params={"state": "combat"}),
        ])
        result = engine.evaluate(group, base_ctx)
        assert result.satisfied is False

    def test_not_inverts(self, engine, base_ctx):
        group = ConditionGroup(operator="not", conditions=[
            Condition(type=ConditionType.LOCATION, params={"area_id": "forest"}),
        ])
        result = engine.evaluate(group, base_ctx)
        assert result.satisfied is True  # NOT(False) = True

    def test_not_true_becomes_false(self, engine, base_ctx):
        group = ConditionGroup(operator="not", conditions=[
            Condition(type=ConditionType.LOCATION, params={"area_id": "guild_hall"}),
        ])
        result = engine.evaluate(group, base_ctx)
        assert result.satisfied is False  # NOT(True) = False

    def test_nested_groups(self, engine, base_ctx):
        """AND(location=guild_hall, OR(state=combat, party_contains=elf))"""
        group = ConditionGroup(operator="and", conditions=[
            Condition(type=ConditionType.LOCATION, params={"area_id": "guild_hall"}),
            ConditionGroup(operator="or", conditions=[
                Condition(type=ConditionType.GAME_STATE, params={"state": "combat"}),
                Condition(type=ConditionType.PARTY_CONTAINS, params={"character_id": "elf"}),
            ]),
        ])
        result = engine.evaluate(group, base_ctx)
        assert result.satisfied is True

    def test_flash_in_and_group(self, engine, base_ctx):
        """AND(location=guild_hall, flash_evaluate) → satisfied=True + pending_flash"""
        group = ConditionGroup(operator="and", conditions=[
            Condition(type=ConditionType.LOCATION, params={"area_id": "guild_hall"}),
            Condition(type=ConditionType.FLASH_EVALUATE, params={"prompt": "test?"}),
        ])
        result = engine.evaluate(group, base_ctx)
        assert result.satisfied is True  # 结构化部分满足
        assert len(result.pending_flash) == 1

    def test_flash_with_failed_structural(self, engine, base_ctx):
        """AND(location=forest, flash_evaluate) → satisfied=False + pending_flash"""
        group = ConditionGroup(operator="and", conditions=[
            Condition(type=ConditionType.LOCATION, params={"area_id": "forest"}),
            Condition(type=ConditionType.FLASH_EVALUATE, params={"prompt": "test?"}),
        ])
        result = engine.evaluate(group, base_ctx)
        assert result.satisfied is False  # 结构化条件不满足

    def test_empty_group(self, engine, base_ctx):
        group = ConditionGroup(conditions=[])
        result = engine.evaluate(group, base_ctx)
        assert result.satisfied is True


class TestDetails:
    def test_details_populated(self, engine, base_ctx):
        group = ConditionGroup(operator="and", conditions=[
            Condition(type=ConditionType.LOCATION, params={"area_id": "guild_hall"}),
            Condition(type=ConditionType.EVENT_TRIGGERED, params={"event_id": "event_intro"}),
        ])
        result = engine.evaluate(group, base_ctx)
        assert "location" in result.details
        assert "event_triggered:event_intro" in result.details
        assert result.details["location"] is True
        assert result.details["event_triggered:event_intro"] is True
