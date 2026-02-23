"""player/ — 玩家机械子系统：数值变更、图谱适配、角色创建、d20判定、物品注册、D&D规则表。"""
from app.world.player.stats import (
    add_xp, add_gold, add_hp, remove_gold, remove_hp, set_hp,
    sync_combat_rewards,
)
from app.world.player.node_view import PlayerNodeView, translate_character_to_node
from app.world.player.constants import (
    ABILITY_SCORES, SKILLS, SKILL_NAMES, DAMAGE_TYPES, CONDITIONS,
    ALIGNMENTS, EQUIPMENT_SLOTS, PROFICIENCY_BY_LEVEL, XP_BY_LEVEL,
    FULL_CASTER_SPELL_SLOTS, HALF_CASTER_SPELL_SLOTS,
    HIT_DIE_BY_CLASS, CASTER_TYPE_BY_CLASS, EXHAUSTION_EFFECTS,
    default_character_state, default_npc_state, default_player_state,
)
from app.world.player.item_registry import get_item, list_items, search_items
# CharacterService / AbilityCheckService 暂不 re-export：
# 它们依赖已删除的 CharacterStore，eager import 会传染断裂。
# 调用方直接 from app.world.player.character import CharacterService
