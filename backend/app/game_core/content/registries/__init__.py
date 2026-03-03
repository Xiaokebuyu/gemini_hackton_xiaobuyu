"""Concrete content registries for the new game-core kernel."""

from app.game_core.content.registries.characters import (
    CharacterRegistry,
    CharacterTemplate,
    NpcAttack,
    ShopEntry,
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
from app.game_core.content.registries.items import AccessoryData, ItemRegistry, ItemTemplate
from app.game_core.content.registries.lore import LoreEntry, LoreRegistry, WorldRule
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
    MilestoneCondition,
    MilestoneTemplate,
    QuestRegistry,
)
from app.game_core.content.registries.map_types import (
    CheckPath,
    ContainerData,
    Discovery,
    EncounterEntry,
    HostileConfig,
    HostileGroup,
    HostileTemplate,
    InteractableTemplate,
    SubAreaClusterConfig,
    SubLocationTemplate,
    TrapData,
)
from app.game_core.content.registries.class_types import (
    Feature,
    ResourceConfig,
    SpellcastingConfig,
)
from app.game_core.content.registries.shared_types import Effect, LootTableDef
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
    "AccessoryData",
    "ItemRegistry",
    "ItemTemplate",
    "AreaTemplate",
    "LootEntry",
    "LoreEntry",
    "LoreRegistry",
    "WorldRule",
    "MapRegistry",
    "MonsterAttack",
    "MonsterRegistry",
    "MonsterTemplate",
    "ChapterMeta",
    "InitialEvent",
    "MilestoneCondition",
    "MilestoneTemplate",
    "QuestRegistry",
    "RaceTemplate",
    "NpcAttack",
    "ShopEntry",
    "ShopInventory",
    "SubclassTemplate",
    "SkillRegistry",
    "SkillTemplate",
    "TagDimension",
    "TagRegistry",
    # class_types
    "Feature",
    "ResourceConfig",
    "SpellcastingConfig",
    # shared_types
    "Effect",
    "LootTableDef",
    # map_types
    "EncounterEntry",
    "CheckPath",
    "ContainerData",
    "Discovery",
    "HostileConfig",
    "HostileGroup",
    "HostileTemplate",
    "InteractableTemplate",
    "SubAreaClusterConfig",
    "SubLocationTemplate",
    "TrapData",
]
