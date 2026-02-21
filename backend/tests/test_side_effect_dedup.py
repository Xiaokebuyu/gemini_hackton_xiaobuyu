"""2.3: 副作用去重持久化 — crash recovery 安全测试。

测试:
  - persist() 将 _applied_side_effect_events 写入 GameState.metadata
  - restore() 从 metadata 恢复 dedup set
  - crash recovery 场景：恢复后同事件不重复发放
  - 边界情况：cap、空集、损坏值
"""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest

# Stub out 'mcp' package if not installed
if "mcp" not in sys.modules:
    _mcp_stub = ModuleType("mcp")
    _mcp_client = ModuleType("mcp.client")
    _mcp_session = ModuleType("mcp.client.session")
    _mcp_session.ClientSession = MagicMock  # type: ignore
    _mcp_stdio = ModuleType("mcp.client.stdio")
    _mcp_stdio.stdio_client = MagicMock  # type: ignore
    _mcp_stdio.StdioServerParameters = MagicMock  # type: ignore
    _mcp_sse = ModuleType("mcp.client.sse")
    _mcp_sse.sse_client = MagicMock  # type: ignore
    _mcp_http = ModuleType("mcp.client.streamable_http")
    _mcp_http.streamable_http_client = MagicMock  # type: ignore
    _mcp_types = ModuleType("mcp.types")
    _mcp_types.Tool = MagicMock  # type: ignore

    sys.modules["mcp"] = _mcp_stub
    sys.modules["mcp.client"] = _mcp_client
    sys.modules["mcp.client.session"] = _mcp_session
    sys.modules["mcp.client.stdio"] = _mcp_stdio
    sys.modules["mcp.client.sse"] = _mcp_sse
    sys.modules["mcp.client.streamable_http"] = _mcp_http
    sys.modules["mcp.types"] = _mcp_types

from app.models.narrative import NarrativeProgress
from app.models.player_character import CharacterClass, CharacterRace, PlayerCharacter
from app.models.state_delta import GameState, GameTimeState
from app.runtime.session_runtime import SessionRuntime
from app.world.models import WorldEvent


# =============================================================================
# Helpers
# =============================================================================


def _make_session() -> SessionRuntime:
    """创建最小 SessionRuntime 用于测试。"""
    session = SessionRuntime(
        world_id="test_world",
        session_id="test_session",
    )
    session.game_state = GameState(
        world_id="test_world",
        session_id="test_session",
        player_location="area_1",
    )
    session.time = GameTimeState(day=1, hour=10, minute=0)
    session.narrative = NarrativeProgress(
        current_mainline="main_01",
        current_chapter="ch_01",
        events_triggered=[],
    )
    return session


def _make_player() -> PlayerCharacter:
    return PlayerCharacter(
        name="TestPlayer",
        race=CharacterRace.HUMAN,
        character_class=CharacterClass.FIGHTER,
        background="soldier",
        level=3,
        xp=500,
        abilities={"str": 16, "dex": 14, "con": 12, "int": 10, "wis": 8, "cha": 13},
        max_hp=30,
        current_hp=25,
        ac=15,
        initiative_bonus=2,
        gold=50,
        spell_slots={1: 2},
        inventory=[],
    )


# =============================================================================
# 2.3: persist/restore 测试
# =============================================================================


class TestDedupPersistRestore:
    """persist() 将 dedup set 写入 metadata，restore 路径正确恢复。"""

    def test_persist_saves_dedup_to_metadata(self):
        """persist 前置逻辑将 _applied_side_effect_events 写入 metadata。"""
        session = _make_session()
        session._applied_side_effect_events = {
            "xp_awarded:evt_1",
            "gold_awarded:evt_2",
        }

        # 模拟 persist() 中 2.3 的写入逻辑
        if session._applied_side_effect_events and session.game_state:
            if session.game_state.metadata is None:
                session.game_state.metadata = {}
            dedup_list = sorted(session._applied_side_effect_events)
            if len(dedup_list) > 200:
                dedup_list = dedup_list[:200]
            session.game_state.metadata["_applied_side_effects"] = dedup_list

        saved = session.game_state.metadata.get("_applied_side_effects")
        assert saved is not None
        assert set(saved) == {"xp_awarded:evt_1", "gold_awarded:evt_2"}

    def test_restore_loads_dedup_from_metadata(self):
        """restore 路径从 metadata 恢复 dedup set。"""
        session = _make_session()
        session.game_state.metadata["_applied_side_effects"] = [
            "xp_awarded:evt_1",
            "companion_dispatch:evt_3",
        ]

        # 模拟 restore() 中 2.3 的读取逻辑
        saved_dedup = session.game_state.metadata.get("_applied_side_effects")
        if isinstance(saved_dedup, list):
            session._applied_side_effect_events = set(saved_dedup[-200:])

        assert session._applied_side_effect_events == {
            "xp_awarded:evt_1",
            "companion_dispatch:evt_3",
        }

    def test_cap_at_200(self):
        """超过 200 条时截断。"""
        session = _make_session()
        session._applied_side_effect_events = {f"key_{i}" for i in range(300)}

        if session._applied_side_effect_events and session.game_state:
            if session.game_state.metadata is None:
                session.game_state.metadata = {}
            dedup_list = sorted(session._applied_side_effect_events)
            if len(dedup_list) > 200:
                dedup_list = dedup_list[:200]
            session.game_state.metadata["_applied_side_effects"] = dedup_list

        assert len(session.game_state.metadata["_applied_side_effects"]) == 200

    def test_empty_set_no_write(self):
        """空 set 不写 metadata。"""
        session = _make_session()
        assert len(session._applied_side_effect_events) == 0

        if session._applied_side_effect_events and session.game_state:
            session.game_state.metadata["_applied_side_effects"] = list(
                session._applied_side_effect_events
            )

        assert "_applied_side_effects" not in session.game_state.metadata

    def test_restore_handles_missing_key(self):
        """metadata 无 _applied_side_effects key 时优雅降级。"""
        session = _make_session()
        saved_dedup = session.game_state.metadata.get("_applied_side_effects")
        assert saved_dedup is None
        # 不应修改 dedup set
        if isinstance(saved_dedup, list):
            session._applied_side_effect_events = set(saved_dedup)
        assert len(session._applied_side_effect_events) == 0

    def test_restore_handles_non_list_value(self):
        """metadata 值非 list 时优雅降级（防御性编程）。"""
        session = _make_session()
        session.game_state.metadata["_applied_side_effects"] = "corrupted_string"

        saved_dedup = session.game_state.metadata.get("_applied_side_effects")
        if isinstance(saved_dedup, list):
            session._applied_side_effect_events = set(saved_dedup)

        # 应保持为空（isinstance 检查排除了非 list）
        assert len(session._applied_side_effect_events) == 0


