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
| `MapRegistry` | [Phase1+P29] | 连通性/区域/encounter weight/多起点 warning + 查询方法；R-4 补 get_sub_location()；P29-A3 加 string→float danger_level 兜底；P29-A6a 古代遗迹 3 子地点 hostile_config 数据 |
| `CharacterRegistry` | [Phase2] | name/tags/class/faction + shop_inventory 验证 + 查询方法 |
| `ItemRegistry` | [F-A完成] | typed sub-struct（WeaponData/ArmorData/ConsumableData）+ 7 查询方法 |
| `MonsterRegistry` | [F-C完成] | CR/creature_type/abilities/attacks/resistances + 平衡 warning + 查询方法；F-C 补齐 ai_personality/flee_threshold/xp_reward + MonsterAttack.damage_dice/hit_bonus/damage_type |
| `SkillRegistry` | [F-B完成] | 新增 SkillEffect/SkillCost/StatusEffectTemplate typed struct；effect/cost 迁移为 typed；status_effects 子表 + get_status_effect/list_status_effects；旧键别名（damage/heal/duration_ticks/amount）load 时归一；R-4 补 get_by_category/get_combat_skills/get_exploration_skills + 修正非法术 category 加载 |
| `ClassRegistry` | [Phase2] | hit_die/hp/ac/level_features + race/background/subclass 字段 + xp_curve 单调性 |
| `FactionRegistry` | [Phase3] | name/description/alignment/relations + tags 每项校验 + get_by_tag()；R-4 补 get_factions_in_area/get_relations_of/get_behavioral_rules |
| `LoreRegistry` | [Phase3] | name/title/content/text + tags 每项校验 + get_by_tag()；R-4 补 get_rules_for_context() |
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

**ItemRegistry** 新增（D-C04 Phase1）：
- 消费者字段：`name` / `base_price` 非负整数
- 游戏机制：`type`（10 种白名单）、`rarity`（5 级白名单）、`damage_dice` / `damage_type` / `ac_bonus` / `weight` / `requires_attunement`
- 查询方法：`get_equippable()` / `get_by_type()` / `get_by_rarity()`

**ItemRegistry** F-A 迁移（D-C-FA）：
- 移除平铺字段 `damage_dice` / `damage_type` / `ac_bonus`，迁移为 typed sub-struct
- 新增 `WeaponData`（damage_dice/damage_type/properties/range/proficiency/slot/versatile_dice）
- 新增 `ArmorData`（armor_type/base_ac/dex_cap/str_requirement/stealth_disadvantage/proficiency）；armor_type 从 subtype 推断，dex_cap/stealth_disadvantage 按类型自动派生
- 新增 `ConsumableData`（trigger/charges/effect）；heal_amount > 0 时自动构建
- 新增 `description` 字段（设计规范对齐）
- 新增查询方法：`get_weapons(properties?)` / `get_armors(armor_type?)` / `get_consumables()` / `get_by_price_range(min, max)` / `get_by_tags(tags)`
- 修复 `base_price=0` 被 `or` 短路 Bug（原有缺陷）

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

## Dataclass 全链路迁移 — D-C07

**目标**：方案 B 全链路迁移（Registry 内部 dataclass 化 + 消费端 typed 属性访问）。
**详细计划**：`内容层dataclass迁移计划.md`

### Batch 0 + Batch 1（2026-03-01）— FactionRegistry / LoreRegistry / TagRegistry

**基础设施（Batch 0）**：
1. `base.py:_extract_tags` — 兼容 dict 和 dataclass（`getattr` fallback）
2. `world.py:_entry_id` — 签名从 `Mapping` 放宽为 `Any`，支持 dataclass
3. `world.py:_validate_tag_value_refs` — 遍历所有 registry 时兼容 dict/dataclass 混合
4. `world.py:_validate_character_faction_refs` — 改为直接属性访问

**三个 Registry（Batch 1）**：
5. `FactionTemplate` dataclass — 9 字段（id, name, description, alignment, relations, tags, behavioral_rules, initial_standing, base_standing）
6. `LoreEntry` dataclass — 4 字段（id, title, content, tags）。load 时归一化 name/title → title, content/text → content
7. `TagDimension` dataclass — 3 字段（id, description, tags）

**迁移模式**：
- load() 做格式验证 + 收集 `_load_issues`，同时构造 typed 实例
- validate() 返回 `_load_issues`（格式错误）+ 语义检查
- get() 直接返回实例（不再 defensive copy）
- list_all() 返回实例列表

**消费端更新**：
- `ai_osiris.py:_build_rules_context` — 3 处循环去掉 isinstance/Mapping 防御，直接属性访问
- `state/base.py:_from_world` — faction 循环改为属性访问
- `registries/__init__.py` — 导出 FactionTemplate, LoreEntry, TagDimension

**测试**：503 passed（500 + 3 新增），零回归

### Batch 2A + 2B（2026-03-01）— MonsterRegistry / ItemRegistry

**MonsterRegistry（Batch 2A）**：
1. `MonsterTemplate` dataclass — 18 字段（id, name, hp, max_hp, ac, cr, creature_type, abilities, resistances, immunities, attacks, gold_drop, gold, gold_reward, loot_table, spells, ability_refs, tags）
2. `MonsterAttack` dataclass — 1 字段（name）
3. `LootEntry` dataclass — 3 字段（item_id, chance, count）
4. `_load_issues` 模式（格式校验在 load 时，平衡 warning 在 validate 时）
5. `ability_refs` 字段：保留 raw abilities 为 list 时的 skill 引用数据（跨 registry 验证用）

**ItemRegistry（Batch 2B）**：
1. `ItemTemplate` dataclass — 17 字段（id, name, type, rarity, base_price, price, heal_amount, heal, restore_hp, slot, tags, weight, requires_attunement, damage_dice, damage_type, ac_bonus）
2. `requires_attunement` load 时 bool-like → bool 转换
3. 三组 fallback alias 保留：heal_amount/heal/restore_hp + base_price/price

**消费端更新（6 个文件）**：
- `combat.py`：`_build_participants` 属性访问 + `_resolve_heal_amount` 改 getattr + `_compute_use_combat_item` 简化
- `encounter.py`：`_compute_generate_loot` isinstance→None 检查 + `_resolve_gold` 属性访问 + `_resolve_loot_items` LootEntry 属性访问
- `economy.py`：`_base_price_for_item` 属性访问
- `inventory.py`：`_resolve_heal_amount` 改 getattr
- `ai_osiris.py`：`_extract_content_tags` item 属性访问 + `_resolve_entity_name` 兼容 dataclass
- `world.py`：`_validate_monster_loot_refs` / `_validate_encounter_monster_refs` / `_validate_monster_skill_refs` 属性访问 + `_collect_entry_ids` 通用辅助方法

