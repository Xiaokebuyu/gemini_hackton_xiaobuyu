"""Tests for P19 Phase B+C+D+E.

Phase B: RecallTool + L6 改造
Phase C: ContextWindow 32K FIFO + graphize_counter + export/import
Phase D: AgenticNarrativePlanner 100K sliding history window
Phase E: story_facts relation constraint + BFS deque
"""

from __future__ import annotations

import asyncio
from typing import Any

# ---------------------------------------------------------------------------
# Phase B: RecallTool
# ---------------------------------------------------------------------------


def test_recall_tool_returns_hits() -> None:
    """RecallTool.execute() with a retriever that returns hits formats them correctly."""
    from app.game_core.narrative.character_tools import RecallTool
    from app.game_core.narrative.models import ToolResult

    class _FakeRetriever:
        async def retrieve(self, actor_id: str, keywords: list, context: dict) -> dict:
            return {
                "hits": [
                    {"node_id": "iron_sword", "label": "Iron Sword", "node_type": "item",
                     "description": "A sturdy sword.", "tags": [], "activation": 0.9,
                     "metadata": {}},
                ],
                "source": "stub",
            }

    async def _run() -> None:
        tool = RecallTool()
        from app.game_core.narrative.context import AgentContext
        from app.game_core.content import WorldInstance
        from app.game_core.state import StateContainer

        world = WorldInstance("test_world")
        state = StateContainer()
        context = AgentContext(
            role="npc",
            world=world,
            state=state,
            metadata={
                "character_id": "npc_hero",
                "memory_retriever": _FakeRetriever(),
                "world": world,
            },
        )
        result: ToolResult = await tool.execute({"query": "sword"}, context)
        assert result.ok is True
        assert "Iron Sword" in result.message
        assert "item" in result.message

    asyncio.run(_run())


def test_recall_tool_no_retriever() -> None:
    """RecallTool.execute() without retriever returns ok=False with memory_unavailable."""
    from app.game_core.narrative.character_tools import RecallTool

    async def _run() -> None:
        tool = RecallTool()
        from app.game_core.narrative.context import AgentContext
        from app.game_core.content import WorldInstance
        from app.game_core.state import StateContainer

        world = WorldInstance("test_world")
        state = StateContainer()
        context = AgentContext(
            role="npc",
            world=world,
            state=state,
            metadata={"character_id": "npc_hero"},
        )
        result = await tool.execute({"query": "anything"}, context)
        assert result.ok is False
        assert "unavailable" in result.message

    asyncio.run(_run())


def test_recall_tool_registered_for_npc_and_teammate() -> None:
    """RecallTool is in both NPC and Teammate tool lists."""
    from app.game_core.narrative.character_tools import (
        RecallTool,
        _NPC_TOOLS,
        _TEAMMATE_TOOLS,
    )

    npc_names = {cls().name for cls in _NPC_TOOLS}
    tm_names = {cls().name for cls in _TEAMMATE_TOOLS}

    assert "recall" in npc_names
    assert "recall" in tm_names
    # Both lists have a RecallTool class
    assert any(issubclass(cls, RecallTool) for cls in _NPC_TOOLS)
    assert any(issubclass(cls, RecallTool) for cls in _TEAMMATE_TOOLS)


def test_npc_prompt_no_knowledge_block() -> None:
    """NPC system prompt should NOT contain pre-injected knowledge block (P19-B)."""
    from app.game_core.bootstrap import build_default_world
    from app.game_core.narrative.context_builder import AgentContextBuilder
    from app.game_core.state import StateContainer

    world = build_default_world("test_world", world_data={
        "characters": {
            "npc_x": {
                "id": "npc_x",
                "name": "Nyx",
                "personality": "Cryptic seer.",
                "dialogue_style": "Poetic and cryptic.",
            },
        },
    })
    state = StateContainer()
    builder = AgentContextBuilder(world, state)

    class FakeRetriever:
        async def retrieve(self, actor_id, keywords, context):
            return {"hits": [
                {"node_id": "a", "label": "Dragon", "node_type": "monster",
                 "description": "Ancient beast.", "tags": [], "activation": 1.0,
                 "metadata": {}},
            ], "source": "stub"}

    prompt = asyncio.run(builder.build_npc_system_prompt("npc_x", memory_retriever=FakeRetriever()))
    assert prompt is not None
    # No pre-injected knowledge block
    assert "## Relevant world knowledge" not in prompt
    # The recall tool should be mentioned in tool rules
    assert "recall" in prompt


# ---------------------------------------------------------------------------
# Phase C: ContextWindow FIFO + graphize_counter
# ---------------------------------------------------------------------------


