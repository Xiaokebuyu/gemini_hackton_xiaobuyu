"""NavigationHandler implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content import WorldInstance
from app.game_core.location_utils import (
    build_visited_area_flag,
    build_visited_location_flag,
    build_visited_room_flag,
)
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import get_non_empty_string, handler_success
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer


_PERIOD_HOUR_MID: dict[str, int] = {"dawn": 7, "day": 13, "dusk": 19, "night": 1}


def _is_location_open(available_hours: tuple[int, int] | None, period: str) -> bool:
    """Return True if the location is open during the given time period."""
    if available_hours is None:
        return True
    start, end = available_hours
    mid = _PERIOD_HOUR_MID.get(period, 12)
    if start <= end:
        return start <= mid < end
    return mid >= start or mid < end  # 跨午夜


class NavigationHandler(StaticCommandHandler):
    COMMAND_TYPES = ("move_area", "enter_sub_location", "leave_sub_location", "enter_room", "leave_room")

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        if cmd.type == "move_area":
            return self._validate_move_area(cmd, state, world)
        if cmd.type == "enter_sub_location":
            return self._validate_enter_sub_location(cmd, state, world)
        if cmd.type == "leave_sub_location":
            return self._validate_leave_sub_location(cmd, state)
        if cmd.type == "enter_room":
            return self._validate_enter_room(cmd, state, world)
        if cmd.type == "leave_room":
            return self._validate_leave_room(state)
        return ValidationResult(ok=False, reason=f"unsupported command: {cmd.type}")

    def compute(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        validation = self.validate(cmd, state, world)
        if not validation.ok:
            return ExecuteResult.error(validation.reason or "validation failed")

        if cmd.type == "move_area":
            return self._compute_move_area(cmd, state, world)
        if cmd.type == "enter_sub_location":
            return self._compute_enter_sub_location(cmd, state, world)
        if cmd.type == "leave_sub_location":
            return self._compute_leave_sub_location(state)
        if cmd.type == "enter_room":
            return self._compute_enter_room(cmd, state, world)
        if cmd.type == "leave_room":
            return self._compute_leave_room(state, world)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _validate_move_area(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not world.has_registry("maps"):
            return ValidationResult(ok=False, reason="maps registry is required")
        target_area = self._resolve_target_area(cmd.params)
        if target_area is None:
            return ValidationResult(
                ok=False,
                reason="area_id/to must be a non-empty string",
            )
        if world.maps.get(target_area) is None:
            return ValidationResult(ok=False, reason=f"unknown area: {target_area}")
        from_area = get_non_empty_string(cmd.params, "from")
        current_area = state.player.current_area
        if from_area is not None and current_area and from_area != current_area:
            return ValidationResult(
                ok=False,
                reason=f"from area mismatch: expected {current_area}, got {from_area}",
            )
        if current_area:
            if target_area not in world.maps.get_adjacent(current_area):
                return ValidationResult(
                    ok=False,
                    reason=f"no connection from {current_area} to {target_area}",
                )
        return ValidationResult(ok=True)

    def _validate_enter_sub_location(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not world.has_registry("maps"):
            return ValidationResult(ok=False, reason="maps registry is required")
        area_id = self._resolve_area_for_sub_location(cmd.params, state)
        if area_id is None:
            return ValidationResult(
                ok=False,
                reason="current area is required before entering a sub-location",
            )
        area_template = world.maps.get(area_id)
        if area_template is None:
            return ValidationResult(ok=False, reason=f"unknown area: {area_id}")
        location_id = self._resolve_location_alias(cmd.params)
        if location_id is None:
            return ValidationResult(
                ok=False,
                reason="location_id/location must be a non-empty string",
            )
        if location_id in area_template.sub_locations:
            sub_loc = world.maps.get_sub_location(area_id, location_id)
            if sub_loc is not None and sub_loc.available_hours is not None:
                period = state.time.period if state.has_slice("time") else "day"
                if not _is_location_open(sub_loc.available_hours, period):
                    return ValidationResult(ok=False, reason="location_closed")
            return ValidationResult(ok=True)
        # Fallback: check dynamic sub-areas
        if state.has_slice("areas"):
            for sub_area in state.areas.list_temporary_sub_areas(area_id):
                if str(sub_area.get("id", "")) == location_id:
                    return ValidationResult(ok=True)
        return ValidationResult(
            ok=False,
            reason=f"unknown sub-location '{location_id}' in area '{area_id}'",
        )

    def _validate_leave_sub_location(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        if state.player.current_location is None:
            return ValidationResult(
                ok=False,
                reason="player is not currently in a sub-location",
            )
        area_id = get_non_empty_string(cmd.params, "area_id")
        if area_id is None:
            area_id = get_non_empty_string(cmd.params, "area")
        if area_id is not None and area_id != state.player.current_area:
            return ValidationResult(
                ok=False,
                reason=f"area mismatch: expected {state.player.current_area}, got {area_id}",
            )
        return ValidationResult(ok=True)

    def _compute_move_area(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        target_area = self._resolve_target_area(cmd.params) or ""
        conn = world.maps.get_connection(state.player.current_area or "", target_area)
        time_cost = float(conn.travel_slots) if conn is not None else 1.0

        # Resolve auto-placement sub-location for the target area
        to_location = self._resolve_auto_sub_location(target_area, world)
        # Auto-place into default room when the sub_location has one
        to_room = self._resolve_default_room(target_area, to_location, world) if to_location else None

        changes: list[StateChange] = [
            StateChange("player", "set", "current_area", target_area),
            StateChange("player", "set", "current_location", to_location),
            StateChange("player", "set", "current_room", to_room),
        ]
        # Write persistent visited_area flag when flags slice is present
        if state.has_slice("flags"):
            changes.append(
                StateChange("flags", "set", f"flags.{build_visited_area_flag(target_area)}", True)
            )
            if to_location:
                changes.append(
                    StateChange(
                        "flags",
                        "set",
                        f"flags.{build_visited_location_flag(target_area, to_location)}",
                        True,
                    )
                )
            if to_room and to_location:
                changes.append(
                    StateChange(
                        "flags",
                        "set",
                        f"flags.{build_visited_room_flag(target_area, to_location, to_room)}",
                        True,
                    )
                )
        return handler_success(
            "navigation",
            "move_area",
            changes=changes,
            time_cost=time_cost,
            metadata={
                "from_area": state.player.current_area,
                "to_area": target_area,
                "from_location": state.player.current_location,
                "to_location": to_location,
            },
            omit_empty_delta=False,
        )

    @staticmethod
    def _resolve_auto_sub_location(area_id: str, world: WorldInstance) -> str | None:
        """Return the sub-location to auto-place the player in when entering *area_id*.

        Resolution order:
        1. ``AreaTemplate.default_sub_location`` when non-empty and present in sub_locations.
        2. First key in ``AreaTemplate.sub_locations``.
        3. ``None`` when the area has no sub_locations.
        """
        if not world.has_registry("maps"):
            return None
        return world.maps.resolve_auto_sub_location(area_id)

    def _compute_enter_sub_location(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        location_id = self._resolve_location_alias(cmd.params) or ""
        area_id = state.player.current_area

        # Auto-place player into default room when the sub_location has one
        default_room = self._resolve_default_room(area_id, location_id, world)

        changes: list[StateChange] = [
            StateChange("player", "set", "current_location", location_id),
            StateChange("player", "set", "current_room", default_room),
        ]
        if state.has_slice("flags") and area_id and default_room:
            changes.append(
                StateChange(
                    "flags",
                    "set",
                    f"flags.{build_visited_room_flag(area_id, location_id, default_room)}",
                    True,
                )
            )
        if state.has_slice("flags") and area_id:
            changes.append(
                StateChange(
                    "flags",
                    "set",
                    f"flags.{build_visited_location_flag(area_id, location_id)}",
                    True,
                )
            )
        metadata: dict[str, Any] = {
            "area_id": area_id,
            "from_location": state.player.current_location,
            "to_location": location_id,
        }

        # --- Phase 8: planted encounter detection ---
        if state.has_slice("areas") and area_id:
            hostile = state.areas.get_hostile_state(location_id)
            if hostile and hostile.get("status") == "planted" and not hostile.get("cleared"):
                expiry = int(hostile.get("expiry_ticks", -1))
                created = int(hostile.get("created_at_tick", 0))
                current_tick = state.time.absolute_tick() if state.has_slice("time") else 0
                expired = expiry > 0 and (current_tick - created) >= expiry

                if expired:
                    changes.append(
                        StateChange(
                            "areas",
                            "modify",
                            f"hostile_tracking.{location_id}",
                            {**hostile, "status": "expired", "cleared": True},
                        )
                    )
                else:
                    changes.append(
                        StateChange(
                            "areas",
                            "modify",
                            f"hostile_tracking.{location_id}",
                            {**hostile, "status": "spotted"},
                        )
                    )
                    metadata["encounter_spotted"] = {
                        "sub_area_id": location_id,
                        "area_id": hostile.get("area_id", area_id),
                        "description": hostile.get("description", ""),
                        "threat_level": hostile.get("threat_level", "moderate"),
                        "blocking": hostile.get("blocking", True),
                        "monster_ids": hostile.get("monster_ids", []),
                        "map_category": hostile.get("map_category"),
                        "map_tags": hostile.get("map_tags", []),
                        "source": "plant_encounter",
                    }

        return handler_success(
            "navigation",
            "enter_sub_location",
            changes=changes,
            time_cost=1.0 / 6.0,
            metadata=metadata,
            omit_empty_delta=False,
        )

    def _compute_leave_sub_location(self, state: StateContainer) -> ExecuteResult:
        changes: list[StateChange] = [
            StateChange("player", "set", "current_location", None),
        ]
        # Phase 3: leaving a sub_location also clears current_room
        if state.player.current_room is not None:
            changes.append(StateChange("player", "set", "current_room", None))
        return handler_success(
            "navigation",
            "leave_sub_location",
            changes=changes,
            time_cost=1.0 / 12.0,
            metadata={
                "area_id": state.player.current_area,
                "from_location": state.player.current_location,
                "to_location": None,
            },
            omit_empty_delta=False,
        )

    def _validate_enter_room(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        current_location = state.player.current_location
        if not current_location:
            return ValidationResult(
                ok=False,
                reason="player must be in a sub_location before entering a room",
            )
        room_id = self._resolve_room_alias(cmd.params)
        if room_id is None:
            return ValidationResult(
                ok=False,
                reason="room_id/room must be a non-empty string",
            )
        # Verify the room exists in the current sub_location
        if not world.has_registry("maps"):
            return ValidationResult(ok=False, reason="maps registry is required")
        area_id = state.player.current_area
        sub_loc = world.maps.get_sub_location(area_id, current_location)
        if sub_loc is None:
            return ValidationResult(
                ok=False,
                reason=f"unknown sub_location '{current_location}' in area '{area_id}'",
            )
        if room_id not in sub_loc.rooms:
            # Check dynamic rooms as fallback
            if state.has_slice("areas"):
                dyn = state.areas.list_dynamic_rooms(area_id, current_location)
                dyn_match = next((r for r in dyn if r.get("room_id") == room_id), None)
                if dyn_match is not None:
                    # Dynamic room found — check discoverable flag
                    if dyn_match.get("discoverable") and not state.areas.is_room_discovered(
                        area_id, current_location, room_id
                    ):
                        return ValidationResult(
                            ok=False,
                            reason=f"room '{room_id}' has not been discovered yet",
                        )
                    return ValidationResult(ok=True)
            return ValidationResult(
                ok=False,
                reason=f"unknown room '{room_id}' in sub_location '{current_location}'",
            )
        room_template = sub_loc.rooms[room_id]
        # Discoverable rooms must have been previously discovered
        if room_template.discoverable and state.has_slice("areas"):
            if not state.areas.is_room_discovered(area_id, current_location, room_id):
                return ValidationResult(
                    ok=False,
                    reason=f"room '{room_id}' has not been discovered yet",
                )
        return ValidationResult(ok=True)

    def _validate_leave_room(self, state: StateContainer) -> ValidationResult:
        if state.player.current_room is None:
            return ValidationResult(
                ok=False,
                reason="player is not currently in a room",
            )
        return ValidationResult(ok=True)

    def _compute_enter_room(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        room_id = self._resolve_room_alias(cmd.params) or ""
        area_id = state.player.current_area
        current_location = state.player.current_location

        changes: list[StateChange] = [
            StateChange("player", "set", "current_room", room_id),
        ]
        if state.has_slice("flags") and area_id and current_location:
            changes.append(
                StateChange(
                    "flags",
                    "set",
                    f"flags.{build_visited_room_flag(area_id, current_location, room_id)}",
                    True,
                )
            )
        return handler_success(
            "navigation",
            "enter_room",
            changes=changes,
            time_cost=1.0 / 24.0,  # ~2.5 min — minimal cost for moving within sub_location
            metadata={
                "area_id": area_id,
                "sub_location_id": current_location,
                "from_room": state.player.current_room,
                "to_room": room_id,
            },
            omit_empty_delta=False,
        )

    def _compute_leave_room(self, state: StateContainer, world: WorldInstance) -> ExecuteResult:
        # Fall back to default_room so the player is never at sub_location
        # level without a room (which would bypass room-level NPC filtering).
        target_room: str | None = None
        area_id = state.player.current_area
        location_id = state.player.current_location
        if location_id and world.has_registry("maps"):
            sub_loc = world.maps.get_sub_location(area_id, location_id)
            if sub_loc is not None:
                raw_dr = getattr(sub_loc, "default_room", "")
                target_room = raw_dr.strip() or None
        # If target equals current room, player is already at default — set None
        if target_room == state.player.current_room:
            target_room = None
        return handler_success(
            "navigation",
            "leave_room",
            changes=[
                StateChange("player", "set", "current_room", target_room),
            ],
            time_cost=0.0,
            metadata={
                "area_id": area_id,
                "sub_location_id": location_id,
                "from_room": state.player.current_room,
                "to_room": target_room,
            },
            omit_empty_delta=False,
        )

    @staticmethod
    def _resolve_default_room(
        area_id: str | None,
        location_id: str | None,
        world: WorldInstance,
    ) -> str | None:
        """Return the default room for a sub_location, or None if no rooms."""
        if not area_id or not location_id or not world.has_registry("maps"):
            return None
        sub_loc = world.maps.get_sub_location(area_id, location_id)
        if sub_loc is None:
            return None
        default_room = getattr(sub_loc, "default_room", "")
        return default_room.strip() or None

    @staticmethod
    def _resolve_target_area(params: Mapping[str, Any]) -> str | None:
        return get_non_empty_string(params, "area_id") or get_non_empty_string(
            params,
            "to",
        )

    @staticmethod
    def _resolve_location_alias(params: Mapping[str, Any]) -> str | None:
        return get_non_empty_string(
            params,
            "location_id",
        ) or get_non_empty_string(params, "location")

    @staticmethod
    def _resolve_room_alias(params: Mapping[str, Any]) -> str | None:
        return get_non_empty_string(params, "room_id") or get_non_empty_string(params, "room")

    @staticmethod
    def _resolve_area_for_sub_location(
        params: Mapping[str, Any],
        state: StateContainer,
    ) -> str | None:
        requested_area = get_non_empty_string(params, "area_id")
        if requested_area is None:
            requested_area = get_non_empty_string(params, "area")
        current_area = state.player.current_area
        if requested_area is None:
            return current_area or None
        if current_area and requested_area != current_area:
            return None
        return requested_area