**测试**：507 passed（503 + 4 新增），零回归

### Batch 3（2026-03-01）— SkillRegistry

**SkillTemplate dataclass** — 19 字段（id, name, category, spell_level, school, concentration, ritual, range, targets, action_type, applies_status, duration, status_duration, duration_ticks, upcast_dice, effect, cost, tags）

**关键设计决策**：
- `effect`/`cost` 保持 `dict[str, Any]` 不做子 dataclass — spell.py 的 `_read_*` 系列 helpers 同时接收 effect(dict) 和 template，做子 dataclass 需全部重写
- `category` 归一化自 raw "category"/"type"/"kind"，只要其中一个为 "spell" 就存 "spell"
- `spell_level` 归一化自 raw "spell_level"/"level"

**消费端更新（2 个文件）**：
- `spell.py`：`_resolve_spell_template` 改 None 检查 + category 检查、`_resolve_spell_level`/`_resolve_effect_payload`/`_resolve_effect_type`/`_resolve_action_type`/`_resolve_resource_cost` 改属性访问、5 个 `_read_*` helpers 新增 `_source_get`/`_source_has` 兼容 Mapping+dataclass
- `ai_osiris.py`：`_extract_content_tags` skill isinstance→None 检查 + 属性访问

**测试**：509 passed（507 + 2 新增），零回归

### Batch 4（2026-03-01）— CharacterRegistry

**CharacterTemplate dataclass** — 24 字段（id, name, area_id, current_area, location_id, current_location, tags, character_class, class_id, faction, faction_id, personality, dialogue_style, speech_pattern, appearance, backstory, inventory, shop, shop_inventory, base_disposition, sell_markup, buy_rate, refresh_on）

**ShopInventory dataclass** — 7 字段（sell_markup, buy_rate, base_pool, rotating_pool, rotating_slots, refresh_on）

**关键设计决策**：
- `ShopInventory` 做 dataclass，但 pool entries 留 `list[dict]` — economy.py `_normalize_shop_entry` 密集用 `.get()` + `isinstance(entry, Mapping)`，dataclass 化零收益
- `inventory` / `shop` / `base_disposition` 保持原始类型（list[dict] / dict / dict）— 访问量低，不值得子 dataclass
- 别名双字段保留（area_id + current_area 等）— 消费端同时访问两个字段名
- `base_disposition` 在 load 时归一化 `initial_disposition` fallback

**消费端更新（11 个文件）**：
- `economy.py`：7 方法签名 `Mapping → Any`，去掉 3 处 `or {}` 防御，shop_inventory 属性访问
- `ai_osiris.py`：5 处 `isinstance(template, Mapping)` → `template is not None` + 属性访问
- `agent_orchestration.py`：`_profile_get()` 辅助函数（兼容 dict + dataclass），2 函数签名 + 2 处调用
- `gm_tools.py`：`dataclasses.asdict(char_data)` 序列化
- `npc_schedule.py`：`dataclasses.asdict(item)` 替代 `item.items()`
- `interaction_service.py` / `interaction.py`：删除 isinstance + `getattr()` 属性访问
- `state/base.py`：删除 isinstance + `item.id` / `item.base_disposition` 属性访问
- `world.py`：4 个验证方法属性访问 + `getattr()`

**计划外修正**：
- `_build_npc_system_prompt` / `_build_teammate_system_prompt` 改 `getattr()` 后，测试传 dict 参数失败。添加 `_profile_get()` 辅助函数解决（isinstance(Mapping) → `.get()`，否则 getattr）

**测试**：511 passed（509 + 2 新增），零回归

### Batch 5（2026-03-01）— ClassRegistry

**4 个 dataclass**：
- `ClassTemplate` — 16 字段（id, name, description, hit_die, base_hp, hp_per_level, base_ac, starting_gold, subclass_level, spellcasting_ability, prepared_limit, prepared_formula, level_features, starting_equipment, default_equipped）
- `SubclassTemplate` — 4 字段（id, class_id, features, level_features）
- `RaceTemplate` — 5 字段（id, name, description, stat_bonuses, racial_traits）
- `BackgroundTemplate` — 7 字段（id, name, description, feature, gold_bonus, starting_gold, skill_proficiency）

**关键设计决策**：
- `level_features` 留 `dict[str, Any]` — key 可以是 int 或 str，growth.py 双路 `.get(level, .get(str(level)))` 访问
- `hit_die` 保留 `str | int | None` union — 可以是 `"d8"` (str) 或 `10` (int)
- `xp_curve` 留在 registry 级别不做 dataclass — 不属于任何单个 template
- `_load_validated_string_list` 新辅助方法 — 对 racial_traits/features 做逐元素校验

**消费端更新（4 个文件）**：
- `growth.py`：8 方法签名 `Mapping → Any`，5 处 `or {}` → None check + early return，3 处 isinstance 去掉，stat_bonuses 直接属性访问
- `spell.py`：`_get_class_template` 返回 `Any`（None 代替 {}），`_prepare_mode` / `_resolve_prepared_limit` / `_resolve_spellcasting_ability` 属性访问
- `runtime.py`：3 normalize 方法 + 2 collect 方法签名改 `Any`，全部 `.get()` → `getattr()`
- `session_store.py`：`_resolve_player_class_label` isinstance(Mapping) → is not None + 属性访问

**测试**：513 passed（511 + 2 新增），零回归

### Batch 6A（2026-03-01）— MapRegistry

**AreaTemplate dataclass** — 9 字段（id, name, region, base_danger, connections, sub_locations, encounter_profile, is_starting_area, tags）

**关键设计决策**：
- **1 个 dataclass 而非原计划的 4 个** — sub_locations 留 `dict[str, dict[str, Any]]`，encounter_profile 留 `dict[str, Any] | None`，符合"从目的推导"原则
- **别名合并 at load time** — base_danger/danger_level → base_danger，connections/adjacent_areas → connections，is_starting_area/starting_area/is_start → is_starting_area
- **sub_locations 支持 list 格式** — 测试中 npc_schedule 使用 `[{"id": "square"}]` 列表格式，load 时归一化为 `dict[str, dict]`

