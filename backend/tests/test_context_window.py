"""Tests for ContextWindow and WindowMessage (N-1 Phase 1)."""

from __future__ import annotations

from app.game_core.narrative.context_window import ContextWindow, WindowMessage, window_to_history


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
        # graphize_counter reaches graphize_threshold exactly
        cw = ContextWindow(actor_id="npc_01", max_tokens=1000, graphize_threshold=90)
        result = cw.add_message(_msg(tokens=90))   # exactly at threshold
        assert result is True

    def test_add_message_returns_true_above_threshold(self) -> None:
        # graphize_counter exceeds graphize_threshold
        cw = ContextWindow(actor_id="npc_01", max_tokens=1000, graphize_threshold=90)
        result = cw.add_message(_msg(tokens=95))   # above threshold
        assert result is True

    def test_should_graphize_false_below_threshold(self) -> None:
        cw = ContextWindow(actor_id="npc_01", max_tokens=1000, overflow_threshold=0.9)
        cw.add_message(_msg(tokens=500))
        assert cw.should_graphize is False

    def test_should_graphize_true_at_threshold(self) -> None:
        # graphize_counter-based threshold (new in P19-C)
        cw = ContextWindow(actor_id="npc_01", max_tokens=1000, graphize_threshold=90)
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
# TestCollectForGraphize — replaces removed pop_oldest_for_graphize
# ------------------------------------------------------------------


class TestCollectForGraphize:
    def test_collect_returns_ungraphized_messages(self) -> None:
        """collect_for_graphize returns all un-graphized messages."""
        cw = ContextWindow(actor_id="npc_01")
        for i in range(5):
            cw.add_message(_msg(content=str(i), tokens=10))
        collected = cw.collect_for_graphize()
        assert len(collected) == 5
        assert collected[0].content == "0"
        assert collected[4].content == "4"

    def test_collect_does_not_remove_messages(self) -> None:
        """collect_for_graphize marks messages but keeps them in the window."""
        cw = ContextWindow(actor_id="npc_01")
        for _ in range(6):
            cw.add_message(_msg(tokens=10))
        cw.collect_for_graphize()
        assert len(cw.messages) == 6
        assert cw.current_tokens == 60

    def test_collect_marks_is_graphized(self) -> None:
        """Messages returned by collect_for_graphize are marked is_graphized=True."""
        cw = ContextWindow(actor_id="npc_01")
        for _ in range(4):
            cw.add_message(_msg(tokens=10))
        collected = cw.collect_for_graphize()
        assert all(m.is_graphized for m in collected)
        assert all(m.is_graphized for m in cw.messages)

    def test_collect_empty_window_returns_empty(self) -> None:
        cw = ContextWindow(actor_id="npc_01")
        assert cw.collect_for_graphize() == []
        assert cw.current_tokens == 0

    def test_collect_skips_already_graphized(self) -> None:
        """Second call to collect_for_graphize returns empty (all already marked)."""
        cw = ContextWindow(actor_id="npc_01")
        cw.add_message(_msg(tokens=10))
        cw.collect_for_graphize()  # first call marks everything
        second = cw.collect_for_graphize()
        assert second == []


# ------------------------------------------------------------------
# TestWindowMessageParts — parts field (Phase 2)
# ------------------------------------------------------------------


class TestWindowMessageParts:
    def test_parts_defaults_to_none(self) -> None:
        msg = WindowMessage(role="user", content="hi", token_count=5)
        assert msg.parts is None

    def test_parts_stored_when_provided(self) -> None:
        fc = {"function_call": {"name": "speak", "args": {"text": "hello"}}}
        msg = WindowMessage(role="model", content="hello", token_count=5, parts=[fc])
        assert msg.parts == [fc]


# ------------------------------------------------------------------
# TestExportImportParts — export_messages / import_messages with parts
# ------------------------------------------------------------------


