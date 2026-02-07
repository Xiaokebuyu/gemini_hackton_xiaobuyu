"""
Tests for SessionHistory and SessionHistoryManager.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.session_history import SessionHistory, SessionHistoryManager


class TestSessionHistory:
    """Unit tests for SessionHistory."""

    def test_record_round_basic(self):
        """Record a round and verify stats."""
        history = SessionHistory(world_id="test_world", session_id="test_session")

        result = history.record_round(
            player_input="我观察周围环境",
            gm_response="你看到一片宁静的森林，阳光穿过树冠洒下斑驳的光影。",
        )

        assert result["message_count"] == 2
        assert result["total_tokens"] > 0
        assert result["round_tokens"] > 0
        assert result["usage_ratio"] < 0.01  # 远低于阈值
        assert result["should_graphize"] is False

    def test_record_multiple_rounds(self):
        """Record multiple rounds and verify accumulation."""
        history = SessionHistory(world_id="test_world", session_id="test_session")

        for i in range(5):
            result = history.record_round(
                player_input=f"行动 {i}",
                gm_response=f"GM 响应 {i}: 这是一段描述性的文字。",
            )

        assert result["message_count"] == 10  # 5 rounds * 2 messages
        assert result["total_tokens"] > 0

    def test_record_teammate_response(self):
        """Record teammate responses."""
        history = SessionHistory(world_id="test_world", session_id="test_session")

        history.record_round("你好", "你好，冒险者。")
        history.record_teammate_response(
            character_id="priestess",
            name="女神官",
            response="哥布林杀手先生，我们要小心行事。",
        )

        assert history._window.message_count == 3

    def test_get_recent_history(self):
        """Get recent history formatted as text."""
        history = SessionHistory(world_id="test_world", session_id="test_session")

        history.record_round("我走进酒馆", "酒馆里热闹非凡，酒杯碰撞的声响此起彼伏。")
        history.record_round("我找一个位置坐下", "你在角落找到了一张空桌。")

        text = history.get_recent_history()
        assert "玩家: 我走进酒馆" in text
        assert "GM: 酒馆里热闹非凡" in text
        assert "玩家: 我找一个位置坐下" in text

    def test_get_recent_history_empty(self):
        """Empty history returns empty string."""
        history = SessionHistory(world_id="test_world", session_id="test_session")
        assert history.get_recent_history() == ""

    def test_get_recent_messages(self):
        """Get recent messages as list of dicts."""
        history = SessionHistory(world_id="test_world", session_id="test_session")

        history.record_round("输入1", "输出1")
        history.record_round("输入2", "输出2")

        messages = history.get_recent_messages(count=3)
        assert len(messages) == 3  # last 3 of 4
        assert messages[-1]["content"] == "输出2"

    def test_stats(self):
        """Verify stats output."""
        history = SessionHistory(world_id="w1", session_id="s1")
        stats = history.stats
        assert stats["world_id"] == "w1"
        assert stats["session_id"] == "s1"
        assert stats["message_count"] == 0
        assert stats["total_graphize_runs"] == 0
        assert stats["graphize_in_progress"] is False

    def test_graphize_trigger_on_high_usage(self):
        """Graphize should be triggered when usage exceeds threshold."""
        # Use a very small window to easily trigger
        history = SessionHistory(
            world_id="test_world",
            session_id="test_session",
            max_tokens=100,
            graphize_threshold=0.9,
            keep_recent_tokens=20,
        )

        # Fill the window past the threshold
        long_text = "这是一段很长的文本用来测试。" * 20
        result = history.record_round(
            player_input=long_text,
            gm_response=long_text,
        )

        assert result["should_graphize"] is True

    @pytest.mark.asyncio
    async def test_maybe_graphize_not_triggered(self):
        """maybe_graphize returns None when not triggered."""
        history = SessionHistory(world_id="test_world", session_id="test_session")
        history.record_round("短输入", "短输出")

        graphizer = MagicMock()
        result = await history.maybe_graphize(graphizer=graphizer)

        assert result is None
        graphizer.graphize.assert_not_called()

    @pytest.mark.asyncio
    async def test_maybe_graphize_triggered(self):
        """maybe_graphize runs graphizer when triggered."""
        history = SessionHistory(
            world_id="test_world",
            session_id="test_session",
            max_tokens=100,
            graphize_threshold=0.5,
            keep_recent_tokens=10,
        )

        long_text = "测试文本填充。" * 30
        history.record_round(long_text, long_text)

        # Mock graphizer
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.nodes_added = 3
        mock_result.edges_added = 5
        mock_result.error = None

        graphizer = AsyncMock()
        graphizer.graphize = AsyncMock(return_value=mock_result)

        result = await history.maybe_graphize(graphizer=graphizer, game_day=1)

        assert result is not None
        assert result["success"] is True
        assert result["nodes_added"] == 3
        assert result["edges_added"] == 5
        graphizer.graphize.assert_called_once()

    @pytest.mark.asyncio
    async def test_maybe_graphize_failure_raises(self):
        """maybe_graphize raises when graphizer fails."""
        history = SessionHistory(
            world_id="test_world",
            session_id="test_session",
            max_tokens=100,
            graphize_threshold=0.5,
            keep_recent_tokens=10,
        )

        long_text = "测试文本填充。" * 30
        history.record_round(long_text, long_text)

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.error = "LLM extraction failed"

        graphizer = AsyncMock()
        graphizer.graphize = AsyncMock(return_value=mock_result)

        with pytest.raises(RuntimeError, match="Graphization failed"):
            await history.maybe_graphize(graphizer=graphizer, game_day=1)


class TestSessionHistoryManager:
    """Unit tests for SessionHistoryManager."""

    def test_get_or_create(self):
        """get_or_create creates new history."""
        manager = SessionHistoryManager()
        h1 = manager.get_or_create("world1", "session1")
        assert h1 is not None
        assert h1.world_id == "world1"
        assert h1.session_id == "session1"

    def test_get_or_create_same_key(self):
        """get_or_create returns same instance for same key."""
        manager = SessionHistoryManager()
        h1 = manager.get_or_create("world1", "session1")
        h2 = manager.get_or_create("world1", "session1")
        assert h1 is h2

    def test_get_or_create_different_keys(self):
        """get_or_create returns different instances for different keys."""
        manager = SessionHistoryManager()
        h1 = manager.get_or_create("world1", "session1")
        h2 = manager.get_or_create("world1", "session2")
        assert h1 is not h2

    def test_get_nonexistent(self):
        """get returns None for nonexistent session."""
        manager = SessionHistoryManager()
        assert manager.get("world1", "session1") is None

    def test_get_existing(self):
        """get returns existing history."""
        manager = SessionHistoryManager()
        h1 = manager.get_or_create("world1", "session1")
        h2 = manager.get("world1", "session1")
        assert h1 is h2

    def test_remove(self):
        """remove deletes the session history."""
        manager = SessionHistoryManager()
        manager.get_or_create("world1", "session1")
        assert manager.active_count == 1

        manager.remove("world1", "session1")
        assert manager.active_count == 0
        assert manager.get("world1", "session1") is None

    def test_remove_nonexistent(self):
        """remove on nonexistent key is no-op."""
        manager = SessionHistoryManager()
        manager.remove("world1", "session1")  # should not raise

    def test_active_count(self):
        """active_count tracks number of histories."""
        manager = SessionHistoryManager()
        assert manager.active_count == 0

        manager.get_or_create("w1", "s1")
        assert manager.active_count == 1

        manager.get_or_create("w1", "s2")
        assert manager.active_count == 2

        manager.get_or_create("w1", "s1")  # same key
        assert manager.active_count == 2
