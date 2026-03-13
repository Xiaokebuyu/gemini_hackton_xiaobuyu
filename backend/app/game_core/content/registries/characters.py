"""CharacterRegistry implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry


# ------------------------------------------------------------------
# Typed data structures
# ------------------------------------------------------------------


@dataclass(slots=True)
class NpcAttack:
    """单次 NPC 攻击动作描述。"""

    name: str = ""
    hit_bonus: int = 0
    damage_dice: str = "1d4"
    damage_type: str = "physical"   # slashing / piercing / bludgeoning / fire / ...
    range: int = 1                  # 攻击范围（格）
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SecretEntry:
    """NPC 秘密条目，含独立 trust 门槛（NPC规范 §七.5）。"""

    content: str
    trust_threshold: int = 50
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ShopEntry:
    """商店库存条目（typed，替代旧 dict）。"""

    item_id: str = ""
    count: str = "1"            # 库存数量："3" / "1d4+1" / "unlimited"
    min_player_level: int = 0   # 0 = 始终可用
    restock: bool = True        # 售罄后是否在刷新时补货


@dataclass(slots=True)
class ShopInventory:
    """Typed economy shop inventory attached to a merchant character."""

    sell_markup: float | None = None
    buy_rate: float | None = None
    base_pool: list[ShopEntry] = field(default_factory=list)
    rotating_pool: list[ShopEntry] = field(default_factory=list)
    rotating_slots: int = 0
    refresh_on: str | list[str] | None = None
    level_scaling: bool = False


@dataclass(slots=True)
class CharacterTemplate:
    """Typed NPC / character template.

    TODO: 设计规范包含 NPC 战斗属性（base_hp / stats / attacks），
          供 NPC 直接参与战斗时使用。当前 NPC 战斗通过 MonsterRegistry 模板挂载，
          NPC 战斗系统独立深化时在此补齐对应字段。
    """

    id: str
    name: str = ""
    area_id: str = ""
    current_area: str = ""
    location_id: str = ""
    current_location: str = ""
    tags: list[str] = field(default_factory=list)
    schedule: dict[str, Any] | None = None
    character_class: str = ""
    class_id: str = ""
    faction: str = ""
    faction_id: str = ""
    personality: str = ""
    dialogue_style: str = ""
    speech_pattern: str = ""
    appearance: str = ""
    backstory: str = ""
    inventory: list[dict[str, Any]] = field(default_factory=list)
    shop: dict[str, Any] | None = None
    shop_inventory: ShopInventory | None = None
    base_disposition: dict[str, int] | None = None
    sell_markup: float | None = None
    buy_rate: float | None = None
    refresh_on: str | list[str] | None = None
    # NPC 战斗属性
    base_hp: int | None = None
    base_ac: int | None = None
    stats: dict[str, int] = field(default_factory=dict)
    level: int = 1
    proficiency_bonus: int = 2
    combat_capable: bool = False
    attacks: list[NpcAttack] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    secrets: list[SecretEntry] = field(default_factory=list)
    # 战斗 AI 个性：aggressive / defensive / protective / tactical
    ai_personality: str | None = None


# ------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------


class CharacterRegistry(ContentRegistry):
    """Registry for NPC templates."""

    def __init__(self) -> None:
        super().__init__("characters")
        self._items: dict[str, CharacterTemplate] = {}
        self._load_issues: list[str] = []

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def load(self, data: dict[str, Any]) -> None:
        self._items = {}
        self._load_issues = []
        coerced = self._coerce_dict_mapping(data)
        for char_id, raw in coerced.items():
            self._items[char_id] = self._build_template(char_id, raw)

    def get(self, content_id: str) -> CharacterTemplate | None:
        return self._items.get(content_id)

    def list_all(self) -> list[CharacterTemplate]:
        return list(self._items.values())

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_by_area(self, area_id: str) -> list[CharacterTemplate]:
        """Return characters whose area_id or current_area matches."""
        normalized = area_id.strip()
        results: list[CharacterTemplate] = []
        for item in self._items.values():
            if item.area_id.strip() == normalized or item.current_area.strip() == normalized:
                results.append(item)
        return results

    def get_merchants(self) -> list[CharacterTemplate]:
        """Return characters that have shop or shop_inventory."""
        return [
            item for item in self._items.values()
            if isinstance(item.shop, dict) or item.shop_inventory is not None
        ]

    def get_by_faction(self, faction_id: str) -> list[CharacterTemplate]:
        """Return characters matching the given faction/faction_id."""
        normalized = faction_id.strip()
        return [
            item for item in self._items.values()
            if item.faction.strip() == normalized
            or item.faction_id.strip() == normalized
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        issues = list(self._load_issues)
        for char_id, item in self._items.items():
            if item.shop_inventory is not None and item.shop_inventory.sell_markup is not None:
                if item.shop_inventory.sell_markup > 2.0:
                    issues.append(
                        f"character '{char_id}' balance warning: "
                        f"sell_markup {item.shop_inventory.sell_markup} exceeds 2.0"
                    )
        return issues

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_template(self, char_id: str, raw: dict[str, Any]) -> CharacterTemplate:
        """Construct a CharacterTemplate from raw dict, collecting format issues."""
        raw_id = raw.get("id")
        if not raw_id:
            self._load_issues.append(f"character '{char_id}' missing id")

        # -- Scalar fields with format validation --
        name = self._extract_optional_string(char_id, raw, "name")
        area_id = self._extract_optional_string(char_id, raw, "area_id")
        current_area = self._extract_optional_string(char_id, raw, "current_area")
        location_id = str(raw.get("location_id", "")).strip() if "location_id" in raw else ""
        current_location = str(raw.get("current_location", "")).strip() if "current_location" in raw else ""
        character_class = self._extract_optional_string(char_id, raw, "character_class")
        class_id = self._extract_optional_string(char_id, raw, "class_id")
        faction = self._extract_optional_string(char_id, raw, "faction")
        faction_id = self._extract_optional_string(char_id, raw, "faction_id")
        personality = str(raw.get("personality", "")).strip() if "personality" in raw else ""
        dialogue_style = str(raw.get("dialogue_style", "")).strip() if "dialogue_style" in raw else ""
        speech_pattern = str(raw.get("speech_pattern", "")).strip() if "speech_pattern" in raw else ""
        appearance = str(raw.get("appearance", "")).strip() if "appearance" in raw else ""
        backstory = str(raw.get("backstory", "")).strip() if "backstory" in raw else ""

        # -- Tags --
        tags: list[str] = []
        raw_tags = raw.get("tags")
        if raw_tags is not None:
            if isinstance(raw_tags, list):
                tags = [str(t) for t in raw_tags]
            else:
                self._load_issues.append(f"character '{char_id}' has invalid tags")

        # -- Inventory --
        inventory = self._load_inventory(char_id, raw)

        # -- Simple shop --
        shop = self._load_shop(char_id, raw)

        # -- Economy shop_inventory --
        shop_inventory = self._load_shop_inventory(char_id, raw)

        # -- Base disposition (fallback initial_disposition) --
        base_disposition = self._load_disposition(raw)

        # -- Top-level economy fallbacks --
        sell_markup = self._coerce_float(raw.get("sell_markup")) if "sell_markup" in raw else None
        buy_rate = self._coerce_float(raw.get("buy_rate")) if "buy_rate" in raw else None
        raw_refresh = raw.get("refresh_on")
        refresh_on: str | list[str] | None = None
        if isinstance(raw_refresh, str):
            refresh_on = raw_refresh
        elif isinstance(raw_refresh, list):
            refresh_on = [str(r) for r in raw_refresh]

        # -- NPC 战斗属性 --
        base_hp = self._coerce_non_negative_int(raw.get("base_hp"))
        base_ac = self._coerce_non_negative_int(raw.get("base_ac"))
        level = self._coerce_positive_int(raw.get("level")) or 1
        proficiency_bonus = self._coerce_non_negative_int(raw.get("proficiency_bonus")) or 2
        attacks = self._build_npc_attacks(char_id, raw)
        # combat_capable: 显式设置优先；否则有 attacks 或 base_hp 自动推导
        raw_cc = raw.get("combat_capable")
        if raw_cc is not None:
            combat_capable = bool(raw_cc)
        else:
            combat_capable = bool(attacks) or base_hp is not None
        raw_stats = raw.get("stats")
        stats: dict[str, int] = {}
        if isinstance(raw_stats, Mapping):
            for attr in ("str", "dex", "con", "int", "wis", "cha"):
                if attr in raw_stats:
                    val = self._coerce_non_negative_int(raw_stats[attr])
                    if val is not None:
                        stats[attr] = val
        raw_skills = raw.get("skills")
        skills: list[str] = (
            [str(s) for s in raw_skills if isinstance(s, str) and str(s).strip()]
            if isinstance(raw_skills, list) else []
        )
        raw_secrets = raw.get("secrets")
        secrets: list[SecretEntry] = []
        if isinstance(raw_secrets, list):
            for s in raw_secrets:
                if isinstance(s, str) and s.strip():
                    secrets.append(SecretEntry(content=s.strip()))
                elif isinstance(s, Mapping):
                    secrets.append(SecretEntry(
                        content=str(s.get("content", "")),
                        trust_threshold=int(s.get("trust_threshold", 50)),
                        tags=[str(t) for t in s.get("tags", []) if isinstance(t, str)],
                    ))

        # -- Schedule --
        raw_schedule = raw.get("schedule")
        schedule: dict[str, Any] | None = None
        if isinstance(raw_schedule, Mapping):
            schedule = {}
            for k, v in raw_schedule.items():
                if isinstance(v, Mapping):
                    schedule[str(k)] = dict(v)
                else:
                    schedule[str(k)] = str(v)

        # -- AI personality (combat AI) --
        raw_ai_personality = raw.get("ai_personality")
        ai_personality: str | None = None
        if raw_ai_personality is not None:
            val = self._coerce_non_empty_string(raw_ai_personality)
            if val is not None:
                ai_personality = val
            else:
                self._load_issues.append(f"character '{char_id}' has invalid ai_personality")

        return CharacterTemplate(
            id=str(raw_id or char_id),
            name=name,
            area_id=area_id,
            current_area=current_area,
            location_id=location_id,
            current_location=current_location,
            tags=tags,
            character_class=character_class,
            class_id=class_id,
            faction=faction,
            faction_id=faction_id,
            personality=personality,
            dialogue_style=dialogue_style,
            speech_pattern=speech_pattern,
            appearance=appearance,
            backstory=backstory,
            schedule=schedule,
            inventory=inventory,
            shop=shop,
            shop_inventory=shop_inventory,
            base_disposition=base_disposition,
            sell_markup=sell_markup,
            buy_rate=buy_rate,
            refresh_on=refresh_on,
            base_hp=base_hp,
            base_ac=base_ac,
            stats=stats,
            level=level,
            proficiency_bonus=proficiency_bonus,
            combat_capable=combat_capable,
            attacks=attacks,
            skills=skills,
            secrets=secrets,
            ai_personality=ai_personality,
        )

    def _build_npc_attacks(self, char_id: str, raw: dict[str, Any]) -> list[NpcAttack]:
        raw_attacks = raw.get("attacks")
        if raw_attacks is None:
            return []
        if not isinstance(raw_attacks, list):
            self._load_issues.append(f"character '{char_id}' has invalid attacks")
            return []
        result: list[NpcAttack] = []
        for idx, entry in enumerate(raw_attacks):
            if not isinstance(entry, Mapping):
                self._load_issues.append(
                    f"character '{char_id}' attacks[{idx}] must be a mapping"
                )
                continue
            name = self._coerce_non_empty_string(entry.get("name"))
            if name is None:
                self._load_issues.append(
                    f"character '{char_id}' attacks[{idx}] missing name"
                )
                continue
            result.append(NpcAttack(
                name=name,
                hit_bonus=int(entry["hit_bonus"]) if entry.get("hit_bonus") is not None else 0,
                damage_dice=str(entry.get("damage_dice", "1d4")).strip() or "1d4",
                damage_type=str(entry.get("damage_type", "physical")).strip() or "physical",
                range=int(entry["range"]) if entry.get("range") is not None else 1,
                tags=[str(t) for t in entry["tags"] if isinstance(t, str)]
                     if isinstance(entry.get("tags"), list) else [],
            ))
        return result

    def _extract_optional_string(
        self, char_id: str, raw: dict[str, Any], field_name: str,
    ) -> str:
        """Extract a string field, validating non-empty when present."""
        if field_name not in raw:
            return ""
        value = self._coerce_non_empty_string(raw.get(field_name))
        if value is None:
            self._load_issues.append(f"character '{char_id}' has invalid {field_name}")
            return ""
        return value

    def _load_inventory(self, char_id: str, raw: dict[str, Any]) -> list[dict[str, Any]]:
        raw_inv = raw.get("inventory")
        if raw_inv is None:
            return []
        if not isinstance(raw_inv, list):
            self._load_issues.append(f"character '{char_id}' has invalid inventory")
            return []
        result: list[dict[str, Any]] = []
        for index, entry in enumerate(raw_inv):
            if not isinstance(entry, Mapping):
                self._load_issues.append(
                    f"character '{char_id}' inventory[{index}] must be a mapping"
                )
                continue
            if self._coerce_non_empty_string(entry.get("item_id")) is None:
                self._load_issues.append(
                    f"character '{char_id}' inventory[{index}] missing item_id"
                )
            if "count" in entry and self._coerce_non_negative_int(entry.get("count")) is None:
                self._load_issues.append(
                    f"character '{char_id}' inventory[{index}] has invalid count"
                )
            result.append(dict(entry))
        return result

    def _load_shop(self, char_id: str, raw: dict[str, Any]) -> dict[str, Any] | None:
        shop = raw.get("shop")
        if shop is None:
            return None
        if not isinstance(shop, Mapping):
            self._load_issues.append(f"character '{char_id}' has invalid shop")
            return None
        shop_dict = dict(shop)
        shop_inv_list = shop.get("inventory")
        if shop_inv_list is not None:
            if not isinstance(shop_inv_list, list):
                self._load_issues.append(f"character '{char_id}' has invalid shop inventory")
            else:
                for index, entry in enumerate(shop_inv_list):
                    if not isinstance(entry, Mapping):
                        self._load_issues.append(
                            f"character '{char_id}' shop.inventory[{index}] must be a mapping"
                        )
                        continue
                    if self._coerce_non_empty_string(entry.get("item_id")) is None:
                        self._load_issues.append(
                            f"character '{char_id}' shop.inventory[{index}] missing item_id"
                        )
                    if "price" in entry and self._coerce_non_negative_int(entry.get("price")) is None:
                        self._load_issues.append(
                            f"character '{char_id}' shop.inventory[{index}] has invalid price"
                        )
        return shop_dict

    def _load_shop_inventory(self, char_id: str, raw: dict[str, Any]) -> ShopInventory | None:
        raw_si = raw.get("shop_inventory")
        if raw_si is None:
            return None
        if not isinstance(raw_si, Mapping):
            self._load_issues.append(f"character '{char_id}' has invalid shop_inventory")
            return None

        # sell_markup
        sell_markup: float | None = None
        if "sell_markup" in raw_si:
            markup = self._coerce_float(raw_si.get("sell_markup"))
            if markup is None or markup < 0:
                self._load_issues.append(
                    f"character '{char_id}' shop_inventory has invalid sell_markup"
                )
            else:
                sell_markup = markup

        # buy_rate
        buy_rate: float | None = None
        if "buy_rate" in raw_si:
            rate = self._coerce_float(raw_si.get("buy_rate"))
            if rate is None or rate < 0.0 or rate > 1.0:
                self._load_issues.append(
                    f"character '{char_id}' shop_inventory has invalid buy_rate"
                )
            else:
                buy_rate = rate

        # rotating_slots
        rotating_slots = 0
        if "rotating_slots" in raw_si:
            rs = self._coerce_non_negative_int(raw_si.get("rotating_slots"))
            if rs is None:
                self._load_issues.append(
                    f"character '{char_id}' shop_inventory has invalid rotating_slots"
                )
            else:
                rotating_slots = rs

        # pools
        base_pool = self._load_pool(char_id, raw_si, "base_pool")
        rotating_pool = self._load_pool(char_id, raw_si, "rotating_pool")

        # refresh_on
        refresh_on: str | list[str] | None = None
        raw_refresh = raw_si.get("refresh_on")
        if raw_refresh is not None:
            if isinstance(raw_refresh, str):
                if self._coerce_non_empty_string(raw_refresh) is None:
                    self._load_issues.append(
                        f"character '{char_id}' shop_inventory has invalid refresh_on"
                    )
                else:
                    refresh_on = raw_refresh
            elif isinstance(raw_refresh, list):
                refresh_on = []
                for index, entry in enumerate(raw_refresh):
                    if self._coerce_non_empty_string(entry) is None:
                        self._load_issues.append(
                            f"character '{char_id}' shop_inventory.refresh_on[{index}] "
                            f"must be a non-empty string"
                        )
                    else:
                        refresh_on.append(str(entry))
            else:
                self._load_issues.append(
                    f"character '{char_id}' shop_inventory has invalid refresh_on"
                )

        return ShopInventory(
            sell_markup=sell_markup,
            buy_rate=buy_rate,
            base_pool=base_pool,
            rotating_pool=rotating_pool,
            rotating_slots=rotating_slots,
            refresh_on=refresh_on,
        )

    def _load_pool(
        self, char_id: str, raw_si: Mapping[str, Any], pool_name: str,
    ) -> list[ShopEntry]:
        pool = raw_si.get(pool_name)
        if pool is None:
            return []
        if not isinstance(pool, list):
            self._load_issues.append(
                f"character '{char_id}' shop_inventory has invalid {pool_name}"
            )
            return []
        result: list[ShopEntry] = []
        for index, entry in enumerate(pool):
            if not isinstance(entry, Mapping):
                self._load_issues.append(
                    f"character '{char_id}' shop_inventory.{pool_name}[{index}] must be a mapping"
                )
                continue
            item_id = self._coerce_non_empty_string(entry.get("item_id"))
            if item_id is None:
                self._load_issues.append(
                    f"character '{char_id}' shop_inventory.{pool_name}[{index}] missing item_id"
                )
                continue
            result.append(ShopEntry(
                item_id=item_id,
                count=str(entry.get("count", "1")).strip() or "1",
                min_player_level=int(entry["min_player_level"])
                                 if entry.get("min_player_level") is not None else 0,
                restock=bool(entry.get("restock", True)),
            ))
        return result

    @staticmethod
    def _load_disposition(raw: dict[str, Any]) -> dict[str, int] | None:
        for field_name in ("base_disposition", "initial_disposition"):
            disp = raw.get(field_name)
            if isinstance(disp, Mapping):
                return dict(disp)
        return None