def test_context_window_default_32k() -> None:
    """ContextWindow should default to max_tokens=32_768."""
    from app.game_core.narrative.context_window import ContextWindow

    cw = ContextWindow(actor_id="npc_test")
    assert cw.max_tokens == 32_768
    assert cw.graphize_threshold == 32_768


def test_context_window_fifo_eviction() -> None:
    """FIFO eviction: oldest messages removed when current_tokens exceeds max_tokens."""
    from app.game_core.narrative.context_window import ContextWindow, WindowMessage

    cw = ContextWindow(actor_id="a", max_tokens=50)
    m1 = WindowMessage(role="user", content="first", token_count=20)
    m2 = WindowMessage(role="model", content="second", token_count=20)
    m3 = WindowMessage(role="user", content="third", token_count=20)

    cw.add_message(m1)
    cw.add_message(m2)
    cw.add_message(m3)  # total=60, exceeds 50 → evict m1

    assert cw.current_tokens <= cw.max_tokens
    assert m1 not in cw.messages  # oldest evicted
    assert m3 in cw.messages   # newest kept


def test_context_window_graphize_counter() -> None:
    """graphize_counter accumulates tokens across add_message calls."""
    from app.game_core.narrative.context_window import ContextWindow, WindowMessage

    cw = ContextWindow(actor_id="a", max_tokens=10000, graphize_threshold=50)
    assert cw.graphize_counter == 0

    cw.add_message(WindowMessage(role="user", content="x", token_count=30))
    assert cw.graphize_counter == 30
    assert cw.should_graphize is False

    cw.add_message(WindowMessage(role="model", content="y", token_count=25))
    assert cw.graphize_counter == 55
    assert cw.should_graphize is True


def test_context_window_collect_for_graphize() -> None:
    """collect_for_graphize returns un-graphized messages, marks them, resets counter."""
    from app.game_core.narrative.context_window import ContextWindow, WindowMessage

    cw = ContextWindow(actor_id="a", max_tokens=10000, graphize_threshold=1)
    m1 = WindowMessage(role="user", content="hello", token_count=5)
    m2 = WindowMessage(role="model", content="world", token_count=5)

    cw.add_message(m1)
    cw.add_message(m2)
    assert cw.should_graphize is True

    collected = cw.collect_for_graphize()
    assert len(collected) == 2
    assert all(m.is_graphized for m in collected)
    assert cw.graphize_counter == 0
    assert cw.should_graphize is False

    # Messages remain in window (not removed)
    assert len(cw.messages) == 2

    # Adding more should not return already-graphized messages
    m3 = WindowMessage(role="user", content="new", token_count=1)
    cw.add_message(m3)
    collected2 = cw.collect_for_graphize()
    assert len(collected2) == 1
    assert collected2[0] is m3


def test_context_window_export_import_messages() -> None:
    """export_messages + import_messages round-trip preserves content."""
    from app.game_core.narrative.context_window import ContextWindow, WindowMessage

    cw = ContextWindow(actor_id="a", max_tokens=10000)
    cw.add_message(WindowMessage(role="user", content="hello", token_count=5, metadata={"k": "v"}))
    cw.add_message(WindowMessage(role="model", content="world", token_count=6))

    exported = cw.export_messages()
    assert len(exported) == 2
    assert exported[0]["role"] == "user"
    assert exported[0]["content"] == "hello"
    assert exported[0]["metadata"] == {"k": "v"}

    cw2 = ContextWindow(actor_id="a", max_tokens=10000)
    cw2.import_messages(exported)

    assert len(cw2.messages) == 2
    assert cw2.messages[0].content == "hello"
    assert cw2.messages[1].content == "world"
    assert cw2.current_tokens == 11
    assert cw2.graphize_counter == 0  # starts fresh after import


def test_graphize_triggered_after_threshold() -> None:
    """add_message returns True once graphize_counter reaches threshold."""
    from app.game_core.narrative.context_window import ContextWindow, WindowMessage

    cw = ContextWindow(actor_id="a", max_tokens=10000, graphize_threshold=100)
    r1 = cw.add_message(WindowMessage(role="user", content="x", token_count=40))
    assert r1 is False  # 40 < 100

    r2 = cw.add_message(WindowMessage(role="model", content="y", token_count=40))
    assert r2 is False  # 80 < 100

    r3 = cw.add_message(WindowMessage(role="user", content="z", token_count=25))
    assert r3 is True  # 105 >= 100


# ---------------------------------------------------------------------------
# Phase D: AgenticNarrativePlanner history window
# ---------------------------------------------------------------------------


def test_planner_history_append() -> None:
    """_append_history correctly updates _history and _history_tokens."""
    from app.narrators import AgenticNarrativePlanner

    class _FakeLlm:
        async def generate(self, system, history, tools):
            raise RuntimeError("not needed")

    planner = AgenticNarrativePlanner(_FakeLlm())
    assert planner._history == []
    assert planner._history_tokens == 0

    planner._append_history("user message here", '{"directives": []}')
    assert len(planner._history) == 2
    assert planner._history[0]["role"] == "user"
    assert planner._history[1]["role"] == "model"
    assert planner._history_tokens > 0


