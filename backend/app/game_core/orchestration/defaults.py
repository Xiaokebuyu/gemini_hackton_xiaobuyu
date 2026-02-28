"""Default orchestration assembly helpers."""

from __future__ import annotations

from app.game_core.orchestration.action_dispatcher import ActionDispatcher
from app.game_core.orchestration.hooks import (
    AIOsirisHook,
    EncounterHook,
    EventConditionHook,
    GmNarrationHook,
    NarrativePlannerHook,
    NpcScheduleHook,
    SceneBusResetHook,
    ScheduledEventHook,
    SettlementHook,
    StatusEffectHook,
    TimeAdvanceHook,
)
from app.game_core.orchestration.tick_coordinator import TickCoordinator


DEFAULT_ACTION_COMMAND_TYPES: tuple[tuple[str, str], ...] = (
    ("skill_check", "skill_check"),
    ("saving_throw", "saving_throw"),
    ("contest", "contest"),
    ("move_area", "move_area"),
    ("enter_sub_location", "enter_sub_location"),
    ("leave_sub_location", "leave_sub_location"),
    ("pick_up", "pick_up"),
    ("drop", "drop"),
    ("equip", "equip"),
    ("unequip", "unequip"),
    ("use_item", "use_item"),
    ("add_xp", "add_xp"),
    ("level_up", "level_up"),
    ("apply_asi", "apply_asi"),
    ("choose_subclass", "choose_subclass"),
    ("create_character", "create_character"),
    ("set_flag", "set_flag"),
    ("modify_disposition", "modify_disposition"),
    ("modify_approval", "modify_approval"),
    ("advance_quest", "advance_quest"),
    ("schedule_event", "schedule_event"),
    ("create_rumor", "create_rumor"),
    ("modify_location", "modify_location"),
    ("add_knowledge", "add_knowledge"),
    ("modify_completion", "modify_completion"),
    ("adjust_danger", "adjust_danger"),
    ("trade_buy", "trade_buy"),
    ("trade_sell", "trade_sell"),
    ("rest_short", "rest_short"),
    ("rest_long", "rest_long"),
    ("set_camp", "set_camp"),
    ("steal", "steal"),
    ("lockpick", "lockpick"),
    ("open_container", "open_container"),
    ("disarm_trap", "disarm_trap"),
    ("take_from_container", "take_from_container"),
    ("take_all", "take_all"),
    ("cast_spell", "cast_spell"),
    ("prepare_spells", "prepare_spells"),
    ("break_concentration", "break_concentration"),
    ("attack", "attack"),
    ("defend", "defend"),
    ("disengage", "disengage"),
    ("dash", "dash"),
    ("shove", "shove"),
    ("flee", "flee"),
    ("use_combat_item", "use_combat_item"),
    ("offhand_attack", "offhand_attack"),
)

DEFAULT_SETTLEMENT_HOOK_TYPES: tuple[type[SettlementHook], ...] = (
    ScheduledEventHook,
    StatusEffectHook,
    AIOsirisHook,
    NarrativePlannerHook,
    EncounterHook,
    EventConditionHook,
    NpcScheduleHook,
    TimeAdvanceHook,
    GmNarrationHook,
    SceneBusResetHook,
)


def build_default_action_dispatcher() -> ActionDispatcher:
    """Build an ActionDispatcher with the canonical action map."""
    dispatcher = ActionDispatcher()
    register_default_action_mappings(dispatcher)
    return dispatcher


def register_default_action_mappings(dispatcher: ActionDispatcher) -> list[str]:
    """Register missing default action mappings without overriding custom ones."""
    added: list[str] = []
    for action_type, command_type in DEFAULT_ACTION_COMMAND_TYPES:
        if dispatcher.register_if_missing(
            action_type,
            command_type=command_type,
        ):
            added.append(action_type)
    return added


def build_default_settlement_hooks() -> list[SettlementHook]:
    """Build a fresh default settlement hook chain."""
    return [hook_type() for hook_type in DEFAULT_SETTLEMENT_HOOK_TYPES]


def register_default_settlement_hooks(
    coordinator: TickCoordinator,
) -> list[SettlementHook]:
    """Register missing default settlement hooks exactly once per hook name."""
    existing_names = {hook.name for hook in coordinator.settlement_hooks}
    added: list[SettlementHook] = []
    for hook in build_default_settlement_hooks():
        if hook.name in existing_names:
            continue
        coordinator.register_settlement_hook(hook)
        existing_names.add(hook.name)
        added.append(hook)
    return added
