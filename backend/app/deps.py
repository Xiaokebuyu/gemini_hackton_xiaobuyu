"""Shared dependencies for the Game Core API."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from app.agent_orchestration import AgentOrchestrationService

from fastapi import FastAPI, HTTPException

from app.admin_coordinator import AdminCoordinator
from app.api_models import StructuredActionRequest
from app.interaction_service import InteractionService
from app.game_core import GameRuntime, ManagedSession
from app.game_core.adapters import FastAPIInputPort, InputPort
from app.game_core.orchestration.models import PipelineResult, SSEEvent
from app.world_seed import WORLD_CATALOG


# Module-level LLM provider reference — set during _build_game_runtime().
# get_llm_provider() returns this value (None when no API key is configured).
_llm_provider: Any = None


def _build_fallback_planner_system_factory() -> Any:
    """Return a no-LLM planner system factory (C-3: W5-3 deterministic fallback).

    The returned factory builds a PlannerSystemAssembly that:
    - Runs bootstrap events through OpeningBootstrapQuestAgent (deterministic)
    - Returns empty result for all other events (graceful degradation)
    """
    from app.game_core.adapters.planner_system import PlannerSystemAssembly
    from app.game_core.planning.opening_bootstrap import OpeningBootstrapQuestAgent

    class _FallbackBlackboard:
        """No-op blackboard for no-LLM mode."""

        @property
        def history_key(self) -> str:
            return "__deterministic_fallback_blackboard__"

        async def plan(self, context: dict) -> dict:
            return {
                "directives": [],
                "story_facts": [],
                "strategy_notes": "",
                "metadata": {"provider": "deterministic_fallback", "reason": "no_llm"},
            }

        def export_history(self) -> list:
            return []

        def import_history(self, data: list) -> None:
            pass

    class _FallbackAgent:
        """No-op subsystem agent for no-LLM mode."""

        def __init__(self, name: str) -> None:
            self._name = name

        @property
        def history_key(self) -> str:
            return f"__deterministic_fallback_{self._name}__"

        async def evaluate(self, context: dict) -> dict:
            return {
                "directives": [],
                "story_facts": [],
                "strategy_notes": "",
                "metadata": {"provider": "deterministic_fallback", "reason": "no_llm"},
            }

        def export_history(self) -> list:
            return []

        def import_history(self, data: list) -> None:
            pass

    def _factory() -> PlannerSystemAssembly:
        return PlannerSystemAssembly(
            blackboard=_FallbackBlackboard(),
            quest_manager_agent=OpeningBootstrapQuestAgent(),
            npc_director_agent=_FallbackAgent("npc_director"),
            world_builder_agent=_FallbackAgent("world_builder"),
            narrative_weaver_agent=_FallbackAgent("narrative_weaver"),
        )

    return _factory


def _build_game_runtime() -> GameRuntime:
    """Build the singleton GameRuntime, constructing all app-layer services here."""
    global _llm_provider
    llm_provider = None
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if api_key:
        from app.llm_gemini import GeminiLlmAdapter

        llm_provider = GeminiLlmAdapter()
    _llm_provider = llm_provider

    # WorldKnowledgeGraph is always enabled (no API key needed).
    # Graph is seeded lazily on first query for each WorldInstance.
    from app.world_knowledge_graph import WorldKnowledgeGraph
    from app.memory_retriever_impl import KnowledgeGraphMemoryRetriever
    from app.game_core.narrative.instance_manager import InstanceManager

    # WorldKnowledgeGraph accepts llm_provider for write_episode + lore enrichment.
    # llm_provider may be None (no API key) — graph degrades gracefully to static mode.
    knowledge_graph = WorldKnowledgeGraph(llm=llm_provider)
    memory_retriever = KnowledgeGraphMemoryRetriever(knowledge_graph)
    instance_manager = InstanceManager()

    # ── app-layer service construction (composition root) ─────────────────────
    agent_orchestration = None
    if llm_provider is not None:
        from app.game_core.narrative.executor import AgenticExecutor
        from app.game_core.narrative.gm_tools import register_gm_tools
        from app.game_core.narrative.character_tools import (
            register_npc_tools,
            register_teammate_tools,
        )
        from app.game_core.narrative.registry import RoleToolRegistry
        from app.agent_orchestration import AgentOrchestrationService

        registry = RoleToolRegistry()
        register_gm_tools(registry)
        register_npc_tools(registry)
        register_teammate_tools(registry)
        executor = AgenticExecutor(tool_registry=registry, llm=llm_provider)
        agent_orchestration = AgentOrchestrationService(
            executor,
            memory_retriever=memory_retriever,
            instance_manager=instance_manager,
        )

    # Osiris LLM evaluator removed — AIOsirisHook now uses MechanicalOsirisEngine (pure rules).
    osiris_evaluator_factory = None

    gm_narrator_factory = None
    if llm_provider is not None:
        llm = llm_provider
        # Capture knowledge_graph reference for GM graphize callback
        _gm_graph = knowledge_graph

        def _build_gm_narrator(world: Any, state: Any) -> Any:
            from app.game_core.narrative.executor import AgenticExecutor
            from app.game_core.narrative.gm_tools import register_gm_tools
            from app.game_core.narrative.registry import RoleToolRegistry
            from app.narrators import AgenticGmNarrator

            registry = RoleToolRegistry()
            register_gm_tools(registry)
            executor = AgenticExecutor(tool_registry=registry, llm=llm)
            graphize_callback = _make_graphize_callback(_gm_graph)
            return AgenticGmNarrator(
                executor=executor,
                world=world,
                state=state,
                graphize_callback=graphize_callback,
            )

        gm_narrator_factory = _build_gm_narrator

    def _make_graphize_callback(graph: Any) -> Callable | None:
        """Build an async graphize callback that writes episodes to WorldKnowledgeGraph."""
        if graph is None:
            return None

        async def _callback(actor_id: str, messages: list, session_id: str = "") -> None:
            try:
                await graph.write_episode(
                    actor_id=actor_id,
                    messages=messages,
                    context={},
                    session_id=session_id,
                )
            except Exception:
                pass  # graphize failures must not break planner execution

        return _callback

    planner_system_factory = None
    if llm_provider is not None:
        def _build_planner_system() -> Any:
            from app.game_core.adapters.planner_system import PlannerSystemAssembly
            from app.game_core.narrative.executor import AgenticExecutor
            from app.game_core.narrative.registry import RoleToolRegistry
            from app.game_core.narrative.planner_tools import register_planner_tools
            from app.llm_gemini import GeminiLlmAdapter
            from pathlib import Path
            from app.design_skill_provider import LocalDesignSkillProvider
            from app.narrators import (
                AgenticNarrativePlanner,
                UNIFIED_PLANNER_PROMPT,
                _format_subsystem_context,
            )

            # Phase 2 refactor: 1 unified LLM + 1 registry replaces the former 5
            unified_registry = RoleToolRegistry()
            register_planner_tools(
                unified_registry,
                roles=["planner"],
            )

            unified_llm = GeminiLlmAdapter(thinking_level="high", profile_name="planner")

            # Build graphize callback for the unified planner instance
            graphize_callback = _make_graphize_callback(knowledge_graph)

            return PlannerSystemAssembly(
                blackboard=AgenticNarrativePlanner(
                    llm=unified_llm,
                    executor=AgenticExecutor(tool_registry=unified_registry, llm=unified_llm),
                    design_skill_port=LocalDesignSkillProvider(
                        base_dir=Path(__file__).resolve().parent.parent / "data" / "goblin_slayer" / "v2",
                        world_id_in_path=False,
                    ),
                    world_id="",
                    role="planner",
                    system_prompt=UNIFIED_PLANNER_PROMPT,
                    provider_name="planner",
                    history_key="__planner__",
                    context_formatter=_format_subsystem_context,
                    allowed_skill_categories=["quests", "npcs", "areas", "environments", "encounters", "narrative", "social"],
                    graphize_callback=graphize_callback,
                    memory_retriever=memory_retriever,
                ),
                quest_manager_agent=None,
                npc_director_agent=None,
                world_builder_agent=None,
                narrative_weaver_agent=None,
            )

        planner_system_factory = _build_planner_system
    else:
        # C-3: W5-3 — no LLM: use deterministic fallback (bootstrap works, regular planning noop)
        planner_system_factory = _build_fallback_planner_system_factory()
    # ── end app-layer construction ─────────────────────────────────────────────

    return GameRuntime(
        instance_manager=instance_manager,
        agent_orchestration=agent_orchestration,
        gm_narrator_factory=gm_narrator_factory,
        osiris_evaluator_factory=osiris_evaluator_factory,
        planner_system_factory=planner_system_factory,
    )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Deferred composition root: build runtime on server startup, not at import time."""
    if getattr(app.state, "game_runtime", None) is None:
        app.state.game_runtime = _build_game_runtime()
    if getattr(app.state, "admin_coordinator", None) is None:
        app.state.admin_coordinator = AdminCoordinator(app.state.game_runtime)
    if getattr(app.state, "input_port", None) is None:
        app.state.input_port = FastAPIInputPort()
    if not hasattr(app.state, "interaction_service"):
        app.state.interaction_service = None
    yield


