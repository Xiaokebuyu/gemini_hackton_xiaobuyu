"""Application-level session lifecycle service for the new game-core kernel."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import uuid
from typing import TYPE_CHECKING, Any, Callable, Mapping

if TYPE_CHECKING:
    from app.game_core.adapters.firestore_persistence import FirestorePersistencePort
    from app.game_core.orchestration.hooks.ai_osiris import AIOsirisEvaluator
    from app.game_core.orchestration.hooks.gm_narration import GmNarrator
    from app.game_core.adapters.planner_system import PlannerSystemAssembly
    from app.game_core.state import StateContainer
from app.game_core.adapters.local_persistence import LocalFilePersistencePort
from app.game_core.adapters.session_store import SaveResult, SaveStore
from app.game_core.bootstrap import (
    DefaultRuntime,
    build_narrative_planner_hook,
    build_default_world,
    build_runtime_for_world,
)
from app.game_core.content import WorldInstance
from app.game_core.orchestration.hooks.narrative_planner import NarrativePlannerHook
from app.game_core.orchestration.models import SSEEvent
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning.opening_bootstrap import (
    build_opening_bootstrap_planner_system,
)
from app.game_core.rules import Command
from app.game_core.state.slices import SceneSlice
from app.world_data_loader import load_goblin_slayer_world_data
from app.world_seed import _shell_world_seed

logger = logging.getLogger(__name__)

_GOBLIN_SLAYER_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "goblin_slayer" / "structured_new"
_V2_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "goblin_slayer" / "v2"


@dataclass(slots=True)
class ManagedSession:
    """One managed session handle bound to its runtime."""

    world_id: str
    session_id: str
    runtime: DefaultRuntime
    phase: str = "character_creation"


@dataclass(slots=True)
class CharacterCreationSpec:
    """Validated high-level input for first-pass character creation."""

    name: str
    race_id: str
    class_id: str
    background_id: str
    ability_scores: dict[str, int]
    backstory: str = ""


@dataclass(slots=True)
class CharacterCreationOptions:
    """Structured options for the character-creation UI."""

    races: list[dict[str, Any]]
    classes: list[dict[str, Any]]
    backgrounds: list[dict[str, Any]]


@dataclass(slots=True)
class CharacterCreationResult:
    """Result of completing character creation for one managed session."""

    session: ManagedSession
    phase: str
    player: dict[str, Any]


@dataclass(slots=True)
class SessionSummary:
    """Compact session summary used by save management surfaces."""

    player_name: str
    player_class: str
    level: int
    location: str
    day: int | None
    play_time_hours: float | None = None


@dataclass(slots=True)
class SavedSessionInfo:
    """One saved-session card in the local catalog."""

    session_id: str
    created_at: float
    last_played: float
    phase: str
    summary: SessionSummary


class GameRuntime:
    """Own world caching and top-level session lifecycle orchestration."""

    def __init__(
        self,
        save_store: SaveStore | None = None,
        agent_orchestration: Any = None,
        instance_manager: Any = None,
        gm_narrator_factory: Callable[[WorldInstance, StateContainer], GmNarrator] | None = None,
        osiris_evaluator_factory: Callable[[], AIOsirisEvaluator] | None = None,
        planner_system_factory: Callable[[], PlannerSystemAssembly] | None = None,
    ) -> None:
        self._save_store = save_store or SaveStore(self._default_persistence_port())
        self._agent_orchestration = agent_orchestration
        self._instance_manager = instance_manager
        self._gm_narrator_factory = gm_narrator_factory
        self._osiris_evaluator_factory = osiris_evaluator_factory
        self._planner_system_factory = planner_system_factory
        self._world_cache: dict[str, WorldInstance] = {}
        self._execution_locks_guard = asyncio.Lock()
        self._execution_locks: dict[str, asyncio.Lock] = {}

    async def session_lock(self, session_id: str) -> asyncio.Lock:
        """Return the per-session execution lock (create if missing)."""
        async with self._execution_locks_guard:
            lock = self._execution_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._execution_locks[session_id] = lock
            return lock

    @staticmethod
    def _default_persistence_port() -> LocalFilePersistencePort | FirestorePersistencePort:
        backend = os.environ.get("PERSISTENCE_BACKEND", "local")
        if backend == "firestore":
            from app.game_core.adapters.firestore_persistence import FirestorePersistencePort
            return FirestorePersistencePort()
        return LocalFilePersistencePort()

    def get_world(
        self,
        world_id: str,
        world_data: dict[str, Any] | None = None,
        *,
        force_reload: bool = False,
    ) -> WorldInstance:
        """Get one cached world, or build and cache it on first use."""
        if not force_reload and world_id in self._world_cache:
            return self._world_cache[world_id]
        resolved_world_data = world_data if world_data is not None else self._load_world_data(world_id)
        world = build_default_world(world_id, world_data=resolved_world_data)
        self._world_cache[world_id] = world
        return world

    def ensure_world(
        self,
        world_id: str,
        *,
        force_reload: bool = False,
    ) -> WorldInstance:
        """Load one world from the canonical source if it is not already cached."""
        return self.get_world(world_id, force_reload=force_reload)

    def has_world(self, world_id: str) -> bool:
        """Whether a world is already cached."""
        return world_id in self._world_cache

    def invalidate_world(self, world_id: str) -> None:
        """Drop one cached world."""
        self._world_cache.pop(world_id, None)

    def get_character_creation_options(
        self,
        world_id: str,
        *,
        world_data: dict[str, Any] | None = None,
    ) -> CharacterCreationOptions:
        """Return first-pass race/class/background options for one world."""
        world = self.get_world(world_id, world_data=world_data)
        return CharacterCreationOptions(
            races=[self._normalize_race_option(item) for item in world.classes.list_races()],
            classes=[
                self._normalize_class_option(item)
                for item in world.classes.list_classes()
            ],
            backgrounds=[
                self._normalize_background_option(item)
                for item in world.classes.list_backgrounds()
            ],
        )

    @property
    def agent_orchestration(self) -> Any:
        """Return the injected AgentOrchestrationService (None if LLM unavailable)."""
        return self._agent_orchestration

    async def create_session(
        self,
        world_id: str,
        *,
        world_data: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> ManagedSession:
        """Create a new session and persist its initial snapshot."""
        world = self.get_world(world_id, world_data=world_data)
        runtime = build_runtime_for_world(
            world,
            gm_narrator_factory=self._gm_narrator_factory,
            osiris_evaluator_factory=self._osiris_evaluator_factory,
            planner_system_factory=self._planner_system_factory,
            instance_manager=self._instance_manager,
        )
        self._bind_runtime_services(runtime)
        resolved_session_id = session_id or self._new_session_id()
        await self._save_store.save_runtime(resolved_session_id, runtime)
        return ManagedSession(
            world_id=world_id,
            session_id=resolved_session_id,
            runtime=runtime,
            phase="character_creation",
        )

    async def complete_character_creation(
        self,
        session: ManagedSession,
        spec: CharacterCreationSpec,
    ) -> CharacterCreationResult:
        """Apply the first-pass character-creation flow to one session."""
        if session.world_id != session.runtime.world.world_id:
            raise ValueError("managed session world_id does not match runtime world")
        player = session.runtime.state.player
        if player.character_id or player.character_name:
            raise ValueError("character already created")

        world = session.runtime.world
        class_template = world.classes.get_class(spec.class_id)
        if class_template is None:
            raise ValueError(f"unknown class: {spec.class_id}")
        starting_item_ids = self._collect_starting_item_ids(class_template)
        default_equipped = self._collect_default_equipped(class_template)
        for slot in default_equipped:
            if slot not in session.runtime.state.player.equipment:
                raise ValueError(f"unknown equipment slot: {slot}")
        for item_id in default_equipped.values():
            if item_id not in starting_item_ids:
                starting_item_ids.append(item_id)
        for item_id in starting_item_ids:
            if world.items.get(item_id) is None:
                raise ValueError(f"unknown item: {item_id}")

        starting_area = world.maps.starting_area()
        if starting_area is None:
            raise ValueError("world has no starting area")
        starting_area_id = self._normalized_string(starting_area.id)
        if starting_area_id is None:
            raise ValueError("starting area is missing an id")
        starting_location_id = self._resolve_starting_location_id(starting_area)

        character_id = self._new_character_id()
        creation_result = session.runtime.rules_engine.execute(
            Command(
                type="create_character",
                source="engine",
                params={
                    "character_id": character_id,
                    "name": spec.name,
                    "race_id": spec.race_id,
                    "class_id": spec.class_id,
                    "background_id": spec.background_id,
                    "ability_scores": dict(spec.ability_scores),
                    "backstory": spec.backstory,
                },
            ),
            session.runtime.state,
            session.runtime.world,
        )
        self._apply_execute_result(session, creation_result)

        for item_id in starting_item_ids:
            result = session.runtime.rules_engine.execute(
                Command(
                    type="pick_up",
                    source="engine",
                    params={
                        "item_id": item_id,
                        "count": 1,
                    },
                ),
                session.runtime.state,
                session.runtime.world,
            )
            self._apply_execute_result(session, result)

        for slot, item_id in default_equipped.items():
            result = session.runtime.rules_engine.execute(
                Command(
                    type="equip",
                    source="engine",
                    params={
                        "item_id": item_id,
                        "slot": slot,
                    },
                ),
                session.runtime.state,
                session.runtime.world,
            )
            self._apply_execute_result(session, result)

        location_result = session.runtime.rules_engine.execute(
            Command(
                type="modify_location",
                source="engine",
                params={
                    "area_id": starting_area_id,
                    "location_id": starting_location_id,
                },
            ),
            session.runtime.state,
            session.runtime.world,
        )
        self._apply_execute_result(session, location_result)

        session.runtime.state.areas.set_exploration(starting_area_id, "discovered")
        session.phase = "opening_ready"
        await self.bootstrap_opening_planner(session)
        await self.save_session(session)
        return CharacterCreationResult(
            session=session,
            phase=session.phase,
            player=session.runtime.state.player.snapshot(),
        )

    async def resume_session(
        self,
        world_id: str,
        session_id: str,
        *,
        world_data: dict[str, Any] | None = None,
    ) -> ManagedSession | None:
        """Restore an existing session, or return None if no save exists."""
        world = self.get_world(world_id, world_data=world_data)
        runtime, meta = await self._save_store.load_runtime_record_for_world(
            world, session_id,
            gm_narrator_factory=self._gm_narrator_factory,
            osiris_evaluator_factory=self._osiris_evaluator_factory,
            planner_system_factory=self._planner_system_factory,
            instance_manager=self._instance_manager,
        )
        if runtime is None:
            return None
        self._bind_runtime_services(runtime)
        # 恢复后强制同步队友位置到玩家当前区域
        from app.game_core.orchestration.companion_manager import CompanionManager
        CompanionManager(runtime.state).sync_to_player()
        self._restore_knowledge_graph(runtime)
        self._restore_context_windows(runtime)
        phase = str(meta.get("phase", "")).strip() or "character_creation"
        return ManagedSession(
            world_id=world_id,
            session_id=session_id,
            runtime=runtime,
            phase=phase,
        )

    async def save_session(self, session: ManagedSession) -> SaveResult:
        """Persist one managed session through the configured save store."""
        if session.world_id != session.runtime.world.world_id:
            raise ValueError("managed session world_id does not match runtime world")
        self._sync_knowledge_graph_state(session.runtime)
        self._save_context_windows(session)
        return await self._save_store.save_runtime(
            session.session_id,
            session.runtime,
            phase=session.phase,
        )

    def _sync_knowledge_graph_state(self, runtime: DefaultRuntime) -> None:
        """Export WKG actor-private state into NarrativePlanSlice for persistence."""
        graph = getattr(runtime.tick_coordinator, "knowledge_graph", None)
        if graph is None or not runtime.state.has_slice("narrative_plan"):
            return
        export_fn = getattr(graph, "export_actor_state", None)
        if not callable(export_fn):
            return
        actor_state = export_fn()
        if actor_state:
            runtime.state.narrative_plan.set_actor_knowledge(actor_state)

    def _save_context_windows(self, session: ManagedSession) -> None:
        """Serialize all active ContextWindows + planner history into NarrativePlanSlice."""
        if not session.runtime.state.has_slice("narrative_plan"):
            return
        im = self._instance_manager
        windows_data: dict[str, Any] = {}
        if im is not None:
            iter_fn = getattr(im, "iter_instances", None)
            if callable(iter_fn):
                for actor_id, instance in iter_fn():
                    cw = instance.context_window
                    if cw is not None and cw.messages:
                        windows_data[actor_id] = cw.export_messages()
        # Save planner/blackboard/subsystem histories alongside window data
        hook = self._find_narrative_planner_hook(session)
        history_participants = (
            hook.history_participants()
            if hook is not None and hasattr(hook, "history_participants")
            else {}
        )
        for history_key, participant in history_participants.items():
            export_fn = getattr(participant, "export_history", None)
            if not callable(export_fn):
                continue
            history = export_fn()
            if history:
                windows_data[str(history_key)] = history
        if windows_data:
            session.runtime.state.narrative_plan.set_context_windows_data(windows_data)

    def _restore_context_windows(self, runtime: DefaultRuntime) -> None:
        """Restore ContextWindows and planner history from NarrativePlanSlice."""
        if not runtime.state.has_slice("narrative_plan"):
            return
        windows_data = runtime.state.narrative_plan.context_windows_data
        if not isinstance(windows_data, dict) or not windows_data:
            return
        im = self._instance_manager
        planner_data = dict(windows_data)
        legacy_history = planner_data.pop("__planner__", None)
        if legacy_history is not None and "__planner_blackboard__" not in planner_data:
            planner_data["__planner_blackboard__"] = legacy_history
        if im is not None:
            get_fn = getattr(im, "get_or_create", None)
            if callable(get_fn):
                for actor_id, messages in planner_data.items():
                    if actor_id.startswith("__"):
                        continue
                    if isinstance(messages, list):
                        instance = get_fn(actor_id)
                        if instance is not None:
                            instance.context_window.import_messages(messages)
        history_payloads = {
            key: value
            for key, value in planner_data.items()
            if isinstance(key, str) and key.startswith("__")
        }
        if history_payloads:
            for hook in runtime.tick_coordinator.settlement_hooks:
                if not isinstance(hook, NarrativePlannerHook):
                    continue
                participants = (
                    hook.history_participants()
                    if hasattr(hook, "history_participants")
                    else {}
                )
                for history_key, payload in history_payloads.items():
                    participant = participants.get(history_key)
                    if participant is None:
                        continue
                    import_fn = getattr(participant, "import_history", None)
                    if callable(import_fn) and isinstance(payload, list):
                        import_fn(payload)
                break

    async def list_sessions(self, world_id: str) -> list[SavedSessionInfo]:
        """List saved sessions for one world."""
        records = await self._save_store.list_session_meta(world_id=world_id)
        results: list[SavedSessionInfo] = []
        for record in records:
            summary_payload = record.get("summary", {})
            if not isinstance(summary_payload, Mapping):
                summary_payload = {}
            results.append(
                SavedSessionInfo(
                    session_id=str(record.get("session_id", "")),
                    created_at=self._coerce_float(record.get("created_at"), 0.0),
                    last_played=self._coerce_float(record.get("last_played"), 0.0),
                    phase=str(record.get("phase", "character_creation")),
                    summary=SessionSummary(
                        player_name=self._string_or_default(
                            summary_payload.get("player_name"),
                            "",
                        ),
                        player_class=self._string_or_default(
                            summary_payload.get("player_class"),
                            "",
                        ),
                        level=self._coerce_int(summary_payload.get("level"), 1),
                        location=self._string_or_default(
                            summary_payload.get("location"),
                            "",
                        ),
                        day=self._coerce_optional_int(summary_payload.get("day"), None),
                        play_time_hours=self._coerce_optional_float(
                            summary_payload.get("play_time_hours"),
                            None,
                        ),
                    ),
                )
            )
        return results

    async def delete_session(self, world_id: str, session_id: str) -> bool:
        """Delete one saved session if it belongs to the requested world."""
        sessions = await self.list_sessions(world_id)
        if not any(item.session_id == session_id for item in sessions):
            return False
        return await self._save_store.delete_session(session_id)

    def _new_session_id(self) -> str:
        return f"sess_{uuid.uuid4().hex[:12]}"

    def _new_character_id(self) -> str:
        return f"pc_{uuid.uuid4().hex[:12]}"

    def _load_world_data(self, world_id: str) -> dict[str, Any]:
        if world_id == "goblin_slayer":
            if (_V2_DATA_DIR / "characters.json").exists():
                from app.game_data_loader_v2 import load_v2_world_data

                return load_v2_world_data()
            if _GOBLIN_SLAYER_DATA_DIR.exists():
                return load_goblin_slayer_world_data()
        return _shell_world_seed(world_id)

    def _apply_execute_result(
        self,
        session: ManagedSession,
        result: Any,
    ) -> None:
        if not getattr(result, "executed", False):
            errors = getattr(result, "errors", None)
            if isinstance(errors, list) and errors:
                raise ValueError(str(errors[0]))
            raise ValueError("command execution failed")
        delta = getattr(result, "delta", None)
        if delta is not None:
            session.runtime.state.apply(delta)

    async def bootstrap_opening_planner(
        self,
        session: ManagedSession,
        *,
        persist: bool = False,
    ) -> list[SSEEvent]:
        """Seed opening quests without advancing the normal tick lifecycle."""
        hook = self._resolve_bootstrap_planner_hook(session)
        if hook is None:
            return []
        try:
            result = await hook.bootstrap(self._build_bootstrap_context(session))
        except Exception as exc:
            logger.exception("hook failed: narrative_planner_bootstrap")
            return [
                SSEEvent(
                    event_type="hook_error",
                    payload={
                        "hook": "narrative_planner_bootstrap",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
            ]
        if persist and (
            int(result.metadata.get("applied_count", 0) or 0) > 0
            or int(result.metadata.get("story_fact_count", 0) or 0) > 0
        ):
            await self.save_session(session)
        return list(result.sse_events)

    def _build_bootstrap_context(self, session: ManagedSession) -> SettlementContext:
        return SettlementContext(
            change_log=[],
            state=session.runtime.state,
            world=session.runtime.world,
            scene_bus=SceneBus(SceneSlice()),
            _rules_engine=session.runtime.rules_engine,
            _apply_delta=session.runtime.state.apply,
            knowledge_graph=getattr(session.runtime.tick_coordinator, "knowledge_graph", None),
        )

    def _resolve_bootstrap_planner_hook(
        self,
        session: ManagedSession,
    ) -> NarrativePlannerHook | None:
        hook = self._find_narrative_planner_hook(session)
        if hook is not None and getattr(hook, "_dispatcher", None) is not None:
            return hook
        return build_narrative_planner_hook(
            build_opening_bootstrap_planner_system(),
            state=session.runtime.state,
            instance_manager=self._instance_manager,
        )

    def _bind_runtime_services(self, runtime: DefaultRuntime) -> None:
        if self._agent_orchestration is not None:
            runner = getattr(self._agent_orchestration, "run_post_action_round", None)
            if callable(runner):
                runtime.pipeline.set_stage_b_runner(runner)
        runtime.tick_coordinator.knowledge_graph = self._resolve_knowledge_graph()

    def _resolve_knowledge_graph(self) -> Any | None:
        return getattr(
            getattr(self._agent_orchestration, "_memory_retriever", None),
            "_graph",
            None,
        )

    @staticmethod
    def _restore_knowledge_graph(runtime: DefaultRuntime) -> None:
        if not runtime.state.has_slice("narrative_plan"):
            return
        graph = getattr(runtime.tick_coordinator, "knowledge_graph", None)
        if graph is None:
            return
        # 1. Restore story facts (existing)
        facts = runtime.state.narrative_plan.story_facts
        if facts:
            graph.inject_story_facts(facts)
        # 2. Restore actor-private knowledge (new)
        actor_knowledge = runtime.state.narrative_plan.actor_knowledge
        if actor_knowledge:
            import_fn = getattr(graph, "import_actor_state", None)
            if callable(import_fn):
                import_fn(actor_knowledge)

    @staticmethod
    def _find_narrative_planner_hook(
        session: ManagedSession,
    ) -> NarrativePlannerHook | None:
        for hook in session.runtime.tick_coordinator.settlement_hooks:
            if isinstance(hook, NarrativePlannerHook):
                return hook
        return None

    def _resolve_starting_location_id(
        self,
        area_template: Any,
    ) -> str | None:
        default_location = self._normalized_string(
            getattr(area_template, "default_location", None)
        )
        if default_location is not None:
            return default_location
        sub_locations = area_template.sub_locations
        for key in sub_locations:
            normalized = self._normalized_string(key)
            if normalized is not None:
                return normalized
        return None

    def _collect_starting_item_ids(self, class_template: Any) -> list[str]:
        raw_equipment = class_template.starting_equipment
        if not isinstance(raw_equipment, list):
            return []
        item_ids: list[str] = []
        for item_id in raw_equipment:
            normalized = self._normalized_string(item_id)
            if normalized is not None:
                item_ids.append(normalized)
        return item_ids

    def _collect_default_equipped(
        self,
        class_template: Any,
    ) -> dict[str, str]:
        raw_default_equipped = class_template.default_equipped
        if not isinstance(raw_default_equipped, Mapping):
            return {}
        normalized_mapping: dict[str, str] = {}
        for slot, item_id in raw_default_equipped.items():
            normalized_slot = self._normalized_string(slot)
            normalized_item_id = self._normalized_string(item_id)
            if normalized_slot is None or normalized_item_id is None:
                continue
            normalized_mapping[normalized_slot] = normalized_item_id
        return normalized_mapping

    def _normalize_race_option(self, payload: Any) -> dict[str, Any]:
        raw_traits = getattr(payload, "racial_traits", [])
        stat_bonuses = getattr(payload, "stat_bonuses", {})
        return {
            "id": self._string_or_default(getattr(payload, "id", None), ""),
            "name": self._string_or_default(getattr(payload, "name", None), ""),
            "description": self._string_or_default(getattr(payload, "description", None), ""),
            "stat_bonuses": dict(stat_bonuses) if isinstance(stat_bonuses, Mapping) else {},
            "racial_traits": [
                str(item)
                for item in raw_traits
                if self._normalized_string(item) is not None
            ] if isinstance(raw_traits, list) else [],
        }

    def _normalize_class_option(self, payload: Any) -> dict[str, Any]:
        raw_starting_equipment = getattr(payload, "starting_equipment", [])
        raw_default_equipped = getattr(payload, "default_equipped", {})
        hit_die = getattr(payload, "hit_die", None)
        if hit_die is None:
            hit_die = getattr(payload, "base_hp", None)
        return {
            "id": self._string_or_default(getattr(payload, "id", None), ""),
            "name": self._string_or_default(getattr(payload, "name", None), ""),
            "description": self._string_or_default(getattr(payload, "description", None), ""),
            "hit_die": hit_die,
            "starting_equipment": [
                str(item)
                for item in raw_starting_equipment
                if self._normalized_string(item) is not None
            ] if isinstance(raw_starting_equipment, list) else [],
            "default_equipped": (
                {
                    str(key): str(value)
                    for key, value in raw_default_equipped.items()
                    if self._normalized_string(key) is not None
                    and self._normalized_string(value) is not None
                }
                if isinstance(raw_default_equipped, Mapping)
                else {}
            ),
        }

    def _normalize_background_option(self, payload: Any) -> dict[str, Any]:
        raw_skills = getattr(payload, "skill_proficiency", [])
        return {
            "id": self._string_or_default(getattr(payload, "id", None), ""),
            "name": self._string_or_default(getattr(payload, "name", None), ""),
            "description": self._string_or_default(getattr(payload, "description", None), ""),
            "skill_proficiency": [
                str(item)
                for item in raw_skills
                if self._normalized_string(item) is not None
            ] if isinstance(raw_skills, list) else [],
            "gold_bonus": self._coerce_optional_int(getattr(payload, "gold_bonus", None), None),
            "starting_gold": self._coerce_optional_int(getattr(payload, "starting_gold", None), None),
            "feature": self._string_or_default(getattr(payload, "feature", None), ""),
        }

    @staticmethod
    def _normalized_string(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _string_or_default(value: Any, default: str) -> str:
        if not isinstance(value, str):
            return default
        normalized = value.strip()
        return normalized or default

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:
        if value is None or isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_optional_int(value: Any, default: int | None) -> int | None:
        if value is None or isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_float(value: Any, default: float) -> float:
        if value is None or isinstance(value, bool):
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_optional_float(value: Any, default: float | None) -> float | None:
        if value is None or isinstance(value, bool):
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
