"""Default rules assembly helpers."""

from __future__ import annotations

from app.game_core.rules.base import CommandHandler
from app.game_core.rules.engine import RulesEngine
from app.game_core.rules.handlers import (
    BoardHandler,
    CombatHandler,
    CompanionHandler,
    ContainerHandler,
    CrimeHandler,
    DiscoveryHandler,
    EconomyHandler,
    EncounterHandler,
    GrowthHandler,
    HostileAreaHandler,
    InteractableHandler,
    InventoryHandler,
    NavigationHandler,
    PlannerItemHandler,
    PlannerNpcHandler,
    PlannerQuestHandler,
    PlannerRuntimeHandler,
    PlannerWorldHandler,
    ReceptionistHandler,
    RestHandler,
    SkillCheckHandler,
    SpellHandler,
    StatusEffectHandler,
    WorldStateHandler,
)


DEFAULT_RULE_HANDLER_TYPES: tuple[type[CommandHandler], ...] = (
    CompanionHandler,
    CombatHandler,
    SkillCheckHandler,
    NavigationHandler,
    InventoryHandler,
    EconomyHandler,
    GrowthHandler,
    RestHandler,
    CrimeHandler,
    EncounterHandler,
    ContainerHandler,
    WorldStateHandler,
    PlannerQuestHandler,
    PlannerNpcHandler,
    PlannerWorldHandler,
    PlannerItemHandler,
    PlannerRuntimeHandler,
    StatusEffectHandler,
    SpellHandler,
    DiscoveryHandler,
    BoardHandler,
    ReceptionistHandler,
    InteractableHandler,
    HostileAreaHandler,
)


def build_default_rules_handlers() -> list[CommandHandler]:
    """Build a fresh default rule-handler chain."""
    return [handler_type() for handler_type in DEFAULT_RULE_HANDLER_TYPES]


def register_default_rules_handlers(engine: RulesEngine) -> list[str]:
    """Register default handlers without overriding existing command routes."""
    added: list[str] = []
    for handler in build_default_rules_handlers():
        added.extend(engine.register_if_missing(handler))
    return added
