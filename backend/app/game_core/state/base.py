"""Base abstractions for session state."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Iterable, Mapping, cast

from app.game_core.state.delta import StateChange, StateDelta
from app.game_core.state.internal import SupportsStateChange

if TYPE_CHECKING:
    from app.game_core.content.world import WorldInstance
    from app.game_core.state.slices.area import AreaSlice
    from app.game_core.state.slices.events import EventSlice
    from app.game_core.state.slices.flags import FlagSlice
    from app.game_core.state.slices.narrative_plan import NarrativePlanSlice
    from app.game_core.state.slices.party import PartySlice
    from app.game_core.state.slices.player import PlayerSlice
    from app.game_core.state.slices.quests import QuestSlice
    from app.game_core.state.slices.relations import RelationSlice
    from app.game_core.state.slices.scene import SceneSlice
    from app.game_core.state.slices.time import TimeSlice


class StateSlice(ABC):
    """Contract for one cohesive state slice."""

    def __init__(self, name: str) -> None:
        if not name:
            raise ValueError("name must not be empty")
        self._name = name
        self._dirty = False

    @property
    def name(self) -> str:
        """Canonical slice name used by StateContainer."""
        return self._name

    @abstractmethod
    def restore(self, payload: Mapping[str, Any]) -> None:
        """Restore the slice from persisted payload."""

    @abstractmethod
    def serialize(self) -> dict[str, Any]:
        """Return the slice payload for persistence."""

    @abstractmethod
    def snapshot(self) -> dict[str, Any]:
        """Return the slice payload for inspection."""

    @property
    def dirty(self) -> bool:
        """Whether this slice has unpersisted changes."""
        return self._dirty

    def clear_dirty(self) -> None:
        """Reset dirty tracking after persistence."""
        self._dirty = False

    def validate(self) -> list[str]:
        """Return slice validation issues. Empty means valid."""
        return []


class StateContainer:
    """Container that owns all session state slices."""

    def __init__(self) -> None:
        self._slices: dict[str, StateSlice] = {}

    @classmethod
    def create_new(cls, world: WorldInstance) -> StateContainer:
        """Create a world-seeded state container for a new session."""
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

        container = cls()

        time_slice = TimeSlice()
        time_slice.restore(cls._initial_time_payload())
        container.register(time_slice)

        player_slice = PlayerSlice()
        player_slice.clear_dirty()
        container.register(player_slice)

        relation_slice = RelationSlice()
        relation_slice.restore(cls._initial_relation_payload(world))
        container.register(relation_slice)

        quest_slice = QuestSlice()
        quest_slice.restore(cls._initial_quest_payload(world))
        container.register(quest_slice)

        flag_slice = FlagSlice()
        flag_slice.restore(cls._initial_flag_payload())
        container.register(flag_slice)

        area_slice = AreaSlice()
        area_slice.restore(cls._initial_area_payload(world))
        container.register(area_slice)

        event_slice = EventSlice()
        event_slice.restore(cls._initial_event_payload(world))
        container.register(event_slice)

        party_slice = PartySlice()
        party_slice.restore(cls._initial_party_payload())
        container.register(party_slice)

        narrative_plan_slice = NarrativePlanSlice()
        narrative_plan_slice.clear_dirty()
        container.register(narrative_plan_slice)

        scene_slice = SceneSlice()
        scene_slice.restore(cls._initial_scene_payload())
        container.register(scene_slice)

        return container

    @classmethod
    def create_restored(
        cls,
        world: WorldInstance,
        session_data: Mapping[str, Mapping[str, Any]],
    ) -> StateContainer:
        """Create a restored state container from serialized session payload."""
        container = cls.create_new(world)
        restorable_payload: dict[str, Mapping[str, Any]] = {}
        for name, slice_payload in session_data.items():
            if name == "scene":
                continue
            if not isinstance(slice_payload, Mapping):
                continue
            restorable_payload[str(name)] = slice_payload
        container.restore(restorable_payload)
        # SceneSlice is a per-tick buffer and never survives across sessions.
        container.scene.restore(cls._initial_scene_payload())
        return container

    def register(self, slice_obj: StateSlice) -> None:
        """Register a state slice by its canonical name."""
        name = slice_obj.name
        if name in self._slices:
            raise ValueError(f"slice already registered: {name}")
        self._slices[name] = slice_obj

    def get_slice(self, name: str) -> StateSlice:
        """Return one registered slice."""
        try:
            return self._slices[name]
        except KeyError as exc:
            raise KeyError(f"unknown slice: {name}") from exc

    def has_slice(self, name: str) -> bool:
        """Whether a slice is registered."""
        return name in self._slices

    def all_slices(self) -> list[tuple[str, StateSlice]]:
        """Return all registered slices in insertion order."""
        return list(self._slices.items())

    # -- Typed convenience accessors (cast at access time) --

    @property
    def time(self) -> TimeSlice:
        return cast("TimeSlice", self.get_slice("time"))

    @property
    def player(self) -> PlayerSlice:
        return cast("PlayerSlice", self.get_slice("player"))

    @property
    def areas(self) -> AreaSlice:
        return cast("AreaSlice", self.get_slice("areas"))

    @property
    def relations(self) -> RelationSlice:
        return cast("RelationSlice", self.get_slice("relations"))

    @property
    def quests(self) -> QuestSlice:
        return cast("QuestSlice", self.get_slice("quests"))

    @property
    def flags(self) -> FlagSlice:
        return cast("FlagSlice", self.get_slice("flags"))

    @property
    def events(self) -> EventSlice:
        return cast("EventSlice", self.get_slice("events"))

    @property
    def party(self) -> PartySlice:
        return cast("PartySlice", self.get_slice("party"))

    @property
    def narrative_plan(self) -> NarrativePlanSlice:
        return cast("NarrativePlanSlice", self.get_slice("narrative_plan"))

    @property
    def scene(self) -> SceneSlice:
        return cast("SceneSlice", self.get_slice("scene"))

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Return a complete state snapshot."""
        return {
            name: slice_obj.snapshot()
            for name, slice_obj in self._slices.items()
        }

    def restore(self, payload: Mapping[str, Mapping[str, Any]]) -> None:
        """Restore all known slices from a persisted payload."""
        for name, slice_payload in payload.items():
            if name not in self._slices:
                continue
            self._slices[name].restore(slice_payload)

    def export_dirty(self) -> dict[str, dict[str, Any]]:
        """Return serialized data for dirty slices (does NOT write to storage)."""
        return {
            name: slice_obj.serialize()
            for name, slice_obj in self._slices.items()
            if slice_obj.dirty
        }

    def mark_clean(self, slice_names: Iterable[str]) -> None:
        """Clear dirty tracking for slices already handled by an outer boundary."""
        for name in slice_names:
            self.get_slice(name).clear_dirty()

    def apply(self, delta: StateDelta) -> None:
        """Dispatch each StateChange to its target slice.

        Concrete slices can expose an internal `apply_state_change(change)` hook.
        This keeps the public StateSlice ABC aligned with the docs while still
        letting the container consume StateDelta in tests and early scaffolding.
        """
        for change in delta.changes:
            self._apply_change(change)

    def _apply_change(self, change: StateChange) -> None:
        slice_obj = self.get_slice(change.slice)
        if not isinstance(slice_obj, SupportsStateChange):
            raise NotImplementedError(
                f"slice '{change.slice}' does not implement apply_state_change()"
            )
        handler = cast(SupportsStateChange, slice_obj)
        handler.apply_state_change(change)

    def validate(self) -> dict[str, list[str]]:
        """Collect validation issues from all slices."""
        return {
            name: issues
            for name, slice_obj in self._slices.items()
            if (issues := slice_obj.validate())
        }

    @staticmethod
    def _initial_time_payload() -> dict[str, Any]:
        return {
            "day": 1,
            "slot": 8,
            "period": "day",
            "action_count": 0,
            "accumulated": 0.0,
        }

    @classmethod
    def _initial_relation_payload(cls, world: WorldInstance) -> dict[str, Any]:
        npc_dispositions: dict[str, dict[str, int]] = {}
        faction_standings: dict[str, int] = {}

        if world.has_registry("characters"):
            for item in world.characters.list_all():
                character_id = cls._normalize_identifier(item.id)
                if character_id is None:
                    continue
                disposition = item.base_disposition if item.base_disposition is not None else {}
                npc_dispositions[character_id] = {
                    "approval": cls._as_int(disposition.get("approval"), 0),
                    "trust": cls._as_int(disposition.get("trust"), 0),
                    "fear": cls._as_int(disposition.get("fear"), 0),
                    "romance": cls._as_int(disposition.get("romance"), 0),
                }

        if world.has_registry("factions"):
            for faction in world.factions.list_all():
                faction_id = cls._normalize_identifier(faction.id)
                if faction_id is None:
                    continue
                raw_value = (
                    faction.initial_standing
                    if faction.initial_standing is not None
                    else (faction.base_standing if faction.base_standing is not None else 0)
                )
                faction_standings[faction_id] = cls._as_int(raw_value, 0)

        return {
            "npc_dispositions": npc_dispositions,
            "relationship_stages": {},
            "faction_standings": faction_standings,
            "npc_impressions": {},
            "shop_states": {},
        }

    @classmethod
    def _initial_quest_payload(cls, world: WorldInstance) -> dict[str, Any]:
        milestone_states: dict[str, dict[str, Any]] = {}
        chapter_completion: dict[str, float] = {}

        if world.has_registry("quests"):
            for item in world.quests.list_all():
                milestone_id = cls._normalize_identifier(item.id)
                if milestone_id is None:
                    continue
                has_prerequisites = len(item.prerequisites) > 0
                milestone_states[milestone_id] = {
                    "state": "LOCKED" if has_prerequisites else "AVAILABLE",
                    "activated_tick": None,
                    "completed_tick": None,
                }

            for chapter in world.quests.chapters():
                chapter_id = cls._normalize_identifier(chapter.id)
                if chapter_id is None:
                    continue
                chapter_completion[chapter_id] = 0.0

        return {
            "milestone_states": milestone_states,
            "dynamic_quests": {},
            "chapter_completion": chapter_completion,
        }

    @staticmethod
    def _initial_flag_payload() -> dict[str, Any]:
        return {"flags": {}}

    @classmethod
    def _initial_area_payload(cls, world: WorldInstance) -> dict[str, Any]:
        areas: dict[str, dict[str, Any]] = {}
        if world.has_registry("maps"):
            for item in world.maps.list_all():
                area_id = cls._normalize_identifier(item.id)
                if area_id is None:
                    continue
                raw_danger = item.base_danger if item.base_danger is not None else 1.0
                tags = [str(tag) for tag in item.tags]

                # Populate npc_locations from sub_location.resident_npcs
                npc_locations: dict[str, str | None] = {}
                for sub_id, sub_template in item.sub_locations.items():
                    sub_id_str = str(sub_id).strip()
                    if not sub_id_str:
                        continue
                    for npc_id in getattr(sub_template, "resident_npcs", []):
                        npc_id_str = str(npc_id).strip()
                        if npc_id_str:
                            npc_locations[npc_id_str] = sub_id_str

                areas[area_id] = {
                    "exploration": "undiscovered",
                    "danger_level": cls._as_float(raw_danger, 1.0),
                    "properties": {},
                    "tags": tags,
                    "temporary_sub_areas": [],
                    "discovered_items": [],
                    "npc_locations": npc_locations,
                    "board_bulletins": {},
                    "container_states": {},
                    "hostile_tracking": {},
                    "permanent_hostile_slots": {},
                }

        # Place NPCs from CharacterRegistry that aren't already placed by resident_npcs
        if world.has_registry("characters"):
            placed_npcs: set[str] = set()
            for area_data in areas.values():
                placed_npcs.update(area_data["npc_locations"].keys())
            for char in world.characters.list_all():
                char_id = cls._normalize_identifier(char.id)
                if char_id is None or char_id in placed_npcs:
                    continue
                char_area = (char.area_id or char.current_area or "").strip()
                if char_area and char_area in areas:
                    char_location = (char.location_id or char.current_location or "").strip()
                    areas[char_area]["npc_locations"][char_id] = char_location or None

        return {"areas": areas}

    @classmethod
    def _initial_event_payload(cls, world: WorldInstance) -> dict[str, Any]:
        active_events: dict[str, dict[str, Any]] = {}
        if world.has_registry("quests"):
            for item in world.quests.initial_events():
                event_id = cls._normalize_identifier(item.id)
                if event_id is None:
                    continue
                conditions = item.conditions
                if not isinstance(conditions, (list, dict)):
                    conditions = []
                active_events[event_id] = {
                    "id": event_id,
                    "event_id": event_id,
                    "state": "locked",
                    "status": "locked",
                    "event_type": item.event_type or "generic",
                    "conditions": conditions,
                    "payload": dict(item.payload),
                    "metadata": dict(item.metadata),
                    "source": "quest",
                }
        return {
            "active_events": active_events,
            "pending_events": [],
            "rumors": [],
        }

    @staticmethod
    def _initial_party_payload() -> dict[str, Any]:
        return {
            "members": {},
            "companion_approval": {},
            "shared_experiences": [],
        }

    @staticmethod
    def _initial_scene_payload() -> dict[str, Any]:
        return {
            "entries": [],
            "state_changes": [],
        }

    @staticmethod
    def _normalize_identifier(value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _as_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _as_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
