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
        if self.has_registry("maps") and self.has_registry("characters"):
            issues.extend(self._validate_sub_location_npc_refs())
        if self.has_registry("maps") and self.has_registry("monsters"):
            issues.extend(self._validate_hostile_pool_monster_refs())
        if self.has_registry("maps") and self.has_registry("items"):
            issues.extend(self._validate_sub_location_loot_refs())
        if self.has_registry("maps"):
            issues.extend(self._validate_discovery_reward_refs())
        if (self.has_registry("quests") and self.has_registry("characters")
                and self.has_registry("maps")):
            issues.extend(self._validate_milestone_npc_location_refs())
        if self.has_registry("factions") and self.has_registry("characters"):
            issues.extend(self._validate_faction_member_refs())
        if self.has_registry("factions") and self.has_registry("maps"):
            issues.extend(self._validate_faction_area_refs())
        if (self.has_registry("lore") and self.has_registry("maps")
                and self.has_registry("factions")):
            issues.extend(self._validate_world_rule_scope_refs())
        return issues

    def _validate_character_map_refs(self) -> list[str]:
        issues: list[str] = []
        map_ids = {item.id for item in self.maps.list_all() if item.id}
        for character in self.characters.list_all():
            character_id = character.id or "<unknown>"
            for field_name in ("area_id", "current_area"):
                area_id = self._coerce_non_empty_string(getattr(character, field_name, None))
                if area_id is None:
                    continue
                if area_id not in map_ids:
                    issues.append(
                        f"character '{character_id}' references unknown map '{area_id}' via {field_name}"
                    )
        return issues

    def _validate_character_item_refs(self) -> list[str]:
        issues: list[str] = []
        item_ids = self._collect_entry_ids(self.items)
        for character in self.characters.list_all():
            character_id = character.id or "<unknown>"
            if character.inventory:
                issues.extend(
                    self._validate_item_refs(
                        owner_id=character_id,
                        owner_label="character",
                        container=character.inventory,
                        item_ids=item_ids,
                        prefix="inventory",
                    )
                )
            if isinstance(character.shop, dict):
                shop_inv_list = character.shop.get("inventory")
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
            if character.shop_inventory is not None:
                for pool_name in ("base_pool", "rotating_pool"):
                    pool = getattr(character.shop_inventory, pool_name, [])
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
        item_ids = self._collect_entry_ids(self.items)
        for monster in self.monsters.list_all():
            monster_id = monster.id or "<unknown>"
            for index, loot_entry in enumerate(monster.loot_table):
                if loot_entry.item_id and loot_entry.item_id not in item_ids:
                    issues.append(
                        f"monster '{monster_id}' references unknown item '{loot_entry.item_id}' via loot_table[{index}]"
                    )
        return issues

    def _validate_encounter_monster_refs(self) -> list[str]:
        """Check that encounter_table entries reference real monsters."""
        issues: list[str] = []
        monster_ids = {m.id for m in self.monsters.list_all() if m.id}
        for area in self.maps.list_all():
            area_id = area.id or "<unknown>"
            for idx, entry in enumerate(area.encounter_table):
                for mid in entry.monster_ids:
                    if mid not in monster_ids:
                        issues.append(
                            f"map '{area_id}' encounter_table[{idx}] references unknown monster '{mid}'"
                        )
        return issues

    def _validate_character_class_refs(self) -> list[str]:
        """Check that character class references exist in ClassRegistry."""
        issues: list[str] = []
        class_ids = self._collect_entry_ids(self.classes)
        for character in self.characters.list_all():
            character_id = character.id or "<unknown>"
            for field_name in ("character_class", "class_id"):
                cid = self._coerce_non_empty_string(getattr(character, field_name, None))
                if cid is not None and cid not in class_ids:
                    issues.append(
                        f"character '{character_id}' references unknown class '{cid}' via {field_name}"
                    )
        return issues

    def _validate_character_faction_refs(self) -> list[str]:
        """Check that character faction references exist in FactionRegistry."""
        issues: list[str] = []
        faction_ids = {f.id for f in self.factions.list_all() if f.id}
        for character in self.characters.list_all():
            character_id = character.id or "<unknown>"
            for field_name in ("faction", "faction_id"):
                fid = self._coerce_non_empty_string(getattr(character, field_name, None))
                if fid is not None and fid not in faction_ids:
                    issues.append(
                        f"character '{character_id}' references unknown faction '{fid}' via {field_name}"
                    )
        return issues

    def _validate_monster_skill_refs(self) -> list[str]:
        """Check that monster spell/ability references exist in SkillRegistry."""
        issues: list[str] = []
        skill_ids = self._collect_entry_ids(self.skills)
        for monster in self.monsters.list_all():
            monster_id = monster.id or "<unknown>"
            for field_name, refs in (("spells", monster.spells), ("abilities", monster.ability_refs)):
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
        all_milestone_ids = {m.id for m in self.quests.list_all() if m.id}
        for milestone in self.quests.list_all():
            milestone_id = milestone.id or "<unknown>"
            for index, prereq in enumerate(milestone.prerequisites):
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
                if isinstance(entry, Mapping):
                    entry_tags = entry.get("tags")
                    entry_id = self._entry_id(entry)
                elif hasattr(entry, "tags"):
                    entry_tags = entry.tags
                    entry_id = getattr(entry, "id", "<unknown>")
                else:
                    continue
                if not isinstance(entry_tags, list):
                    continue
                for index, tag in enumerate(entry_tags):
                    tag_str = self._coerce_non_empty_string(tag)
                    if tag_str is not None and tag_str not in valid_tags:
                        issues.append(
                            f"{reg_name} entry '{entry_id}' references unknown tag '{tag_str}' via tags[{index}]"
                        )
        return issues

    def _validate_sub_location_npc_refs(self) -> list[str]:
        """Check that sub_location.resident_npcs reference existing characters."""
        issues: list[str] = []
        for area in self.maps.list_all():
            area_id = area.id or "<unknown>"
            for sub_id, sub in area.sub_locations.items():
                for npc_id in sub.resident_npcs:
                    if self.characters.get(npc_id) is None:
                        issues.append(
                            f"area '{area_id}' sub_location '{sub_id}' references unknown character '{npc_id}'"
                        )
        return issues

    def _validate_hostile_pool_monster_refs(self) -> list[str]:
        """Check that hostile_pool monster_ids reference existing monsters."""
        issues: list[str] = []
        monster_ids = {m.id for m in self.monsters.list_all() if m.id}
        for area in self.maps.list_all():
            area_id = area.id or "<unknown>"
            if not area.hostile_pool:
                continue
            for ht in area.hostile_pool:
                ht_id = ht.id or "<unknown>"
                for group in ht.hostile_config.hostile_groups:
                    for mid in group.monster_ids:
                        if mid not in monster_ids:
                            issues.append(
                                f"area '{area_id}' hostile '{ht_id}' references unknown monster '{mid}'"
                            )
        return issues

    def _validate_sub_location_loot_refs(self) -> list[str]:
        """Check that interactable container loot item ids reference existing items."""
        issues: list[str] = []
        item_ids = self._collect_entry_ids(self.items)
        for area in self.maps.list_all():
            area_id = area.id or "<unknown>"
            for sub_id, sub in area.sub_locations.items():
                for ia in sub.interactables:
                    if ia.container_data is None or not ia.container_data.loot.items:
                        continue
                    for idx, entry in enumerate(ia.container_data.loot.items):
                        if isinstance(entry, str):
                            iid = self._coerce_non_empty_string(entry)
                        elif isinstance(entry, Mapping):
                            iid = self._coerce_non_empty_string(entry.get("item_id"))
                        else:
                            iid = None
                        if iid and iid not in item_ids:
                            issues.append(
                                f"area '{area_id}' interactable '{ia.id}' loot[{idx}] references unknown item '{iid}'"
                            )
        return issues

    def _validate_discovery_reward_refs(self) -> list[str]:
        """Check that discovery rewards of type 'item' reference existing items."""
        issues: list[str] = []
        item_ids = self._collect_entry_ids(self.items) if self.has_registry("items") else set()
        for area in self.maps.list_all():
            area_id = area.id or "<unknown>"
            for disc in area.discoveries:
                reward = disc.reward
                if not isinstance(reward, Mapping) or reward.get("type") != "item":
                    continue
                ref_id = self._coerce_non_empty_string(reward.get("id"))
                if ref_id and ref_id not in item_ids:
                    issues.append(
                        f"area '{area_id}' discovery '{disc.id}' reward references unknown item '{ref_id}'"
                    )
        return issues

    def _validate_milestone_npc_location_refs(self) -> list[str]:
        """Check milestone involved_npcs and involved_locations references."""
        issues: list[str] = []
        char_ids = self._collect_entry_ids(self.characters)
        map_ids = {m.id for m in self.maps.list_all() if m.id}
        for milestone in self.quests.list_all():
            milestone_id = milestone.id or "<unknown>"
            for npc_id in milestone.involved_npcs:
                if npc_id not in char_ids:
                    issues.append(
                        f"milestone '{milestone_id}' references unknown character '{npc_id}'"
                    )
            for loc_id in milestone.involved_locations:
                if loc_id not in map_ids:
                    issues.append(
                        f"milestone '{milestone_id}' references unknown location '{loc_id}'"
                    )
        return issues

    def _validate_faction_member_refs(self) -> list[str]:
        """Check faction leader_id and member_ids reference existing characters."""
        issues: list[str] = []
        char_ids = self._collect_entry_ids(self.characters)
        for faction in self.factions.list_all():
            faction_id = faction.id or "<unknown>"
            if faction.leader_id and faction.leader_id not in char_ids:
                issues.append(
                    f"faction '{faction_id}' references unknown leader '{faction.leader_id}'"
                )
            for mid in faction.member_ids:
                if mid not in char_ids:
                    issues.append(
                        f"faction '{faction_id}' references unknown member '{mid}'"
                    )
        return issues

    def _validate_faction_area_refs(self) -> list[str]:
        """Check faction influence_areas reference existing map areas."""
        issues: list[str] = []
        map_ids = {m.id for m in self.maps.list_all() if m.id}
        for faction in self.factions.list_all():
            faction_id = faction.id or "<unknown>"
            for area_id in faction.influence_areas:
                if area_id not in map_ids:
                    issues.append(
                        f"faction '{faction_id}' references unknown influence_area '{area_id}'"
                    )
        return issues

    def _validate_world_rule_scope_refs(self) -> list[str]:
        """Check WorldRule scope_id references existing area or faction."""
        issues: list[str] = []
        map_ids = {m.id for m in self.maps.list_all() if m.id}
        faction_ids = {f.id for f in self.factions.list_all() if f.id}
        for rule in self.lore.list_rules():
            if not rule.scope_id:
                continue
            if rule.scope == "area" and rule.scope_id not in map_ids:
                issues.append(
                    f"rule '{rule.id}' references unknown area '{rule.scope_id}'"
                )
            elif rule.scope == "faction" and rule.scope_id not in faction_ids:
                issues.append(
                    f"rule '{rule.id}' references unknown faction '{rule.scope_id}'"
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
            if isinstance(entry, Mapping):
                item_id = self._coerce_non_empty_string(entry.get("item_id"))
            else:
                item_id = self._coerce_non_empty_string(getattr(entry, "item_id", None))
            if item_id is None or item_id in item_ids:
                continue
            issues.append(
                f"{owner_label} '{owner_id}' references unknown item '{item_id}' via {prefix}[{index}]"
            )
        return issues

    @staticmethod
    def _entry_id(entry: Any) -> str:
        if isinstance(entry, Mapping):
            raw = entry.get("id")
        else:
            raw = getattr(entry, "id", None)
        normalized = WorldInstance._coerce_non_empty_string(raw)
        if normalized is None:
            return "<unknown>"
        return normalized

    @staticmethod
    def _collect_entry_ids(registry: ContentRegistry) -> set[str]:
        """Collect all entry IDs from a registry (dict or dataclass entries)."""
        ids: set[str] = set()
        for entry in registry.list_all():
            if isinstance(entry, Mapping):
                raw = entry.get("id")
            else:
                raw = getattr(entry, "id", None)
            if raw:
                ids.add(str(raw))
        return ids

    @staticmethod
    def _coerce_non_empty_string(value: Any) -> str | None:
        return ContentRegistry._coerce_non_empty_string(value)
