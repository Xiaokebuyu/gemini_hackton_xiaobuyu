# ❶ 内容层施工记录

**设计文档**：内容层设计规范（已读）.md
**代码路径**：`app/game_core/content/`
**Phase**：0A（基类）+ 3.5（具体 Registry）

## 模块状态

| 组件 | 状态 | 说明 |
|------|------|------|
| `ContentRegistry` ABC | [完成] | 含 `_coerce_dict_mapping` 共享方法 |
| `WorldInstance` | [完成] | typed properties + 三步加载顺序 |
| `TagRegistry` | [骨架] | 能 load/get/list_all，无语义校验 |
| `MapRegistry` | [Phase1] | 连通性/区域/encounter weight/多起点 warning + 查询方法 |
| `CharacterRegistry` | [Phase2] | name/tags/class/faction + shop_inventory 验证 + 查询方法 |
| `ItemRegistry` | [Phase1] | type/rarity 白名单 + 武器/防具字段 + 查询方法 |
| `MonsterRegistry` | [Phase1] | CR/creature_type/abilities/attacks/resistances + 平衡 warning + 查询方法 |
| `SkillRegistry` | [Phase1] | effect type/school/concentration/duration/action_type 白名单 + 查询方法 |
| `ClassRegistry` | [Phase2] | hit_die/hp/ac/level_features + race/background/subclass 字段 + xp_curve 单调性 |
| `FactionRegistry` | [Phase3] | name/description/alignment/relations + tags 每项校验 + get_by_tag() |
| `LoreRegistry` | [Phase3] | name/title/content/text + tags 每项校验 + get_by_tag() |
| `QuestRegistry` | [Phase2] | milestone/chapter/event 字段验证 + 查询方法 |

## 决策记录

### [D-C01] list_all 改为 @abstractmethod

见 `骨架搭建.md` [D-001]。

### [D-C02] _coerce_dict_mapping 提到基类

见 `骨架搭建.md` [D-003]。8 个 Registry 共享同一个 list-or-dict 归一化逻辑。

### [D-C03] WorldInstance.load_all 三步加载

```python
ordered_groups = [
    ["tags"],                                          # Step 1: Tag 先行
    ["maps", "classes", "skills", "lore"],            # Step 2: 基础 Registry
    ["characters", "items", "monsters", "factions", "quests"],  # Step 3: 依赖基础的
]
```

未在 ordered_groups 中的 Registry 最后加载（兜底）。

### [D-C04] Tier 4 Phase 1 — Monster/Item/Skill/Map 语义深度

**覆盖范围**：4 个核心 registry 完整语义深度 + WorldInstance 4 条新跨 registry 引用链。

**MonsterRegistry** 新增：
- 消费者字段：`name` 非空字符串
- 游戏机制：`cr`（非负 float）、`creature_type`（15 种白名单）、`abilities`（6 属性 stat block）、`resistances`/`immunities`（字符串列表）、`attacks`（Mapping 列表，含 name）
- 平衡 warning：CR↔HP / CR↔AC 范围检查（不阻塞加载）
- 查询方法：`get_by_cr(min, max)` / `get_by_type(creature_type)`

**ItemRegistry** 新增：
- 消费者字段：`name` / `base_price` 非负整数
- 游戏机制：`type`（10 种白名单）、`rarity`（5 级白名单）、`damage_dice` / `damage_type` / `ac_bonus` / `weight` / `requires_attunement`
- 查询方法：`get_equippable()` / `get_by_type()` / `get_by_rarity()`

**SkillRegistry** 新增：
- 消费者字段：`concentration`（bool-like）、`duration` / `status_duration` / `duration_ticks`（非负整数）
- 游戏机制：effect `type` 白名单（7 种）、`school`（8 学派白名单）、`action_type` 白名单（4 种，顶层 + cost 内）、`ritual`（bool-like）、`range`（字符串/整数）、`targets`（字符串/正整数）
- 查询方法：`get_spells()` / `get_spells_by_level()` / `get_spells_by_school()`

**MapRegistry** 新增：
- 消费者字段：encounter template `weight`（正数）
- 游戏机制：`connections` / `adjacent_areas`（字符串列表）、`region`（非空字符串）、多起点 warning
- 查询方法：`get_adjacent(area_id)` / `get_by_region(region)`

