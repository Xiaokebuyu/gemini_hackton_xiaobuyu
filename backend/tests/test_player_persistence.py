"""Player 持久化路径 — 单元测试（无 fallback）。"""
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

    _run(session.persist())

    assert session._dirty_player is False
    session._persist_world_graph_snapshot.assert_awaited_once()


def test_persist_snapshot_fail_retains_dirty():
    """快照失败时（无 fallback）应保留 _dirty_player。"""
    session = _make_session(world_graph=MagicMock())
    session._persist_world_graph_snapshot = AsyncMock(return_value=False)

    with patch.object(logging.getLogger("app.runtime.session_runtime"), "error") as mock_err:
        _run(session.persist())

    assert session._dirty_player is True
    err_calls = [str(c) for c in mock_err.call_args_list]
    assert any("Player 数据未持久化" in c for c in err_calls)


def test_persist_world_graph_failed_retains_dirty():
    """world_graph_failed=True 时跳过快照并保留 _dirty_player。"""
    mock_store = MagicMock()
    mock_store.save_character = AsyncMock()

    session = _make_session(
        world_graph=MagicMock(),
        world_graph_failed=True,
        character_store=mock_store,
    )

    _run(session.persist())

    # 无 fallback：CharacterStore 不应被触发
    mock_store.save_character.assert_not_awaited()
    assert session._dirty_player is True


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


def test_snapshot_ok_does_not_touch_character_store():
    """快照成功时不触发 CharacterStore。"""
    mock_store = MagicMock()
    mock_store.save_character = AsyncMock()

    session = _make_session(world_graph=MagicMock())
    session._character_store = mock_store
    session._persist_world_graph_snapshot = AsyncMock(return_value=True)

    _run(session.persist())

    mock_store.save_character.assert_not_awaited()
    assert session._dirty_player is False


def test_persist_player_not_dirty_skips_both():
    """player 未脏时，不触发 CharacterStore 路径。"""
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
