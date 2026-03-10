"""NavigationHandler implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content import WorldInstance
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
    COMMAND_TYPES = ("move_area", "enter_sub_location", "leave_sub_location")

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
            return self._compute_enter_sub_location(cmd, state)
        if cmd.type == "leave_sub_location":
            return self._compute_leave_sub_location(state)
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
        return handler_success(
            "navigation",
            "move_area",
            changes=[
                StateChange("player", "set", "current_area", target_area),
                StateChange("player", "set", "current_location", None),
            ],
            time_cost=time_cost,
            metadata={
                "from_area": state.player.current_area,
                "to_area": target_area,
                "from_location": state.player.current_location,
                "to_location": None,
            },
            omit_empty_delta=False,
        )

    def _compute_enter_sub_location(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        location_id = self._resolve_location_alias(cmd.params) or ""
        area_id = state.player.current_area

        changes: list[StateChange] = [
            StateChange("player", "set", "current_location", location_id),
        ]
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
        return handler_success(
            "navigation",
            "leave_sub_location",
            changes=[
                StateChange("player", "set", "current_location", None),
            ],
            time_cost=1.0 / 12.0,
            metadata={
                "area_id": state.player.current_area,
                "from_location": state.player.current_location,
                "to_location": None,
            },
            omit_empty_delta=False,
        )

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

