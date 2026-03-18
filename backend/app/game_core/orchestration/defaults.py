"""Default orchestration assembly helpers."""

from __future__ import annotations

from app.game_core.orchestration.action_dispatcher import ActionDispatcher
from app.game_core.orchestration.hooks import (
    AIOsirisHook,
    CampfireHook,
    DirectiveTriggerHook,
    DynamicSubAreaExpiryHook,
    EncounterHook,
    EventConditionHook,
    GmNarrationHook,
    MilestoneCompletionHook,
    MilestoneUnlockHook,
    NarrativePlannerHook,
    NpcAutonomyHook,
    NpcScheduleHook,
    PassivePerceptionHook,
    QuestExpiryHook,
    QuestObjectiveTrackingHook,
    RelationshipHook,
    SceneBusResetHook,
    ScheduledEventHook,
    SettlementHook,
    SharedExperienceHook,
    StatusEffectHook,
    TimeAdvanceHook,
    XpAdvancementHook,
)
from app.game_core.orchestration.tick_coordinator import TickCoordinator


DEFAULT_ACTION_COMMAND_TYPES: tuple[tuple[str, str], ...] = (
    ("skill_check", "skill_check"),
    ("saving_throw", "saving_throw"),
    ("search_area", "investigate"),
    ("contest", "contest"),
    ("move_area", "move_area"),
    ("enter_sub_location", "enter_sub_location"),
    ("leave_sub_location", "leave_sub_location"),
    ("enter_room", "enter_room"),
    ("leave_room", "leave_room"),
    ("pick_up", "pick_up"),
    ("drop", "drop"),
    ("equip", "equip"),
    ("unequip", "unequip"),
    ("use_item", "use_item"),
    ("use_resource", "consume_resource"),
    ("add_xp", "add_xp"),
    ("level_up", "level_up"),
    ("apply_asi", "apply_asi"),
    ("choose_subclass", "choose_subclass"),
    ("create_character", "create_character"),
    ("set_flag", "set_flag"),
    ("remove_flag", "remove_flag"),
    ("schedule_npc_move", "schedule_npc_move"),
    ("transition_event_state", "transition_event_state"),
    ("change_relationship_stage", "change_relationship_stage"),
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
    ("interact_object", "interact_object"),
    ("cast_spell", "cast_spell"),
    ("prepare_spells", "prepare_spells"),
    ("break_concentration", "break_concentration"),
    ("refresh_shop", "refresh_shop"),
    ("night_watch", "night_watch"),
    ("discover", "discover"),
    ("passive_scan", "passive_scan"),
    ("interact_object_v2", "interact_object_v2"),
    ("investigate_clue", "investigate_clue"),
    ("resolve_clue_option", "resolve_clue_option"),
    ("browse_board", "browse_board"),
    ("board_accept_quest", "board_accept_quest"),
    ("board_complete_quest", "board_complete_quest"),
    ("board_retire_quest", "board_retire_quest"),
    ("donate", "donate"),
    ("accept_quest", "receptionist_accept_quest"),
    ("report_quest", "receptionist_report_quest"),
    # v2 SRPG combat commands
    ("combat_move", "combat_move"),
    ("combat_end_turn", "combat_end_turn"),
    ("combat_disengage", "combat_disengage"),
    ("combat_dash", "combat_dash"),
    ("combat_attack", "combat_attack"),
    ("combat_defend", "combat_defend"),
    ("combat_npc_turn", "combat_npc_turn"),
)

DEFAULT_SETTLEMENT_HOOK_TYPES: tuple[type[SettlementHook], ...] = (
    ScheduledEventHook,
    StatusEffectHook,
    # AIOsirisHook is always constructed manually in bootstrap.py (with injected phases).
    # EncounterHook, PassivePerceptionHook, EventConditionHook are now coordinated as
    # phases inside AIOsirisHook and no longer registered as independent hooks.
    NarrativePlannerHook,
    MilestoneCompletionHook,
    MilestoneUnlockHook,
    QuestObjectiveTrackingHook,  # P56 — auto-track objectives with structured conditions
    XpAdvancementHook,           # P58 — XP threshold check → level_up SSE
    NpcScheduleHook,
    NpcAutonomyHook,        # P61 — deterministic NPC/companion blackboard update
    SharedExperienceHook,   # P62
    CampfireHook,           # NEW (P63)
    RelationshipHook,
    DirectiveTriggerHook,   # P76 — directive → NPC chat invitation
    TimeAdvanceHook,
    QuestExpiryHook,        # A-6 (S3-02): retire dynamic quests past expiry_ticks
    DynamicSubAreaExpiryHook,
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
