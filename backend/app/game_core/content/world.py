"""World-level registry container for static content."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, cast

from app.game_core.content.base import ContentRegistry

if TYPE_CHECKING:
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


class WorldInstance:
    """Container for all static content registries in one world."""

    def __init__(self, world_id: str) -> None:
        if not world_id:
            raise ValueError("world_id must not be empty")
        self.world_id = world_id
        self._registries: dict[str, ContentRegistry] = {}

    def register(self, registry: ContentRegistry) -> None:
        """Register one content registry by its canonical name."""
        name = registry.name
        if name in self._registries:
            raise ValueError(f"registry already registered: {name}")
        self._registries[name] = registry

    def get_registry(self, name: str) -> ContentRegistry:
        """Return a registered content registry."""
        try:
            return self._registries[name]
        except KeyError as exc:
            raise KeyError(f"unknown registry: {name}") from exc

    def has_registry(self, name: str) -> bool:
        """Whether a registry is registered."""
        return name in self._registries

    def all_registries(self) -> list[tuple[str, ContentRegistry]]:
        """Return registered registries in insertion order."""
        return list(self._registries.items())

    def load_all(self, world_data: dict[str, Any]) -> None:
        """Load registries in dependency order."""
        ordered_groups = [
            ["tags"],
            ["maps", "classes", "skills", "lore"],
            ["characters", "items", "monsters", "factions", "quests"],
        ]
        loaded: set[str] = set()
        for group in ordered_groups:
            for name in group:
                registry = self._registries.get(name)
                if registry is None:
                    continue
                registry.load(world_data.get(name, {}))
                loaded.add(name)

        for name, registry in self._registries.items():
            if name in loaded:
                continue
            registry.load(world_data.get(name, {}))

    def query_by_tags(
        self,
        tags: list[str],
        registry_name: str | None = None,
        match_all: bool = True,
    ) -> list[Any]:
        """Query one registry or all registries by tags."""
        if registry_name is not None:
            return self.get_registry(registry_name).query_by_tags(
                tags,
                match_all=match_all,
            )

        results: list[Any] = []
        for registry in self._registries.values():
            results.extend(registry.query_by_tags(tags, match_all=match_all))
        return results

    # -- Typed convenience accessors (cast at access time) --

    @property
    def tags(self) -> TagRegistry:
        return cast("TagRegistry", self.get_registry("tags"))

    @property
    def maps(self) -> MapRegistry:
        return cast("MapRegistry", self.get_registry("maps"))

    @property
    def characters(self) -> CharacterRegistry:
        return cast("CharacterRegistry", self.get_registry("characters"))

    @property
    def items(self) -> ItemRegistry:
        return cast("ItemRegistry", self.get_registry("items"))

    @property
    def skills(self) -> SkillRegistry:
        return cast("SkillRegistry", self.get_registry("skills"))

    @property
    def classes(self) -> ClassRegistry:
        return cast("ClassRegistry", self.get_registry("classes"))

    @property
    def monsters(self) -> MonsterRegistry:
        return cast("MonsterRegistry", self.get_registry("monsters"))

    @property
    def factions(self) -> FactionRegistry:
        return cast("FactionRegistry", self.get_registry("factions"))

    @property
    def lore(self) -> LoreRegistry:
        return cast("LoreRegistry", self.get_registry("lore"))

    @property
    def quests(self) -> QuestRegistry:
        return cast("QuestRegistry", self.get_registry("quests"))

    def snapshot(self) -> dict[str, Any]:
        """Return a world-level registry summary."""
        return {
            "world_id": self.world_id,
            "registries": {
                name: registry.snapshot()
                for name, registry in self._registries.items()
            },
        }

    def validate(self) -> dict[str, list[str]]:
        """Collect validation issues from all registries."""
        issues_by_registry = {
            name: issues
            for name, registry in self._registries.items()
            if (issues := registry.validate())
        }
        world_issues = self._validate_cross_registry_refs()
        if world_issues:
            issues_by_registry["_world"] = world_issues
        return issues_by_registry

    def _validate_cross_registry_refs(self) -> list[str]:
        issues: list[str] = []
        if self.has_registry("characters") and self.has_registry("maps"):
            issues.extend(self._validate_character_map_refs())
        if self.has_registry("characters") and self.has_registry("items"):
            issues.extend(self._validate_character_item_refs())
        if self.has_registry("monsters") and self.has_registry("items"):
            issues.extend(self._validate_monster_loot_refs())
        if self.has_registry("maps") and self.has_registry("monsters"):
            issues.extend(self._validate_encounter_monster_refs())
        if self.has_registry("characters") and self.has_registry("classes"):
            issues.extend(self._validate_character_class_refs())
        if self.has_registry("characters") and self.has_registry("factions"):
            issues.extend(self._validate_character_faction_refs())
        if self.has_registry("monsters") and self.has_registry("skills"):
            issues.extend(self._validate_monster_skill_refs())
        if self.has_registry("quests"):
            issues.extend(self._validate_milestone_prerequisites())
        if self.has_registry("tags"):
            issues.extend(self._validate_tag_value_refs())
        return issues

    def _validate_character_map_refs(self) -> list[str]:
        issues: list[str] = []
        map_ids = {
            str(item.get("id"))
            for item in self.maps.list_all()
            if isinstance(item, Mapping) and item.get("id")
        }
        for character in self.characters.list_all():
            if not isinstance(character, Mapping):
                continue
            character_id = self._entry_id(character)
            for field_name in ("area_id", "current_area"):
                area_id = self._coerce_non_empty_string(character.get(field_name))
                if area_id is None:
                    continue
                if area_id not in map_ids:
                    issues.append(
                        f"character '{character_id}' references unknown map '{area_id}' via {field_name}"
                    )
        return issues

    def _validate_character_item_refs(self) -> list[str]:
        issues: list[str] = []
        item_ids = {
            str(item.get("id"))
            for item in self.items.list_all()
            if isinstance(item, Mapping) and item.get("id")
        }
        for character in self.characters.list_all():
            if not isinstance(character, Mapping):
                continue
            character_id = self._entry_id(character)
            inventory = character.get("inventory")
            if isinstance(inventory, list):
                issues.extend(
                    self._validate_item_refs(
                        owner_id=character_id,
                        owner_label="character",
                        container=inventory,
                        item_ids=item_ids,
                        prefix="inventory",
                    )
                )
            shop = character.get("shop")
            if isinstance(shop, Mapping):
                shop_inv_list = shop.get("inventory")
                if isinstance(shop_inv_list, list):
                    issues.extend(
                        self._validate_item_refs(
                            owner_id=character_id,
                            owner_label="character",
                            container=shop_inv_list,
                            item_ids=item_ids,
                            prefix="shop.inventory",
                        )
                    )
            shop_inventory = character.get("shop_inventory")
            if isinstance(shop_inventory, Mapping):
                for pool_name in ("base_pool", "rotating_pool"):
                    pool = shop_inventory.get(pool_name)
                    if isinstance(pool, list):
                        issues.extend(
                            self._validate_item_refs(
                                owner_id=character_id,
                                owner_label="character",
                                container=pool,
                                item_ids=item_ids,
                                prefix=f"shop_inventory.{pool_name}",
                            )
                        )
        return issues

    def _validate_monster_loot_refs(self) -> list[str]:
        issues: list[str] = []
        item_ids = {
            str(item.get("id"))
            for item in self.items.list_all()
            if isinstance(item, Mapping) and item.get("id")
        }
        for monster in self.monsters.list_all():
            if not isinstance(monster, Mapping):
                continue
            monster_id = self._entry_id(monster)
            loot_table = monster.get("loot_table")
            if not isinstance(loot_table, list):
                continue
            issues.extend(
                self._validate_item_refs(
                    owner_id=monster_id,
                    owner_label="monster",
                    container=loot_table,
                    item_ids=item_ids,
                    prefix="loot_table",
                )
            )
        return issues

    def _validate_encounter_monster_refs(self) -> list[str]:
        """Check that encounter profile template IDs reference real monsters."""
        issues: list[str] = []
        monster_ids = {
            str(m.get("id"))
            for m in self.monsters.list_all()
            if isinstance(m, Mapping) and m.get("id")
        }
        for area in self.maps.list_all():
            if not isinstance(area, Mapping):
                continue
            area_id = self._entry_id(area)
            profile = area.get("encounter_profile")
            if not isinstance(profile, Mapping):
                continue
            templates = profile.get("templates")
            if not isinstance(templates, list):
                continue
            for index, template in enumerate(templates):
                if not isinstance(template, Mapping):
                    continue
                tid = self._coerce_non_empty_string(template.get("id"))
                if tid is not None and tid not in monster_ids:
                    issues.append(
                        f"map '{area_id}' encounter template[{index}] references unknown monster '{tid}'"
                    )
        return issues

    def _validate_character_class_refs(self) -> list[str]:
        """Check that character class references exist in ClassRegistry."""
        issues: list[str] = []
        class_ids = {
            str(c.get("id"))
            for c in self.classes.list_all()
            if isinstance(c, Mapping) and c.get("id")
        }
        for character in self.characters.list_all():
            if not isinstance(character, Mapping):
                continue
            character_id = self._entry_id(character)
            for field_name in ("character_class", "class_id"):
                cid = self._coerce_non_empty_string(character.get(field_name))
                if cid is not None and cid not in class_ids:
                    issues.append(
                        f"character '{character_id}' references unknown class '{cid}' via {field_name}"
                    )
        return issues

    def _validate_character_faction_refs(self) -> list[str]:
        """Check that character faction references exist in FactionRegistry."""
        issues: list[str] = []
        faction_ids = {
            str(f.get("id"))
            for f in self.factions.list_all()
            if isinstance(f, Mapping) and f.get("id")
        }
        for character in self.characters.list_all():
            if not isinstance(character, Mapping):
                continue
            character_id = self._entry_id(character)
            for field_name in ("faction", "faction_id"):
                fid = self._coerce_non_empty_string(character.get(field_name))
                if fid is not None and fid not in faction_ids:
                    issues.append(
                        f"character '{character_id}' references unknown faction '{fid}' via {field_name}"
                    )
        return issues

    def _validate_monster_skill_refs(self) -> list[str]:
        """Check that monster spell/ability references exist in SkillRegistry."""
        issues: list[str] = []
        skill_ids = {
            str(s.get("id"))
            for s in self.skills.list_all()
            if isinstance(s, Mapping) and s.get("id")
        }
        for monster in self.monsters.list_all():
            if not isinstance(monster, Mapping):
                continue
            monster_id = self._entry_id(monster)
            for field_name in ("spells", "abilities"):
                refs = monster.get(field_name)
                if not isinstance(refs, list):
                    continue
                for index, entry in enumerate(refs):
                    sid: str | None = None
                    if isinstance(entry, str):
                        sid = self._coerce_non_empty_string(entry)
                    elif isinstance(entry, Mapping):
                        sid = self._coerce_non_empty_string(entry.get("id"))
                    if sid is not None and sid not in skill_ids:
                        issues.append(
                            f"monster '{monster_id}' references unknown skill '{sid}' via {field_name}[{index}]"
                        )
        return issues

    def _validate_milestone_prerequisites(self) -> list[str]:
        """Check that milestone prerequisites reference existing milestones."""
        issues: list[str] = []
        all_milestone_ids = {
            str(m.get("id"))
            for m in self.quests.list_all()
            if isinstance(m, Mapping) and m.get("id")
        }
        for milestone in self.quests.list_all():
            if not isinstance(milestone, Mapping):
                continue
            milestone_id = self._entry_id(milestone)
            prerequisites = milestone.get("prerequisites")
            if not isinstance(prerequisites, list):
                continue
            for index, prereq in enumerate(prerequisites):
                prereq_id = self._coerce_non_empty_string(prereq)
                if prereq_id is not None and prereq_id not in all_milestone_ids:
                    issues.append(
                        f"milestone '{milestone_id}' prerequisite[{index}] references unknown milestone '{prereq_id}'"
                    )
        return issues

    def _validate_tag_value_refs(self) -> list[str]:
        """Check that tag values in all registries exist in TagRegistry."""
        issues: list[str] = []
        valid_tags = self.tags.all_tags()
        for reg_name, registry in self._registries.items():
            if reg_name == "tags":
                continue
            for entry in registry.list_all():
                if not isinstance(entry, Mapping):
                    continue
                entry_tags = entry.get("tags")
                if not isinstance(entry_tags, list):
                    continue
                entry_id = self._entry_id(entry)
                for index, tag in enumerate(entry_tags):
                    tag_str = self._coerce_non_empty_string(tag)
                    if tag_str is not None and tag_str not in valid_tags:
                        issues.append(
                            f"{reg_name} entry '{entry_id}' references unknown tag '{tag_str}' via tags[{index}]"
                        )
        return issues

    def _validate_item_refs(
        self,
        *,
        owner_id: str,
        owner_label: str,
        container: list[Any],
        item_ids: set[str],
        prefix: str,
    ) -> list[str]:
        issues: list[str] = []
        for index, entry in enumerate(container):
            if not isinstance(entry, Mapping):
                continue
            item_id = self._coerce_non_empty_string(entry.get("item_id"))
            if item_id is None or item_id in item_ids:
                continue
            issues.append(
                f"{owner_label} '{owner_id}' references unknown item '{item_id}' via {prefix}[{index}]"
            )
        return issues

    @staticmethod
    def _entry_id(entry: Mapping[str, Any]) -> str:
        normalized = WorldInstance._coerce_non_empty_string(entry.get("id"))
        if normalized is None:
            return "<unknown>"
        return normalized

    @staticmethod
    def _coerce_non_empty_string(value: Any) -> str | None:
        return ContentRegistry._coerce_non_empty_string(value)
