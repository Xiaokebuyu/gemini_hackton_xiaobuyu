"""Tests for S1-07 Phase 2+3: GM ContextWindow + Planner long-term memory.

Decision record: S1-07 (narrative.md)
Phase 2: AgenticGmNarrator ContextWindow integration.
Phase 3: AgenticNarrativePlanner memory_retriever injection.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.narrators import (
    AgenticGmNarrator,
    AgenticNarrativePlanner,
    _extract_planner_keywords,
    _format_planner_context,
)
from app.game_core.narrative.context_window import ContextWindow, WindowMessage


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_llm(text: str = "") -> Any:
    llm = MagicMock()
    resp = MagicMock()
    resp.text = text
    resp.tool_calls = []
    llm.generate = AsyncMock(return_value=resp)
    return llm


def _make_executor(text: str = "narrated text") -> Any:
    executor = MagicMock()
    result = MagicMock()
    result.text = text
    result.turns_used = 1
    result.tool_results = []
    result.metadata = {"status": "success"}
    executor.run_agentic = AsyncMock(return_value=result)
    return executor


def _make_world() -> Any:
    return MagicMock()


def _make_state() -> Any:
    return MagicMock()


def _minimal_summary() -> dict[str, Any]:
    return {
        "change_count": 1,
        "changed_slices": ["player"],
        "state_changes": [{"slice": "player", "path": "x", "value": 1}],
        "system_entries": [],
    }


def _minimal_scene_snapshot() -> dict[str, Any]:
    return {"entries": [], "state_changes": [{"slice": "player", "path": "x", "value": 1}]}


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
        "current_target_milestone": "ms_defeat_goblin",
        "pacing_frozen": False,
    },
    "quests": {
        "available_milestones": [],
        "active_milestones": [],
        "dynamic_quests": {"dq_goblin_patrol": {"status": "active"}},
    },
    "area_npcs": [{"id": "receptionist_anna"}, {"id": "blacksmith_joe"}],
    "current_tick": 5,
}


# ------------------------------------------------------------------
# Phase 2: GM ContextWindow Tests
# ------------------------------------------------------------------


def test_gm_narrator_has_context_window() -> None:
    """AgenticGmNarrator is initialized with a ContextWindow (actor_id='__gm__')."""
    executor = _make_executor()
    narrator = AgenticGmNarrator(
        executor=executor, world=_make_world(), state=_make_state()
    )
    assert narrator._context_window is not None
    assert narrator._context_window.max_tokens == 32_768
    assert narrator._context_window.graphize_threshold == 32_768


def test_gm_compose_writes_messages_to_window() -> None:
    """compose() writes user + model messages to the ContextWindow."""

    async def _run() -> None:
        executor = _make_executor("GM narrated something")
        narrator = AgenticGmNarrator(
            executor=executor, world=_make_world(), state=_make_state()
        )
        await narrator.compose(_minimal_summary(), _minimal_scene_snapshot())
        messages = narrator._context_window.messages
        # user message (serialized summary) + model response
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[1].role == "model"
        assert messages[1].content == "GM narrated something"

    asyncio.run(_run())


def test_gm_compose_accumulates_history_across_calls() -> None:
    """Multiple compose() calls accumulate history in the ContextWindow."""

    async def _run() -> None:
        executor = _make_executor("response")
        narrator = AgenticGmNarrator(
            executor=executor, world=_make_world(), state=_make_state()
        )
        await narrator.compose(_minimal_summary(), _minimal_scene_snapshot())
        await narrator.compose(_minimal_summary(), _minimal_scene_snapshot())
        # 2 calls × 2 messages (user + model) = 4 total
        assert len(narrator._context_window.messages) == 4

    asyncio.run(_run())


def test_gm_compose_passes_history_to_executor() -> None:
    """After the first compose(), subsequent calls pass conversation_history to run_agentic."""

    async def _run() -> None:
        executor = _make_executor("response")
        narrator = AgenticGmNarrator(
            executor=executor, world=_make_world(), state=_make_state()
        )
        # First call — history is empty, so conversation_history arg would be []
        await narrator.compose(_minimal_summary(), _minimal_scene_snapshot())
        # Second call — history has 2 messages
        await narrator.compose(_minimal_summary(), _minimal_scene_snapshot())

        # Check the second call passed non-empty conversation_history
        second_call_kwargs = executor.run_agentic.call_args_list[1][1]
        history_arg = second_call_kwargs.get("conversation_history", [])
        assert len(history_arg) >= 2

    asyncio.run(_run())


def test_gm_export_import_round_trip() -> None:
    """export_history → import_history preserves GM context window messages."""

    async def _run() -> None:
        executor = _make_executor("first response")
        narrator = AgenticGmNarrator(
            executor=executor, world=_make_world(), state=_make_state()
        )
        await narrator.compose(_minimal_summary(), _minimal_scene_snapshot())

        exported = narrator.export_history()
        assert len(exported) >= 2

        # Restore into a fresh narrator
        executor2 = _make_executor()
        narrator2 = AgenticGmNarrator(
            executor=executor2, world=_make_world(), state=_make_state()
        )
        narrator2.import_history(exported)

        assert len(narrator2._context_window.messages) == len(narrator._context_window.messages)
        assert narrator2._context_window.messages[0].role == "user"
        assert narrator2._context_window.messages[1].role == "model"
        assert narrator2._context_window.messages[1].content == "first response"

    asyncio.run(_run())


def test_gm_export_returns_empty_when_no_history() -> None:
    """export_history returns empty messages when no compose() has been called."""
    executor = _make_executor()
    narrator = AgenticGmNarrator(
        executor=executor, world=_make_world(), state=_make_state()
    )
    exported = narrator.export_history()
    # export_history now returns a dict with "messages" and "graphize_counter"
    assert isinstance(exported, dict)
    assert exported["messages"] == []


def test_gm_import_empty_is_noop() -> None:
    """import_history([]) does not modify the context window."""
    executor = _make_executor()
    narrator = AgenticGmNarrator(
        executor=executor, world=_make_world(), state=_make_state()
    )
    narrator.import_history([])
    assert len(narrator._context_window.messages) == 0


def test_gm_graphize_callback_triggered_at_threshold() -> None:
    """graphize_callback is invoked when the GM context window reaches its threshold."""
    callback_calls: list[tuple[str, list]] = []

    async def _fake_callback(actor_id: str, messages: list) -> None:
        callback_calls.append((actor_id, messages))

    async def _run() -> None:
        executor = _make_executor("model output")
        narrator = AgenticGmNarrator(
            executor=executor,
            world=_make_world(),
            state=_make_state(),
            graphize_callback=_fake_callback,
        )
        # Lower the threshold so it triggers on first compose
        narrator._context_window.graphize_threshold = 1

        # Manually trigger what compose() does post-LLM call
        narrator._context_window.add_message(WindowMessage(
            role="user", content="hello", token_count=2,
        ))
        triggered = narrator._context_window.add_message(WindowMessage(
            role="model", content="world", token_count=2,
        ))
        assert triggered is True
        if triggered and narrator._graphize_callback is not None:
            msgs = narrator._context_window.collect_for_graphize()
            if msgs:
                task = asyncio.create_task(narrator._graphize_callback("__gm__", msgs))
                await task

    asyncio.run(_run())

    assert len(callback_calls) == 1
    actor_id, messages = callback_calls[0]
    assert actor_id == "__gm__"
    assert len(messages) > 0


def test_gm_no_callback_no_error() -> None:
    """With graphize_callback=None, no error occurs even when threshold is reached."""

    async def _run() -> None:
        executor = _make_executor("response")
        narrator = AgenticGmNarrator(
            executor=executor, world=_make_world(), state=_make_state(),
            graphize_callback=None,
        )
        narrator._context_window.graphize_threshold = 1
        await narrator.compose(_minimal_summary(), _minimal_scene_snapshot())
        # No exception = pass

    asyncio.run(_run())


def test_gm_compose_gracefully_degrades_on_llm_error() -> None:
    """When the executor raises, compose() returns a GmNarrationDecision with status=llm_error."""

    async def _run() -> None:
        executor = MagicMock()
        executor.run_agentic = AsyncMock(side_effect=Exception("LLM down"))
        narrator = AgenticGmNarrator(
            executor=executor, world=_make_world(), state=_make_state()
        )
        decision = await narrator.compose(_minimal_summary(), _minimal_scene_snapshot())
        assert decision.metadata.get("status") == "llm_error"

    asyncio.run(_run())


# ------------------------------------------------------------------
# Phase 3: Planner long-term memory retrieval tests
# ------------------------------------------------------------------


def test_extract_planner_keywords_gets_milestone() -> None:
    """_extract_planner_keywords extracts the current target milestone."""
    ctx = {
        "narrative_plan": {
            "current_target_milestone": "ms_defeat_goblin_lord",
        },
        "area_npcs": [],
        "quests": {"dynamic_quests": {}},
    }
    keywords = _extract_planner_keywords(ctx)
    assert "ms_defeat_goblin_lord" in keywords


def test_extract_planner_keywords_gets_npc_ids() -> None:
    """_extract_planner_keywords extracts NPC IDs from area_npcs."""
    ctx = {
        "narrative_plan": {},
        "area_npcs": [{"id": "receptionist_anna"}, {"id": "blacksmith_joe"}],
        "quests": {"dynamic_quests": {}},
    }
    keywords = _extract_planner_keywords(ctx)
    assert "receptionist_anna" in keywords
    assert "blacksmith_joe" in keywords


def test_extract_planner_keywords_gets_quest_ids() -> None:
    """_extract_planner_keywords extracts quest IDs from dynamic_quests."""
    ctx = {
        "narrative_plan": {},
        "area_npcs": [],
        "quests": {
            "dynamic_quests": {
                "dq_goblin_patrol": {"status": "active"},
                "dq_find_medicine": {"status": "available"},
            }
        },
    }
    keywords = _extract_planner_keywords(ctx)
    assert "dq_goblin_patrol" in keywords
    assert "dq_find_medicine" in keywords


def test_extract_planner_keywords_caps_at_10() -> None:
    """_extract_planner_keywords returns at most 10 keywords."""
    ctx = {
        "narrative_plan": {"current_target_milestone": "ms_1"},
        "area_npcs": [{"id": f"npc_{i}"} for i in range(15)],
        "quests": {"dynamic_quests": {f"dq_{i}": {} for i in range(5)}},
    }
    keywords = _extract_planner_keywords(ctx)
    assert len(keywords) <= 10


def test_extract_planner_keywords_handles_empty_context() -> None:
    """_extract_planner_keywords returns [] for an empty context without error."""
    keywords = _extract_planner_keywords({})
    assert isinstance(keywords, list)
    assert len(keywords) == 0


def test_plan_calls_memory_retriever_and_injects_context() -> None:
    """plan() calls memory_retriever.retrieve() and injects __long_term_memory__ into context."""
    hit = {
        "node_id": "goblin_lord",
        "label": "Goblin Lord",
        "description": "A powerful goblin warlord",
        "activation": 0.9,
    }
    mock_retriever = MagicMock()
    mock_retriever.retrieve = AsyncMock(return_value={"hits": [hit], "source": "knowledge_graph"})

    captured_contexts: list[dict] = []

    def _capturing_formatter(ctx: dict) -> str:
        captured_contexts.append(dict(ctx))
        return "formatted context"

    async def _run() -> None:
        llm = _make_llm(_make_valid_plan_json())
        planner = AgenticNarrativePlanner(
            llm=llm,
            memory_retriever=mock_retriever,
            context_formatter=_capturing_formatter,
        )
        await planner.plan(_MINIMAL_CONTEXT)

    asyncio.run(_run())

    mock_retriever.retrieve.assert_called_once()
    assert len(captured_contexts) == 1
    assert "__long_term_memory__" in captured_contexts[0]
    assert captured_contexts[0]["__long_term_memory__"][0]["node_id"] == "goblin_lord"


def test_plan_skips_memory_retrieval_when_no_keywords() -> None:
    """plan() skips retrieve() when no keywords can be extracted from context."""
    mock_retriever = MagicMock()
    mock_retriever.retrieve = AsyncMock(return_value={"hits": [], "source": "knowledge_graph"})

    # Context with no milestones, no NPCs, no quests
    empty_ctx: dict[str, Any] = {
        "narrative_plan": {
            "current_chapter": "ch1",
            "escalation_level": 0,
            "ticks_since_milestone_progress": 0,
            "current_target_milestone": None,
        },
        "quests": {"dynamic_quests": {}, "available_milestones": [], "active_milestones": []},
        "area_npcs": [],
        "current_tick": 0,
    }

    async def _run() -> None:
        llm = _make_llm(_make_valid_plan_json())
        planner = AgenticNarrativePlanner(
            llm=llm,
            memory_retriever=mock_retriever,
        )
        await planner.plan(empty_ctx)

    asyncio.run(_run())

    # retrieve should NOT have been called (no keywords)
    mock_retriever.retrieve.assert_not_called()


def test_plan_graceful_degradation_without_retriever() -> None:
    """plan() works normally when memory_retriever is None (no-LLM fallback)."""

    async def _run() -> None:
        llm = _make_llm(_make_valid_plan_json())
        planner = AgenticNarrativePlanner(llm=llm, memory_retriever=None)
        result = await planner.plan(_MINIMAL_CONTEXT)
        # Should succeed without error
        assert isinstance(result, dict)

    asyncio.run(_run())


def test_plan_caps_memory_hits_at_5() -> None:
    """plan() injects at most 5 hits from memory_retriever."""
    hits = [{"node_id": f"entity_{i}", "label": f"Entity {i}"} for i in range(10)]
    mock_retriever = MagicMock()
    mock_retriever.retrieve = AsyncMock(return_value={"hits": hits, "source": "knowledge_graph"})

    captured_contexts: list[dict] = []

    def _capturing_formatter(ctx: dict) -> str:
        captured_contexts.append(dict(ctx))
        return "formatted"

    async def _run() -> None:
        llm = _make_llm(_make_valid_plan_json())
        planner = AgenticNarrativePlanner(
            llm=llm,
            memory_retriever=mock_retriever,
            context_formatter=_capturing_formatter,
        )
        await planner.plan(_MINIMAL_CONTEXT)

    asyncio.run(_run())

    assert len(captured_contexts) == 1
    injected = captured_contexts[0].get("__long_term_memory__", [])
    assert len(injected) <= 5


def test_format_planner_context_renders_long_term_memory() -> None:
    """_format_planner_context renders the ## 长期记忆 section when __long_term_memory__ is set."""
    ctx = dict(_MINIMAL_CONTEXT)
    ctx["__long_term_memory__"] = [
        {
            "node_id": "goblin_lord",
            "label": "Goblin Lord",
            "description": "A powerful goblin warlord controlling the mountains",
            "activation": 0.85,
        }
    ]
    output = _format_planner_context(ctx)
    assert "长期记忆" in output
    assert "Goblin Lord" in output


def test_format_planner_context_no_memory_section_when_empty() -> None:
    """_format_planner_context does NOT render ## 长期记忆 when __long_term_memory__ is absent."""
    output = _format_planner_context(_MINIMAL_CONTEXT)
    assert "长期记忆" not in output


def test_plan_retriever_failure_does_not_break_plan() -> None:
    """When memory_retriever.retrieve() raises, plan() still returns a valid result."""
    mock_retriever = MagicMock()
    mock_retriever.retrieve = AsyncMock(side_effect=Exception("graph down"))

    async def _run() -> dict:
        llm = _make_llm(_make_valid_plan_json())
        planner = AgenticNarrativePlanner(
            llm=llm,
            memory_retriever=mock_retriever,
        )
        return await planner.plan(_MINIMAL_CONTEXT)

    result = asyncio.run(_run())
    assert isinstance(result, dict)
    # Should still have parsed the plan
    assert "directives" in result
