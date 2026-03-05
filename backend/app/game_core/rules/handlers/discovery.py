"""DiscoveryHandler — active discovery search command."""

from __future__ import annotations

import random

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import (
    build_dice_roll,
    get_non_empty_string,
    handler_success,
    handler_success_no_delta,
)
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer


class DiscoveryHandler(StaticCommandHandler):
    COMMAND_TYPES = ("discover", "passive_scan")

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if cmd.type == "passive_scan":
            return ValidationResult(ok=True)
        # discover: requires player + maps registry
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice required")
        if not world.has_registry("maps"):
            return ValidationResult(ok=False, reason="maps registry required")
        area_id = get_non_empty_string(cmd.params, "area_id")
        if area_id is None:
            return ValidationResult(ok=False, reason="area_id required")
        discovery_id = get_non_empty_string(cmd.params, "discovery_id")
        if discovery_id is None:
            return ValidationResult(ok=False, reason="discovery_id required")
        return ValidationResult(ok=True)

    def compute(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        if cmd.type == "passive_scan":
            # Passive scans are handled by PassivePerceptionHook at settlement
            return ExecuteResult(
                success=True,
                metadata={"status": "deferred_to_hook", "reason": "passive_scan handled by PassivePerceptionHook"},
            )
        return self._compute_discover(cmd, state, world)

    def _compute_discover(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        area_id = get_non_empty_string(cmd.params, "area_id") or ""
        discovery_id = get_non_empty_string(cmd.params, "discovery_id") or ""

        if not state.has_slice("areas"):
            return ExecuteResult.error("areas slice required")

        # Already found
        if state.areas.is_discovery_found(area_id, discovery_id):
            return ExecuteResult.error("already_discovered")

        # Find the discovery template
        area_template = world.maps.get(area_id)
        if area_template is None:
            return ExecuteResult.error(f"unknown area: {area_id}")
        template = next((d for d in area_template.discoveries if d.id == discovery_id), None)
        if template is None:
            return ExecuteResult.error(f"discovery_not_found: {discovery_id}")

        # Skill check
        skill = template.check_type or "perception"
        try:
            modifier = state.player.get_skill_bonus(skill)
        except (KeyError, TypeError):
            modifier = 0

        raw_roll = random.randint(1, 20)
        total = raw_roll + modifier
        passed = total >= template.dc

        dice_roll = build_dice_roll(
            purpose=f"discover_{discovery_id}",
            dice="1d20",
            result=raw_roll,
            modifiers=[{"name": skill, "value": modifier}],
            total=total,
        )

        if not passed:
            return handler_success_no_delta(
                "discovery", "discover",
                time_cost=1.0 / 6.0,
                rolls=[dice_roll],
                metadata={
                    "passed": False,
                    "discovery_id": discovery_id,
                    "area_id": area_id,
                    "skill": skill,
                    "roll": raw_roll,
                    "modifier": modifier,
                    "total": total,
                    "dc": template.dc,
                },
            )

        return handler_success(
            "discovery", "discover",
            changes=[
                StateChange("areas", "set", f"{area_id}.discovered_items.{discovery_id}", True),
            ],
            time_cost=1.0 / 6.0,
            rolls=[dice_roll],
            metadata={
                "passed": True,
                "discovery_id": discovery_id,
                "area_id": area_id,
                "skill": skill,
                "roll": raw_roll,
                "modifier": modifier,
                "total": total,
                "dc": template.dc,
                "reward": dict(template.reward) if template.reward else {},
            },
        )
