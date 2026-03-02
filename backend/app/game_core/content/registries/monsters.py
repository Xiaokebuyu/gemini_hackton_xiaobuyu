"""MonsterRegistry implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry

_CREATURE_TYPES = frozenset({
    "humanoid", "beast", "undead", "fiend", "dragon", "construct",
    "aberration", "celestial", "elemental", "fey", "giant",
    "monstrosity", "ooze", "plant", "swarm",
})

_ABILITY_NAMES = ("str", "dex", "con", "int", "wis", "cha")

# CR-to-stat heuristic ranges for balance warnings.
_CR_HP_RANGES = [(1, 1, 50), (5, 20, 150), (10, 50, 250), (999, 100, 500)]
_CR_AC_RANGES = [(5, 10, 18), (10, 13, 20), (999, 15, 22)]


@dataclass(slots=True)
class MonsterAttack:
    """A single attack entry."""

    name: str = ""
    damage_dice: str = "1d4"       # 伤害骰格式：NdM / NdM+B / NdM-B
    hit_bonus: int = 0              # 命中修正（加到 d20 上）
    damage_type: str = "physical"   # 伤害类型（占位，防御计算深化时消费）


@dataclass(slots=True)
class LootEntry:
    """A single loot table entry."""

    item_id: str = ""
    chance: float = 1.0
    count: int = 1


@dataclass(slots=True)
class MonsterTemplate:
    """Typed monster definition."""

    id: str
    name: str = ""
    hp: int | None = None
    max_hp: int | None = None
    ac: int | None = None
    cr: float | None = None
    creature_type: str = ""
    abilities: dict[str, int] = field(default_factory=dict)
    resistances: list[str] = field(default_factory=list)
    immunities: list[str] = field(default_factory=list)
    attacks: list[MonsterAttack] = field(default_factory=list)
    gold_drop: int | None = None
    loot_table: list[LootEntry] = field(default_factory=list)
    spells: list[Any] = field(default_factory=list)
    ability_refs: list[Any] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    ai_personality: str = "aggressive"  # aggressive / defensive / cowardly
    flee_threshold: float = 0.0         # 逃跑阈值（hp/max_hp 比例），0.0 = 不逃
    xp_reward: int = 0                  # 击杀 XP（TODO: speed 等字段待数据管线就绪后补）


class MonsterRegistry(ContentRegistry):
    """Registry for monster templates."""

    def __init__(self) -> None:
        super().__init__("monsters")
        self._items: dict[str, MonsterTemplate] = {}
        self._load_issues: list[str] = []

    def load(self, data: dict[str, Any]) -> None:
        self._items = {}
        self._load_issues = []
        coerced = self._coerce_dict_mapping(data)
        for mid, raw in coerced.items():
            # -- Format validation → _load_issues --
            if not raw.get("id"):
                self._load_issues.append(f"monster '{mid}' missing id")

            raw_name = raw.get("name")
            if raw_name is not None and self._coerce_non_empty_string(raw_name) is None:
                self._load_issues.append(f"monster '{mid}' has invalid name")

            for fn in ("hp", "max_hp", "ac"):
                if fn in raw and self._coerce_positive_int(raw.get(fn)) is None:
                    self._load_issues.append(f"monster '{mid}' has invalid {fn}")

            for fn in ("gold_drop", "gold", "gold_reward"):
                if fn in raw and self._coerce_non_negative_int(raw.get(fn)) is None:
                    self._load_issues.append(f"monster '{mid}' has invalid {fn}")
            # Merge gold / gold_reward aliases into gold_drop during load
            _gold_drop: int | None = None
            for fn in ("gold_drop", "gold", "gold_reward"):
                val = self._coerce_non_negative_int(raw.get(fn))
                if val is not None:
                    _gold_drop = val
                    break

            raw_cr = raw.get("cr")
            cr_val: float | None = None
            if raw_cr is not None:
                cr_val = self._coerce_float(raw_cr)
                if cr_val is None or cr_val < 0:
                    self._load_issues.append(f"monster '{mid}' has invalid cr")
                    cr_val = None

            if "creature_type" in raw:
                ct = self._coerce_non_empty_string(raw.get("creature_type"))
                if ct is None or ct.lower() not in _CREATURE_TYPES:
                    self._load_issues.append(f"monster '{mid}' has invalid creature_type")

            abilities = self._load_abilities(mid, raw)
            resistances = self._load_list_of_strings(mid, raw, "resistances")
            immunities = self._load_list_of_strings(mid, raw, "immunities")
            attacks = self._load_attacks(mid, raw)
            loot_table = self._load_loot_table(mid, raw)

            raw_tags = raw.get("tags")
            tags: list[str] = []
            if isinstance(raw_tags, list):
                tags = [str(t) for t in raw_tags if isinstance(t, str) and str(t).strip()]

            raw_spells = raw.get("spells")
            spells: list[Any] = list(raw_spells) if isinstance(raw_spells, list) else []

            # abilities can be a stat block (Mapping) or a list of skill refs
            raw_abilities = raw.get("abilities")
            ability_refs: list[Any] = (
                list(raw_abilities) if isinstance(raw_abilities, list) else []
            )

            # ai_personality
            ai_personality = "aggressive"
            raw_ai = self._coerce_non_empty_string(raw.get("ai_personality"))
            if raw_ai and raw_ai in ("aggressive", "defensive", "cowardly"):
                ai_personality = raw_ai
            elif raw_ai:
                self._load_issues.append(
                    f"monster '{mid}' has invalid ai_personality '{raw_ai}', using 'aggressive'"
                )

            # flee_threshold
            flee_threshold = 0.0
            if "flee_threshold" in raw:
                ft = self._coerce_float(raw.get("flee_threshold"))
                if ft is not None and 0.0 <= ft <= 1.0:
                    flee_threshold = ft
                else:
                    self._load_issues.append(
                        f"monster '{mid}' has invalid flee_threshold, using 0.0"
                    )

            # xp_reward
            xp_reward = 0
            if "xp_reward" in raw:
                xr = self._coerce_non_negative_int(raw.get("xp_reward"))
                if xr is not None:
                    xp_reward = xr
                else:
                    self._load_issues.append(
                        f"monster '{mid}' has invalid xp_reward, using 0"
                    )

            self._items[mid] = MonsterTemplate(
                id=str(raw.get("id", mid)),
                name=str(raw.get("name") or ""),
                hp=self._coerce_positive_int(raw.get("hp")),
                max_hp=self._coerce_positive_int(raw.get("max_hp")),
                ac=self._coerce_positive_int(raw.get("ac")),
                cr=cr_val,
                creature_type=str(raw.get("creature_type") or "").strip().lower(),
                abilities=abilities,
                resistances=resistances,
                immunities=immunities,
                attacks=attacks,
                gold_drop=_gold_drop,
                loot_table=loot_table,
                spells=spells,
                ability_refs=ability_refs,
                tags=tags,
                ai_personality=ai_personality,
                flee_threshold=flee_threshold,
                xp_reward=xp_reward,
            )

    def get(self, content_id: str) -> MonsterTemplate | None:
        return self._items.get(content_id)

    def list_all(self) -> list[MonsterTemplate]:
        return list(self._items.values())

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_by_cr(self, min_cr: float = 0, max_cr: float = 999) -> list[MonsterTemplate]:
        """Return monsters whose CR falls within [min_cr, max_cr]."""
        return [
            m for m in self._items.values()
            if m.cr is not None and min_cr <= m.cr <= max_cr
        ]

    def get_by_type(self, creature_type: str) -> list[MonsterTemplate]:
        """Return monsters matching the given creature_type."""
        normalized = creature_type.strip().lower()
        return [
            m for m in self._items.values()
            if m.creature_type == normalized
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        issues = list(self._load_issues)
        for mid, monster in self._items.items():
            self._check_balance(mid, monster, issues)
        return issues

    # ------------------------------------------------------------------
    # Load helpers (format validation + dataclass construction)
    # ------------------------------------------------------------------

    def _load_abilities(
        self, mid: str, raw: dict[str, Any],
    ) -> dict[str, int]:
        raw_abilities = raw.get("abilities")
        if raw_abilities is None:
            return {}
        if not isinstance(raw_abilities, Mapping):
            self._load_issues.append(f"monster '{mid}' has invalid abilities")
            return {}
        result: dict[str, int] = {}
        for attr in _ABILITY_NAMES:
            if attr not in raw_abilities:
                continue
            val = self._coerce_positive_int(raw_abilities.get(attr))
            if val is None:
                self._load_issues.append(f"monster '{mid}' abilities has invalid {attr}")
            else:
                result[attr] = val
        return result

    def _load_list_of_strings(
        self, mid: str, raw: dict[str, Any], field_name: str,
    ) -> list[str]:
        value = raw.get(field_name)
        if value is None:
            return []
        if not isinstance(value, list):
            self._load_issues.append(f"monster '{mid}' has invalid {field_name}")
            return []
        result: list[str] = []
        for index, entry in enumerate(value):
            s = self._coerce_non_empty_string(entry)
            if s is None:
                self._load_issues.append(
                    f"monster '{mid}' {field_name}[{index}] must be a non-empty string"
                )
            else:
                result.append(s)
        return result

    def _load_attacks(
        self, mid: str, raw: dict[str, Any],
    ) -> list[MonsterAttack]:
        raw_attacks = raw.get("attacks")
        if raw_attacks is None:
            return []
        if not isinstance(raw_attacks, list):
            self._load_issues.append(f"monster '{mid}' has invalid attacks")
            return []
        result: list[MonsterAttack] = []
        for index, entry in enumerate(raw_attacks):
            if not isinstance(entry, Mapping):
                self._load_issues.append(
                    f"monster '{mid}' attacks[{index}] must be a mapping"
                )
                continue
            name = self._coerce_non_empty_string(entry.get("name"))
            if name is None:
                self._load_issues.append(
                    f"monster '{mid}' attacks[{index}] has invalid name"
                )
                continue
            # damage_dice
            damage_dice = "1d4"
            if "damage_dice" in entry:
                dd = self._coerce_non_empty_string(entry.get("damage_dice"))
                if dd:
                    damage_dice = dd
            # hit_bonus（可为负数，直接 int 转换）
            hit_bonus = 0
            if "hit_bonus" in entry:
                hb_raw = entry.get("hit_bonus")
                if hb_raw is not None:
                    try:
                        hit_bonus = int(hb_raw)
                    except (TypeError, ValueError):
                        self._load_issues.append(
                            f"monster '{mid}' attacks[{index}] has invalid hit_bonus"
                        )
            # damage_type
            damage_type = "physical"
            if "damage_type" in entry:
                dt = self._coerce_non_empty_string(entry.get("damage_type"))
                if dt:
                    damage_type = dt
            result.append(MonsterAttack(
                name=name,
                damage_dice=damage_dice,
                hit_bonus=hit_bonus,
                damage_type=damage_type,
            ))
        return result

    def _load_loot_table(
        self, mid: str, raw: dict[str, Any],
    ) -> list[LootEntry]:
        raw_loot = raw.get("loot_table")
        if raw_loot is None:
            return []
        if not isinstance(raw_loot, list):
            self._load_issues.append(f"monster '{mid}' has invalid loot_table")
            return []
        result: list[LootEntry] = []
        for index, entry in enumerate(raw_loot):
            if not isinstance(entry, Mapping):
                self._load_issues.append(
                    f"monster '{mid}' loot_table[{index}] must be a mapping"
                )
                continue
            # item_id
            item_id_val = ""
            if "item_id" in entry:
                s = self._coerce_non_empty_string(entry.get("item_id"))
                if s is None:
                    self._load_issues.append(
                        f"monster '{mid}' loot_table[{index}] has invalid item_id"
                    )
                else:
                    item_id_val = s
            # count
            count_val = 1
            if "count" in entry:
                c = self._coerce_non_negative_int(entry.get("count"))
                if c is None:
                    self._load_issues.append(
                        f"monster '{mid}' loot_table[{index}] has invalid count"
                    )
                else:
                    count_val = c
            # chance
            chance_val = 1.0
            if "chance" in entry:
                ch = self._coerce_float(entry.get("chance"))
                if ch is None or ch < 0.0 or ch > 1.0:
                    self._load_issues.append(
                        f"monster '{mid}' loot_table[{index}] has invalid chance"
                    )
                else:
                    chance_val = ch
            result.append(LootEntry(
                item_id=item_id_val,
                chance=chance_val,
                count=count_val,
            ))
        return result

    def _check_balance(
        self, mid: str, monster: MonsterTemplate, issues: list[str],
    ) -> None:
        if monster.cr is None or monster.cr < 0:
            return
        hp = monster.hp or monster.max_hp
        if hp is not None and hp > 0:
            for max_cr, low, high in _CR_HP_RANGES:
                if monster.cr <= max_cr:
                    if hp < low or hp > high:
                        issues.append(
                            f"monster '{mid}' balance warning: CR {monster.cr} with HP {hp} outside expected range {low}-{high}"
                        )
                    break
        if monster.ac is not None and monster.ac > 0:
            for max_cr, low, high in _CR_AC_RANGES:
                if monster.cr <= max_cr:
                    if monster.ac < low or monster.ac > high:
                        issues.append(
                            f"monster '{mid}' balance warning: CR {monster.cr} with AC {monster.ac} outside expected range {low}-{high}"
                        )
                    break