**消费端更新（13 个文件）**：
- `state/base.py`：`_initial_area_payload` 属性访问
- `runtime.py`：`starting_area().id` + `_resolve_starting_location_id` 签名
- `context_assembler.py`：L2 `dataclasses.asdict()` + L3 属性访问（含 StubRegistry 兼容 `is_dataclass` check）
- `navigation.py`：`_validate_enter_sub_location` 属性访问
- `world_state.py`：`_validate_location_in_area` 属性访问
- `npc_schedule.py`：`_is_valid_location` 属性访问
- `encounter.py` (hook)：`_assemble_encounter_context` 属性访问
- `session_store.py`：`_resolve_location_label` 属性访问
- `interaction_service.py` + `interaction.py`：area_sub_locations 构建
- `world.py`：2 验证方法属性访问
- `panels.py`：build_map_panel 全属性访问
- `gm_tools.py`：`dataclasses.asdict()` 序列化

**计划外修正**：
- context_assembler 测试用 `StubRegistry("maps")` 返回 plain dict → `dataclasses.is_dataclass()` 分支处理
- sub_locations list 格式支持（npc_schedule 测试 fixture 用了 `[{"id": "square"}]`）

**测试**：514 passed（513 + 1 新增），零回归

### Batch 6B（2026-03-01）— QuestRegistry（最终批次）

**3 个 dataclass** — MilestoneTemplate（7 字段）、ChapterMeta（3 字段）、InitialEvent（5 字段）

**关键设计决策**：
- **别名合并 at load time** — chapter 的 id/chapter_id → id，event 的 id/event_id → id，conditions/preconditions → conditions
- **conditions 类型保留 `list | dict`** — validate() 允许两种形式
- **格式校验全部移至 build time** — `_build_milestone`/`_build_chapter`/`_build_initial_event` 收集 issues，validate() 仅做语义交叉引用

**消费端更新（3 个文件）**：
- `world.py`：`_validate_milestone_prerequisites` — `.id`/`.prerequisites` 属性访问，去除 isinstance guards
- `state/base.py`：`_initial_quest_payload` — `.id`/`.prerequisites` 属性访问；`_initial_event_payload` — `.id`/`.event_type`/`.conditions`/`.payload`/`.metadata` 属性访问
- `world_state.py`：`_world_has_chapter` — `chapter.id` 属性访问

**测试**：515 passed（514 + 1 新增），零回归

---

## Dataclass 迁移完成汇总

---

## 屎山清理（2026-03-02）

**MonsterRegistry 三 gold 字段合并**：
- 移除 `MonsterTemplate.gold` / `.gold_reward` 两个冗余字段，只保留 `gold_drop`
- load() 时按 `gold_drop → gold → gold_reward` 优先级合并到 `gold_drop`
- `encounter.py._resolve_gold()` 从三 fallback 简化为直接读 `monster.gold_drop`

**ItemRegistry heal/price 字段合并**：
- 移除 `ItemTemplate.heal` / `.restore_hp` / `.price` 三个冗余字段，只保留 `heal_amount` 和 `base_price`
- load() 时 `heal/restore_hp` → `heal_amount`，`price` → `base_price`（别名合并 at load time）
- `_resolve_heal_amount` 从 combat.py + inventory.py 两处私有方法提取为 `handler_utils.resolve_item_heal_amount()`
- economy.py `_base_price_for_item` 删除 `item_template.price` fallback

**测试**：831 passed（+19 含同期 stash pop 带入的其他改动）

---

**全部 10/10 Registry 已迁移完毕。**

| Registry | 状态 | Batch | dataclass 数 |
|----------|------|-------|-------------|
| ~~FactionRegistry~~ | ✅ | Batch 1 | 1 (FactionTemplate) |
| ~~LoreRegistry~~ | ✅ | Batch 1 | 1 (LoreEntry) |
| ~~TagRegistry~~ | ✅ | Batch 1 | 1 (TagDimension) |
| ~~MonsterRegistry~~ | ✅ | Batch 2A | 3 (MonsterTemplate, MonsterAttack, LootEntry) |
| ~~ItemRegistry~~ | ✅ | Batch 2B | 1 (ItemTemplate) |
| ~~SkillRegistry~~ | ✅ | Batch 3 | 1 (SkillTemplate) |
| ~~CharacterRegistry~~ | ✅ | Batch 4 | 2 (CharacterTemplate, ShopInventory) |
| ~~ClassRegistry~~ | ✅ | Batch 5 | 4 (ClassTemplate, SubclassTemplate, RaceTemplate, BackgroundTemplate) |
| ~~MapRegistry~~ | ✅ | Batch 6A | 1 (AreaTemplate) |
| ~~QuestRegistry~~ | ✅ | Batch 6B | 3 (MilestoneTemplate, ChapterMeta, InitialEvent) |
| **合计** | | Batch 0~6B | **18 dataclass** |

---

## [F-D] Connection typed dataclass + MapRegistry 扩展

**日期**：2026-03-02

**背景**：`AreaTemplate.connections` 原为 `list[str]`，丢失旅行时间/连接类型元数据；
`NavigationHandler` 无连接存在性校验，`time_cost` 硬编码 1.0。

**改动**：

1. **新增 `Connection` dataclass**（`maps.py`）：
   - 字段：`target: str`, `type: str = "travel"`, `travel_time_minutes: int = 60`, `travel_slots: int = 1`, `description: str = ""`, `tags: list[str]`
   - `source` 隐含（AreaTemplate 拥有），`blocked` 是运行时状态（AreaState 管理）

2. **`AreaTemplate.connections: list[Connection]`**（原 `list[str]`）

3. **`MapRegistry` 扩展**：
   - `_load_connections()` 返回 `list[Connection]`（接受 str 和 dict 两种格式）
   - 新增 `_parse_connection_entry()` — 解析单条 entry（str → Connection，dict → Connection）
   - 新增 `_parse_travel_minutes(s)` — "30分钟"→30, "2小时"→120, 无法解析→60
   - 新增 `get_connections(area_id) -> list[Connection]`
   - 新增 `get_connection(from_id, to_id) -> Connection | None`
   - `get_adjacent()` 保持 `list[str]`（向后兼容，提取 `conn.target`）

4. **消费端同步**：
   - `world_knowledge_graph.py` 第 355 行：`conn.target` 替换直接使用 `connected_id`

**[T-2] world_data_loader.py 数据管线修复**（同步）：

- `_load_maps()` 简化为直接透传 dict，不再剥离 connection 对象（MapRegistry 统一解析）
- `_load_items()` 新增 properties 展平：`adapted.pop("properties")` → 子字段提取到顶层（已有字段优先）

**测试**：`test_content_registries.py` 新增 4 个测试（Connection dataclass / 时间解析 / get_connections / get_adjacent 向后兼容）；`test_navigation_handler.py` 补 connections；`test_world_data_loader.py` 更新连接断言

---

## [F-E] ClassTemplate class_resources_schema

**日期**：2026-03-02

**背景**：`GrowthHandler._compute_level_up()` 已处理 level_features（特性字符串），但不初始化 `class_resources`；`ClassTemplate` 无 resource 配置字段。

