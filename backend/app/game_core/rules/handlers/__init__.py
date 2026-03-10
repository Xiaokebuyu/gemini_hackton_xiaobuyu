"""Concrete rules handlers."""

from app.game_core.rules.handlers.companion import CompanionHandler
from app.game_core.rules.handlers.combat import CombatHandler
from app.game_core.rules.handlers.board import BoardHandler
from app.game_core.rules.handlers.container import ContainerHandler
from app.game_core.rules.handlers.crime import CrimeHandler
from app.game_core.rules.handlers.discovery import DiscoveryHandler
from app.game_core.rules.handlers.economy import EconomyHandler
from app.game_core.rules.handlers.encounter import EncounterHandler
from app.game_core.rules.handlers.growth import GrowthHandler
from app.game_core.rules.handlers.hostile_area import HostileAreaHandler
from app.game_core.rules.handlers.interactable import InteractableHandler
from app.game_core.rules.handlers.inventory import InventoryHandler
from app.game_core.rules.handlers.navigation import NavigationHandler
from app.game_core.rules.handlers.planner import (
    PlannerItemHandler,
    PlannerNpcHandler,
    PlannerQuestHandler,
    PlannerRuntimeHandler,
    PlannerWorldHandler,
)
from app.game_core.rules.handlers.receptionist import ReceptionistHandler
from app.game_core.rules.handlers.rest import RestHandler
from app.game_core.rules.handlers.skill_check import SkillCheckHandler
from app.game_core.rules.handlers.spell import SpellHandler
from app.game_core.rules.handlers.status_effect import StatusEffectHandler
from app.game_core.rules.handlers.world_state import WorldStateHandler

__all__ = [
    "CompanionHandler",
    "CombatHandler",
    "BoardHandler",
    "ContainerHandler",
    "CrimeHandler",
    "DiscoveryHandler",
    "EconomyHandler",
    "EncounterHandler",
    "GrowthHandler",
    "HostileAreaHandler",
    "InteractableHandler",
    "InventoryHandler",
    "NavigationHandler",
    "PlannerItemHandler",
    "PlannerNpcHandler",
    "PlannerQuestHandler",
    "PlannerRuntimeHandler",
    "PlannerWorldHandler",
    "ReceptionistHandler",
    "RestHandler",
    "SkillCheckHandler",
    "SpellHandler",
    "StatusEffectHandler",
    "WorldStateHandler",
]