class TestExportImportParts:
    def test_export_includes_parts_when_set(self) -> None:
        """export_messages must include 'parts' key when the message has parts."""
        cw = ContextWindow(actor_id="npc_x")
        fc = {"function_call": {"name": "speak", "args": {"text": "hi"}}}
        cw.add_message(WindowMessage(role="model", content="hi", token_count=2, parts=[fc]))
        exported = cw.export_messages()
        row = exported["messages"][0]
        assert "parts" in row
        assert row["parts"] == [fc]

    def test_export_omits_parts_when_none(self) -> None:
        """export_messages must NOT include 'parts' key when parts is None."""
        cw = ContextWindow(actor_id="npc_y")
        cw.add_message(WindowMessage(role="user", content="hi", token_count=2))
        exported = cw.export_messages()
        row = exported["messages"][0]
        assert "parts" not in row

    def test_import_restores_parts(self) -> None:
        """import_messages must restore the parts list from exported data."""
        cw = ContextWindow(actor_id="npc_z")
        fc = {"function_call": {"name": "emote", "args": {"action": "smile"}}}
        cw.add_message(WindowMessage(role="model", content="*smiles*", token_count=3, parts=[fc]))
        exported = cw.export_messages()

        cw2 = ContextWindow(actor_id="npc_z")
        cw2.import_messages(exported)
        assert len(cw2.messages) == 1
        assert cw2.messages[0].parts == [fc]

    def test_import_without_parts_gives_none(self) -> None:
        """import_messages on legacy data (no 'parts' key) must give parts=None."""
        data = {
            "messages": [{"role": "user", "content": "old msg", "token_count": 5}],
            "graphize_counter": 0,
        }
        cw = ContextWindow(actor_id="npc_old")
        cw.import_messages(data)
        assert cw.messages[0].parts is None

    def test_roundtrip_preserves_parts(self) -> None:
        """Full export → import roundtrip preserves parts field exactly."""
        import json
        cw = ContextWindow(actor_id="npc_rt")
        fc = {"function_call": {"name": "speak", "args": {"text": "test"}}}
        cw.add_message(WindowMessage(role="user", content="ping", token_count=1))
        cw.add_message(WindowMessage(role="model", content="pong", token_count=1, parts=[fc]))

        exported = cw.export_messages()
        # Must be JSON-serialisable
        json.dumps(exported)

        cw2 = ContextWindow(actor_id="npc_rt")
        cw2.import_messages(exported)
        assert cw2.messages[0].parts is None    # user message: no parts
        assert cw2.messages[1].parts == [fc]    # model message: parts preserved


# ------------------------------------------------------------------
# TestWindowToHistory — public module-level function (Phase 2c)
# ------------------------------------------------------------------


class TestWindowToHistory:
    def test_excludes_graphized_messages(self) -> None:
        """Graphized messages must be excluded from the history list."""
        cw = ContextWindow(actor_id="npc_hist")
        cw.add_message(WindowMessage(role="user", content="visible", token_count=1))
        cw.add_message(WindowMessage(
            role="model", content="hidden", token_count=1, is_graphized=True,
        ))
        history = window_to_history(cw)
        assert len(history) == 1
        assert history[0]["role"] == "user"

    def test_text_fallback_when_parts_is_none(self) -> None:
        """When parts=None, the content text must be wrapped in a text part."""
        cw = ContextWindow(actor_id="npc_tf")
        cw.add_message(WindowMessage(role="user", content="hello", token_count=2))
        history = window_to_history(cw)
        assert history == [{"role": "user", "parts": [{"text": "hello"}]}]

    def test_parts_preferred_over_content(self) -> None:
        """When parts is set, it must be used directly without content wrapping."""
        fc = {"function_call": {"name": "speak", "args": {"text": "hi"}}}
        cw = ContextWindow(actor_id="npc_pp")
        cw.add_message(WindowMessage(role="model", content="hi", token_count=2, parts=[fc]))
        history = window_to_history(cw)
        assert history == [{"role": "model", "parts": [fc]}]

    def test_parts_list_is_copied(self) -> None:
        """Mutation of returned parts list must not affect the stored WindowMessage."""
        fc = {"function_call": {"name": "speak", "args": {"text": "original"}}}
        cw = ContextWindow(actor_id="npc_copy")
        cw.add_message(WindowMessage(role="model", content="original", token_count=2, parts=[fc]))
        history = window_to_history(cw)
        history[0]["parts"].clear()
        assert cw.messages[0].parts == [fc]  # original unchanged

    def test_empty_window_returns_empty_list(self) -> None:
        cw = ContextWindow(actor_id="npc_empty")
        assert window_to_history(cw) == []