**WorldInstance 跨 registry 引用**（4 条新链）：
1. Map encounter template → MonsterRegistry
2. Character class_id/character_class → ClassRegistry
3. Character faction/faction_id → FactionRegistry
4. Monster spells[]/abilities[] → SkillRegistry

**测试**：14 新 in `test_content_registries.py` + 4 新 in `test_world_instance.py` = 18 新测试。基线 373→391。

### [D-C05] Tier 4 Phase 2 — Character/Quest/Class 语义深度

**CharacterRegistry** 新增：
- 消费者字段：`name` / `tags` / `character_class` / `class_id` / `faction` / `faction_id`
- shop_inventory 结构：`sell_markup`（float >= 0，>2.0 balance warning）/ `buy_rate`（0~1）/ `rotating_slots` / `base_pool` / `rotating_pool`（list of Mapping + item_id）/ `refresh_on`（字符串或字符串列表）
- 查询方法：`get_by_area()` / `get_merchants()` / `get_by_faction()`

**QuestRegistry** 新增：
- Milestone：`prerequisites`（list + 每项非空字符串）/ `title` / `description` / `tags`
- Chapter：`title` / `description`
- Initial event：`event_type` / `conditions` / `preconditions`（list or Mapping）/ `payload`（Mapping）/ `metadata`（Mapping）
- 查询方法：`get_chapter()` / `get_initial_event()`

**ClassRegistry** 新增：
- Class：`hit_die`（正整数或非空字符串）/ `base_hp` / `hp_per_level`（正整数）/ `base_ac`（非负整数）/ `subclass_level`（正整数）/ `starting_gold`（非负整数）/ `level_features`（Mapping，keys numeric，values list）
- Race：`stat_bonuses`（Mapping）/ `racial_traits`（list of non-empty strings）
- Background：`feature`（非空字符串）/ `gold_bonus`（非负整数）
- Subclass：`features`（list of non-empty strings）/ `level_features`（Mapping）
- XP curve：单调递增检查（list + dict 模式）

**WorldInstance 跨 registry 引用**（2 条新链）：
8. Milestone prerequisites → Milestone（自引用）
9. Character shop_inventory pool → ItemRegistry

**测试**：14 新 in `test_content_registries.py` + 2 新 in `test_world_instance.py` = 16 新测试。基线 391→407。

## 填充 TODO

- [x] `MapRegistry`：地图拓扑校验 — D-C04
- [x] `CharacterRegistry`：consumer + shop_inventory 语义 — D-C05
- [x] `ItemRegistry`：装备槽位 + 消耗品类型校验 — D-C04
- [x] `MonsterRegistry`：CR + 属性完整性校验 — D-C04
- [x] `SkillRegistry`：法术等级 + 法术位消耗校验 — D-C04
- [x] `ClassRegistry`：等级特性 + race/background 校验 — D-C05
- [x] `QuestRegistry`：milestone/chapter/event 字段校验 — D-C05
- [x] `WorldInstance.validate()`：跨 Registry 引用校验 — D-C04 + D-C05（9 条引用链）
### [D-C06] Tier 4 Phase 3 — Faction/Tag/Lore 语义深度 + 跨 Registry Tag 值校验

**FactionRegistry** 新增：
- 消费者字段：`name` / `description` 非空字符串
- 游戏机制：`alignment`（非空字符串）/ `relations`（Mapping）
- tags 列表每项非空字符串校验
- 查询方法：`get_by_tag()`

**TagRegistry** 新增：
- `description` 非空字符串校验
- 查询方法：`all_tags()` / `get_dimension_for_tag()`

**LoreRegistry** 新增：
- 消费者字段：`name` / `title` / `content` / `text` 非空字符串
- tags 列表每项非空字符串校验
- 查询方法：`get_by_tag()`

**WorldInstance 跨 Registry Tag 值校验**：
10. 所有 registry 的 `tags[]` → TagRegistry `all_tags()` 存在性

**测试**：6 新 in `test_content_registries.py` + 1 新 in `test_world_instance.py` = 7 新测试。基线 407→414。

## Tier 4 完成状态

- [x] Phase 1: Monster/Item/Skill/Map — D-C04
- [x] Phase 2: Character/Quest/Class — D-C05
- [x] Phase 3: Faction/Tag/Lore + 跨 registry tag 值校验 — D-C06
- **10 个 registry 全部完成语义深度**
- **WorldInstance 10 条跨 registry 引用链**
- **总测试增量**：41 新测试（373→414）
