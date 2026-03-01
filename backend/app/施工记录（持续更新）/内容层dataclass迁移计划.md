# 内容层 Dataclass 全链路迁移计划

> 方案 B：Registry 内部 dataclass 化 + 所有消费端同步改为 typed 属性访问
> 基线：500 passed | 制定日期：2026-03-01

---

## 一、迁移原则

1. **每批自闭合** — 每批改完后系统完全可用，测试全绿
2. **先建模型，再改 Registry，最后改消费端** — 每批内部三步走
3. **不改公共 API 签名** — `get(id)` 返回类型从 `dict | None` 改为 `XxxTemplate | None`，方法名不变
4. **validate() 迁移到构造时** — dataclass `__post_init__` 承担字段校验，validate() 保留为兼容入口但委托给构造检查
5. **防御性降级** — 加载失败的条目记录 issue 但不阻断整个 registry

---

## 二、消费端热力图

| Registry | 消费文件数 | 访问点数 | 最重消费端 | 迁移优先级 |
|----------|-----------|---------|-----------|-----------|
| ClassRegistry | 2 | ~25+ | growth.py（创角/升级/子职） | 高（字段最密集） |
| ItemRegistry | 4 | ~23 | economy.py（价格链）| 高 |
| CharacterRegistry | 5 | ~15 | economy.py（商店配置）| 高 |
| MapRegistry | 6 | ~11 | navigation + context_assembler | 中 |
| MonsterRegistry | 2 | ~9 | combat + encounter | 中 |
| SkillRegistry | 2 | ~3 | spell.py | 中 |
| QuestRegistry | 2 | ~2 | world_state.py | 低 |
| FactionRegistry | 1 | ~0 | ai_osiris（list_all 遍历） | 低 |
| LoreRegistry | 1 | ~0 | ai_osiris（list_all 遍历） | 低 |
| TagRegistry | 0 | 0 | 无直接消费 | 低 |

---

## 三、分批计划

### Batch 0：基础设施（模式建立）

**目标**：在 base.py 中建立 dataclass 迁移的共用模式

**改动**：
- `base.py`：新增 `ContentModel` 协议或基类（可选，看是否需要）
- 确认 `load()` 的新模式：`raw dict → dataclass(**fields)` + issue 收集
- 确认 `get()` 的新返回类型约定

**体量**：~20 行，低风险，1 个测试

---

### Batch 1：简单三件套（Faction + Lore + Tag）

**目标**：用最简单的 registry 验证迁移模式

| Registry | 新 dataclass | 字段数 | 消费端改动 |
|----------|-------------|-------|-----------|
| FactionRegistry | `FactionTemplate` | 6（id, name, description, alignment, relations, tags） | ai_osiris.py `_build_rules_context`（list_all 遍历） |
| LoreRegistry | `LoreEntry` | 4（id, content, tags, title） | ai_osiris.py `_build_rules_context`（list_all 遍历） |
| TagRegistry | `TagDimension` | 3（id, description, tags） | ai_osiris.py `_build_rules_context`（list_all 遍历） |

**消费端**：仅 `ai_osiris.py`（3 个 list_all 循环），当前已用 `isinstance(entry, Mapping)` 防御 → 改为直接属性访问

**体量估算**：
- 3 个 dataclass 定义：~60 行
- 3 个 Registry 重构：~90 行改动
- 1 个消费端更新（ai_osiris.py 3 处）：~30 行
- 测试：~5-8 个新测试
- **总计：~200 行改动，2-3 小时**

---

### Batch 2：Monster + Item（中等复杂度，高消费频率）

#### 2A：MonsterRegistry

| 新 dataclass | 字段 |
|-------------|------|
| `MonsterTemplate` | id, name, hp, ac, cr, creature_type, abilities, resistances, immunities, gold_drop, loot_table, attacks |
| `MonsterAttack` | name（+ 未来扩展：damage_dice, attack_bonus） |
| `LootEntry` | item_id, chance, count |

**消费端**：
- `combat.py`：hp, ac, name（~5 点）
- `encounter.py`：gold_drop, loot_table, name, rarity（~4 点）

**体量**：3 个 dataclass ~50 行 + registry ~60 行 + 2 消费端 ~40 行 = ~150 行

#### 2B：ItemRegistry

