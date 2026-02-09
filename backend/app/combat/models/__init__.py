"""Data models for the combat system."""

from .combatant import Combatant, CombatantType, StatusEffect, StatusEffectInstance
from .action import (
    ActionType,
    ActionOption,
    DiceRoll,
    AttackRoll,
    DamageRoll,
    ActionResult,
)
from .combat_session import (
    CombatSession,
    CombatState,
    CombatEndReason,
    CombatLogEvent,
    TurnRequest,
)
from .combat_result import CombatResult, CombatRewards, CombatPenalty
from .v3 import EnemySpec, CombatActionOptionV3, CombatResolutionV3

__all__ = [
    "Combatant",
    "CombatantType",
    "StatusEffect",
    "StatusEffectInstance",
    "ActionType",
    "ActionOption",
    "DiceRoll",
    "AttackRoll",
    "DamageRoll",
    "ActionResult",
    "CombatSession",
    "CombatState",
    "CombatEndReason",
    "CombatLogEvent",
    "TurnRequest",
    "CombatResult",
    "CombatRewards",
    "CombatPenalty",
    "EnemySpec",
    "CombatActionOptionV3",
    "CombatResolutionV3",
]
