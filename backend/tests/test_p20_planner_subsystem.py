"""Tests for P20 Phase 1a+1b: PlannerSubSystem + DesignSkillPort + planner tools.

All async tests use asyncio.run() wrappers — pytest-asyncio is not installed.

Decision record: D-P20a (narrative.md)
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.game_core.planning.subsystem import (
    PlannerDispatcher,
    PlannerEvent,
    SubSystemResult,
)
from app.game_core.adapters.design_skill import DesignSkillPort, NullDesignSkillPort
from app.game_core.narrative.planner_tools import (
    ListDesignSkillsTool,
    ReadDesignSkillTool,
    register_planner_tools,
)
from app.game_core.narrative.registry import RoleToolRegistry
from app.design_skill_provider import LocalDesignSkillProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _AcceptAllSubSystem:
    """Test stub that accepts every event and returns a fixed SubSystemResult."""

    def __init__(
        self,
        name: str,
        handles: frozenset[str],
        *,
        result: SubSystemResult | None = None,
        delay: float = 0.0,
    ) -> None:
        self._name = name
        self._handles = handles
        self._result = result or SubSystemResult(directives=["stub"])
        self._delay = delay
        self.evaluate_calls: list[PlannerEvent] = []
        self.apply_calls: list[tuple[str, dict]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def handles(self) -> frozenset[str]:
        return self._handles

    def accepts_event(self, event: PlannerEvent) -> bool:
        return True

    async def evaluate(self, event: PlannerEvent, context: Any) -> SubSystemResult:
        if self._delay:
            await asyncio.sleep(self._delay)
        self.evaluate_calls.append(event)
        return self._result

    def apply_directive(
        self,
        kind: str,
        payload: dict[str, Any],
        context: Any,
        *,
        current_tick: int,
    ) -> bool:
        self.apply_calls.append((kind, payload))
        return True


class _SelectiveSubSystem(_AcceptAllSubSystem):
    """Only accepts events of specific kinds."""

    def __init__(self, name: str, handles: frozenset[str], accept_kinds: frozenset[str]) -> None:
        super().__init__(name, handles)
        self._accept_kinds = accept_kinds

    def accepts_event(self, event: PlannerEvent) -> bool:
        return event.kind in self._accept_kinds


def _make_event(kind: str = "tick_settlement", tick: int = 1) -> PlannerEvent:
    return PlannerEvent(kind=kind, tick=tick)


# ---------------------------------------------------------------------------
# Phase 1a: Dispatcher tests
# ---------------------------------------------------------------------------


def test_dispatcher_register_and_route():
    """Two sub-systems with different handles receive routed directives correctly."""
    async def _run():
        dispatcher = PlannerDispatcher()
        ss_a = _AcceptAllSubSystem("a", frozenset({"create_quest"}))
        ss_b = _AcceptAllSubSystem("b", frozenset({"direct_npc"}))
        dispatcher.register(ss_a)
        dispatcher.register(ss_b)

        event = _make_event()
        results = await dispatcher.dispatch(event, context=None)

        assert len(results) == 2
        assert len(ss_a.evaluate_calls) == 1
        assert len(ss_b.evaluate_calls) == 1

    asyncio.run(_run())


def test_dispatcher_unhandled_kind_returns_false():
    """apply_directive returns False when no sub-system handles the kind."""
    dispatcher = PlannerDispatcher()
    ss = _AcceptAllSubSystem("a", frozenset({"create_quest"}))
    dispatcher.register(ss)

    result = dispatcher.apply_directive("unknown_kind", {}, None, current_tick=0)
    assert result is False
    assert ss.apply_calls == []


def test_dispatcher_known_kind_delegates():
    """apply_directive delegates to the matching sub-system."""
    dispatcher = PlannerDispatcher()
    ss = _AcceptAllSubSystem("a", frozenset({"create_quest"}))
    dispatcher.register(ss)

    result = dispatcher.apply_directive("create_quest", {"quest_id": "q1"}, None, current_tick=5)
    assert result is True
    assert ss.apply_calls == [("create_quest", {"quest_id": "q1"})]


def test_dispatcher_busy_lock_queues_event():
    """A sub-system is marked busy during evaluate; subsequent events queue."""
    events_seen: list[str] = []
    received_while_busy = False

    class _BusyTrackingSS:
        """Records whether a second event arrives while the first is running."""

        name = "tracker"
        handles = frozenset({"create_quest"})
        busy = False

        def accepts_event(self, event):
            return True

        async def evaluate(self, event, context):
            nonlocal received_while_busy
            # Simulate work so the caller can queue a second event
            self.busy = True
            events_seen.append(event.kind)
            await asyncio.sleep(0)  # yield
            self.busy = False
            return SubSystemResult()

        def apply_directive(self, kind, payload, context, *, current_tick):
            return True

    async def _run():
        dispatcher = PlannerDispatcher()
        ss = _BusyTrackingSS()
        dispatcher.register(ss)

        event1 = _make_event("tick_settlement", tick=1)
        event2 = _make_event("tick_settlement", tick=2)

        # Dispatch first event; before it completes, manually enqueue second
        # by dispatching from a parallel coroutine
        async def dispatch_both():
            # First dispatch (runs immediately)
            t1 = asyncio.create_task(dispatcher.dispatch(event1, None))
            # Second dispatch (ss is busy → should queue)
            # Ensure first task has started
            await asyncio.sleep(0)
            t2 = asyncio.create_task(dispatcher.dispatch(event2, None))
            await t1
            await t2

        await dispatch_both()
        # Both events must have been processed
        assert len(events_seen) == 2

    asyncio.run(_run())


def test_dispatcher_queue_depth_limit():
    """Events beyond _MAX_QUEUE_DEPTH are silently dropped."""
    dispatcher = PlannerDispatcher()
    ss = _AcceptAllSubSystem("a", frozenset({"x"}))
    dispatcher.register(ss)
    # Manually fill the queue past the limit
    name = "a"
    for i in range(dispatcher._MAX_QUEUE_DEPTH + 2):
        dispatcher._enqueue(name, _make_event("x", tick=i))
    assert len(dispatcher._queue[name]) == dispatcher._MAX_QUEUE_DEPTH


def test_dispatcher_drains_queue_after_evaluate():
    """All queued events are processed after the current evaluate completes."""
    processed: list[int] = []

    class _DrainingSubSystem:
        name = "drain"
        handles = frozenset({"x"})

        def accepts_event(self, event):
            return True

        async def evaluate(self, event, context):
            processed.append(event.tick)
            return SubSystemResult()

        def apply_directive(self, kind, payload, context, *, current_tick):
            return True

    async def _run():
        dispatcher = PlannerDispatcher()
        ss = _DrainingSubSystem()
        dispatcher.register(ss)

        # Pre-fill the queue with 2 events (below cap)
        dispatcher._queue["drain"] = [
            _make_event("x", tick=10),
            _make_event("x", tick=11),
        ]
        # Now dispatch one more event; it runs immediately and then drains
        await dispatcher.dispatch(_make_event("x", tick=9), None)
        # Expect: tick=9 (immediate) then tick=10, tick=11 (drained)
        assert processed == [9, 10, 11]

    asyncio.run(_run())


def test_hook_with_dispatcher_full_flow():
    """NarrativePlannerHook._apply_normalized_decision delegates to dispatcher when set."""
    from app.game_core.orchestration.hooks.narrative_planner import (
        NarrativePlannerDecision,
        NarrativePlannerHook,
    )

    applied: list[tuple] = []

    # Use a minimal stub sub-system that captures apply_directive calls
    class _CapturingSubSystem:
        name = "capturing"
        handles = frozenset({"create_quest"})

        def accepts_event(self, event):
            return True

        async def evaluate(self, event, context):
            return SubSystemResult()

        def apply_directive(self, kind, payload, context, *, current_tick):
            applied.append((kind, payload, current_tick))
            return True

    dispatcher = PlannerDispatcher()
    dispatcher.register(_CapturingSubSystem())

    hook = NarrativePlannerHook(planner=None)
    hook._dispatcher = dispatcher

    decision = NarrativePlannerDecision(
        directives=[
            {"kind": "create_quest", "payload": {"quest_id": "dq_x"}},
        ]
    )

    # _apply_normalized_decision is synchronous (it's the inner loop)
    result = hook._apply_normalized_decision(
        decision,
        context=None,
        current_tick=42,
        allowed_directives={"create_quest"},
    )
    req, applied_count, skip_unsupported, skip_invalid, kinds = result
    assert applied_count == 1
    assert kinds == ["create_quest"]
    assert applied == [("create_quest", {"quest_id": "dq_x"}, 42)]


# ---------------------------------------------------------------------------
# Phase 1b: Tool tests
# ---------------------------------------------------------------------------


def test_read_skill_tool_returns_content():
    """ReadDesignSkillTool returns file content from a mock port."""
    async def _run():
        tool = ReadDesignSkillTool()

        mock_port = AsyncMock()
        mock_port.read_skill = AsyncMock(return_value="# Hunt Quest Template\nDetails here.")

        from app.game_core.narrative.context import AgentContext
        ctx = AgentContext(
            role="planner",
            world=None,
            state=None,
            metadata={"design_skill_port": mock_port, "world_id": "test_world"},
        )
        result = await tool.execute({"category": "quests", "name": "hunt"}, ctx)
        assert result.ok is True
        assert "Hunt Quest Template" in result.message
        mock_port.read_skill.assert_awaited_once_with("test_world", "quests", "hunt")

    asyncio.run(_run())


def test_read_skill_tool_not_found():
    """ReadDesignSkillTool returns ok=False when port returns None."""
    async def _run():
        tool = ReadDesignSkillTool()
        mock_port = AsyncMock()
        mock_port.read_skill = AsyncMock(return_value=None)

        from app.game_core.narrative.context import AgentContext
        ctx = AgentContext(
            role="planner",
            world=None,
            state=None,
            metadata={"design_skill_port": mock_port, "world_id": "w"},
        )
        result = await tool.execute({"category": "quests", "name": "nonexistent"}, ctx)
        assert result.ok is False
        assert result.metadata["status"] == "not_found"

    asyncio.run(_run())


def test_read_skill_tool_no_port():
    """ReadDesignSkillTool returns ok=False when metadata has no design_skill_port."""
    async def _run():
        tool = ReadDesignSkillTool()
        from app.game_core.narrative.context import AgentContext
        ctx = AgentContext(role="planner", world=None, state=None, metadata={})
        result = await tool.execute({"category": "quests", "name": "hunt"}, ctx)
        assert result.ok is False
        assert result.metadata["status"] == "no_port"

    asyncio.run(_run())


def test_read_skill_tool_missing_params():
    """ReadDesignSkillTool rejects calls with missing required params."""
    async def _run():
        tool = ReadDesignSkillTool()
        from app.game_core.narrative.context import AgentContext
        ctx = AgentContext(role="planner", world=None, state=None, metadata={})
        result = await tool.execute({"category": "", "name": "hunt"}, ctx)
        assert result.ok is False
        assert result.metadata["status"] == "invalid_params"

    asyncio.run(_run())


def test_list_skills_tool():
    """ListDesignSkillsTool returns skills from the port."""
    async def _run():
        tool = ListDesignSkillsTool()
        mock_port = AsyncMock()
        mock_port.list_skills = AsyncMock(return_value=[
            {"category": "quests", "name": "hunt"},
            {"category": "quests", "name": "escort"},
        ])
        from app.game_core.narrative.context import AgentContext
        ctx = AgentContext(
            role="planner",
            world=None,
            state=None,
            metadata={"design_skill_port": mock_port, "world_id": "w"},
        )
        result = await tool.execute({}, ctx)
        assert result.ok is True
        assert result.metadata["count"] == 2
        assert result.metadata["skills"][0]["name"] == "hunt"

    asyncio.run(_run())


def test_planner_tools_registered_for_role():
    """register_planner_tools adds tools accessible for the 'planner' role."""
    registry = RoleToolRegistry()
    register_planner_tools(registry)

    tools = registry.get_tools_for("planner")
    tool_names = {t.name for t in tools}
    assert "read_design_skill" in tool_names
    assert "list_design_skills" in tool_names
    # Must not bleed into other roles
    gm_tools = registry.get_tools_for("gm")
    assert all(t.name not in {"read_design_skill", "list_design_skills"} for t in gm_tools)


def test_local_provider_reads_file():
    """LocalDesignSkillProvider reads .md files from temporary directory."""
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            skill_dir = base / "test_world" / "planner_skills" / "quests"
            skill_dir.mkdir(parents=True)
            (skill_dir / "hunt.md").write_text("# Hunt\nContent here.", encoding="utf-8")

            provider = LocalDesignSkillProvider(base_dir=base)
            content = await provider.read_skill("test_world", "quests", "hunt")
            assert content is not None
            assert "Hunt" in content

    asyncio.run(_run())


def test_local_provider_not_found():
    """LocalDesignSkillProvider returns None for missing files."""
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = LocalDesignSkillProvider(base_dir=Path(tmpdir))
            content = await provider.read_skill("test_world", "quests", "missing")
            assert content is None

    asyncio.run(_run())


def test_local_provider_path_traversal():
    """LocalDesignSkillProvider rejects path-traversal segments."""
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = LocalDesignSkillProvider(base_dir=Path(tmpdir))
            # Each bad segment variant
            assert await provider.read_skill("../evil", "quests", "hunt") is None
            assert await provider.read_skill("world", "../etc", "hunt") is None
            assert await provider.read_skill("world", "quests", "../passwd") is None
            assert await provider.read_skill("world", "quests/evil", "hunt") is None
            assert await provider.read_skill("world", "quests", "hunt/../../etc") is None

    asyncio.run(_run())


def test_local_provider_list_skills():
    """LocalDesignSkillProvider lists all templates under a world."""
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            quests_dir = base / "w" / "planner_skills" / "quests"
            npcs_dir = base / "w" / "planner_skills" / "npcs"
            quests_dir.mkdir(parents=True)
            npcs_dir.mkdir(parents=True)
            (quests_dir / "hunt.md").write_text("hunt", encoding="utf-8")
            (quests_dir / "escort.md").write_text("escort", encoding="utf-8")
            (npcs_dir / "trader.md").write_text("trader", encoding="utf-8")

            provider = LocalDesignSkillProvider(base_dir=base)
            all_skills = await provider.list_skills("w")
            assert len(all_skills) == 3
            quest_skills = await provider.list_skills("w", category="quests")
            assert len(quest_skills) == 2
            names = {s["name"] for s in quest_skills}
            assert names == {"hunt", "escort"}

    asyncio.run(_run())


def test_planner_multi_turn_with_tool():
    """AgenticNarrativePlanner.plan() uses executor when provided."""
    async def _run():
        from app.narrators import AgenticNarrativePlanner
        from app.game_core.adapters.llm import NullLlmProvider

        mock_executor = MagicMock()
        mock_executor.run_agentic = AsyncMock(return_value=MagicMock(
            text='{"directives": [], "strategy_notes": "", "story_facts": []}',
            tool_results=[],
            turns_used=1,
            metadata={"status": "completed"},
        ))

        planner = AgenticNarrativePlanner(
            llm=NullLlmProvider(),
            executor=mock_executor,
            design_skill_port=NullDesignSkillPort(),
            world_id="test_world",
        )
        context: dict = {
            "current_tick": 1,
            "narrative_plan": {},
            "quests": {},
            "time": {},
            "location": {},
            "__world__": None,
            "__state__": None,
            "__world_id__": "test_world",
        }
        result = await planner.plan(context)
        assert "directives" in result
        assert mock_executor.run_agentic.await_count == 1
        call_kwargs = mock_executor.run_agentic.call_args
        assert call_kwargs.kwargs.get("role") == "planner" or call_kwargs.args[0] == "planner"

    asyncio.run(_run())


def test_planner_fallback_single_shot():
    """AgenticNarrativePlanner.plan() uses single-shot LLM when executor=None."""
    async def _run():
        from app.narrators import AgenticNarrativePlanner
        from app.game_core.adapters.llm import NullLlmProvider

        planner = AgenticNarrativePlanner(
            llm=NullLlmProvider(),
            executor=None,
        )
        context: dict = {
            "current_tick": 1,
            "narrative_plan": {},
            "quests": {},
            "time": {},
            "location": {},
        }
        result = await planner.plan(context)
        # NullLlmProvider returns empty text → JSON parse fails → noop
        assert isinstance(result, dict)
        assert "directives" in result

    asyncio.run(_run())


def test_null_design_skill_port():
    """NullDesignSkillPort returns None/empty safely."""
    async def _run():
        port = NullDesignSkillPort()
        assert await port.read_skill("w", "quests", "hunt") is None
        assert await port.list_skills("w") == []
        assert await port.list_skills("w", category="quests") == []

    asyncio.run(_run())
