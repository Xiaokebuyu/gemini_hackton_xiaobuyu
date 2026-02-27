"""Concrete rules handlers."""

from app.game_core.rules.handlers.combat import CombatHandler
from app.game_core.rules.handlers.container import ContainerHandler
from app.game_core.rules.handlers.crime import CrimeHandler
from app.game_core.rules.handlers.economy import EconomyHandler
from app.game_core.rules.handlers.encounter import EncounterHandler
from app.game_core.rules.handlers.growth import GrowthHandler
from app.game_core.rules.handlers.inventory import InventoryHandler
from app.game_core.rules.handlers.navigation import NavigationHandler
from app.game_core.rules.handlers.rest import RestHandler
from app.game_core.rules.handlers.skill_check import SkillCheckHandler
from app.game_core.rules.handlers.spell import SpellHandler
from app.game_core.rules.handlers.status_effect import StatusEffectHandler
from app.game_core.rules.handlers.world_state import WorldStateHandler

__all__ = [
    "CombatHandler",
    "ContainerHandler",
    "CrimeHandler",
    "EconomyHandler",
    "EncounterHandler",
    "GrowthHandler",
    "InventoryHandler",
    "NavigationHandler",
    "RestHandler",
    "SkillCheckHandler",
    "SpellHandler",
    "StatusEffectHandler",
    "WorldStateHandler",
]
