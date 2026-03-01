"""Root-level bootstrap helpers for a default game-core runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from app.game_core.content import ContentRegistry, WorldInstance
from app.game_core.content.registries import (
    CharacterRegistry,
    ClassRegistry,
    FactionRegistry,
    ItemRegistry,
    LoreRegistry,
    MapRegistry,
    MonsterRegistry,
    QuestRegistry,
    SkillRegistry,
    TagRegistry,
)
from app.game_core.orchestration import (
    ActionDispatcher,
    PipelineOrchestrator,
    SceneBus,
    TickCoordinator,
    build_default_action_dispatcher,
    register_default_settlement_hooks,
)
from app.game_core.orchestration.hooks.ai_osiris import AIOsirisEvaluator, AIOsirisHook
from app.game_core.orchestration.hooks.gm_narration import GmNarrationHook, GmNarrator
from app.game_core.orchestration.hooks.narrative_planner import NarrativePlannerHook, NarrativePlannerProvider
from app.game_core.rules import RulesEngine, register_default_rules_handlers
from app.game_core.state import StateContainer, StateSlice
from app.game_core.state.slices import (
    AreaSlice,
    EventSlice,
    FlagSlice,
    NarrativePlanSlice,
    PartySlice,
    PlayerSlice,
    QuestSlice,
    RelationSlice,
    SceneSlice,
    TimeSlice,
)


DEFAULT_CONTENT_REGISTRY_TYPES: tuple[type[ContentRegistry], ...] = (
    TagRegistry,
    MapRegistry,
    ClassRegistry,
    SkillRegistry,
    LoreRegistry,
    CharacterRegistry,
    ItemRegistry,
    MonsterRegistry,
    FactionRegistry,
    QuestRegistry,
)

DEFAULT_STATE_SLICE_TYPES: tuple[type[StateSlice], ...] = (
    TimeSlice,
    PlayerSlice,
    RelationSlice,
    QuestSlice,
    FlagSlice,
    AreaSlice,
    EventSlice,
    PartySlice,
    NarrativePlanSlice,
    SceneSlice,
)


@dataclass(slots=True)
class DefaultRuntime:
    """Fully assembled default runtime components."""

    world: WorldInstance
    state: StateContainer
    rules_engine: RulesEngine
    action_dispatcher: ActionDispatcher
    scene_bus: SceneBus
    pipeline: PipelineOrchestrator
    tick_coordinator: TickCoordinator


def build_default_world(
    world_id: str,
    world_data: dict[str, Any] | None = None,
) -> WorldInstance:
    """Build a default world container with the canonical registries."""
    world = WorldInstance(world_id)
    for registry_type in DEFAULT_CONTENT_REGISTRY_TYPES:
        world.register(registry_type())
    if world_data is not None:
        world.load_all(world_data)
    return world


def build_default_state() -> StateContainer:
    """Build a scaffold-only empty state container with canonical slices."""
    state = StateContainer()
    for slice_type in DEFAULT_STATE_SLICE_TYPES:
        state.register(slice_type())
    return state


def build_default_runtime(
    world_id: str,
    world_data: dict[str, Any] | None = None,
) -> DefaultRuntime:
    """Build a fully wired default runtime for a new session."""
    world = build_default_world(world_id, world_data=world_data)
    return build_runtime_for_world(world)


def build_restored_runtime(
    world_id: str,
    session_data: Mapping[str, Mapping[str, Any]],
    world_data: dict[str, Any] | None = None,
) -> DefaultRuntime:
    """Build a fully wired default runtime from restored session payload."""
    world = build_default_world(world_id, world_data=world_data)
    return build_restored_runtime_for_world(world, session_data)


def build_runtime_for_world(
    world: WorldInstance,
    *,
    gm_narrator_factory: Callable[[WorldInstance, StateContainer], GmNarrator] | None = None,
    osiris_evaluator_factory: Callable[[], AIOsirisEvaluator] | None = None,
    narrative_planner_factory: Callable[[], NarrativePlannerProvider] | None = None,
) -> DefaultRuntime:
    """Build a fully wired default runtime for an already loaded world.

    If *osiris_evaluator_factory* is provided, it is called to create an
    LLM-driven AIOsirisEvaluator.  If *gm_narrator_factory* is provided,
    it is called with (world, state) to create an LLM-driven GmNarrator.
    If *narrative_planner_factory* is provided, it is called to create an
    LLM-driven NarrativePlanner. All resulting hooks are registered before
    the defaults so the deterministic fallbacks are skipped.
    """
    state = StateContainer.create_new(world)
    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)
    action_dispatcher = build_default_action_dispatcher()
    pipeline = PipelineOrchestrator(action_dispatcher=action_dispatcher)
    scene_bus = SceneBus(state.scene)
    tick_coordinator = TickCoordinator(
        world=world,
        state=state,
        rules_engine=rules_engine,
        scene_bus=scene_bus,
        pipeline=pipeline,
    )
    if osiris_evaluator_factory is not None:
        evaluator = osiris_evaluator_factory()
        tick_coordinator.register_settlement_hook(
            AIOsirisHook(evaluator=evaluator)
        )
    if gm_narrator_factory is not None:
        narrator = gm_narrator_factory(world, state)
        tick_coordinator.register_settlement_hook(
            GmNarrationHook(narrator=narrator)
        )
    if narrative_planner_factory is not None:
        planner = narrative_planner_factory()
        tick_coordinator.register_settlement_hook(
            NarrativePlannerHook(planner=planner)
        )
    register_default_settlement_hooks(tick_coordinator)
    return DefaultRuntime(
        world=world,
        state=state,
        rules_engine=rules_engine,
        action_dispatcher=action_dispatcher,
        scene_bus=scene_bus,
        pipeline=pipeline,
        tick_coordinator=tick_coordinator,
    )


def build_restored_runtime_for_world(
    world: WorldInstance,
    session_data: Mapping[str, Mapping[str, Any]],
    *,
    gm_narrator_factory: Callable[[WorldInstance, StateContainer], GmNarrator] | None = None,
    osiris_evaluator_factory: Callable[[], AIOsirisEvaluator] | None = None,
    narrative_planner_factory: Callable[[], NarrativePlannerProvider] | None = None,
) -> DefaultRuntime:
    """Build a fully wired default runtime from restored session payload."""
    state = StateContainer.create_restored(world, session_data)
    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)
    action_dispatcher = build_default_action_dispatcher()
    pipeline = PipelineOrchestrator(action_dispatcher=action_dispatcher)
    scene_bus = SceneBus(state.scene)
    tick_coordinator = TickCoordinator(
        world=world,
        state=state,
        rules_engine=rules_engine,
        scene_bus=scene_bus,
        pipeline=pipeline,
    )
    if osiris_evaluator_factory is not None:
        evaluator = osiris_evaluator_factory()
        tick_coordinator.register_settlement_hook(
            AIOsirisHook(evaluator=evaluator)
        )
    if gm_narrator_factory is not None:
        narrator = gm_narrator_factory(world, state)
        tick_coordinator.register_settlement_hook(
            GmNarrationHook(narrator=narrator)
        )
    if narrative_planner_factory is not None:
        planner = narrative_planner_factory()
        tick_coordinator.register_settlement_hook(
            NarrativePlannerHook(planner=planner)
        )
    register_default_settlement_hooks(tick_coordinator)
    return DefaultRuntime(
        world=world,
        state=state,
        rules_engine=rules_engine,
        action_dispatcher=action_dispatcher,
        scene_bus=scene_bus,
        pipeline=pipeline,
        tick_coordinator=tick_coordinator,
    )