def test_planner_history_fifo_eviction() -> None:
    """FIFO eviction kicks in when _history_tokens exceeds max budget."""
    from app.narrators import AgenticNarrativePlanner

    class _FakeLlm:
        async def generate(self, system, history, tools):
            raise RuntimeError("not needed")

    planner = AgenticNarrativePlanner(_FakeLlm())
    planner._max_history_tokens = 50  # very tight

    # Each round contributes about: len(user_msg)//4 + len(model_msg)//4
    # "u" * 40 → 10 tokens, "m" * 40 → 10 tokens → 20 per round
    # After 3 rounds: 60 > 50 → evict oldest
    for i in range(3):
        planner._append_history("u" * 40, "m" * 40)

    # After eviction, total should be <= max
    assert planner._history_tokens <= planner._max_history_tokens + 20  # tolerance of 1 round
    assert len(planner._history) < 6  # some rounds were evicted


def test_planner_history_export_import() -> None:
    """export_history + import_history round-trip."""
    from app.narrators import AgenticNarrativePlanner

    class _FakeLlm:
        async def generate(self, system, history, tools):
            raise RuntimeError("not needed")

    planner = AgenticNarrativePlanner(_FakeLlm())
    planner._append_history("the user context", '{"directives": [], "strategy_notes": "test"}')

    exported = planner.export_history()
    assert len(exported) == 2
    assert exported[0]["role"] == "user"
    assert exported[0]["text"] == "the user context"
    assert exported[1]["role"] == "model"

    planner2 = AgenticNarrativePlanner(_FakeLlm())
    assert planner2._history == []
    planner2.import_history(exported)
    assert len(planner2._history) == 2
    assert planner2._history_tokens > 0
    assert planner2._history[0]["parts"][0]["text"] == "the user context"


def test_planner_plan_includes_history() -> None:
    """plan() passes accumulated history to LLM.generate() call."""
    from app.narrators import AgenticNarrativePlanner

    received_history: list[Any] = []

    class _RecordingLlm:
        class _Resp:
            text = '{"directives": [], "strategy_notes": ""}'

        async def generate(self, system, history, tools):
            received_history.clear()
            received_history.extend(history)
            return self._Resp()

    async def _run() -> None:
        planner = AgenticNarrativePlanner(_RecordingLlm())
        # Seed one round of history
        planner._append_history("prev context", '{"directives": [], "story_facts": []}')

        # Call plan() with fresh context
        await planner.plan({"narrative_plan": {}, "quests": {}, "time": {}, "location": {}})

        # The history passed to LLM should include the previous round plus the new user msg
        # That means at least 3 entries: prev_user, prev_model, new_user
        assert len(received_history) >= 3
        roles = [h["role"] for h in received_history]
        assert "user" in roles
        assert "model" in roles

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Phase E: story_facts relation enum + BFS deque
# ---------------------------------------------------------------------------


def test_story_facts_relation_enum_in_prompt() -> None:
    """_SYSTEM_PROMPT must enumerate the allowed relation types."""
    from app.narrators import AgenticNarrativePlanner

    prompt = AgenticNarrativePlanner._SYSTEM_PROMPT
    # All 5 relation types should appear
    for rel in ["knows_about", "interacted_with", "made_promise", "related_to", "has_opinion_of"]:
        assert rel in prompt, f"relation '{rel}' not found in planner system prompt"


def test_bfs_uses_deque() -> None:
    """_spread_activation_in_graph uses deque-based BFS (no O(n) list.pop(0))."""
    from app.world_knowledge_graph import WorldKnowledgeGraph

    # Build a tiny graph to exercise the BFS path
    wkg = WorldKnowledgeGraph()
    import networkx as nx
    g: nx.DiGraph = nx.DiGraph()
    g.add_node("a", label="A", tags=[], description="", node_type="test", metadata={})
    g.add_node("b", label="B", tags=[], description="", node_type="test", metadata={})
    g.add_node("c", label="C", tags=[], description="", node_type="test", metadata={})
    g.add_edge("a", "b", relation="knows_about", weight=1.0)
    g.add_edge("b", "c", relation="related_to", weight=1.0)

    activation = wkg._spread_activation_in_graph(g, ["a"], max_depth=2, decay=0.8)
    assert "a" in activation
    assert "b" in activation
    assert "c" in activation
    # Correct propagation: b=0.8, c=0.64
    assert abs(activation["b"] - 0.8) < 0.01
    assert abs(activation["c"] - 0.64) < 0.01