**改动**：

1. **`ClassTemplate.class_resources_schema: dict[str, Any]`**（`classes.py`）：
   - 格式：`{"resource_key": {"max_at_level": {"2": 1, "17": 2}, "recovery": "short_rest"}}`
   - 新增 `_load_class_resources_schema()` 验证：is Mapping + 每 value 含 max_at_level(Mapping) + recovery(str)

**测试**：`test_content_registries.py` 新增 2 个测试（正确加载 + 格式错误 load_issues）

---

## [T-2b] 数据管线修复（world_data_loader.py 补全）

**日期**：2026-03-02

**背景**：T-2 只处理了 characters/items/maps，对 monsters 和 skills 的格式差异未适配，导致战斗系统和技能系统在真实数据下实质性失效（所有怪物 hp/ac=None，108 条角色技能全部丢失）。

**改动**（`app/world_data_loader.py`）：

- **M1-M5 `_load_monsters` 全面重写**：
  - M1: `stats` 子对象展平 → 顶层 `hp`/`ac`；六维属性构建 `abilities` Mapping
  - M2: `type` → `creature_type` 别名
  - M3: `challenge_rating`（中文等级）→ `_CR_TIER_MAP` 映射为 float cr（白瓷=0.25…青铜=12.0）
  - M4: `attacks[].damage` → `attacks[].damage_dice` 重命名（兼容 `roll_damage_dice` 格式）
  - M5: 过滤 `stats.hp == 0` 的规则描述条目（"世界生态系统…" 等）
- **S1 `_load_skills` 递归展平**：`_flatten()` 递归处理嵌套列表，108 条角色技能恢复加载
- **I1 `_load_items` 价格解析**：`_parse_price_sp()` 解析 "30 sp"/"5 gp" → int 银币；`base_price` 自动填充
- **常量**：新增 `_CR_TIER_MAP` + `_parse_price_sp()` 工具函数

**测试**：`test_world_data_loader.py` 新增 8 个断言（monsters stats/abilities/creature_type/attacks/hp过滤/cr数值、skills数量≥50、items base_price）

**基线**：906 passed（+13 相对 893，含本次 8 个新测试）

---

## [增量执行计划] Batch 1-1a — shared_types + map_types 新建

**日期**：2026-03-03
**基线**：906 → 932 passed（+26）

**背景**：`AreaTemplate.sub_locations` 计划迁移为 `dict[str, SubLocationTemplate]` typed struct（Batch 1-1b）。
1-1a 是纯增量前置阶段：新建类型定义文件和导出，不触碰现有 maps.py 类型变更。

**新建文件（2 个）**：

1. **`app/game_core/content/registries/shared_types.py`**：
   - `Effect` dataclass（type/params/target/tags）— 跨 registry 共享，用于 ConsumableData + AccessoryData（后续 batch）
   - `LootTableDef` dataclass（gold/items）— items 用 `list[Any]` 避免与 monsters.py 循环 import

2. **`app/game_core/content/registries/map_types.py`**：
   - 10 个 dataclass（全部 `@dataclass(slots=True)`，全字段有默认值）：
     `CheckPath`, `TrapData`, `ContainerData`, `InteractableTemplate`,
     `HostileGroup`, `HostileConfig`, `HostileTemplate`,
     `Discovery`, `SubAreaClusterConfig`, `SubLocationTemplate`
   - `ContainerData.loot` / `HostileTemplate.hostile_config` 使用 `field(default_factory=...)` 避免可变默认值

**修改文件（1 个）**：
- `app/game_core/content/registries/__init__.py`：追加 12 个新类型的 import 和 `__all__` 导出

**新建测试（1 个）**：
- `tests/test_map_types.py`：26 个测试覆盖所有新 dataclass 的默认值 + 完整值 + 嵌套构造 + `__init__` re-export

## [增量执行计划] Batch 1-1b — maps.py 类型变更 + 消费端修复

**日期**：2026-03-03
**基线**：932 → 939 passed（+7 MapRegistry 集成测试）

**主要改动**：

1. **`app/game_core/content/registries/maps.py`**（主要）：
   - `AreaTemplate.sub_locations` 类型：`dict[str, dict[str, Any]]` → `dict[str, SubLocationTemplate]`，移除 TODO 注释
   - `AreaTemplate` 新增 5 字段：`description`/`danger_level`（str）/`discoveries`/`hostile_pool`/`sub_area_cluster_config`
   - `_load_sub_locations()` 完全重写：返回 `dict[str, SubLocationTemplate]`，list/dict 两种格式均支持
   - 新增 10 个 `_build_*()` helper：`_build_sub_location`/`_build_interactable`/`_build_check_path`/`_build_trap_data`/`_build_container_data`/`_build_hostile_config`/`_build_hostile_group`/`_build_hostile_template`/`_build_discovery`/`_build_cluster_config`
   - `_build_template()` 追加新字段提取逻辑
   - `base_danger` 解析：不再把字符串 `danger_level` 当 float 别名，只在 `danger_level` 为数值时向后兼容

2. **消费端修复（各 2-5 行）**：
   - `app/routers/panels.py`：`isinstance(Mapping)` → `getattr(.name)` + Mapping fallback
   - `app/game_core/adapters/session_store.py`：`isinstance(Mapping)` → `is not None` + `getattr(.name)`
   - `app/game_core/orchestration/context_assembler.py`：加 `elif dataclasses.is_dataclass(location_data): template = dataclasses.asdict(location_data)`
   - `app/game_core/narrative/context_builder.py`：同上

3. **测试修复**：
   - `tests/test_content_registries.py:592`：`sub_locations["inn"]["name"]` → `sub_locations["inn"].name`
   - `tests/test_map_types.py`：追加 7 个 MapRegistry 集成测试

**不需要改动的文件**（key-only 访问，类型变更透明）：
`navigation.py` / `world_state.py` / `interaction.py` / `interaction_service.py` / `npc_schedule.py` / `runtime.py` / `world_knowledge_graph.py` / `gm_tools.py`

## [增量执行计划] Batch 1-2 — ClassRegistry 扩展

**日期**：2026-03-03
**基线**：939 → 957 passed（+18 新测试）

**主要改动**：

1. **新建 `app/game_core/content/registries/class_types.py`**：
   - `ResourceConfig`（max_at_level/recovery）
   - `Feature`（id/name/description/type/skill_id/resource_config）
   - `SpellcastingConfig`（stat/cantrips_known/spell_slots/spells_known/prepared_formula）

