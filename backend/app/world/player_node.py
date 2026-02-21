"""
PlayerNodeView — 图节点适配器，为 WorldGraph 的 player 节点提供 PlayerCharacter 兼容 API。

图为唯一运行时真理源：所有运行时读写通过本适配器操作图节点 state/properties，
setter 自动标记 wg._dirty_nodes 确保 snapshot 捕获。

字段映射:
  PlayerCharacter.current_hp  ↔  node.state["hp"]
  PlayerCharacter.spell_slots ↔  node.state["spell_slots_max"]  (int 键 ↔ str 键)
  其余字段                    ↔  node.state[同名] / node.properties[同名]
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.world.models import WorldNode
    from app.world.world_graph import WorldGraph

logger = logging.getLogger(__name__)


class PlayerNodeView:
    """WorldGraph player 节点的 PlayerCharacter 兼容适配器。

    提供与 PlayerCharacter 相同的属性名和方法签名，
    内部读写均指向 WorldNode.state / WorldNode.properties。

    Usage::

        view = PlayerNodeView(node, wg)
        view.current_hp = 20          # → node.state["hp"] = 20; dirty
        text = view.to_summary_text() # 与 PlayerCharacter 格式一致
    """

    __slots__ = ("_node", "_wg")

    def __init__(self, node: "WorldNode", wg: "WorldGraph") -> None:
        object.__setattr__(self, "_node", node)
        object.__setattr__(self, "_wg", wg)

    # -- helpers --

    def _mark_dirty(self) -> None:
        self._wg._dirty_nodes.add("player")

    @property
    def _state(self) -> Dict[str, Any]:
        return self._node.state

    @property
    def _props(self) -> Dict[str, Any]:
        return self._node.properties

    # =========================================================================
    # Identity properties (from node.name / properties)
    # =========================================================================

    @property
    def character_id(self) -> str:
        return "player"

    @property
    def name(self) -> str:
        return self._node.name

    @name.setter
    def name(self, value: str) -> None:
        self._node.name = value
        self._mark_dirty()

    @property
    def race(self) -> Any:
        return self._props.get("race", "")

    @property
    def character_class(self) -> Any:
        return self._props.get("character_class", "")

    @property
    def background(self) -> str:
        return self._props.get("background", "")

    @property
    def backstory(self) -> str:
        return self._props.get("backstory", "")

    # =========================================================================
    # State properties — 名称与 PlayerCharacter 一致
    # =========================================================================

    # --- current_hp ↔ state["hp"] ---
    @property
    def current_hp(self) -> int:
        return int(self._state.get("hp", 0))

    @current_hp.setter
    def current_hp(self, value: int) -> None:
        self._state["hp"] = max(0, int(value))
        self._mark_dirty()

    @property
    def max_hp(self) -> int:
        return int(self._state.get("max_hp", 0))

    @max_hp.setter
    def max_hp(self, value: int) -> None:
        self._state["max_hp"] = int(value)
        self._mark_dirty()

    @property
    def level(self) -> int:
        return int(self._state.get("level", 1))

    @level.setter
    def level(self, value: int) -> None:
        self._state["level"] = int(value)
        self._mark_dirty()

    @property
    def xp(self) -> int:
        return int(self._state.get("xp", 0))

    @xp.setter
    def xp(self, value: int) -> None:
        self._state["xp"] = max(0, int(value))
        self._mark_dirty()

    @property
    def xp_to_next_level(self) -> int:
        return int(self._state.get("xp_to_next_level", 300))

    @xp_to_next_level.setter
    def xp_to_next_level(self, value: int) -> None:
        self._state["xp_to_next_level"] = int(value)
        self._mark_dirty()

    @property
    def ac(self) -> int:
        return int(self._state.get("ac", 10))

    @ac.setter
    def ac(self, value: int) -> None:
        self._state["ac"] = int(value)
        self._mark_dirty()

    @property
    def initiative_bonus(self) -> int:
        return int(self._state.get("initiative_bonus", 0))

    @property
    def proficiency_bonus(self) -> int:
        return int(self._state.get("proficiency_bonus", 2))

    @proficiency_bonus.setter
    def proficiency_bonus(self, value: int) -> None:
        self._state["proficiency_bonus"] = int(value)
        self._mark_dirty()

    @property
    def speed(self) -> int:
        return int(self._state.get("speed", 30))

    @property
    def abilities(self) -> Dict[str, int]:
        return self._state.get("abilities", {})

    @property
    def gold(self) -> int:
        return int(self._state.get("gold", 0))

    @gold.setter
    def gold(self, value: int) -> None:
        self._state["gold"] = max(0, int(value))
        self._mark_dirty()

    # --- spell_slots ↔ state["spell_slots_max"] (str 键 → int 键) ---
    @property
    def spell_slots(self) -> Dict[int, int]:
        raw = self._state.get("spell_slots_max", {})
        return {int(k): int(v) for k, v in raw.items()}

    @spell_slots.setter
    def spell_slots(self, value: Dict[int, int]) -> None:
        self._state["spell_slots_max"] = {str(k): int(v) for k, v in value.items()}
        self._mark_dirty()

    @property
    def spell_slots_used(self) -> Dict[int, int]:
        raw = self._state.get("spell_slots_used", {})
        return {int(k): int(v) for k, v in raw.items()}

    @spell_slots_used.setter
    def spell_slots_used(self, value: Dict[int, int]) -> None:
        self._state["spell_slots_used"] = {str(k): int(v) for k, v in value.items()}
        self._mark_dirty()

    @property
    def spells_known(self) -> List[str]:
        return self._state.get("spells_known", [])

    # --- List/Dict state fields (直接同名) ---
    @property
    def equipment(self) -> Dict[str, Optional[str]]:
        return self._state.get("equipment", {})

    @property
    def inventory(self) -> List[Dict[str, Any]]:
        return self._state.get("inventory", [])

    @inventory.setter
    def inventory(self, value: List[Dict[str, Any]]) -> None:
        self._state["inventory"] = value
        self._mark_dirty()

    @property
    def conditions(self) -> List[Dict[str, Any]]:
        return self._state.get("conditions", [])

    @property
    def skill_proficiencies(self) -> List[str]:
        return self._state.get("skill_proficiencies", [])

    @property
    def saving_throw_proficiencies(self) -> List[str]:
        return self._state.get("saving_throw_proficiencies", [])

    @property
    def weapon_proficiencies(self) -> List[str]:
        return self._state.get("weapon_proficiencies", [])

    @property
    def armor_proficiencies(self) -> List[str]:
        return self._state.get("armor_proficiencies", [])

    @property
    def feats(self) -> List[str]:
        return self._state.get("feats", [])

    @property
    def class_features(self) -> List[str]:
        return self._state.get("class_features", [])

    @property
    def racial_traits(self) -> List[str]:
        return self._state.get("racial_traits", [])

    # =========================================================================
    # Methods — 与 PlayerCharacter 签名一致
    # =========================================================================

    def ability_modifier(self, ability: str) -> int:
        score = self.abilities.get(ability, 10)
        return (score - 10) // 2

    def add_item(
        self,
        item_id: str,
        item_name: str,
        quantity: int = 1,
        properties: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        inv = self.inventory
        for item in inv:
            if item.get("item_id") == item_id:
                item["quantity"] = item.get("quantity", 1) + quantity
                self._mark_dirty()
                return item
        new_item: Dict[str, Any] = {
            "item_id": item_id,
            "name": item_name,
            "quantity": quantity,
        }
        if properties:
            new_item["properties"] = properties
        inv.append(new_item)
        self._mark_dirty()
        return new_item

    def remove_item(self, item_id: str, quantity: int = 1) -> bool:
        inv = self.inventory
        for i, item in enumerate(inv):
            if item.get("item_id") == item_id:
                current_qty = item.get("quantity", 1)
                if current_qty <= quantity:
                    inv.pop(i)
                else:
                    item["quantity"] = current_qty - quantity
                self._mark_dirty()
                return True
        return False

    def has_item(self, item_id: str) -> bool:
        return any(item.get("item_id") == item_id for item in self.inventory)

    def to_summary_text(self) -> str:
        mods = {k: self.ability_modifier(k) for k in self.abilities}
        mod_str = " ".join(f"{k[:3].upper()}:{v:+d}" for k, v in mods.items())
        equip_parts = []
        for slot, item_id in self.equipment.items():
            if item_id:
                equip_parts.append(f"{slot}={item_id}")
        equip_str = ", ".join(equip_parts) if equip_parts else "无装备"

        conditions_str = ""
        if self.conditions:
            conditions_str = f" | 状态: {', '.join(c.get('name', '?') for c in self.conditions)}"

        race_val = self.race.value if hasattr(self.race, "value") else str(self.race)
        class_val = self.character_class.value if hasattr(self.character_class, "value") else str(self.character_class)

        return (
            f"[玩家角色] {self.name} | {race_val} {class_val} Lv{self.level} | "
            f"HP:{self.current_hp}/{self.max_hp} AC:{self.ac} | "
            f"{mod_str} | 装备: {equip_str}{conditions_str}"
        )

    def _get_equipped_weapon_stats(self) -> Optional[Dict[str, Any]]:
        weapon_id = self.equipment.get("main_hand")
        if not weapon_id:
            return None
        try:
            from app.services.item_registry import get_item
            item = get_item(weapon_id)
            if item and item.get("type") == "weapon":
                return item
        except Exception:
            pass
        return None

    def to_combat_player_state(self) -> Dict[str, Any]:
        str_mod = self.ability_modifier("str")
        dex_mod = self.ability_modifier("dex")
        best_attack_mod = max(str_mod, dex_mod)
        attack_bonus = best_attack_mod + self.proficiency_bonus

        damage_dice = "1d6"
        damage_bonus = best_attack_mod
        damage_type = "slashing"

        weapon = self._get_equipped_weapon_stats()
        if weapon:
            props = weapon.get("properties", {})
            if props.get("damage"):
                damage_dice = props["damage"]
            subtype = weapon.get("subtype", "")
            if "ranged" in subtype:
                attack_bonus = dex_mod + self.proficiency_bonus
                damage_bonus = dex_mod
            else:
                attack_bonus = str_mod + self.proficiency_bonus
                damage_bonus = str_mod
            weapon_name = weapon.get("name", "")
            if any(k in weapon_name for k in ("弓", "弩", "投")):
                damage_type = "piercing"
            elif any(k in weapon_name for k in ("锤", "棍", "铳")):
                damage_type = "bludgeoning"
            else:
                damage_type = "slashing"

        return {
            "name": self.name,
            "hp": self.current_hp,
            "max_hp": self.max_hp,
            "ac": self.ac,
            "level": self.level,
            "abilities": dict(self.abilities),
            "proficiency_bonus": self.proficiency_bonus,
            "initiative_bonus": self.initiative_bonus,
            "attack_bonus": attack_bonus,
            "damage_dice": damage_dice,
            "damage_bonus": damage_bonus,
            "damage_type": damage_type,
            "equipment": dict(self.equipment),
            "spell_slots": dict(self.spell_slots),
            "spell_slots_used": dict(self.spell_slots_used),
            "spells_known": list(self.spells_known),
            "class": str(self.character_class.value if hasattr(self.character_class, "value") else self.character_class),
            "race": str(self.race.value if hasattr(self.race, "value") else self.race),
        }

    def model_dump(self, **kwargs) -> Dict[str, Any]:
        """返回 PlayerCharacter 兼容格式（反向字段映射）。"""
        # spell_slots: str 键 → int 键
        spell_slots_int = self.spell_slots
        spell_slots_used_int = self.spell_slots_used

        return {
            "character_id": "player",
            "name": self.name,
            "race": str(self.race.value if hasattr(self.race, "value") else self.race),
            "character_class": str(self.character_class.value if hasattr(self.character_class, "value") else self.character_class),
            "background": self.background,
            "backstory": self.backstory,
            "level": self.level,
            "xp": self.xp,
            "xp_to_next_level": self.xp_to_next_level,
            "abilities": dict(self.abilities),
            "max_hp": self.max_hp,
            "current_hp": self.current_hp,
            "ac": self.ac,
            "initiative_bonus": self.initiative_bonus,
            "proficiency_bonus": self.proficiency_bonus,
            "speed": self.speed,
            "skill_proficiencies": list(self.skill_proficiencies),
            "saving_throw_proficiencies": list(self.saving_throw_proficiencies),
            "weapon_proficiencies": list(self.weapon_proficiencies),
            "armor_proficiencies": list(self.armor_proficiencies),
            "feats": list(self.feats),
            "class_features": list(self.class_features),
            "racial_traits": list(self.racial_traits),
            "equipment": dict(self.equipment),
            "inventory": list(self.inventory),
            "gold": self.gold,
            "conditions": list(self.conditions),
            "spell_slots": spell_slots_int,
            "spell_slots_used": spell_slots_used_int,
            "spells_known": list(self.spells_known),
        }


# =============================================================================
# translate_character_to_node — PlayerCharacter → 图节点 state + properties
# =============================================================================


def translate_character_to_node(
    pc: Any,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """将 PlayerCharacter 翻译为图节点的 (state, properties)。

    Args:
        pc: PlayerCharacter 实例。

    Returns:
        (state, properties) 二元组。
        state 基于 default_player_state() + pc 值覆盖。
        properties 包含身份字段 (race, character_class, background, backstory)。
    """
    from app.world.constants import default_player_state

    state = default_player_state()

    # 直接同名映射
    for field in (
        "level", "xp", "xp_to_next_level", "max_hp", "ac",
        "initiative_bonus", "proficiency_bonus", "speed",
        "abilities", "equipment", "inventory", "gold",
        "conditions", "spells_known",
        "skill_proficiencies", "saving_throw_proficiencies",
        "weapon_proficiencies", "armor_proficiencies",
        "feats", "class_features", "racial_traits",
    ):
        val = getattr(pc, field, None)
        if val is not None:
            state[field] = val

    # 名不同映射: current_hp → hp
    state["hp"] = getattr(pc, "current_hp", state.get("hp", 10))

    # spell_slots: int 键 → str 键
    raw_slots = getattr(pc, "spell_slots", {})
    if raw_slots:
        state["spell_slots_max"] = {str(k): int(v) for k, v in raw_slots.items()}

    raw_used = getattr(pc, "spell_slots_used", {})
    if raw_used:
        state["spell_slots_used"] = {str(k): int(v) for k, v in raw_used.items()}

    # Properties: 身份字段
    properties: Dict[str, Any] = {}
    race = getattr(pc, "race", None)
    if race is not None:
        properties["race"] = race.value if hasattr(race, "value") else str(race)
    char_class = getattr(pc, "character_class", None)
    if char_class is not None:
        properties["character_class"] = char_class.value if hasattr(char_class, "value") else str(char_class)
    for field in ("background", "backstory"):
        val = getattr(pc, field, None)
        if val:
            properties[field] = val

    return state, properties
