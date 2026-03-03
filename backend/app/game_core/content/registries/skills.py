"""SkillRegistry implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry


_EFFECT_TYPES = frozenset({
    "heal", "damage", "buff", "debuff", "utility", "summon", "control",
})

_SPELL_SCHOOLS = frozenset({
    "abjuration", "conjuration", "divination", "enchantment",
    "evocation", "illusion", "necromancy", "transmutation",
})

_ACTION_TYPES = frozenset({"action", "bonus_action", "reaction", "free"})


# ---------------------------------------------------------------------------
# Typed sub-structures (设计规范 §8)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SkillCost:
    """Typed cost definition for a skill or spell."""

    action_type: str = ""               # action / bonus_action / reaction / free
    spell_slot: int | None = None       # 消耗的法术位等级（None=武技/非法术）
    resource: str | None = None         # 其他资源 key（如 "rage_charge", "ki_point"）
    resource_amount: int = 0            # 消耗量（兼容旧 "amount" 键）
    cooldown: int | None = None         # 冷却回合数（None=无冷却）


@dataclass(slots=True)
class SkillEffect:
    """Typed effect definition for a skill or spell."""

    type: str = ""                      # damage/heal/buff/debuff/control/utility/summon
    target: str = ""                    # self/single/area/cone/line
    range: int | None = None            # 格数（0=自身，1=邻接…）
    area_size: int | None = None        # AOE 范围（格数）
    dice: str | None = None             # 骰子表达式，如 "8d6" / "2d4+2"
    damage_type: str | None = None      # fire / radiant / necrotic / healing
    save: str | None = None             # 豁免属性：dex / con / wis / …
    save_dc_stat: str | None = None     # 法术 DC 基准属性：int / wis / cha
    half_on_save: bool = False          # 豁免成功时受半量伤害
    applies_status: str | None = None   # 应用的状态效果 ID
    status_duration: int | None = None  # 状态持续 ticks（兼容旧 duration_ticks/duration）
    upcast_dice: str | None = None      # 每升一槽额外骰子，如 "1d8"
    concentration: bool = False         # 需要专注（同时只能维持一个）
    modifiers: dict = field(default_factory=dict)   # 属性修正，如 {"ac": 3}
    periodic: dict = field(default_factory=dict)    # 周期效果，如 {"damage": 4}
    tags: list[str] = field(default_factory=list)
    # 固定值（向后兼容，优先于 dice 字段）
    heal_amount: int | None = None      # 固定治疗量（兼容旧 "heal_amount"/"heal" 键）
    damage_amount: int | None = None    # 固定伤害量（兼容旧 "damage_amount"/"damage" 键）


@dataclass(slots=True)
class StatusEffectTemplate:
    """Typed status effect definition (设计规范 §8 StatusEffectTemplate)."""

    id: str
    name: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    category: str = ""                          # buff / debuff
    stackable: bool = False
    tick_damage: str | None = None              # 每回合伤害骰，如 "1d4"
    tick_damage_type: str | None = None         # fire / poison / necrotic
    tick_heal: str | None = None                # 每回合治疗骰
    modifiers: dict | None = None               # 属性修正，如 {"ac": -2, "attack": -1}
    prevents_action: bool = False               # 是否阻止所有行动（如眩晕）
    disadvantage_on: list[str] = field(default_factory=list)   # 劣势检定列表
    duration: int | None = None                 # 默认持续回合数（None=直到被移除）
    save_end_of_turn: str | None = None         # 每回合结束豁免属性
    save_dc: int | None = None                  # 豁免 DC
    cure_conditions: list[str] = field(default_factory=list)   # 移除条件


# ---------------------------------------------------------------------------
# SkillTemplate
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SkillTemplate:
    """Typed skill/spell definition."""

    id: str
    name: str = ""
    description: str = ""               # 叙事描述（供 ❺ 叙事层 context 使用）
    category: str = ""                  # spell / skill / passive
    spell_level: int | None = None      # 0=戏法，1-5=正式法术，None=非法术
    school: str = ""                    # 魔法学派
    tags: list[str] = field(default_factory=list)
    effect: SkillEffect = field(default_factory=SkillEffect)
    cost: SkillCost = field(default_factory=SkillCost)
    requirements: dict[str, Any] = field(default_factory=dict)
    usable_in: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SkillRegistry
# ---------------------------------------------------------------------------

class SkillRegistry(ContentRegistry):
    """Registry for skills, spells, and status-effect templates."""

    def __init__(self) -> None:
        super().__init__("skills")
        self._items: dict[str, SkillTemplate] = {}
        self._status_effects: dict[str, StatusEffectTemplate] = {}
        self._load_issues: list[str] = []

    def load(self, data: dict[str, Any]) -> None:
        self._items = {}
        self._status_effects = {}
        self._load_issues = []
        coerced = self._coerce_dict_mapping(data)

        # Load status effect templates from top-level "status_effects" key
        raw_status_effects = coerced.get("status_effects")
        if isinstance(raw_status_effects, Mapping):
            for se_id, se_raw in raw_status_effects.items():
                if isinstance(se_raw, Mapping):
                    self._status_effects[str(se_id)] = self._parse_status_effect_template(
                        str(se_id), se_raw
                    )

        # Load skill/spell entries (all keys except "status_effects")
        for sid, raw in coerced.items():
            if sid == "status_effects":
                continue
            if not isinstance(raw, Mapping):
                continue
            if not raw.get("id"):
                self._load_issues.append(f"skill entry '{sid}' missing id")

            is_spell = self._is_spell_entry(raw)
            category = ""
            if is_spell:
                category = "spell"
                self._validate_spell_fields(sid, raw)
            else:
                raw_category = str(raw.get("category", "")).strip()
                if raw_category:
                    category = raw_category

            spell_level = self._coerce_non_negative_int(
                raw.get("spell_level", raw.get("level"))
            )

            tags: list[str] = []
            raw_tags = raw.get("tags")
            if isinstance(raw_tags, list):
                tags = [str(t) for t in raw_tags if isinstance(t, str) and str(t).strip()]

            self._items[sid] = SkillTemplate(
                id=str(raw.get("id", sid)),
                name=str(raw.get("name") or ""),
                description=str(raw.get("description") or ""),
                category=category,
                spell_level=spell_level,
                school=str(raw.get("school") or "").strip().lower(),
                tags=tags,
                effect=self._parse_effect(sid, raw),
                cost=self._parse_cost(sid, raw),
                requirements=(
                    dict(raw.get("requirements", {}))
                    if isinstance(raw.get("requirements"), Mapping) else {}
                ),
                usable_in=(
                    [str(u) for u in raw.get("usable_in", []) if str(u).strip()]
                    if isinstance(raw.get("usable_in"), list) else []
                ),
            )

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get(self, content_id: str) -> SkillTemplate | None:
        return self._items.get(content_id)

    def list_all(self) -> list[SkillTemplate]:
        return list(self._items.values())

    def get_spells(self) -> list[SkillTemplate]:
        """Return all entries classified as spells."""
        return [s for s in self._items.values() if s.category == "spell"]

    def get_spells_by_level(self, level: int) -> list[SkillTemplate]:
        """Return spells matching the given spell level."""
        return [
            s for s in self._items.values()
            if s.category == "spell" and s.spell_level == level
        ]

    def get_spells_by_school(self, school: str) -> list[SkillTemplate]:
        """Return spells matching the given school."""
        normalized = school.strip().lower()
        return [
            s for s in self._items.values()
            if s.category == "spell" and s.school == normalized
        ]

    def get_by_category(self, category: str) -> list[SkillTemplate]:
        """Get skills by category (martial / spell / passive)."""
        return [s for s in self._items.values() if s.category == category]

    def get_combat_skills(self) -> list[SkillTemplate]:
        """Get skills usable in combat."""
        return [s for s in self._items.values() if "combat" in s.usable_in]

    def get_exploration_skills(self) -> list[SkillTemplate]:
        """Get skills usable in exploration."""
        return [s for s in self._items.values() if "exploration" in s.usable_in]

    def get_status_effect(self, effect_id: str) -> StatusEffectTemplate | None:
        """Return a StatusEffectTemplate by ID, or None if not registered."""
        return self._status_effects.get(effect_id)

    def list_status_effects(self) -> list[StatusEffectTemplate]:
        """Return all registered StatusEffectTemplates."""
        return list(self._status_effects.values())

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        return list(self._load_issues)

    # ------------------------------------------------------------------
    # Effect / Cost parsing helpers
    # ------------------------------------------------------------------

    def _parse_effect(self, sid: str, raw: Mapping[str, Any]) -> SkillEffect:
        """Parse raw entry → SkillEffect, normalizing field aliases."""
        raw_effect = raw.get("effect")
        e: Mapping[str, Any] = (
            dict(raw_effect) if isinstance(raw_effect, Mapping) else {}
        )

        # type
        effect_type = self._coerce_non_empty_string(e.get("type"))

        # target / range / area
        target = self._coerce_non_empty_string(e.get("target")) or ""
        raw_range = e.get("range")
        range_val: int | None = None
        if raw_range is not None and not isinstance(raw_range, (str, bool)):
            range_val = self._coerce_non_negative_int(raw_range)
        area_size = self._coerce_non_negative_int(e.get("area_size"))

        # dice / damage_type
        dice = self._coerce_non_empty_string(e.get("dice"))
        damage_type = self._coerce_non_empty_string(e.get("damage_type"))

        # saving throw
        save = self._coerce_non_empty_string(e.get("save"))
        save_dc_stat = self._coerce_non_empty_string(e.get("save_dc_stat"))
        half_on_save = bool(e.get("half_on_save", False))

        # applies_status: prefer effect sub-key, then top-level
        applies_status = self._coerce_non_empty_string(e.get("applies_status"))
        if applies_status is None:
            applies_status = self._coerce_non_empty_string(raw.get("applies_status"))

        # status_duration: merge aliases duration_ticks / duration
        status_duration = self._coerce_non_negative_int(
            e.get("status_duration", e.get("duration_ticks", e.get("duration")))
        )

        # upcast_dice: prefer effect, then top-level
        upcast_dice = self._coerce_non_empty_string(
            e.get("upcast_dice") or raw.get("upcast_dice")
        )

        # concentration: prefer effect, then top-level
        concentration = False
        conc_raw = e.get("concentration") if "concentration" in e else raw.get("concentration")
        if self._is_bool_like(conc_raw):
            concentration = bool(conc_raw)

        # modifiers / periodic / tags (effect-level)
        modifiers: dict = {}
        raw_modifiers = e.get("modifiers")
        if isinstance(raw_modifiers, Mapping):
            modifiers = dict(raw_modifiers)

        periodic: dict = {}
        raw_periodic = e.get("periodic")
        if isinstance(raw_periodic, Mapping):
            periodic = dict(raw_periodic)

        effect_tags: list[str] = []
        raw_effect_tags = e.get("tags")
        if isinstance(raw_effect_tags, list):
            effect_tags = [str(t) for t in raw_effect_tags if isinstance(t, str) and t.strip()]

        # Fixed heal/damage amounts — normalize aliases
        heal_amount: int | None = None
        for key in ("heal_amount", "heal"):
            val = self._coerce_non_negative_int(e.get(key, raw.get(key)))
            if val is not None:
                heal_amount = val
                break

        damage_amount: int | None = None
        for key in ("damage_amount", "damage"):
            val = self._coerce_non_negative_int(e.get(key, raw.get(key)))
            if val is not None:
                damage_amount = val
                break

        return SkillEffect(
            type=effect_type or "",
            target=target,
            range=range_val,
            area_size=area_size,
            dice=dice,
            damage_type=damage_type,
            save=save,
            save_dc_stat=save_dc_stat,
            half_on_save=half_on_save,
            applies_status=applies_status,
            status_duration=status_duration,
            upcast_dice=upcast_dice,
            concentration=concentration,
            modifiers=modifiers,
            periodic=periodic,
            tags=effect_tags,
            heal_amount=heal_amount,
            damage_amount=damage_amount,
        )

    def _parse_cost(self, sid: str, raw: Mapping[str, Any]) -> SkillCost:
        """Parse raw entry → SkillCost, normalizing field aliases."""
        raw_cost = raw.get("cost")
        c: Mapping[str, Any] = (
            dict(raw_cost) if isinstance(raw_cost, Mapping) else {}
        )

        # action_type: prefer cost sub-key, then top-level
        action_type = self._coerce_non_empty_string(
            c.get("action_type") or raw.get("action_type")
        ) or ""

        spell_slot = self._coerce_non_negative_int(c.get("spell_slot"))
        resource = self._coerce_non_empty_string(c.get("resource"))

        # resource_amount: normalize "amount" alias
        resource_amount = self._coerce_non_negative_int(
            c.get("resource_amount", c.get("amount"))
        ) or 0

        cooldown = self._coerce_non_negative_int(c.get("cooldown"))

        return SkillCost(
            action_type=action_type,
            spell_slot=spell_slot,
            resource=resource,
            resource_amount=resource_amount,
            cooldown=cooldown,
        )

    def _parse_status_effect_template(
        self,
        se_id: str,
        raw: Mapping[str, Any],
    ) -> StatusEffectTemplate:
        """Parse raw dict → StatusEffectTemplate."""
        tags: list[str] = []
        raw_tags = raw.get("tags")
        if isinstance(raw_tags, list):
            tags = [str(t) for t in raw_tags if isinstance(t, str) and t.strip()]

        disadvantage_on: list[str] = []
        raw_disadv = raw.get("disadvantage_on")
        if isinstance(raw_disadv, list):
            disadvantage_on = [str(v) for v in raw_disadv if str(v).strip()]

        cure_conditions: list[str] = []
        raw_cure = raw.get("cure_conditions")
        if isinstance(raw_cure, list):
            cure_conditions = [str(v) for v in raw_cure if str(v).strip()]

        modifiers: dict | None = None
        raw_mod = raw.get("modifiers")
        if isinstance(raw_mod, Mapping):
            modifiers = dict(raw_mod)

        return StatusEffectTemplate(
            id=str(raw.get("id", se_id)),
            name=str(raw.get("name") or ""),
            description=str(raw.get("description") or ""),
            tags=tags,
            category=str(raw.get("category") or "").strip().lower(),
            stackable=bool(raw.get("stackable", False)),
            tick_damage=self._coerce_non_empty_string(raw.get("tick_damage")),
            tick_damage_type=self._coerce_non_empty_string(raw.get("tick_damage_type")),
            tick_heal=self._coerce_non_empty_string(raw.get("tick_heal")),
            modifiers=modifiers,
            prevents_action=bool(raw.get("prevents_action", False)),
            disadvantage_on=disadvantage_on,
            duration=self._coerce_non_negative_int(raw.get("duration")),
            save_end_of_turn=self._coerce_non_empty_string(raw.get("save_end_of_turn")),
            save_dc=self._coerce_non_negative_int(raw.get("save_dc")),
            cure_conditions=cure_conditions,
        )

    # ------------------------------------------------------------------
    # Load-time validation helpers
    # ------------------------------------------------------------------

    def _validate_spell_fields(self, sid: str, raw: Mapping[str, Any]) -> None:
        """Validate spell-specific fields and collect issues."""
        # Spell level
        spell_level = raw.get("spell_level", raw.get("level"))
        if spell_level is not None and self._coerce_non_negative_int(spell_level) is None:
            self._load_issues.append(f"spell '{sid}' has invalid spell level")

        # Effect
        raw_effect = raw.get("effect")
        if raw_effect is not None and not isinstance(raw_effect, Mapping):
            self._load_issues.append(f"spell '{sid}' has invalid effect")
            return

        # Cost
        raw_cost = raw.get("cost")
        if raw_cost is not None and not isinstance(raw_cost, Mapping):
            self._load_issues.append(f"spell '{sid}' has invalid cost")
        elif isinstance(raw_cost, Mapping):
            if (
                ("resource_amount" in raw_cost or "amount" in raw_cost)
                and self._coerce_positive_int(
                    raw_cost.get("resource_amount", raw_cost.get("amount"))
                ) is None
            ):
                self._load_issues.append(f"spell '{sid}' has invalid resource amount")
            if (
                "action_type" in raw_cost
                and self._coerce_non_empty_string(raw_cost.get("action_type")) is None
            ):
                self._load_issues.append(f"spell '{sid}' has invalid action_type")

        # Upcast dice
        if "upcast_dice" in raw and (
            self._coerce_non_empty_string(raw.get("upcast_dice")) is None
        ):
            self._load_issues.append(f"spell '{sid}' has invalid upcast_dice")

        if not isinstance(raw_effect, Mapping):
            return

        # Effect type and sub-fields
        effect_type = self._coerce_non_empty_string(raw_effect.get("type"))

        if (
            "applies_status" in raw_effect
            and self._coerce_non_empty_string(raw_effect.get("applies_status")) is None
        ):
            self._load_issues.append(f"spell '{sid}' has invalid applies_status")

        if effect_type == "heal":
            has_fixed = any(
                fn in raw_effect or fn in raw
                for fn in ("heal_amount", "heal")
            )
            if has_fixed:
                for fn in ("heal_amount", "heal"):
                    if fn in raw_effect and self._coerce_non_negative_int(raw_effect.get(fn)) is None:
                        self._load_issues.append(f"spell '{sid}' has invalid {fn}")
                    if fn in raw and self._coerce_non_negative_int(raw.get(fn)) is None:
                        self._load_issues.append(f"spell '{sid}' has invalid {fn}")
            elif self._coerce_non_empty_string(
                raw_effect.get("dice", raw.get("dice"))
            ) is None:
                self._load_issues.append(f"spell '{sid}' heal effect missing dice")

        if effect_type == "damage":
            has_fixed = any(
                fn in raw_effect or fn in raw
                for fn in ("damage_amount", "damage")
            )
            if has_fixed:
                for fn in ("damage_amount", "damage"):
                    if fn in raw_effect and self._coerce_non_negative_int(raw_effect.get(fn)) is None:
                        self._load_issues.append(f"spell '{sid}' has invalid {fn}")
                    if fn in raw and self._coerce_non_negative_int(raw.get(fn)) is None:
                        self._load_issues.append(f"spell '{sid}' has invalid {fn}")
            elif self._coerce_non_empty_string(
                raw_effect.get("dice", raw.get("dice"))
            ) is None:
                self._load_issues.append(f"spell '{sid}' damage effect missing dice")

        if effect_type is not None and effect_type.lower() not in _EFFECT_TYPES:
            self._load_issues.append(f"spell '{sid}' has invalid effect type '{effect_type}'")

        # Concentration / duration
        if "concentration" in raw and not self._is_bool_like(raw.get("concentration")):
            self._load_issues.append(f"spell '{sid}' has invalid concentration")
        if "concentration" in raw_effect and not self._is_bool_like(raw_effect.get("concentration")):
            self._load_issues.append(f"spell '{sid}' has invalid concentration")

        for dur_field in ("duration", "status_duration", "duration_ticks"):
            for source in (raw, raw_effect):
                if dur_field in source and self._coerce_non_negative_int(source.get(dur_field)) is None:
                    self._load_issues.append(f"spell '{sid}' has invalid {dur_field}")

        # Action type whitelist
        top_action = self._coerce_non_empty_string(raw.get("action_type"))
        cost_action = (
            self._coerce_non_empty_string(raw_cost.get("action_type"))
            if isinstance(raw_cost, Mapping) and "action_type" in raw_cost
            else None
        )
        for at_value, at_source in [(top_action, "action_type"), (cost_action, "cost.action_type")]:
            if at_value is not None and at_value.lower() not in _ACTION_TYPES:
                self._load_issues.append(f"spell '{sid}' has invalid {at_source} '{at_value}'")

        # School
        if "school" in raw:
            school = self._coerce_non_empty_string(raw.get("school"))
            if school is None or school.lower() not in _SPELL_SCHOOLS:
                self._load_issues.append(f"spell '{sid}' has invalid school")

        # Ritual / range / targets
        if "ritual" in raw and not self._is_bool_like(raw.get("ritual")):
            self._load_issues.append(f"spell '{sid}' has invalid ritual")

        if "range" in raw:
            r = raw.get("range")
            r_valid = (
                (isinstance(r, str) and self._coerce_non_empty_string(r) is not None)
                or (not isinstance(r, (str, bool)) and self._coerce_non_negative_int(r) is not None)
            )
            if not r_valid:
                self._load_issues.append(f"spell '{sid}' has invalid range")

        if "targets" in raw:
            t = raw.get("targets")
            t_valid = (
                (isinstance(t, str) and self._coerce_non_empty_string(t) is not None)
                or (not isinstance(t, (str, bool)) and self._coerce_positive_int(t) is not None)
            )
            if not t_valid:
                self._load_issues.append(f"spell '{sid}' has invalid targets")

    def _is_spell_entry(self, item: Mapping[str, Any]) -> bool:
        for field_name in ("category", "type", "kind"):
            if str(item.get(field_name, "")).strip().lower() == "spell":
                return True
        if "spell_level" in item:
            return True
        return "level" in item and "effect" in item

    @staticmethod
    def _coerce_non_empty_string(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        s = value.strip()
        return s if s else None