2. **`app/game_core/content/registries/classes.py`**（主要）：
   - `ClassTemplate` 新增 7 字段：`tags`/`armor_proficiency`/`weapon_proficiency`/`save_proficiency`/`skill_choices`/`spellcasting: SpellcastingConfig|None`/`subclass_options`
   - `SubclassTemplate` 新增 6 字段：`name`/`description`/`tags`/`requirements`/`additional_proficiency`/`additional_spellcasting: SpellcastingConfig|None`
   - `RaceTemplate` 新增 4 字段：`tags`/`speed`（默认 30）/`languages`/`size`（默认 "medium"）
   - `BackgroundTemplate` 新增 2 字段：`tool_proficiency`/`equipment`
   - 新增 `_build_spellcasting_config()` helper（双层 Mapping 解析 spell_slots，stat 缺失记 issue）
   - 向后兼容回填：SpellcastingConfig.stat → `spellcasting_ability`，SpellcastingConfig.prepared_formula → `prepared_formula`
   - 全部字段均有默认值，**零破坏性变更**

3. **`app/game_core/content/registries/__init__.py`**：新增 `Feature`/`ResourceConfig`/`SpellcastingConfig` 导出

4. **新建测试 `tests/test_class_types.py`**（18 个测试）：
   - 3 个 dataclass 默认值 + 完整值测试
   - `__init__` re-export 验证
   - ClassRegistry 集成：spellcasting 子对象解析 + 回填 / 只有拍平字段 / stat 缺失 issue / proficiency 字段
   - SubclassTemplate / RaceTemplate / BackgroundTemplate 新字段加载验证

**不需要改动的消费端**：
`spell.py` 继续读 `spellcasting_ability`（拍平字段），`growth.py` 继续读 `level_features`，两者均无需修改。

## [增量执行计划] Batch 1-3 — CharacterTemplate 战斗字段 + ShopEntry typed 化

**日期**：2026-03-03
**基线**：957 → 962 passed（+5 新测试）

**主要改动**：

1. **`app/game_core/content/registries/characters.py`**：
   - 新增 `NpcAttack` dataclass（6 字段：name/hit_bonus/damage_dice/damage_type/range/tags）
   - 新增 `ShopEntry` dataclass（4 字段：item_id/count/min_player_level/restock）
   - `ShopInventory` 变更：`base_pool`/`rotating_pool` 类型 `list[dict]` → `list[ShopEntry]`；新增 `level_scaling: bool = False`
   - `CharacterTemplate` 新增 9 个 NPC 战斗字段：`base_hp`/`base_ac`/`stats`/`level`/`proficiency_bonus`/`combat_capable`/`attacks`/`skills`/`secrets`
   - 新增 `_build_npc_attacks()` helper（模式参考 monsters.py `_load_attacks()`）
   - `_load_pool()` 返回类型 `list[dict]` → `list[ShopEntry]`，逐项构建 ShopEntry（缺 item_id → issue + skip）
   - `_build_template()` 追加战斗字段提取逻辑；`combat_capable` 未显式设置时自动推导（有 attacks 或 base_hp → True）

2. **`app/game_core/rules/handlers/economy.py`**：
   - 新增 `from app.game_core.content.registries.characters import ShopEntry`
   - `_normalize_shop_entry()`：ShopEntry 分支（属性访问）+ Mapping 分支（向后兼容）；ShopEntry 的 `count=="unlimited"` → `unlimited=True`，`price_override=None`
   - `_select_rotating_entries()`：去掉 `isinstance(entry, Mapping)` guard，改为 ShopEntry/Mapping 两分支提取 `min_player_level`
   - base pool 调用处 `get_non_empty_string(entry, "item_id")` → `entry.item_id if isinstance(entry, ShopEntry)`

3. **`app/game_core/content/world.py`**（计划外）：
   - `_validate_item_refs()` 原有 `isinstance(entry, Mapping)` guard 导致 ShopEntry 被跳过，改为 Mapping/其他 两分支（Mapping 用 `.get()`，其他用 `getattr()`）

4. **`app/game_core/content/registries/__init__.py`**：新增 `NpcAttack`/`ShopEntry` 导出

5. **测试修复 + 新增**（`tests/test_content_registries.py`）：
   - 修复 `test_character_shop_inventory_typed`：`base_pool[0]["item_id"]` → `base_pool[0].item_id`
   - 新增 5 个：`test_npc_attack_dataclass` / `test_shop_entry_dataclass` / `test_character_combat_fields_load` / `test_character_combat_fields_defaults` / `test_character_shop_pool_typed`

**关键设计决策**：
- `NpcAttack` 与 `MonsterAttack` 相似但不合并：NpcAttack 多 `range`/`tags`，MonsterAttack 无这两字段，职责分离
- `ShopEntry.count` 为字符串（"1"/"unlimited"/"1d4+1"），economy.py 解析时 `count=="unlimited"` → unlimited，否则 `coerce_int(count) or 1`
- `price_override` 未加入 ShopEntry — 设计规范未包含此字段，typed pool 统一走 world registry 价格查询
- world.py `_validate_item_refs` 用 `getattr()` 兼容，不 import ShopEntry（避免跨层依赖）

## [增量执行计划] Batch 1-4 — MonsterTemplate / FactionTemplate / LoreRegistry 补全

**日期**：2026-03-03
**基线**：957 → 971 passed（+14 新测试）

**主要改动**：

1. **`app/game_core/content/registries/monsters.py`**：
   - `MonsterTemplate` 新增 7 字段：`description`/`vulnerabilities`/`speed`（默认 30）/`flee_chance`（默认 0.5，配合现有 `flee_threshold`）/`tactics_notes`/`preferred_terrain`/`group_size`（默认 "solo"）
   - 复用现有 `_load_list_of_strings()` 处理 vulnerabilities/preferred_terrain
   - `flee_chance` 验证：非法值（越界 0~1）记 issue，回退到 0.5

2. **`app/game_core/content/registries/factions.py`**：
   - `FactionTemplate` 新增 3 字段：`influence_areas`/`leader_id`/`member_ids`
   - leader_id 为 str|None，空字符串归 None

3. **`app/game_core/content/registries/lore.py`**（主要改写）：
   - `LoreEntry` 新增 2 字段：`scope`（默认 "global"）/`scope_id`
   - 新增 `WorldRule` dataclass（7 字段）
   - `LoreRegistry` 扩展：`_rules` 存储 + `get_rule()`/`list_rules()`/`list_rules_by_scope()`（过滤+降序排列）
   - `load()` 支持结构化格式 `{"entries":..., "rules":...}` 和旧平坦格式（向后兼容）
   - 新增 `_build_lore_entry()` / `_build_world_rule()` helpers（原 load 内联逻辑提取）
   - `validate()` 加 rules 遍历