app = FastAPI(title="Game Core API", version="0.1.0", lifespan=_lifespan)

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "https://buyus-isekai-adventure.pages.dev",
        "https://isekai.xiaobuyu.trade",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_game_runtime() -> GameRuntime:
    """Return the active runtime for request handlers and tests."""

    return app.state.game_runtime


def get_input_port() -> InputPort:
    """Return the active inbound adapter for request handlers and tests."""

    return app.state.input_port


def get_admin_coordinator() -> AdminCoordinator:
    """Return the active session-level coordinator."""

    runtime = get_game_runtime()
    coordinator = getattr(app.state, "admin_coordinator", None)
    if coordinator is None or getattr(coordinator, "runtime", None) is not runtime:
        coordinator = AdminCoordinator(runtime)
        app.state.admin_coordinator = coordinator
    return coordinator


def get_agent_orchestration() -> AgentOrchestrationService | None:
    """Return the AgentOrchestrationService, or None if LLM is unavailable."""
    return get_game_runtime().agent_orchestration


def get_llm_provider() -> Any:
    """Return the shared LLM provider, or None if no API key is configured."""
    return _llm_provider


def get_interaction_service() -> InteractionService:
    """Return the active interaction service for request handlers and tests."""

    service = getattr(app.state, "interaction_service", None)
    if service is None:
        service = InteractionService(
            execute_structured_action=_execute_structured_action,
        )
        app.state.interaction_service = service
    return service


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    """Build one consistent JSON error payload."""

    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _validate_world_id(world_id: str) -> None:
    """Reject empty or path-like world identifiers."""

    value = world_id.strip()
    if not value:
        raise _api_error(400, "invalid_world_id", "invalid world_id")
    if "/" in value or "\\" in value or ".." in value or value.startswith("."):
        raise _api_error(400, "invalid_world_id", "invalid world_id")


