"""Settlement hook implementations."""

from app.game_core.orchestration.hooks.ai_osiris import AIOsirisHook
from app.game_core.orchestration.hooks.base import NoOpSettlementHook, SettlementHook
from app.game_core.orchestration.hooks.campfire import CampfireHook
from app.game_core.orchestration.hooks.directive_trigger import DirectiveTriggerHook
from app.game_core.orchestration.hooks.dynamic_sub_area_expiry import DynamicSubAreaExpiryHook
from app.game_core.orchestration.hooks.encounter import EncounterHook
from app.game_core.orchestration.hooks.event_condition import EventConditionHook
from app.game_core.orchestration.hooks.gm_narration import GmNarrationHook
from app.game_core.orchestration.hooks.milestone_completion import MilestoneCompletionHook
from app.game_core.orchestration.hooks.milestone_unlock import MilestoneUnlockHook
from app.game_core.orchestration.hooks.narrative_planner import NarrativePlannerHook
from app.game_core.orchestration.hooks.npc_autonomy import NpcAutonomyHook
from app.game_core.orchestration.hooks.npc_schedule import NpcScheduleHook
from app.game_core.orchestration.hooks.passive_perception import PassivePerceptionHook
from app.game_core.orchestration.hooks.quest_expiry import QuestExpiryHook
from app.game_core.orchestration.hooks.quest_objective_tracking import QuestObjectiveTrackingHook
from app.game_core.orchestration.hooks.relationship import RelationshipHook
from app.game_core.orchestration.hooks.shared_experience import SharedExperienceHook
from app.game_core.orchestration.hooks.scene_reset import SceneBusResetHook
from app.game_core.orchestration.hooks.scheduled_event import ScheduledEventHook
from app.game_core.orchestration.hooks.status_effect import StatusEffectHook
from app.game_core.orchestration.hooks.time_advance import TimeAdvanceHook
from app.game_core.orchestration.hooks.xp_advancement import XpAdvancementHook

__all__ = [
    "AIOsirisHook",
    "CampfireHook",
    "DirectiveTriggerHook",
    "DynamicSubAreaExpiryHook",
    "EncounterHook",
    "EventConditionHook",
    "GmNarrationHook",
    "MilestoneCompletionHook",
    "MilestoneUnlockHook",
    "NarrativePlannerHook",
    "NoOpSettlementHook",
    "NpcAutonomyHook",
    "NpcScheduleHook",
    "PassivePerceptionHook",
    "QuestExpiryHook",
    "QuestObjectiveTrackingHook",
    "RelationshipHook",
    "SceneBusResetHook",
    "SharedExperienceHook",
    "ScheduledEventHook",
    "SettlementHook",
    "StatusEffectHook",
    "TimeAdvanceHook",
    "XpAdvancementHook",
]