4. **`app/game_core/content/registries/__init__.py`**：新增 `WorldRule` 导出

**不需要改动的消费端**：所有消费端字段有默认值，零破坏性变更。

## [增量执行计划] Batch 1-5 — QuestRegistry / SkillTemplate / ItemRegistry 补全

**日期**：2026-03-03
**基线**：962 → 977 passed（+6 新测试，其余为 Batch 1-4 带入）

**前置依赖**：`shared_types.py`（Effect）已由 Batch 1-1 完成。全部纯增量，零破坏性变更。

**主要改动**：

1. **`app/game_core/content/registries/quests.py`**：
   - 新增 `MilestoneCondition` dataclass（3 字段：type/params/optional）
   - `MilestoneTemplate` 新增 9 字段：`completion_value`/`sequence`/`narrative_context`/`key_elements`/`involved_npcs`/`involved_locations`/`success_conditions`/`failure_conditions`/`failure_fallback`
   - 新增 `_build_conditions()` helper（缺 type / 非 Mapping → issue + skip）
   - 更新 `_build_milestone()` 提取并传入全部新字段

2. **`app/game_core/content/registries/skills.py`**：
   - `SkillTemplate` 新增 2 字段：`requirements: dict[str, Any]`（默认 {}）/`usable_in: list[str]`（默认 []）
   - 更新 `load()` 中 `SkillTemplate(...)` 构造调用

3. **`app/game_core/content/registries/items.py`**：
   - 新增 `from .shared_types import Effect` 和 `from typing import Mapping`
   - `_ITEM_TYPES` 添加 `"accessory"`（设计文档未显式说明，但 `_build_accessory_data` 需要）
   - 新增 `AccessoryData` dataclass（2 字段：slot/effects: list[Effect]）
   - `ItemTemplate` 新增 `accessory_data: AccessoryData | None = None`
   - 新增 `_build_accessory_data(raw, item_type)` static helper
   - 更新 `load()` 调用 + `ItemTemplate(...)` 传入 `accessory_data`

4. **`app/game_core/content/registries/__init__.py`**：新增 `MilestoneCondition`/`AccessoryData` 导出

**关键设计决策**：
- `MilestoneCondition.type` 不做白名单校验（设计规范注释：仅检查非空，白名单留 validate 时处理）
- `AccessoryData.slot` 同理（head/neck/cloak/hands/finger/feet 白名单留 validate）
- `SkillTemplate.requirements` 留 `dict[str, Any]`，不做 typed struct（结构因技能类型而异）
- `ConsumableData.effect` 保留 `dict[str, Any]`，TODO 注释已存在，留下轮处理

## [增量执行计划] Batch 1-6 — WorldInstance 跨 registry 引用校验

**日期**：2026-03-03
**基线**：971 → 984 passed（+13 新测试，含 7 个新 B6 测试 + 已有 6 个因 B6 结构更新自然增加）

**主要改动**：

1. **`app/game_core/content/world.py`**（主要）：
   - `_validate_cross_registry_refs()` 追加 8 个 has_registry guard + 方法调用
   - 新增 8 个私有校验方法：
     - `_validate_sub_location_npc_refs()`：sub_location.resident_npcs → CharacterRegistry
     - `_validate_hostile_pool_monster_refs()`：area.hostile_pool[].hostile_config.hostile_groups[].monster_ids → MonsterRegistry
     - `_validate_sub_location_loot_refs()`：interactable.container_data.loot.items → ItemRegistry（支持 str / Mapping 两种 item 格式）
     - `_validate_discovery_reward_refs()`：discovery.reward type=="item" → ItemRegistry
     - `_validate_milestone_npc_location_refs()`：milestone.involved_npcs → CharacterRegistry，milestone.involved_locations → MapRegistry
     - `_validate_faction_member_refs()`：faction.leader_id / member_ids → CharacterRegistry
     - `_validate_faction_area_refs()`：faction.influence_areas → MapRegistry
     - `_validate_world_rule_scope_refs()`：WorldRule scope_id → MapRegistry(area) / FactionRegistry(faction)

2. **Task A（__init__.py 导出）**：已由外部改动完成，零工作量。

3. **`tests/test_world_instance.py`**：追加 7 个测试函数（valid + missing 两条路径各覆盖）。

**内容层完整性状态**：Batch 1-1 ~ 1-4 + 1-6 已完成（跳过 1-3/1-5，由外部完成）。WorldInstance 现共有 17 条跨 registry 校验。

---

## R-2a：MonsterRegistry 修正（2026-03-03）

**测试基线**：984 → 1008（+24，含本批 7 个）

**改动原因**：MonsterAttack 缺 range/tags 字段；gold_drop/LootEntry.count 类型与设计文档不符（应为骰子表达式 str）；MonsterTemplate 缺 skills 字段。

**主要改动**：

1. **`app/game_core/content/registries/monsters.py`**：
   - `MonsterAttack`：新增 `range: int = 1`、`tags: list[str]`；`_load_attacks()` 追加读取
   - `LootEntry.count`：`int = 1` → `str = "1"`；`_load_loot_table()` 改为 str 直存，不再做非负整数校验
   - `MonsterTemplate.gold_drop`：`int | None = None` → `str = "0"`；load() 三别名合并逻辑改为 str 转换（int 别名向后兼容）；`MonsterTemplate.skills`：新增 `list[str]`，`_load_list_of_strings` 提取

2. **`app/game_core/rules/handlers/encounter.py`**（消费端同步）：
   - import `roll_damage_dice`
   - `_resolve_gold()`：改为 str 解析（"0"→0，纯数字→int，骰表达式→roll）
   - `_resolve_loot_items()`：count str→int 解析，count<=0 跳过

3. **`tests/test_content_registries.py`**：
   - 更新 `test_monster_registry_validates_combat_and_loot_fields`：删除 gold_drop=-1/count=-1 的旧验证断言（str 类型不在 load 时报 invalid）
   - 更新 `test_monster_registry_basic`：`gold_drop == "5"`
   - 更新 `test_monster_loot_entry_typed`：`count == "3"` / `"1"`
   - 新增 6 个测试（MonsterAttack range/tags、gold_drop str 默认、int 向后兼容、LootEntry count str、skills 字段）

4. **`tests/test_encounter_handler.py`**：新增 `test_resolve_gold_dice_expressions`

---

## R-2b：其他 Registry 字段修正（2026-03-03）

**测试基线**：1008 → 1015（+7，含本批 6 个）

**改动原因**：ConsumableData.effect 存为 dict 与设计文档不符；behavioral_rules 应为 list；AreaTemplate 缺 terrain_type；Connection 缺 blocked 字段。

**主要改动**：

