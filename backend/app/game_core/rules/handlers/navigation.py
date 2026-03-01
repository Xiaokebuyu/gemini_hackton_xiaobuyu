"""NavigationHandler implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import get_non_empty_string, handler_success
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer


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
            return self._compute_move_area(cmd, state)
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
    ) -> ExecuteResult:
        target_area = self._resolve_target_area(cmd.params) or ""
        return handler_success(
            "navigation",
            "move_area",
            changes=[
                StateChange("player", "set", "current_area", target_area),
                StateChange("player", "set", "current_location", None),
            ],
            time_cost=1.0,
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
        return handler_success(
            "navigation",
            "enter_sub_location",
            changes=[
                StateChange("player", "set", "current_location", location_id),
            ],
            time_cost=1.0 / 6.0,
            metadata={
                "area_id": area_id,
                "from_location": state.player.current_location,
                "to_location": location_id,
            },
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