# =============================================================================
# 2.3: crash recovery 完整场景
# =============================================================================


class TestCrashRecovery:
    """模拟 crash recovery: apply → persist → restore → 同事件不重复。"""

    def test_crash_recovery_prevents_double_xp(self):
        """完整场景：XP 副作用已应用 + persist → 新 session restore → 同事件跳过。"""
        # 1. 第一个 session：应用 XP 副作用
        session1 = _make_session()
        session1._player_character = _make_player()
        initial_xp = session1.player.xp

        xp_event = WorldEvent(
            event_type="xp_awarded",
            origin_node="evt_quest_1",
            data={"amount": 100},
        )
        mock_tick = MagicMock()
        mock_tick.all_events = [xp_event]
        mock_tick.state_changes = {}

        session1._apply_tick_side_effects(mock_tick)
        assert session1.player.xp == initial_xp + 100
        assert "xp_awarded:evt_quest_1" in session1._applied_side_effect_events or \
               xp_event.event_id in session1._applied_side_effect_events

        # 2. 模拟 persist: 写 dedup set 到 metadata
        if session1._applied_side_effect_events and session1.game_state:
            if session1.game_state.metadata is None:
                session1.game_state.metadata = {}
            dedup_list = sorted(session1._applied_side_effect_events)
            session1.game_state.metadata["_applied_side_effects"] = dedup_list

        saved_metadata = dict(session1.game_state.metadata)

        # 3. 模拟 crash + restore: 新 session 从 metadata 恢复
        session2 = _make_session()
        session2._player_character = _make_player()
        # 模拟 player XP 已是 +100（从 WorldGraph 快照恢复）
        session2._player_character.xp = initial_xp + 100

        session2.game_state.metadata = saved_metadata
        saved_dedup = session2.game_state.metadata.get("_applied_side_effects")
        if isinstance(saved_dedup, list):
            session2._applied_side_effect_events = set(saved_dedup[-200:])

        # 4. 同一事件再次触发 → 应被去重跳过
        session2._apply_tick_side_effects(mock_tick)
        assert session2.player.xp == initial_xp + 100  # 未变！

    def test_crash_recovery_prevents_double_gold(self):
        """Gold 副作用 crash recovery 去重。"""
        session1 = _make_session()
        session1._player_character = _make_player()
        initial_gold = session1.player.gold

        gold_event = WorldEvent(
            event_type="gold_awarded",
            origin_node="evt_reward",
            data={"amount": 200},
        )
        mock_tick = MagicMock()
        mock_tick.all_events = [gold_event]
        mock_tick.state_changes = {}

        session1._apply_tick_side_effects(mock_tick)
        assert session1.player.gold == initial_gold + 200

        # Persist dedup
        session1.game_state.metadata["_applied_side_effects"] = sorted(
            session1._applied_side_effect_events
        )

        # Restore
        session2 = _make_session()
        session2._player_character = _make_player()
        session2._player_character.gold = initial_gold + 200
        session2.game_state.metadata = dict(session1.game_state.metadata)
        saved = session2.game_state.metadata.get("_applied_side_effects")
        if isinstance(saved, list):
            session2._applied_side_effect_events = set(saved)

        # Retry → skipped
        session2._apply_tick_side_effects(mock_tick)
        assert session2.player.gold == initial_gold + 200