1. **`app/game_core/content/registries/items.py`**：
   - `ConsumableData.effect: dict[str, Any]` → `Effect | None = None`
   - `_build_consumable_data(heal_amount)` → `_build_consumable_data(raw, heal_amount)`：扩展为三路解析（consumable_data 子 Mapping 优先 → heal_amount fallback → 顶层 effect 字段）
   - 调用方改为 `self._build_consumable_data(raw, _heal_amount)`

2. **`app/game_core/rules/handler_utils.py`**（消费端同步）：
   - 追加 `from app.game_core.content.registries.shared_types import Effect`
   - `resolve_item_heal_amount()`: `isinstance(effect, dict)` → `isinstance(effect, Effect)`，属性访问改为 `.type` / `.params`

3. **`app/game_core/content/registries/factions.py`**：
   - `behavioral_rules: str = ""` → `list[str]`
   - load() 新增三路解析：list 格式、旧单字符串向后兼容 → 单元素 list、其他 → []

4. **`app/game_core/content/registries/maps.py`**：
   - `Connection.blocked: bool = False` + `_parse_connection_entry()` 读取
   - `AreaTemplate.terrain_type: str = ""` + `_build_template()` 读取

5. **`tests/test_content_registries.py`**：
   - 更新 2 处已有断言（effect dict 访问 → .type/.params 属性；behavioral_rules str → list）
   - 新增 6 个测试（consumable_data 子 Mapping、顶层 effect、heal_amount compat、behavioral_rules list/str、terrain_type + blocked）

---

## R-2c：ClassRegistry 子类型修正（2026-03-03）

**测试基线**：1015 → 1019（+4）

**改动原因**：R-1b 已实现 `_build_feature()`；本 batch 将 racial_traits / background.feature 从简单类型升级为 Feature typed struct。

**主要改动**：

1. **`app/game_core/content/registries/classes.py`**：
   - `RaceTemplate.racial_traits: list[str]` → `list[Feature]`；`_build_race_template()` 替换 `_load_validated_string_list` 为 dual-format 解析（str 简写 → Feature；Mapping → `_build_feature()`；空白字符串仍发出 load_issue）
   - `BackgroundTemplate.feature: str = ""` → `Feature | None = None`；`_build_background_template()` 改为三路解析（str→Feature；Mapping→`_build_feature()`；空白字符串仍发出 issue）

2. **`app/game_core/rules/handlers/growth.py`**（消费端同步）：
   - 追加 `from app.game_core.content.registries.class_types import Feature`
   - `_resolve_class_features()` 两处改为 Feature-aware 提取（racial_traits + background.feature），保留 str 向后兼容 fallback

3. **`tests/test_content_registries.py`**：
   - 更新 2 处已有断言（racial_traits==["versatile"] → Feature 对象检查；bg.feature=="military_rank" → feature.id 检查）
   - 新增 4 个测试（str 简写、Mapping 格式、background Feature、GrowthHandler E2E）

---

### [D-R4] R-4 — Registry Query API 补全（2026-03-03）

**测试基线**：1031 → 1039（+8）

**改动原因**：各 Registry 只有核心 `get()` / `list_all()`，设计文档要求的查询方法缺失。补全有消费端的 8 个必要方法，纯加方法无破坏性变更。

**主要改动**：

1. **`app/game_core/content/registries/maps.py`**：
   - 新增 `get_sub_location(area_id, loc_id) -> SubLocationTemplate | None`（封装 `area.sub_locations.get(loc_id)`）

2. **`app/game_core/content/registries/factions.py`**：
   - 新增 `get_factions_in_area(area_id)` — 按 `influence_areas` 过滤
   - 新增 `get_relations_of(faction_id)` — 返回 `faction_relations` 副本，不存在返回 `{}`
   - 新增 `get_behavioral_rules(faction_id)` — 返回 `behavioral_rules` 副本，不存在返回 `[]`

3. **`app/game_core/content/registries/lore.py`**：
   - 新增 `get_rules_for_context(*, chapter_id, area_id, faction_ids)` — 合并 global + chapter + area + faction scope，按 priority 降序返回

4. **`app/game_core/content/registries/skills.py`**：
   - 新增 `get_by_category(category)` / `get_combat_skills()` / `get_exploration_skills()`
   - **顺带修正**：`load()` 对非法术条目从未读取原始 `category` 字段（始终为 `""`），导致 `get_by_category("martial")` 等永远返回空。修复：非法术条目追加读取 `raw.get("category")`

5. **`tests/test_content_registries.py`**：
   - 新增 8 个测试（每方法 1 个函数，含正向 + 空结果/miss 断言）

---

## [P28 Track B-1] maps.json 子地点簇扩展（2026-03-12）

**测试基线**：2602 → 2654 passed（+52 新测试）

**背景**：P28 Wave 2 内容数据深化。frontier_town 现有 6 个子地点中 3 个（blacksmith_shop/town_square/north_gate）无 rooms；tavern/adventurer_guild/mother_earth_temple 各需补充 rooms。新增 3 个子地点（market_plaza/training_ground/back_alley）。cow_girl_farm 3 个子地点全部缺 rooms。

**改动**（`data/goblin_slayer/v2/maps.json`）：

1. **adventurer_guild** +2 rooms（archive_room dc=10, basement_armory dc=12）→ 5 rooms total
2. **mother_earth_temple** +2 rooms（herb_garden 非 discoverable, confession_room dc=0）→ 4 rooms total
3. **tavern** +3 rooms（kitchen dc=0, wine_cellar dc=8, back_yard 非 discoverable）→ 5 rooms total
4. **blacksmith_shop** 新建 4 rooms（forge/display_wall/quench_yard/hidden_vault dc=14），default_room=forge
5. **town_square** 新建 4 rooms（fountain_plaza/notice_board/merchant_corner/old_well dc=10），default_room=fountain_plaza
6. **north_gate** 新建 3 rooms（guard_post/watchtower dc=0/supply_depot），default_room=guard_post
7. **market_plaza** 新增子地点，4 rooms（open_market/tea_house dc=0/fortune_teller dc=8/back_stall dc=12），default_room=open_market
8. **training_ground** 新增子地点，3 rooms（sparring_ring/equipment_shed dc=0/archery_range），default_room=sparring_ring
9. **back_alley** 新增子地点，3 rooms（narrow_path/dead_end dc=8[informant]/sewer_entrance dc=14），default_room=narrow_path
10. **cow_girl_farm/main_house** +2 rooms（living_room[cow_girl]/dining_area），default_room=living_room
11. **cow_girl_farm/gs_warehouse** +2 rooms（storage_area/workbench），default_room=storage_area
12. **cow_girl_farm/farm_field** +2 rooms（pasture/well_area），default_room=pasture

