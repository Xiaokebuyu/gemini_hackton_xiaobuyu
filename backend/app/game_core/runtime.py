"""Application-level session lifecycle service for the new game-core kernel."""

from __future__ import annotations

from dataclasses import dataclass
import uuid
from typing import Any, Mapping

from app.game_core.adapters.local_persistence import LocalFilePersistencePort
from app.game_core.adapters.session_store import SaveResult, SaveStore
from app.game_core.bootstrap import (
    DefaultRuntime,
    build_default_world,
    build_runtime_for_world,
)
from app.game_core.content import WorldInstance
from app.game_core.rules import Command


@dataclass(slots=True)
class ManagedSession:
    """One managed session handle bound to its runtime."""

    world_id: str
    session_id: str
    runtime: DefaultRuntime


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

    def __init__(self, save_store: SaveStore | None = None) -> None:
        self._save_store = save_store or SaveStore(LocalFilePersistencePort())
        self._world_cache: dict[str, WorldInstance] = {}

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
        if world_data is None:
            raise ValueError("world_data is required for uncached world")
        world = build_default_world(world_id, world_data=world_data)
        self._world_cache[world_id] = world
        return world

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

    async def create_session(
        self,
        world_id: str,
        *,
        world_data: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> ManagedSession:
        """Create a new session and persist its initial snapshot."""
        world = self.get_world(world_id, world_data=world_data)
        runtime = build_runtime_for_world(world)
        resolved_session_id = session_id or self._new_session_id()
        await self._save_store.save_runtime(resolved_session_id, runtime)
        return ManagedSession(
            world_id=world_id,
            session_id=resolved_session_id,
            runtime=runtime,
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
        starting_area_id = self._normalized_string(starting_area.get("id"))
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
        await self.save_session(session)
        return CharacterCreationResult(
            session=session,
            phase="active",
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
        runtime = await self._save_store.load_runtime_for_world(world, session_id)
        if runtime is None:
            return None
        return ManagedSession(
            world_id=world_id,
            session_id=session_id,
            runtime=runtime,
        )

    async def save_session(self, session: ManagedSession) -> SaveResult:
        """Persist one managed session through the configured save store."""
        if session.world_id != session.runtime.world.world_id:
            raise ValueError("managed session world_id does not match runtime world")
        return await self._save_store.save_runtime(session.session_id, session.runtime)

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

    def _apply_execute_result(
        self,
        session: ManagedSession,
        result: Any,
    ) -> None:
        if not getattr(result, "success", False):
            errors = getattr(result, "errors", None)
            if isinstance(errors, list) and errors:
                raise ValueError(str(errors[0]))
            raise ValueError("command execution failed")
        delta = getattr(result, "delta", None)
        if delta is not None:
            session.runtime.state.apply(delta)

    def _resolve_starting_location_id(
        self,
        area_template: Mapping[str, Any],
    ) -> str | None:
        default_location = self._normalized_string(area_template.get("default_location"))
        if default_location is not None:
            return default_location
        raw_sub_locations = area_template.get("sub_locations", {})
        if isinstance(raw_sub_locations, Mapping):
            for key in raw_sub_locations:
                normalized = self._normalized_string(key)
                if normalized is not None:
                    return normalized
        return None

    def _collect_starting_item_ids(self, class_template: Mapping[str, Any]) -> list[str]:
        raw_equipment = class_template.get("starting_equipment", [])
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
        class_template: Mapping[str, Any],
    ) -> dict[str, str]:
        raw_default_equipped = class_template.get("default_equipped", {})
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

    def _normalize_race_option(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        raw_traits = payload.get("racial_traits", [])
        return {
            "id": self._string_or_default(payload.get("id"), ""),
            "name": self._string_or_default(payload.get("name"), ""),
            "description": self._string_or_default(payload.get("description"), ""),
            "stat_bonuses": (
                dict(payload.get("stat_bonuses"))
                if isinstance(payload.get("stat_bonuses"), Mapping)
                else {}
            ),
            "racial_traits": [
                str(item)
                for item in raw_traits
                if self._normalized_string(item) is not None
            ] if isinstance(raw_traits, list) else [],
        }

    def _normalize_class_option(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        raw_starting_equipment = payload.get("starting_equipment", [])
        raw_default_equipped = payload.get("default_equipped", {})
        return {
            "id": self._string_or_default(payload.get("id"), ""),
            "name": self._string_or_default(payload.get("name"), ""),
            "description": self._string_or_default(payload.get("description"), ""),
            "hit_die": payload.get("hit_die", payload.get("base_hp")),
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

    def _normalize_background_option(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        raw_skills = payload.get("skill_proficiency", [])
        return {
            "id": self._string_or_default(payload.get("id"), ""),
            "name": self._string_or_default(payload.get("name"), ""),
            "description": self._string_or_default(payload.get("description"), ""),
            "skill_proficiency": [
                str(item)
                for item in raw_skills
                if self._normalized_string(item) is not None
            ] if isinstance(raw_skills, list) else [],
            "gold_bonus": self._coerce_optional_int(payload.get("gold_bonus"), None),
            "starting_gold": self._coerce_optional_int(payload.get("starting_gold"), None),
            "feature": self._string_or_default(payload.get("feature"), ""),
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
