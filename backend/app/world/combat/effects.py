"""Status effect helpers."""
from typing import List, Tuple

from .dice import DiceRoller
from .models.combatant import Combatant, StatusEffect
from .spatial import DistanceBand


def apply_start_of_turn_effects(combatant: Combatant) -> List[Tuple[str, int, str]]:
    """Apply damage over time effects. Returns list of (message, damage, type)."""
    results: List[Tuple[str, int, str]] = []
    for effect in combatant.status_effects:
        if effect.effect == StatusEffect.BURNING:
            damage, _ = DiceRoller.roll("1d4")
            results.append((f"{combatant.name}被灼烧", damage, "fire"))
        elif effect.effect == StatusEffect.POISONED:
            damage, _ = DiceRoller.roll("1d4")
            results.append((f"{combatant.name}中毒", damage, "poison"))
    return results


def is_incapacitated(combatant: Combatant) -> bool:
    """Check if combatant cannot act."""
    return combatant.has_status_effect(StatusEffect.STUNNED)


def attack_advantage_state(
    attacker: Combatant,
    target: Combatant,
    distance: DistanceBand,
    is_ranged: bool,
) -> str:
    """Return advantage state: 'advantage', 'disadvantage', or 'normal'."""
    advantage = False
    disadvantage = False

    if attacker.has_status_effect(StatusEffect.BLINDED):
        disadvantage = True
    if attacker.has_status_effect(StatusEffect.FRIGHTENED):
        disadvantage = True
    if target.has_status_effect(StatusEffect.STUNNED):
        advantage = True
    if target.has_status_effect(StatusEffect.RESTRAINED):
        advantage = True
    if target.has_status_effect(StatusEffect.PRONE):
        if is_ranged:
            disadvantage = True
        else:
            advantage = True

    if advantage and not disadvantage:
        return "advantage"
    if disadvantage and not advantage:
        return "disadvantage"
    return "normal"