| 新 dataclass | 字段 |
|-------------|------|
| `ItemTemplate` | id, name, type, rarity, price, tags, weight, slot, requires_attunement |
| `HealingData` | heal_amount（统一 heal_amount/heal/restore_hp 三源） |
| `WeaponData`（可选） | damage_dice, damage_type |
| `ArmorData`（可选） | ac_bonus |

**消费端**（4 个文件）：
- `economy.py`：price/base_price, name, tags（~12 点，最重）
- `inventory.py`：heal_amount（~5 点）
- `combat.py`：heal_amount（~2 点）
- `encounter.py`：name, rarity（~4 点）

**体量**：3-4 个 dataclass ~80 行 + registry ~70 行 + 4 消费端 ~60 行 = ~210 行

**Batch 2 合计：~360 行，4-5 小时**

---

### Batch 3：Skill（法术系统）

| 新 dataclass | 字段 |
|-------------|------|
| `SkillTemplate` | id, name, spell_level, school, concentration, ritual, range, targets, action_type, applies_status, duration, duration_ticks |
| `SpellEffect` | type(heal/damage/buff/control/utility/summon/debuff), heal_amount, damage_amount, dice, upcast_dice |
| `SpellCost` | resource, action_type |

**消费端**（2 个文件）：
- `spell.py`：~15 点（effect.type, cost, concentration, 等）
- `ai_osiris.py`：school, effect.type（~3 点，_extract_content_tags）

**特殊考量**：
- SkillRegistry 验证已有 24 个字段检查，迁移到 `__post_init__` 工作量较大
- effect 子结构是 Mapping，需要 `SpellEffect` dataclass

**体量**：3 个 dataclass ~70 行 + registry ~80 行 + 2 消费端 ~50 行 = ~200 行，3-4 小时

---

### Batch 4：Character（深嵌套）

| 新 dataclass | 字段 |
|-------------|------|
| `CharacterTemplate` | id, name, area_id, tags, character_class, faction, inventory, personality, speech_pattern, appearance, backstory |
| `ShopInventory` | sell_markup, buy_rate, base_pool, rotating_pool, rotating_slots, refresh_on |
| `ShopPoolEntry` | item_id, price, unlimited, remaining, restock |

**消费端**（5 个文件）：
- `economy.py`：shop_inventory 深层访问（sell_markup, buy_rate, base_pool, rotating_pool）~8 点
- `world_state.py`：存在性检查 ~3 点
- `narrative/gm_tools.py`：全模板传递 ~2 点
- `agent_orchestration.py`：name, personality ~2 点
- `ai_osiris.py`：name, tags, faction ~5 点（_build_party, _build_nearby_npcs, _build_player_tags）

**特殊考量**：
- shop_inventory 嵌套最深（3 层），是此批最大挑战
- gm_tools 和 agent_orchestration 传全模板给 LLM → 需要 `asdict()` 或自定义序列化

**体量**：3 个 dataclass ~90 行 + registry ~80 行 + 5 消费端 ~80 行 = ~250 行，4-5 小时

---

### Batch 5：Class（字段最密集）

| 新 dataclass | 字段 |
|-------------|------|
| `ClassTemplate` | id, hit_die, base_hp, hp_per_level, base_ac, starting_gold, subclass_level, spellcasting_ability, level_features |
| `RaceTemplate` | id, stat_bonuses, racial_traits |
| `BackgroundTemplate` | id, feature, starting_gold, gold_bonus |
| `SubclassTemplate` | id, class_id, features, level_features |
| `ClassFeature` | name, description, level（可选，看 level_features 结构） |

**消费端**（1 个文件，但极重）：
- `growth.py`：25+ 访问点，涵盖 create_character / level_up / apply_asi / choose_subclass
  - ClassTemplate：hit_die, base_hp, hp_per_level, base_ac, starting_gold, subclass_level, level_features
  - RaceTemplate：stat_bonuses, racial_traits
  - BackgroundTemplate：feature, starting_gold, gold_bonus
  - SubclassTemplate：features, level_features

**特殊考量**：
- ClassRegistry 内部已有 4 个子 dict（_classes, _subclasses, _races, _backgrounds）+ xp_curve
- growth.py 是唯一重消费端但访问极密集，改动集中
- level_features 是 `dict[int, list]` 结构，需要决定是否 dataclass 化

