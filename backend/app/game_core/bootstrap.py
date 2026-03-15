"""Root-level bootstrap helpers for a default game-core runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from app.game_core.adapters.planner_system import PlannerSystemAssembly
from app.game_core.content import ContentRegistry, WorldInstance
from app.game_core.narrative.companion_runtime import CompanionRuntimeManager
from app.game_core.narrative.instance_manager import InstanceManager
from app.game_core.planning import (
    DynamicSubAreaManager,
    NarrativeWeaverSubSystem,
    NpcDirectorSubSystem,
    PacingControllerSubSystem,
    PlannerDispatcher,
    QuestManagerSubSystem,
    WorldBuilderSubSystem,
)
from app.game_core.content.registries import (
    BattleMapRegistry,
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
from app.game_core.orchestration.hooks.ai_osiris import AIOsirisEvaluator, AIOsirisHook, MechanicalOsirisEngine
from app.game_core.orchestration.hooks.encounter import BasicEncounterDetector, EncounterPhase
from app.game_core.orchestration.hooks.event_condition import BasicEventConditionEvaluator, EventConditionPhase
from app.game_core.orchestration.hooks.passive_perception import PerceptionPhase
from app.game_core.orchestration.hooks.gm_narration import GmNarrationHook, GmNarrator
from app.game_core.orchestration.hooks.narrative_planner import NarrativePlannerHook
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
    BattleMapRegistry,
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
    instance_manager: InstanceManager | None = None
    companion_manager: CompanionRuntimeManager | None = None


def build_narrative_planner_hook(
    planner_system: PlannerSystemAssembly,
    *,
    state: StateContainer,
    instance_manager: InstanceManager | None = None,
) -> NarrativePlannerHook:
    """Wire one planner-system assembly into the canonical planner hook stack."""
    planner_hook = NarrativePlannerHook(blackboard=planner_system.blackboard)
    dispatcher = PlannerDispatcher()
    sub_area_manager = DynamicSubAreaManager(state.areas)
    quest_manager = QuestManagerSubSystem(
        dispatcher=dispatcher,
        agent=planner_system.quest_manager_agent,
        sse_collector=planner_hook._pending_sse,
    )
    dispatcher.register(quest_manager)
    dispatcher.register(NpcDirectorSubSystem(
        instance_manager=instance_manager,
        agent=planner_system.npc_director_agent,
    ))
    dispatcher.register(WorldBuilderSubSystem(
        sub_area_manager=sub_area_manager,
        sse_collector=planner_hook._pending_sse,
        agent=planner_system.world_builder_agent,
    ))
    dispatcher.register(PacingControllerSubSystem())
    dispatcher.register(NarrativeWeaverSubSystem(
        sse_collector=planner_hook._pending_sse,
        agent=planner_system.narrative_weaver_agent,
    ))
    planner_hook._dispatcher = dispatcher
    return planner_hook


def _register_default_content_registries(world: WorldInstance) -> None:
    for registry_type in DEFAULT_CONTENT_REGISTRY_TYPES:
        world.register(registry_type())


def _register_default_state_slices(state: StateContainer) -> None:
    for slice_type in DEFAULT_STATE_SLICE_TYPES:
        state.register(slice_type())


def build_default_world(
    world_id: str,
    world_data: dict[str, Any] | None = None,
) -> WorldInstance:
    """Build a default world container with the canonical registries."""
    world = WorldInstance(world_id)
    _register_default_content_registries(world)
    if world_data is not None:
        world.load_all(world_data)
        issues = world.validate()
        if issues:
            formatted = []
            for registry_name, registry_issues in issues.items():
                for issue in registry_issues:
                    formatted.append(f"{registry_name}: {issue}")
            summary = "; ".join(formatted[:10])
            raise ValueError(f"world validation failed for '{world_id}': {summary}")
    return world


def build_default_state() -> StateContainer:
    """Build a scaffold-only empty state container with canonical slices."""
    state = StateContainer()
    _register_default_state_slices(state)
    return state


def build_default_runtime(
    world_id: str,
    world_data: dict[str, Any] | None = None,
    *,
    instance_manager: InstanceManager | None = None,
) -> DefaultRuntime:
    """Build a fully wired default runtime for a new session."""
    world = build_default_world(world_id, world_data=world_data)
    return build_runtime_for_world(world, instance_manager=instance_manager)


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
    planner_system_factory: Callable[[], PlannerSystemAssembly] | None = None,
    instance_manager: InstanceManager | None = None,
) -> DefaultRuntime:
    """Build a fully wired default runtime for an already loaded world.

    AIOsirisHook is always registered with MechanicalOsirisEngine (no LLM).
    If *gm_narrator_factory* is provided, it is called with (world, state)
    to create an LLM-driven GmNarrator.
    If *planner_system_factory* is provided, it is called to create a
    multi-agent planner system.
    """
    state = StateContainer.create_new(world)
    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)
    action_dispatcher = build_default_action_dispatcher()
    pipeline = PipelineOrchestrator(action_dispatcher=action_dispatcher)
    scene_bus = SceneBus(state.scene)
    runtime_companion_manager = CompanionRuntimeManager()
    tick_coordinator = TickCoordinator(
        world=world,
        state=state,
        rules_engine=rules_engine,
        scene_bus=scene_bus,
        pipeline=pipeline,
        companion_manager=runtime_companion_manager,
    )
    # Always register mechanical Osiris engine (osiris_evaluator_factory is ignored — kept for
    # backward-compat call sites in runtime.py / tests that still pass the parameter)
    tick_coordinator.register_settlement_hook(
        AIOsirisHook(
            engine=MechanicalOsirisEngine(),
            encounter_phase=EncounterPhase(detector=BasicEncounterDetector()),
            perception_phase=PerceptionPhase(),
            event_phase=EventConditionPhase(evaluator=BasicEventConditionEvaluator()),
        )
    )
    if gm_narrator_factory is not None:
        narrator = gm_narrator_factory(world, state)
        tick_coordinator.register_settlement_hook(
            GmNarrationHook(narrator=narrator)
        )
    planner_system = planner_system_factory() if planner_system_factory is not None else None
    if planner_system is not None:
        tick_coordinator.register_settlement_hook(
            build_narrative_planner_hook(
                planner_system,
                state=state,
                instance_manager=instance_manager,
            )
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
        instance_manager=instance_manager,
        companion_manager=runtime_companion_manager,
    )


def build_restored_runtime_for_world(
    world: WorldInstance,
    session_data: Mapping[str, Mapping[str, Any]],
    *,
    gm_narrator_factory: Callable[[WorldInstance, StateContainer], GmNarrator] | None = None,
    osiris_evaluator_factory: Callable[[], AIOsirisEvaluator] | None = None,
    planner_system_factory: Callable[[], PlannerSystemAssembly] | None = None,
    instance_manager: InstanceManager | None = None,
) -> DefaultRuntime:
    """Build a fully wired default runtime from restored session payload."""
    state = StateContainer.create_restored(world, session_data)
    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)
    action_dispatcher = build_default_action_dispatcher()
    pipeline = PipelineOrchestrator(action_dispatcher=action_dispatcher)
    scene_bus = SceneBus(state.scene)
    runtime_companion_manager = CompanionRuntimeManager()
    tick_coordinator = TickCoordinator(
        world=world,
        state=state,
        rules_engine=rules_engine,
        scene_bus=scene_bus,
        pipeline=pipeline,
        companion_manager=runtime_companion_manager,
    )
    # Always register mechanical Osiris engine (osiris_evaluator_factory is ignored — kept for
    # backward-compat call sites in runtime.py / tests that still pass the parameter)
    tick_coordinator.register_settlement_hook(
        AIOsirisHook(
            engine=MechanicalOsirisEngine(),
            encounter_phase=EncounterPhase(detector=BasicEncounterDetector()),
            perception_phase=PerceptionPhase(),
            event_phase=EventConditionPhase(evaluator=BasicEventConditionEvaluator()),
        )
    )
    if gm_narrator_factory is not None:
        narrator = gm_narrator_factory(world, state)
        tick_coordinator.register_settlement_hook(
            GmNarrationHook(narrator=narrator)
        )
    planner_system = planner_system_factory() if planner_system_factory is not None else None
    if planner_system is not None:
        tick_coordinator.register_settlement_hook(
            build_narrative_planner_hook(
                planner_system,
                state=state,
                instance_manager=instance_manager,
            )
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
        instance_manager=instance_manager,
        companion_manager=runtime_companion_manager,
    )
