"""Concrete content registries for the new game-core kernel."""

from app.game_core.content.registries.characters import CharacterRegistry
from app.game_core.content.registries.classes import ClassRegistry
from app.game_core.content.registries.factions import FactionRegistry
from app.game_core.content.registries.items import ItemRegistry
from app.game_core.content.registries.lore import LoreRegistry
from app.game_core.content.registries.maps import MapRegistry
from app.game_core.content.registries.monsters import MonsterRegistry
from app.game_core.content.registries.quests import QuestRegistry
from app.game_core.content.registries.skills import SkillRegistry
from app.game_core.content.registries.tag import TagRegistry

__all__ = [
    "CharacterRegistry",
    "ClassRegistry",
    "FactionRegistry",
    "ItemRegistry",
    "LoreRegistry",
    "MapRegistry",
    "MonsterRegistry",
    "QuestRegistry",
    "SkillRegistry",
    "TagRegistry",
]