def _validate_session_id(session_id: str) -> None:
    """Reject empty or path-like session identifiers."""

    value = session_id.strip()
    if not value:
        raise _api_error(400, "invalid_session_id", "invalid session_id")
    if "/" in value or "\\" in value or ".." in value or value.startswith("."):
        raise _api_error(400, "invalid_session_id", "invalid session_id")


def _world_not_found() -> HTTPException:
    """Return the unified error for an unknown world identifier."""

    raise _api_error(404, "world_not_found", "world not found")


def _session_not_found() -> HTTPException:
    """Return the unified error for a missing session identifier."""

    raise _api_error(404, "session_not_found", "session not found")


def _require_world(world_id: str) -> None:
    """Validate one world id and ensure it belongs to the supported catalog."""

    _validate_world_id(world_id)
    known_ids = {item["world_id"] for item in WORLD_CATALOG}
    if world_id not in known_ids:
        _world_not_found()


def _ensure_shell_world(runtime: GameRuntime, world_id: str) -> None:
    """Ensure one canonical world is loaded and validated."""
    try:
        runtime.ensure_world(world_id)
    except ValueError as exc:
        raise _api_error(500, "world_bootstrap_failed", str(exc)) from exc


async def _load_session_or_404(world_id: str, session_id: str) -> ManagedSession:
    """Resume one session against the canonical shell world."""

    _require_world(world_id)
    _validate_session_id(session_id)
    session = await get_admin_coordinator().get_session(world_id, session_id)
    if session is None:
        _session_not_found()
    return session


def _session_phase(session: ManagedSession) -> str:
    """Return the persisted lifecycle phase for one managed session."""

    return session.phase


async def _execute_structured_action(
    session: ManagedSession,
    request: StructuredActionRequest,
    event_sink: Callable[[SSEEvent], Awaitable[None]] | None = None,
    after_engine: Callable[[PipelineResult], Awaitable[None]] | None = None,
) -> PipelineResult:
    """Execute one structured action through the real tick pipeline."""

    action_type = request.action_type.strip()
    if not action_type:
        raise _api_error(400, "invalid_action_request", "action_type must be non-empty")
    if not isinstance(request.params, dict):
        raise _api_error(400, "invalid_action_request", "params must be an object")
    if not session.runtime.action_dispatcher.has_action(action_type):
        raise _api_error(400, "unknown_action", f"unknown action: {action_type}")
    result = await session.runtime.tick_coordinator.process(
        {
            "action_type": action_type,
            "params": dict(request.params),
            "source": request.source,
            "context": dict(request.context) if isinstance(request.context, dict) else None,
        },
        event_sink=event_sink,
        after_engine=after_engine,
    )
    await get_admin_coordinator().save_session(session)
    return result


async def _finalize_dialogue_turn(
    session: ManagedSession,
    *,
    time_cost: float,
    event_sink: Callable[[SSEEvent], Any] | None = None,
    turn_action_record: Mapping[str, Any] | None = None,
) -> list[SSEEvent]:
    """Finalize one dialogue/private-chat turn through TickCoordinator."""

    settlement_events = await session.runtime.tick_coordinator.finalize_external_turn(
        time_cost=time_cost,
        event_sink=event_sink,
        turn_action_record=turn_action_record,
    )
    await get_admin_coordinator().save_session(session)
    return settlement_events