**验收数据**：
- frontier_town：9 sub_locations，35 rooms，17 discoverable
- cow_girl_farm：3 sub_locations，6 rooms

**新建测试**：`tests/test_p28_maps_rooms.py`（52 个测试，涵盖全部新 rooms 结构验证）

**设计决策**：
- rooms 格式沿用 dict（与 P27 现有 rooms 一致，MapRegistry 也支持 list 格式但统一用 dict）
- traveling_merchant / informant 写入 resident_npcs — MapRegistry 只做字符串列表，不校验 NPC 存在性；Agent 3 并行添加这两个 NPC
- discoverable 共 17 个（设计文档描述 16，实际按数据表格推算为 17，以数据表格为准）

### [D-P28b] P28 Wave 2 Track B-2 — quests + characters + classes 数据填充

**完成时间**：2026-03-12

**变更内容**：

#### quests.json — 7 里程碑完整重写
- **章节**：ch2 改为"边境的暗流"
- **7 个新里程碑**（旧 7 个全部替换）：
  - ms_arrival（序列1）：npc_talked[guild_girl]；xp:100, gold:50
  - ms_town_life（序列2）：location_visited×3；xp:200, gold:100
  - ms_party_encounter（序列3）：npc_talked×2 + level_reached[2]；xp:300, gold:150
  - ms_growing_shadow（序列4）：npc_talked×2；xp:400, gold:200, items:[healing_potion×2]
  - ms_into_the_wilds（序列5）：location_visited[ancient_ruins]；xp:300, gold:100
  - ms_the_hive（序列6）：location_visited + kill_count[goblin≥8]；xp:800, gold:500, items:[quality_healing_potion]
  - ms_water_capital_call（序列7）：npc_talked[cow_girl] + level_reached[3]；xp:500, gold:300
- **rewards.items 格式**：dict 数组 `[{"item_id":"...","count":N}]`（board.py 只支持 Mapping 格式）
- **新条件类型**：`level_reached` — Wave 3 实现，当前 EventEngine 遇到未知类型返回 False（不 crash）

#### items.json — 新增 quality_healing_potion
- 用于 ms_the_hive 奖励，uncommon，heal 4d4+4

#### tags.json — 新增标签
- `investigation`、`tension`、`revelation`（quests.json 使用）
- `worldly`、`shady`（新 NPC 使用）

#### characters.json — 3 新 NPC + 5 schedule 更新
**新 NPC**：
- `traveling_merchant`：market_plaza，tags:[merchant,commoner,worldly]，有 shop_inventory
- `informant`：back_alley，tags:[commoner,shady]，无 faction_id
- `tavern_waitress`：tavern，tags:[commoner,warm]

**schedule 更新**（5 个）：
- priestess：dawn+day=mother_earth_temple, dusk=adventurer_guild, night=mother_earth_temple
- dwarf_shaman：dawn=adventurer_guild, day+dusk=tavern, night=adventurer_guild
- lizard_priest：dawn+day=mother_earth_temple, dusk+night=adventurer_guild（location_id 也从 guild_hall 改为 adventurer_guild）
- high_elf_archer：dawn=training_ground, day=market_plaza, dusk=adventurer_guild, night=tavern
- goblin_slayer：已正确（无需改动）

**注意**：Edit 工具在替换 high_elf_archer 段落时引入了 U+201C/U+201D 曲引号作为 JSON 结构引号，通过全文替换 + 转义内容层曲引号修复。

#### classes.json — 6 职业初始装备
- fighter: cheap_shortsword + round_shield + dirty_chain_mail + healing_potion×2; equipped: main_hand/off_hand/chest
- priest: sturdy_spear + quilted_gambeson + healing_potion×3 + holy_water_flask; equipped: main_hand/chest
- ranger: scouts_hand_axe + leather_armor + healing_potion×2 + hempen_rope + exploration_torch; equipped: main_hand/chest
- wizard: sturdy_spear + quilted_gambeson + healing_potion×2; equipped: main_hand/chest
- martial_artist: leather_armor + healing_potion×2 + bandage_roll; equipped: chest
- scout: throwing_dagger + leather_armor + healing_potion×2 + hempen_rope; equipped: main_hand/chest
- 全部 starting_gold: 50

**测试变更**：
- `tests/test_33_quest_completion.py` — TestQuestsJsonData 类全部更新为新里程碑 ID 和链路
- 测试基线：2602 → 2671（+69）

---

### [D-C-P29-A3] P29-A3: base_danger 数值化（2026-03-13）

**问题**：`maps.json` 的 4 个区域只有语义字符串 `danger_level: "high"`，没有 `base_danger` 数值 → `HostileAreaHandler` 的危险等级一律 fallback 1.0，战斗触发和区域风险感失真。

**改动**：
1. `data/goblin_slayer/v2/maps.json` — 4 区域加 `base_danger` 字段：
   - frontier_town: 0.2, cow_girl_farm: 0.3, water_capital: 0.6, ancient_ruins: 1.2
2. `app/game_core/content/registries/maps.py` — `_build_template()` 加 string→float fallback：
   - 当 `base_danger` 缺失时，按 `danger_level` 字符串转换：none=0.0, low=0.3, medium=0.6, high=1.0, extreme=1.5
   - 未知字符串不崩溃，base_danger 保持 None

**测试**：`tests/test_content_registries.py` 新增 3 个测试（numeric priority / string fallback / unknown string no crash）。

---

### [D-C-P29-A6a] P29-A6a: 遗迹固定敌人 hostile_config 数据（2026-03-13）

**问题**：`ancient_ruins` 的内部子地点没有 `hostile_config` → Planner 无法知道哪些子地点应放置固定敌人，战斗无法按设计触发。

**改动**：`data/goblin_slayer/v2/maps.json` — ancient_ruins 3 个核心子地点加 `hostile_config`：

| 子地点 | monster_ids | stealth_dc | blocking | role |
|--------|------------|------------|----------|------|
| outer_cloisters | goblin×3 | 12 | false | patrol（哨兵，可潜行绕过） |
| sacrificial_altar | hobgoblin+goblin×2 | 14 | true | guard（祭坛守卫，必须清除） |
| inner_sanctum | goblin_rider+hobgoblin+goblin×2 | 16 | true | guard（Boss 房精锐） |

**验证**：`_build_hostile_config()` + `_build_hostile_group()` 在 maps.py 已有完整实现，无需修改；SubLocationTemplate.hostile_config 字段已存在。

**测试**：`tests/test_content_registries.py` 新增 2 个测试（sub_location hostile_config 解析 / goblin_slayer maps.json 实际文件验证）。
