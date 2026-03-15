from __future__ import annotations

import asyncio

from app.game_core import GameRuntime
from app.game_core.adapters import NullPersistencePort, SaveStore
from app.game_core.bootstrap import build_default_runtime
from app.game_core.orchestration.models import SSEEvent


def _runtime() -> GameRuntime:
    return GameRuntime(save_store=SaveStore(NullPersistencePort()))


def test_game_runtime_get_world_caches_and_force_reload_rebuilds() -> None:
    runtime = _runtime()

    world_a = runtime.get_world("test_world", world_data={})
    world_b = runtime.get_world("test_world")
    world_c = runtime.get_world("test_world", world_data={}, force_reload=True)

    assert world_a is world_b
    assert world_c is not world_a
    assert runtime.has_world("test_world") is True


def test_game_runtime_can_create_list_and_delete_sessions_with_in_memory_store() -> None:
    runtime = _runtime()

    session = asyncio.run(
        runtime.create_session(
            "goblin_slayer",
            world_data={},
            session_id="sess_alpha",
        )
    )
    listed = asyncio.run(runtime.list_sessions("goblin_slayer"))

    assert session.session_id == "sess_alpha"
    assert [item.session_id for item in listed] == ["sess_alpha"]
    assert asyncio.run(runtime.delete_session("other_world", "sess_alpha")) is False
    assert asyncio.run(runtime.delete_session("goblin_slayer", "sess_alpha")) is True
    assert asyncio.run(runtime.list_sessions("goblin_slayer")) == []


def test_game_runtime_does_not_delete_legacy_session_without_world_id() -> None:
    port = NullPersistencePort()
    runtime = GameRuntime(save_store=SaveStore(port))
    asyncio.run(
        port.save(
            "sess_legacy",
            {
                "state": {},
                "meta": {"session_id": "sess_legacy", "saved_at": 1.0},
            },
        )
    )

    assert asyncio.run(runtime.delete_session("goblin_slayer", "sess_legacy")) is False


def test_game_runtime_registers_post_action_hook_when_agent_service_is_injected() -> None:
    class StubAgentOrchestration:
        async def run_post_action_round(self, shared, result, apply_delta, event_sink=None):
            return []

    port = NullPersistencePort()
    runtime = GameRuntime(
        save_store=SaveStore(port),
        agent_orchestration=StubAgentOrchestration(),
    )

    created = asyncio.run(
        runtime.create_session(
            "test_world",
            world_data={},
            session_id="sess_agent_hook",
        )
    )
    resumed = asyncio.run(
        runtime.resume_session(
            "test_world",
            "sess_agent_hook",
            world_data={},
        )
    )

    assert created.runtime.pipeline._stage_b_runner is not None
    assert resumed is not None
    assert resumed.runtime.pipeline._stage_b_runner is not None


def test_tick_coordinator_agent_hook_failure_is_non_fatal() -> None:
    runtime = build_default_runtime("test_world", world_data={})

    async def _failing_hook(shared, result, apply_delta, event_sink=None):
        raise RuntimeError("agent round broke")

    runtime.pipeline.set_stage_b_runner(_failing_hook)
    collected: list[SSEEvent] = []

    async def _event_sink(event: SSEEvent) -> None:
        collected.append(event)

    result = asyncio.run(
        runtime.tick_coordinator.process(
            {
                "action_type": "set_flag",
                "params": {"key": "agent_round_survived", "value": True},
            },
            event_sink=_event_sink,
        )
    )

    assert result.executed is True
    assert runtime.state.flags.get("agent_round_survived") is True
    assert any(event.event_type == "agent_hook_error" for event in result.sse_events)
    assert any(event.event_type == "agent_hook_error" for event in collected)


def test_game_runtime_binds_graph_and_rehydrates_story_facts_on_resume() -> None:
    class StubGraph:
        def __init__(self) -> None:
            self.inject_calls: list[list[dict[str, object]]] = []

        def inject_story_facts(self, facts, session_id: str = ""):
            self.inject_calls.append([dict(fact) for fact in facts])

    class StubAgentOrchestration:
        def __init__(self, graph: StubGraph) -> None:
            self._memory_retriever = type("_Retriever", (), {"_graph": graph})()

    async def _run() -> None:
        graph = StubGraph()
        runtime = GameRuntime(
            save_store=SaveStore(NullPersistencePort()),
            agent_orchestration=StubAgentOrchestration(graph),
        )

        session = await runtime.create_session(
            "test_world",
            world_data={},
            session_id="sess_story_facts_runtime",
        )
        assert session.runtime.tick_coordinator.knowledge_graph is graph

        session.runtime.state.narrative_plan.add_story_facts([
            {"subject": "guild", "relation": "warns_about", "object": "raiders"},
        ])
        await runtime.save_session(session)

        graph.inject_calls.clear()
        resumed = await runtime.resume_session(
            "test_world",
            "sess_story_facts_runtime",
            world_data={},
        )

        assert resumed is not None
        assert resumed.runtime.tick_coordinator.knowledge_graph is graph
        assert graph.inject_calls == [[
            {"subject": "guild", "relation": "warns_about", "object": "raiders"},
        ]]

    asyncio.run(_run())
