"""Player 持久化路径加固 — 单元测试。

验证 persist() 中 player 脏标记时序 + CharacterStore 兜底 + 快照失败日志级别。
"""
from __future__ import annotations

import asyncio
import logging
import sys
from types import ModuleType
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Stub out 'mcp' package if not installed (needed to import admin services)
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

from app.models.player_character import (
    CharacterClass,
    CharacterRace,
    PlayerCharacter,
)
from app.runtime.session_runtime import SessionRuntime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _make_player_character() -> PlayerCharacter:
    return PlayerCharacter(
        name="TestHero",
        race=CharacterRace.HUMAN,
        character_class=CharacterClass.FIGHTER,
        abilities={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
        max_hp=12,
        current_hp=12,
        ac=16,
        initiative_bonus=2,
        gold=50,
    )


def _make_session(
    *,
    world_graph: Any = None,
    world_graph_failed: bool = False,
    character_store: Any = None,
    player_character: Optional[PlayerCharacter] = None,
    dirty_player: bool = True,
) -> SessionRuntime:
    """构造最小可测的 SessionRuntime。"""
    session = SessionRuntime(
        world_id="w1",
        session_id="s1",
        character_store=character_store,
    )
    session.world_graph = world_graph
    session._world_graph_failed = world_graph_failed
    session._player_character = player_character or _make_player_character()
    session._dirty_player = dirty_player
    session._restored = True
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_persist_snapshot_ok_clears_dirty():
    """快照成功 → _dirty_player 清除 + 'player' in persisted。"""
    session = _make_session(world_graph=MagicMock())
    session._persist_world_graph_snapshot = AsyncMock(return_value=True)
    session._fallback_persist_player = AsyncMock(return_value=True)

    _run(session.persist())

    assert session._dirty_player is False
    session._persist_world_graph_snapshot.assert_awaited_once()
    # fallback 也被调用（belt & suspenders）
    session._fallback_persist_player.assert_awaited_once()


def test_persist_snapshot_fail_fallback_ok():
    """快照失败 + 兜底成功 → 脏标记清除 + 'player(fallback)' in persisted。"""
    session = _make_session(world_graph=MagicMock())
    session._persist_world_graph_snapshot = AsyncMock(return_value=False)
    session._fallback_persist_player = AsyncMock(return_value=True)

    # 捕获 persisted 列表
    with patch.object(logging.getLogger("app.runtime.session_runtime"), "info") as mock_log:
        _run(session.persist())

    assert session._dirty_player is False
    # 检查 persisted 日志中包含 "player(fallback)"
    log_calls = [str(c) for c in mock_log.call_args_list]
    persist_log = [c for c in log_calls if "persist 完成" in c]
    assert any("player(fallback)" in c for c in persist_log)


def test_persist_both_fail_retains_dirty():
    """两者均失败 → 脏标记保留。"""
    session = _make_session(world_graph=MagicMock())
    session._persist_world_graph_snapshot = AsyncMock(return_value=False)
    session._fallback_persist_player = AsyncMock(return_value=False)

    with patch.object(logging.getLogger("app.runtime.session_runtime"), "error") as mock_err:
        _run(session.persist())

    assert session._dirty_player is True
    # 验证 error 日志
    err_calls = [str(c) for c in mock_err.call_args_list]
    assert any("Player 数据未持久化" in c for c in err_calls)


def test_persist_snapshot_fail_no_world_graph():
    """world_graph_failed → 快照跳过，走兜底。"""
    mock_store = MagicMock()
    mock_store.save_character = AsyncMock()

    session = _make_session(
        world_graph=MagicMock(),
        world_graph_failed=True,
        character_store=mock_store,
    )

    _run(session.persist())

    # 快照不会被调用（world_graph_failed=True → persist 的 if 条件跳过）
    # 兜底应被调用
    mock_store.save_character.assert_awaited_once()
    assert session._dirty_player is False


def test_fallback_converts_player_node_view():
    """PlayerNodeView → PlayerCharacter 转换正确并写入 CharacterStore。"""
    mock_store = MagicMock()
    mock_store.save_character = AsyncMock()

    session = _make_session(character_store=mock_store)

    # 模拟 PlayerNodeView（非 PlayerCharacter 实例）
    mock_view = MagicMock()
    mock_view.model_dump.return_value = _make_player_character().model_dump()

    # 让 session.player 返回 mock_view
    with patch.object(type(session), "player", new_callable=lambda: property(lambda self: mock_view)):
        result = _run(session._fallback_persist_player())

    assert result is True
    mock_store.save_character.assert_awaited_once()
    saved_pc = mock_store.save_character.call_args[0][2]
    assert isinstance(saved_pc, PlayerCharacter)
    assert saved_pc.name == "TestHero"


def test_snapshot_fail_logs_error_not_warning():
    """快照失败记录 error 级别（非 warning）。"""
    session = _make_session(world_graph=MagicMock())

    # 让 capture_snapshot 抛异常
    with patch("app.runtime.session_runtime.logger") as mock_logger:
        with patch(
            "app.world.snapshot.capture_snapshot",
            side_effect=RuntimeError("firestore down"),
        ):
            result = _run(session._persist_world_graph_snapshot())

    assert result is False
    mock_logger.error.assert_called()
    err_msg = str(mock_logger.error.call_args)
    assert "快照保存失败" in err_msg


def test_snapshot_ok_fallback_also_writes():
    """快照成功时 _fallback_persist_player 也被调用（belt & suspenders）。"""
    session = _make_session(world_graph=MagicMock())
    session._persist_world_graph_snapshot = AsyncMock(return_value=True)
    session._fallback_persist_player = AsyncMock(return_value=True)

    _run(session.persist())

    # 兜底也被调用（即使快照成功）
    session._fallback_persist_player.assert_awaited_once()
    # 脏标记通过 snapshot_ok 清除
    assert session._dirty_player is False


def test_persist_player_not_dirty_skips_both():
    """player 未脏时，兜底和快照后的清除逻辑均跳过。"""
    mock_store = MagicMock()
    mock_store.save_character = AsyncMock()

    session = _make_session(
        world_graph=MagicMock(),
        character_store=mock_store,
        dirty_player=False,
    )
    session._persist_world_graph_snapshot = AsyncMock(return_value=True)

    _run(session.persist())

    # 兜底不应被调用（player 不脏）
    mock_store.save_character.assert_not_awaited()
    assert session._dirty_player is False
