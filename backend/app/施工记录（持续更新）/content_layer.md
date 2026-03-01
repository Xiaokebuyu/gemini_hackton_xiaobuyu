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