**体量**：4-5 个 dataclass ~100 行 + registry ~90 行 + 1 消费端 ~80 行 = ~270 行，4-5 小时

---

### Batch 6：Map + Quest（收尾）

#### 6A：MapRegistry

| 新 dataclass | 字段 |
|-------------|------|
| `AreaTemplate` | id, name, region, base_danger, connections, sub_locations, encounter_profile, starting |
| `SubLocationTemplate` | id, name, type, available_periods（可选） |
| `EncounterProfile` | slot_capacity, templates |
| `EncounterEntry` | id, periods, source, weight |

**消费端**（6 个文件，但多为存在性检查）：
- `navigation.py`：sub_locations ~3 点
- `world_state.py`：sub_locations, 存在性 ~4 点
- `combat.py`：存在性 ~1 点
- `encounter.py`：存在性 ~1 点
- `context_assembler.py`：全模板 ~1 点
- `narrative/gm_tools.py`：全模板 ~1 点

**体量**：4 个 dataclass ~80 行 + registry ~70 行 + 6 消费端 ~50 行 = ~200 行

#### 6B：QuestRegistry

| 新 dataclass | 字段 |
|-------------|------|
| `MilestoneTemplate` | id, title, description, tags, prerequisites, next_milestones, chapter_id |
| `ChapterMeta` | id, title, description |
| `InitialEvent` | id, event_type, conditions, payload, metadata |

**消费端**（2 个文件）：
- `world_state.py`：chapters iteration ~2 点
- `ai_osiris.py`：无直接访问

**体量**：3 个 dataclass ~60 行 + registry ~70 行 + 1 消费端 ~20 行 = ~150 行

**Batch 6 合计：~350 行，4-5 小时**

---

## 四、总量汇总

| Batch | 内容 | 新 dataclass 数 | 改动行数 | 预估时间 |
|-------|------|----------------|---------|---------|
| 0 | 基础设施 | 0 | ~20 | 0.5h |
| 1 | Faction + Lore + Tag | 3 | ~200 | 2-3h |
| 2 | Monster + Item | 6-7 | ~360 | 4-5h |
| 3 | Skill | 3 | ~200 | 3-4h |
| 4 | Character | 3 | ~250 | 4-5h |
| 5 | Class | 4-5 | ~270 | 4-5h |
| 6 | Map + Quest | 7 | ~350 | 4-5h |
| **合计** | **10 Registries** | **~28 classes** | **~1,650 行** | **~22-28h** |

> 注：先前估算 44 个 dataclass / 65-85h 是最大范围。实际按消费端实际访问的字段来建模，不需要为未使用的子结构建 class，压缩到 ~28 个 / ~25h。

---

## 五、每批检查清单

- [ ] 定义 dataclass（`app/game_core/content/models/` 或 registry 文件内）
- [ ] 重构 Registry.load()：raw dict → dataclass 实例
- [ ] 重构 Registry.get()：返回类型注解改为 `XxxTemplate | None`
- [ ] validate() 迁移到 `__post_init__` 或保留委托
- [ ] 更新所有消费端：`.get("field")` → `.field`
- [ ] 更新 list_all 遍历的消费端（ai_osiris, context_assembler 等）
- [ ] 需要序列化给 LLM 的场景：确认 `dataclasses.asdict()` 可用
- [ ] 跨 Registry 引用验证（WorldInstance._validate_*）确认不受影响
- [ ] 补/更新测试
- [ ] 全量测试通过
- [ ] 更新 content_layer.md 施工记录

---

## 六、风险与缓释

| 风险 | 缓释 |
|------|------|
| LLM 消费端需要 dict 格式（传给 prompt） | 用 `dataclasses.asdict()` 序列化，或 dataclass 实现 `__iter__` |
| load() 时字段缺失导致 TypeError | `__post_init__` 中用 default + issue 记录，不直接 raise |
| 跨 Registry 引用链断裂 | WorldInstance._validate_* 保持不变，在验证层统一检查 |
| 消费端遗漏未更新 | grep 全量扫描 `.get("field_name")` 模式，确保清零 |

---

## 七、建议执行顺序

```
Batch 0 → Batch 1 → Batch 2A → Batch 2B → Batch 3
       → Batch 4 → Batch 5 → Batch 6A → Batch 6B
```

每批完成后跑全量测试，确认 500+ passed 零回归后再进入下一批。
