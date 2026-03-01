"""Concrete content registries for the new game-core kernel."""

from app.game_core.content.registries.characters import (
    CharacterRegistry,
    CharacterTemplate,
    ShopInventory,
)
from app.game_core.content.registries.classes import (
    BackgroundTemplate,
    ClassRegistry,
    ClassTemplate,
    RaceTemplate,
    SubclassTemplate,
)
from app.game_core.content.registries.factions import FactionRegistry, FactionTemplate
from app.game_core.content.registries.items import ItemRegistry, ItemTemplate
from app.game_core.content.registries.lore import LoreEntry, LoreRegistry
from app.game_core.content.registries.maps import AreaTemplate, MapRegistry
from app.game_core.content.registries.monsters import (
    LootEntry,
    MonsterAttack,
    MonsterRegistry,
    MonsterTemplate,
)
from app.game_core.content.registries.quests import (
    ChapterMeta,
    InitialEvent,
    MilestoneTemplate,
    QuestRegistry,
)
from app.game_core.content.registries.skills import SkillRegistry, SkillTemplate
from app.game_core.content.registries.tag import TagDimension, TagRegistry

__all__ = [
    "BackgroundTemplate",
    "CharacterRegistry",
    "CharacterTemplate",
    "ClassRegistry",
    "ClassTemplate",
    "FactionRegistry",
    "FactionTemplate",
    "ItemRegistry",
    "ItemTemplate",
    "AreaTemplate",
    "LootEntry",
    "LoreEntry",
    "LoreRegistry",
    "MapRegistry",
    "MonsterAttack",
    "MonsterRegistry",
    "MonsterTemplate",
    "ChapterMeta",
    "InitialEvent",
    "MilestoneTemplate",
    "QuestRegistry",
    "RaceTemplate",
    "ShopInventory",
    "SubclassTemplate",
    "SkillRegistry",
    "SkillTemplate",
    "TagDimension",
    "TagRegistry",
]
