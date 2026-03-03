"""Outer session save/load orchestration for game-core runtimes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time as time_module
from typing import TYPE_CHECKING, Any, Mapping, cast

from app.game_core.adapters.persistence import PersistencePort, SessionCatalogPort
from app.game_core.bootstrap import (
    DefaultRuntime,
    build_default_world,
    build_restored_runtime_for_world,
)

if TYPE_CHECKING:
    from app.game_core.content import WorldInstance


@dataclass(slots=True)
class SaveResult:
    """Structured result for one save attempt."""

    session_id: str
    persisted_slices: list[str]
    skipped_slices: list[str]
    wrote_full_snapshot: bool
    wrote_to_port: bool
    meta: dict[str, Any]


class SaveStore:
    """Session-level save/load adapter built on top of PersistencePort."""

    def __init__(self, persistence: PersistencePort) -> None:
        self._persistence = persistence
        self._locks_guard = asyncio.Lock()
        self._session_locks: dict[str, asyncio.Lock] = {}

    async def save_runtime(
        self,
        session_id: str,
        runtime: DefaultRuntime,
    ) -> SaveResult:
        """Persist one runtime into the configured port."""
        lock = await self._lock_for(session_id)
        async with lock:
            raw_dirty = runtime.tick_coordinator.export_dirty()
            dirty_payload = raw_dirty if isinstance(raw_dirty, Mapping) else {}
            persistable_dirty, skipped_slices = self._split_persistable_slices(dirty_payload)

            existing_raw = await self._persistence.load(session_id)
            existing_state, existing_meta = (
                self._normalize_loaded_payload(existing_raw)
                if isinstance(existing_raw, Mapping)
                else ({}, {})
            )

            wrote_full_snapshot = not existing_state and not persistable_dirty
            write_state: dict[str, dict[str, Any]]
            if wrote_full_snapshot:
                write_state = self._full_persistable_snapshot(runtime)
            else:
                write_state = dict(existing_state)
                write_state.update(persistable_dirty)

            meta = self._build_meta(session_id, runtime, existing_meta=existing_meta)
            wrote_to_port = wrote_full_snapshot or bool(persistable_dirty)
            if wrote_to_port:
                await self._persistence.save(
                    session_id,
                    {
                        "state": write_state,
                        "meta": meta,
                    },
                )

            if wrote_full_snapshot:
                persisted_slices = list(write_state.keys())
            else:
                persisted_slices = list(persistable_dirty.keys())

            mark_clean_names = persisted_slices + skipped_slices
            if mark_clean_names:
                runtime.state.mark_clean(mark_clean_names)

            return SaveResult(
                session_id=session_id,
                persisted_slices=persisted_slices,
                skipped_slices=skipped_slices,
                wrote_full_snapshot=wrote_full_snapshot,
                wrote_to_port=wrote_to_port,
                meta=meta,
            )

    async def load_runtime(
        self,
        world_id: str,
        session_id: str,
        world_data: dict[str, Any] | None = None,
    ) -> DefaultRuntime | None:
        """Load and restore one runtime from the configured port."""
        world = build_default_world(world_id, world_data=world_data)
        return await self.load_runtime_for_world(world, session_id)

    async def load_runtime_for_world(
        self,
        world: WorldInstance,
        session_id: str,
        *,
        gm_narrator_factory: Any = None,
        osiris_evaluator_factory: Any = None,
        narrative_planner_factory: Any = None,
    ) -> DefaultRuntime | None:
        """Load and restore one runtime using an already loaded world."""
        raw = await self._persistence.load(session_id)
        if not isinstance(raw, Mapping) or not raw:
            return None
        state_payload, _ = self._normalize_loaded_payload(raw)
        return build_restored_runtime_for_world(
            world, state_payload,
            gm_narrator_factory=gm_narrator_factory,
            osiris_evaluator_factory=osiris_evaluator_factory,
            narrative_planner_factory=narrative_planner_factory,
        )

    async def list_session_meta(
        self,
        world_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List known session metadata, optionally filtered by world."""
        port = self._catalog_port()
        session_ids = await port.list_keys()
        records: list[dict[str, Any]] = []
        for session_id in session_ids:
            raw = await self._persistence.load(session_id)
            if not isinstance(raw, Mapping) or not raw:
                continue
            state_payload, meta_payload = self._normalize_loaded_payload(raw)
            record = self._build_listing_meta(session_id, state_payload, meta_payload)
            record_world_id = str(record.get("world_id", "")).strip()
            if world_id is not None:
                if not record_world_id or record_world_id != world_id:
                    continue
            records.append(record)
        records.sort(key=lambda item: float(item.get("last_played", 0.0)), reverse=True)
        return records

    async def delete_session(self, session_id: str) -> bool:
        """Delete one stored session if the configured port supports it."""
        port = self._catalog_port()
        return await port.delete(session_id)

    async def _lock_for(self, session_id: str) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._session_locks[session_id] = lock
            return lock

    def _normalize_loaded_payload(
        self,
        raw: Mapping[str, Any],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        raw_state = raw.get("state")
        if isinstance(raw_state, Mapping):
            return (
                self._coerce_state_mapping(raw_state),
                dict(raw.get("meta")) if isinstance(raw.get("meta"), Mapping) else {},
            )
        return self._coerce_state_mapping(raw), {}

    def _split_persistable_slices(
        self,
        payload: Mapping[str, Any],
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        persistable: dict[str, dict[str, Any]] = {}
        skipped_slices: list[str] = []
        for name, slice_payload in payload.items():
            if not isinstance(name, str) or not isinstance(slice_payload, Mapping):
                continue
            if name == "scene":
                skipped_slices.append(name)
                continue
            persistable[name] = dict(slice_payload)
        return persistable, skipped_slices

    def _build_meta(
        self,
        session_id: str,
        runtime: DefaultRuntime,
        existing_meta: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = time_module.time()
        existing_meta = existing_meta or {}
        created_at = self._safe_float(existing_meta.get("created_at"))
        if created_at is None:
            created_at = self._safe_float(existing_meta.get("saved_at"))
        if created_at is None:
            created_at = now

        game_day = runtime.state.time.day if runtime.state.has_slice("time") else None
        game_slot = runtime.state.time.slot if runtime.state.has_slice("time") else None
        character_name = (
            runtime.state.player.character_name
            if runtime.state.has_slice("player")
            else ""
        )
        phase = self._derive_phase(runtime)
        summary = {
            "player_name": character_name,
            "player_class": self._resolve_player_class_label(runtime),
            "level": runtime.state.player.level if runtime.state.has_slice("player") else 1,
            "location": self._resolve_location_label(runtime),
            "day": game_day,
            "play_time_hours": None,
        }
        return {
            "world_id": runtime.world.world_id,
            "session_id": session_id,
            "created_at": created_at,
            "last_played": now,
            "saved_at": now,
            "phase": phase,
            "game_day": game_day,
            "game_slot": game_slot,
            "character_name": character_name,
            "summary": summary,
        }

    def _build_listing_meta(
        self,
        session_id: str,
        state_payload: Mapping[str, Any],
        meta_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        world_id = str(meta_payload.get("world_id", "")).strip()
        created_at = self._safe_float(meta_payload.get("created_at"))
        if created_at is None:
            created_at = self._safe_float(meta_payload.get("saved_at"))
        if created_at is None:
            created_at = 0.0

        last_played = self._safe_float(meta_payload.get("last_played"))
        if last_played is None:
            last_played = self._safe_float(meta_payload.get("saved_at"))
        if last_played is None:
            last_played = created_at

        meta_summary = meta_payload.get("summary", {})
        if not isinstance(meta_summary, Mapping):
            meta_summary = {}
        state_summary = self._summary_from_state_payload(state_payload)
        summary = {
            "player_name": self._string_or_default(
                meta_summary.get("player_name"),
                state_summary["player_name"],
            ),
            "player_class": self._string_or_default(
                meta_summary.get("player_class"),
                state_summary["player_class"],
            ),
            "level": self._safe_int(meta_summary.get("level"), state_summary["level"]),
            "location": self._string_or_default(
                meta_summary.get("location"),
                state_summary["location"],
            ),
            "day": self._safe_optional_int(meta_summary.get("day"), state_summary["day"]),
            "play_time_hours": None,
        }

        phase = self._string_or_default(
            meta_payload.get("phase"),
            self._derive_phase_from_state_payload(state_payload),
        )
        return {
            "world_id": world_id,
            "session_id": session_id,
            "created_at": created_at,
            "last_played": last_played,
            "phase": phase,
            "summary": summary,
        }

    def _full_persistable_snapshot(
        self,
        runtime: DefaultRuntime,
    ) -> dict[str, dict[str, Any]]:
        return self._coerce_state_mapping(runtime.state.snapshot())

    def _coerce_state_mapping(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        state_payload: dict[str, dict[str, Any]] = {}
        for name, slice_payload in payload.items():
            if not isinstance(name, str) or name == "scene":
                continue
            if not isinstance(slice_payload, Mapping):
                continue
            state_payload[name] = dict(slice_payload)
        return state_payload

    def _catalog_port(self) -> SessionCatalogPort:
        if isinstance(self._persistence, SessionCatalogPort):
            return cast(SessionCatalogPort, self._persistence)
        raise NotImplementedError(
            "persistence port does not support session catalog operations"
        )

    def _derive_phase(self, runtime: DefaultRuntime) -> str:
        if not runtime.state.has_slice("player"):
            return "character_creation"
        player = runtime.state.player
        if player.character_id and player.character_name and player.character_class:
            return "active"
        return "character_creation"

    def _derive_phase_from_state_payload(self, state_payload: Mapping[str, Any]) -> str:
        player = state_payload.get("player", {})
        if not isinstance(player, Mapping):
            return "character_creation"
        if (
            self._string_or_default(player.get("character_id"), "")
            and self._string_or_default(player.get("character_name"), "")
            and self._string_or_default(player.get("character_class"), "")
        ):
            return "active"
        return "character_creation"

    def _resolve_player_class_label(self, runtime: DefaultRuntime) -> str:
        if not runtime.state.has_slice("player"):
            return ""
        class_id = runtime.state.player.character_class
        if not class_id:
            return ""
        template = runtime.world.classes.get_class(class_id)
        if template is not None:
            name = template.name
            if isinstance(name, str) and name.strip():
                return name.strip()
        return class_id

    def _resolve_location_label(self, runtime: DefaultRuntime) -> str:
        if not runtime.state.has_slice("player"):
            return ""
        player = runtime.state.player
        current_area = player.current_area
        current_location = player.current_location
        if not current_area:
            return ""
        area_template = runtime.world.maps.get(current_area)
        if area_template is not None:
            if current_location:
                location_template = area_template.sub_locations.get(current_location)
                if location_template is not None:
                    name = getattr(location_template, "name", None)
                    if name is None and isinstance(location_template, Mapping):
                        name = location_template.get("name")
                    if isinstance(name, str) and name.strip():
                        return name.strip()
                return current_location
            if area_template.name.strip():
                return area_template.name.strip()
        return current_location or current_area

    def _summary_from_state_payload(
        self,
        state_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        player = state_payload.get("player", {})
        player_state = dict(player) if isinstance(player, Mapping) else {}
        time_state = state_payload.get("time", {})
        time_payload = dict(time_state) if isinstance(time_state, Mapping) else {}
        location = self._string_or_default(
            player_state.get("current_location"),
            self._string_or_default(player_state.get("current_area"), ""),
        )
        return {
            "player_name": self._string_or_default(player_state.get("character_name"), ""),
            "player_class": self._string_or_default(player_state.get("character_class"), ""),
            "level": self._safe_int(player_state.get("level"), 1),
            "location": location,
            "day": self._safe_optional_int(time_payload.get("day"), None),
        }

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        if value is None or isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_optional_int(value: Any, default: int | None) -> int | None:
        if value is None or isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _string_or_default(value: Any, default: str) -> str:
        if not isinstance(value, str):
            return default
        normalized = value.strip()
        return normalized or default
