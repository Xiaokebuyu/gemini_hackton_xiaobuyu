"""ServiceEffectHandler — thin handler for NPC service atomic effects.

Handles effect atoms that cannot map to a pre-existing command type:
- ``restore_hp``: heal the player up to max_hp
- ``modify_gold``: add or subtract gold (floor at 0)

All other effect atoms (grant_item, apply_effect, etc.) are expressed as
their canonical command types and dispatched directly by the tool layer.

Decision record: D-Svc03 (narrative.md)
"""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import coerce_int, handler_success
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer


class ServiceEffectHandler(StaticCommandHandler):
    """Handle the ``npc_service_effect`` command type.

    Params
    ------
    effect_type : str
        Sub-operation: ``"restore_hp"`` or ``"modify_gold"``.
    amount : int
        Magnitude of the effect.  For ``restore_hp`` this is the raw heal
        amount (capped at max_hp internally).  For ``modify_gold`` this may
        be negative (to deduct gold from the player).
    """

    COMMAND_TYPES = ("npc_service_effect",)

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")

        effect_type = cmd.params.get("effect_type", "")
        if effect_type not in {"restore_hp", "modify_gold"}:
            return ValidationResult(
                ok=False,
                reason=f"unknown effect_type '{effect_type}'; expected restore_hp or modify_gold",
            )

        amount = coerce_int(cmd.params.get("amount"))
        if amount is None:
            return ValidationResult(ok=False, reason="amount must be an integer")

        if effect_type == "modify_gold" and amount < 0:
            # Deduction: verify the player can afford it.
            current_gold = int(state.player.gold)
            if current_gold + amount < 0:
                return ValidationResult(
                    ok=False,
                    reason=f"insufficient gold: have {current_gold}, need {-amount}",
                )

        return ValidationResult(ok=True)

    def compute(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        validation = self.validate(cmd, state, world)
        if not validation.ok:
            return ExecuteResult.error(validation.reason or "validation failed")

        effect_type = cmd.params.get("effect_type", "")
        amount = coerce_int(cmd.params.get("amount")) or 0

        if effect_type == "restore_hp":
            return self._compute_restore_hp(amount, state)
        if effect_type == "modify_gold":
            return self._compute_modify_gold(amount, state)

        return ExecuteResult.error(f"unknown effect_type: {effect_type}")

    # ------------------------------------------------------------------
    # Sub-computations
    # ------------------------------------------------------------------

    def _compute_restore_hp(self, amount: int, state: StateContainer) -> ExecuteResult:
        hp = int(state.player.hp)
        max_hp = int(state.player.max_hp)
        heal = min(amount, max_hp - hp)
        if heal <= 0:
            return handler_success(
                "ServiceEffectHandler",
                "npc_service_effect",
                changes=[],
                metadata={"healed": 0, "reason": "already_at_full_hp"},
            )
        new_hp = hp + heal
        return handler_success(
            "ServiceEffectHandler",
            "npc_service_effect",
            changes=[StateChange("player", "set", "hp", new_hp)],
            metadata={"healed": heal, "new_hp": new_hp},
        )

    def _compute_modify_gold(self, amount: int, state: StateContainer) -> ExecuteResult:
        current = int(state.player.gold)
        new_gold = max(0, current + amount)
        return handler_success(
            "ServiceEffectHandler",
            "npc_service_effect",
            changes=[StateChange("player", "set", "gold", new_gold)],
            metadata={"gold_delta": amount, "new_gold": new_gold},
        )
