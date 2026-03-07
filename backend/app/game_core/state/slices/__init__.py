"""Concrete state slices for the new game-core kernel."""

from app.game_core.state.slices.area import AreaSlice, AreaState, BulletinEntry
from app.game_core.state.slices.events import EventSlice
from app.game_core.state.slices.flags import FlagSlice
from app.game_core.state.slices.narrative_plan import NarrativePlanSlice
from app.game_core.state.slices.party import PartySlice
from app.game_core.state.slices.player import ItemStack, PlayerSlice
from app.game_core.state.slices.quests import MilestoneState, QuestSlice
from app.game_core.state.slices.relations import RelationSlice
from app.game_core.state.slices.scene import SceneEntry, SceneSlice
from app.game_core.state.slices.time import TimeSlice

__all__ = [
    "AreaSlice",
    "AreaState",
    "BulletinEntry",
    "EventSlice",
    "FlagSlice",
    "ItemStack",
    "MilestoneState",
    "NarrativePlanSlice",
    "PartySlice",
    "PlayerSlice",
    "QuestSlice",
    "RelationSlice",
    "SceneEntry",
    "SceneSlice",
    "TimeSlice",
]
