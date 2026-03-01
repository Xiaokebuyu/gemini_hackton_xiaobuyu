"""Tests for ContextWindow and WindowMessage (N-1 Phase 1)."""

from __future__ import annotations

from app.game_core.narrative.context_window import ContextWindow, WindowMessage


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _msg(content: str = "hello", tokens: int = 10) -> WindowMessage:
    return WindowMessage(role="user", content=content, token_count=tokens)


# ------------------------------------------------------------------
# TestWindowMessage
# ------------------------------------------------------------------


class TestWindowMessage:
    def test_default_is_graphized_false(self) -> None:
        msg = WindowMessage(role="user", content="hi", token_count=5)
        assert msg.is_graphized is False

    def test_metadata_defaults_empty(self) -> None:
        msg = WindowMessage(role="assistant", content="reply", token_count=3)
        assert msg.metadata == {}

    def test_all_fields_assignable(self) -> None:
        msg = WindowMessage(
            role="system",
            content="ctx",
            token_count=100,
            metadata={"key": "val"},
            is_graphized=True,
        )
        assert msg.role == "system"
        assert msg.token_count == 100
        assert msg.metadata == {"key": "val"}
        assert msg.is_graphized is True


# ------------------------------------------------------------------
# TestContextWindow — basic properties and token accounting
# ------------------------------------------------------------------


class TestContextWindow:
    def test_initial_state(self) -> None:
        cw = ContextWindow(actor_id="npc_01")
        assert cw.actor_id == "npc_01"
        assert cw.current_tokens == 0
        assert len(cw.messages) == 0
        assert cw.usage_ratio == 0.0
        assert cw.should_graphize is False

    def test_add_message_increments_tokens(self) -> None:
        cw = ContextWindow(actor_id="npc_01")
        cw.add_message(_msg(tokens=50))
        cw.add_message(_msg(tokens=30))
        assert cw.current_tokens == 80
        assert len(cw.messages) == 2

    def test_add_message_returns_false_below_threshold(self) -> None:
        cw = ContextWindow(actor_id="npc_01", max_tokens=1000)
        result = cw.add_message(_msg(tokens=100))  # 10 % — below 90 %
        assert result is False

    def test_add_message_returns_true_at_threshold(self) -> None:
        cw = ContextWindow(actor_id="npc_01", max_tokens=100, overflow_threshold=0.9)
        result = cw.add_message(_msg(tokens=90))   # exactly 90 %
        assert result is True

    def test_add_message_returns_true_above_threshold(self) -> None:
        cw = ContextWindow(actor_id="npc_01", max_tokens=100, overflow_threshold=0.9)
        result = cw.add_message(_msg(tokens=95))   # 95 %
        assert result is True

    def test_should_graphize_false_below_threshold(self) -> None:
        cw = ContextWindow(actor_id="npc_01", max_tokens=1000, overflow_threshold=0.9)
        cw.add_message(_msg(tokens=500))
        assert cw.should_graphize is False

    def test_should_graphize_true_at_threshold(self) -> None:
        cw = ContextWindow(actor_id="npc_01", max_tokens=100, overflow_threshold=0.9)
        cw.add_message(_msg(tokens=90))
        assert cw.should_graphize is True

    def test_available_tokens(self) -> None:
        cw = ContextWindow(actor_id="npc_01", max_tokens=200)
        cw.add_message(_msg(tokens=60))
        assert cw.available_tokens == 140

    def test_available_tokens_never_negative(self) -> None:
        cw = ContextWindow(actor_id="npc_01", max_tokens=10)
        cw.add_message(_msg(tokens=20))  # exceed cap
        assert cw.available_tokens == 0

    def test_usage_ratio_zero_max_tokens(self) -> None:
        cw = ContextWindow(actor_id="npc_01", max_tokens=0)
        assert cw.usage_ratio == 0.0

    def test_snapshot_serializable(self) -> None:
        cw = ContextWindow(actor_id="npc_01", max_tokens=1000)
        cw.add_message(_msg(tokens=100))
        snap = cw.snapshot()
        assert snap["actor_id"] == "npc_01"
        assert snap["current_tokens"] == 100
        assert snap["message_count"] == 1
        assert snap["max_tokens"] == 1000
        assert isinstance(snap["usage_ratio"], float)
        assert isinstance(snap["should_graphize"], bool)
        # Must be JSON-serialisable
        import json
        json.dumps(snap)  # should not raise


# ------------------------------------------------------------------
# TestPopOldestForGraphize
# ------------------------------------------------------------------


class TestPopOldestForGraphize:
    def test_pop_removes_oldest_messages(self) -> None:
        cw = ContextWindow(actor_id="npc_01")
        for i in range(9):
            cw.add_message(_msg(content=str(i), tokens=10))
        popped = cw.pop_oldest_for_graphize(fraction=1 / 3)
        assert len(popped) == 3
        assert popped[0].content == "0"
        assert popped[1].content == "1"
        assert popped[2].content == "2"
        assert len(cw.messages) == 6

    def test_pop_recomputes_current_tokens(self) -> None:
        cw = ContextWindow(actor_id="npc_01")
        for _ in range(6):
            cw.add_message(_msg(tokens=10))   # total = 60
        cw.pop_oldest_for_graphize(fraction=1 / 2)  # remove 3 → 30 left
        assert cw.current_tokens == 30

    def test_pop_marks_is_graphized(self) -> None:
        cw = ContextWindow(actor_id="npc_01")
        for _ in range(6):
            cw.add_message(_msg(tokens=10))
        popped = cw.pop_oldest_for_graphize(fraction=0.5)
        assert all(m.is_graphized for m in popped)
        assert all(not m.is_graphized for m in cw.messages)

    def test_pop_empty_window_returns_empty(self) -> None:
        cw = ContextWindow(actor_id="npc_01")
        assert cw.pop_oldest_for_graphize() == []
        assert cw.current_tokens == 0

    def test_pop_at_least_one_message(self) -> None:
        """Even with tiny fraction, at least one message is popped."""
        cw = ContextWindow(actor_id="npc_01")
        cw.add_message(_msg(tokens=10))
        popped = cw.pop_oldest_for_graphize(fraction=0.001)
        assert len(popped) == 1
        assert len(cw.messages) == 0
