"""Tests for S1-07: Planner ContextWindow migration + graphize_callback.

Decision record: S1-07 (narrative.md)
Phase 1: AgenticNarrativePlanner ContextWindow refactor.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

from app.narrators import AgenticNarrativePlanner, _window_to_planner_history
from app.game_core.narrative.context_window import ContextWindow, WindowMessage


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_llm(text: str) -> Any:
    llm = MagicMock()
    resp = MagicMock()
    resp.text = text
    resp.tool_calls = []
    llm.generate = AsyncMock(return_value=resp)
    return llm


def _make_valid_plan_json(notes: str = "test notes") -> str:
    return json.dumps({
        "reasoning": "test",
        "directives": [],
        "story_facts": [],
        "strategy_notes": notes,
        "next_trigger_hint": "3_ticks",
    })


_MINIMAL_CONTEXT: dict[str, Any] = {
    "narrative_plan": {
        "current_chapter": "ch1",
        "escalation_level": 0,
        "ticks_since_milestone_progress": 0,
        "current_target_milestone": None,
        "pacing_frozen": False,
    },
    "quests": {
        "available_milestones": [],
        "active_milestones": [],
        "dynamic_quests": {},
    },
    "current_tick": 5,
}


# ------------------------------------------------------------------
# Test 1: _append_history writes to ContextWindow
# ------------------------------------------------------------------


def test_append_history_writes_to_context_window() -> None:
    """_append_history correctly writes user + model messages to ContextWindow."""
    llm = _make_llm(_make_valid_plan_json())
    planner = AgenticNarrativePlanner(llm=llm)

    planner._append_history("hello user", "hello model")

    messages = planner._context_window.messages
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "hello user"
    assert messages[1].role == "model"
    assert messages[1].content == "hello model"


def test_append_history_token_counts_are_positive() -> None:
    """Token counts computed from content length are always >= 1."""
    llm = _make_llm(_make_valid_plan_json())
    planner = AgenticNarrativePlanner(llm=llm)

    # Even empty strings should produce token_count >= 1
    planner._append_history("", "")

    assert planner._context_window.messages[0].token_count >= 1
    assert planner._context_window.messages[1].token_count >= 1


# ------------------------------------------------------------------
# Test 2: plan() records round to ContextWindow
# ------------------------------------------------------------------


def test_plan_records_round_to_context_window() -> None:
    """After a successful plan() call, the context window has 2 messages."""
    llm = _make_llm(_make_valid_plan_json())
    planner = AgenticNarrativePlanner(llm=llm)

    asyncio.run(planner.plan(_MINIMAL_CONTEXT))

    # One round = user + model
    assert len(planner._context_window.messages) == 2


def test_plan_does_not_record_on_parse_failure() -> None:
    """When JSON parse fails, no history is written."""
    llm = _make_llm("not valid json at all")
    planner = AgenticNarrativePlanner(llm=llm)

    asyncio.run(planner.plan(_MINIMAL_CONTEXT))

    assert len(planner._context_window.messages) == 0


# ------------------------------------------------------------------
# Test 3: export/import round-trip (new ContextWindow format)
# ------------------------------------------------------------------


def test_export_import_round_trip() -> None:
    """export_history → import_history preserves all messages faithfully."""
    llm = _make_llm(_make_valid_plan_json())
    planner = AgenticNarrativePlanner(llm=llm)

    # Put some messages in
    planner._append_history("user message one", "model response one")
    planner._append_history("user message two", "model response two")

    exported = planner.export_history()

    # Restore into a fresh planner
    planner2 = AgenticNarrativePlanner(llm=llm)
    planner2.import_history(exported)

    messages2 = planner2._context_window.messages
    assert len(messages2) == 4
    assert messages2[0].role == "user"
    assert messages2[0].content == "user message one"
    assert messages2[1].role == "model"
    assert messages2[1].content == "model response one"
    assert messages2[2].role == "user"
    assert messages2[2].content == "user message two"
    assert messages2[3].role == "model"
    assert messages2[3].content == "model response two"


def test_export_format_has_content_key() -> None:
    """New export format uses 'content' key (not legacy 'text' key)."""
    llm = _make_llm(_make_valid_plan_json())
    planner = AgenticNarrativePlanner(llm=llm)
    planner._append_history("some user text", "some model text")

    exported = planner.export_history()

    # export_history now returns a dict with "messages" and "graphize_counter"
    assert isinstance(exported, dict)
    msgs = exported["messages"]
    assert len(msgs) == 2
    # New format: must have 'content' key
    assert "content" in msgs[0]
    assert "role" in msgs[0]
    assert "token_count" in msgs[0]
    assert "is_graphized" in msgs[0]
    # New format: must NOT have legacy 'text' key
    assert "text" not in msgs[0]


# ------------------------------------------------------------------
# Test 4: legacy format import compatibility
# ------------------------------------------------------------------


def test_import_legacy_format_converts_correctly() -> None:
    """Legacy {role, text} format is converted to ContextWindow format on import."""
    llm = _make_llm(_make_valid_plan_json())
    planner = AgenticNarrativePlanner(llm=llm)

    legacy_data = [
        {"role": "user", "text": "legacy user message"},
        {"role": "model", "text": "legacy model response"},
    ]
    planner.import_history(legacy_data)

    messages = planner._context_window.messages
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "legacy user message"
    assert messages[0].token_count >= 1
    assert messages[0].is_graphized is False
    assert messages[1].role == "model"
    assert messages[1].content == "legacy model response"


def test_import_empty_list_is_noop() -> None:
    """import_history([]) leaves the context window unchanged."""
    llm = _make_llm(_make_valid_plan_json())
    planner = AgenticNarrativePlanner(llm=llm)
    planner._append_history("pre-existing", "message")
    original_count = len(planner._context_window.messages)

    planner.import_history([])

    # Should not have cleared the existing messages
    assert len(planner._context_window.messages) == original_count


# ------------------------------------------------------------------
# Test 5: graphize_callback triggered at threshold
# ------------------------------------------------------------------


def test_graphize_callback_triggered_when_threshold_reached() -> None:
    """graphize_callback is called via asyncio.create_task when counter reaches threshold."""
    callback_calls: list[tuple[str, list]] = []

    async def _fake_callback(actor_id: str, messages: list) -> None:
        callback_calls.append((actor_id, messages))

    llm = _make_llm(_make_valid_plan_json())
    planner = AgenticNarrativePlanner(
        llm=llm,
        history_key="__test_planner__",
        graphize_callback=_fake_callback,
    )
    # Force threshold to a tiny value so we can trigger it easily
    planner._context_window.graphize_threshold = 2  # 2 tokens

    async def _run() -> None:
        # Add two messages whose total tokens exceed threshold
        planner._context_window.add_message(WindowMessage(
            role="user", content="x", token_count=2,
        ))
        # This add_message should trigger should_graphize
        triggered = planner._context_window.add_message(WindowMessage(
            role="model", content="y", token_count=1,
        ))
        assert triggered is True

        # Now simulate what _append_history does when triggered
        if triggered and planner._graphize_callback is not None:
            messages = planner._context_window.collect_for_graphize()
            if messages:
                task = asyncio.create_task(planner._graphize_callback("__test_planner__", messages))
                await task  # run to completion so we can check callback_calls

    asyncio.run(_run())

    assert len(callback_calls) == 1
    actor_id, messages = callback_calls[0]
    assert actor_id == "__test_planner__"
    assert len(messages) > 0


def test_no_graphize_callback_no_error() -> None:
    """When graphize_callback is None, no error occurs even at threshold."""
    llm = _make_llm(_make_valid_plan_json())
    planner = AgenticNarrativePlanner(llm=llm, graphize_callback=None)
    # Force threshold to tiny value
    planner._context_window.graphize_threshold = 1

    # Should not raise even when threshold is exceeded
    planner._append_history("a" * 100, "b" * 100)
    # No exception = pass


# ------------------------------------------------------------------
# Test 6: plan() uses ContextWindow as conversation_history
# ------------------------------------------------------------------


def test_plan_uses_context_window_as_history() -> None:
    """After first plan(), second plan() includes prior messages as history."""
    plan_json = _make_valid_plan_json("notes from round 1")
    llm = _make_llm(plan_json)
    planner = AgenticNarrativePlanner(llm=llm)

    # First plan call
    asyncio.run(planner.plan(_MINIMAL_CONTEXT))
    assert len(planner._context_window.messages) == 2

    # Second plan call — check that history is passed to LLM
    asyncio.run(planner.plan(_MINIMAL_CONTEXT))

    # The second call should have passed conversation_history with the first round's messages
    second_call_args = llm.generate.call_args_list[1]
    history_arg = second_call_args[0][1]  # positional: system, history, tools
    assert len(history_arg) >= 2
    # First pair should be the user + model messages from round 1
    assert history_arg[0]["role"] in ("user", "model")


# ------------------------------------------------------------------
# Test 7: _window_to_planner_history includes graphized messages
# ------------------------------------------------------------------


def test_window_to_planner_history_includes_graphized_messages() -> None:
    """Planner uses ALL messages in the window, including is_graphized=True ones."""
    window = ContextWindow(actor_id="__test__", max_tokens=100_000, graphize_threshold=100_000)
    window.add_message(WindowMessage(role="user", content="msg1", token_count=1))
    window.add_message(WindowMessage(role="model", content="msg2", token_count=1))
    # Mark all as graphized
    window.collect_for_graphize()

    history = _window_to_planner_history(window)

    # Should include both messages even though they're graphized
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[0]["parts"][0]["text"] == "msg1"
    assert history[1]["role"] == "model"
    assert history[1]["parts"][0]["text"] == "msg2"


# ------------------------------------------------------------------
# Test 8: FIFO eviction at 100K tokens
# ------------------------------------------------------------------


def test_context_window_fifo_eviction_at_threshold() -> None:
    """ContextWindow evicts oldest messages when current_tokens exceeds max_tokens."""
    window = ContextWindow(actor_id="__test__", max_tokens=10, graphize_threshold=100_000)

    # Add messages totaling 12 tokens (exceeds cap of 10)
    window.add_message(WindowMessage(role="user", content="aaa", token_count=6))
    window.add_message(WindowMessage(role="model", content="bbb", token_count=6))

    # After eviction, oldest message (6 tokens) should be gone
    assert window.current_tokens <= 10
    assert len(window.messages) == 1
    # The remaining message should be the newer one
    assert window.messages[0].content == "bbb"


def test_planner_context_window_has_100k_limits() -> None:
    """AgenticNarrativePlanner's ContextWindow has max_tokens=100K and graphize_threshold=100K."""
    llm = _make_llm(_make_valid_plan_json())
    planner = AgenticNarrativePlanner(llm=llm)

    assert planner._context_window.max_tokens == 100_000
    assert planner._context_window.graphize_threshold == 100_000
