# L2 内容层 (Content Layer)

> 路径：`backend/app/game_core/content/`
> 职责：游戏世界的全部静态数据——加载一次，只读，不可变。

---

## 一、整体结构

```
content/
├── base.py              — ContentRegistry ABC（所有 Registry 的基类）
├── world.py             — WorldInstance（聚合全部 Registry）
├── shared_types.py      — 跨 Registry 共用的基础 dataclass（Effect, LootTableDef）
├── __init__.py
└── registries/
    ├── tag.py           — TagRegistry（标签维度）
    ├── maps.py          — MapRegistry（区域/子地点/地图连接）
    ├── map_types.py     — 地图类型相关 dataclass（SubLocationTemplate, RoomTemplate, InteractableTemplate...）
    ├── characters.py    — CharacterRegistry（NPC 模板）
    ├── items.py         — ItemRegistry（物品模板）
    ├── skills.py        — SkillRegistry（技能/法术/状态效果模板）
    ├── classes.py       — ClassRegistry（职业/子职业/种族/背景）
    ├── class_types.py   — 职业相关 dataclass（Feature, ResourceConfig, SpellcastingConfig）
    ├── monsters.py      — MonsterRegistry（怪物模板）
    ├── factions.py      — FactionRegistry（派系模板）
    ├── quests.py        — QuestRegistry（里程碑/章节元数据/初始事件）
    ├── lore.py          — LoreRegistry（世界知识/世界规则）
    └── battle_maps.py   — BattleMapRegistry（战斗地图布局）
```

---

## 二、核心基础设施

### ContentRegistry（基类）

```python
class ContentRegistry(ABC):
    # 必须实现
    def load(data: dict[str, Any]) -> None    # 从世界数据加载
    def get(content_id: str) -> Any | None   # 按 ID 查询
    def list_all() -> list[Any]              # 列出全部

    # 通用方法
    def snapshot() -> dict[str, Any]                          # 返回 {name, size}
    def validate() -> list[str]                               # 校验引用完整性
    def query_by_tags(tags, match_all=False) -> list[Any]     # 标签过滤

    # 辅助（所有子类共用）
    _coerce_dict_mapping()      # dict/list 格式归一化
    _coerce_non_negative_int()  # 类型强制转换
    _coerce_float()
    _extract_tags()
```

### WorldInstance

```python
class WorldInstance:
    world_id: str
    _registries: dict[str, ContentRegistry]

    # 加载顺序（依赖关系保证）
    # Group 1: tags
    # Group 2: maps, battle_maps, classes, skills, lore
    # Group 3: characters, items, monsters, factions, quests

    def register(registry: ContentRegistry) -> None
    def get_registry(name: str) -> ContentRegistry
    def load_all(world_data: dict) -> None

    # 类型化访问器（11 个 property）
    .tags / .maps / .characters / .items / .skills
    .classes / .monsters / .factions / .lore / .quests / .battle_maps
```

---

## 三、各 Registry 详解

### 1. TagRegistry — 标签维度

```python
@dataclass(slots=True)
class TagDimension:
    id: str
    description: str = ""
    tags: list[str] = []
```

方法：`all_tags() → set[str]`；`get_dimension_for_tag(tag) → str|None`；`has_tag(tag) → bool`

---

### 2. CharacterRegistry — NPC 模板

**核心数据结构：**

```python
@dataclass(slots=True)
class CharacterTemplate:
    id: str
    name: str
    area_id / current_area: str          # 所在区域
    location_id / current_location: str  # 所在子地点
    tags: list[str]
    schedule: dict | None                # 日程（period → destination）
    character_class / class_id: str
    faction / faction_id: str
    personality / dialogue_style / speech_pattern / appearance / backstory: str
    inventory: list[dict]
    shop_inventory: ShopInventory | None  # 商店配置
    base_disposition: dict[str,int] | None  # {approval,trust,fear,romance} 初始值
    base_hp / base_ac: int | None
    stats: dict[str,int]
    level: int = 1
    proficiency_bonus: int = 2
    combat_capable: bool = False
    attacks: list[NpcAttack]
    skills: list[str]
    secrets: list[SecretEntry]           # {content, trust_threshold, tags}
    ai_personality: str | None

@dataclass(slots=True)
class ShopInventory:
    sell_markup: float | None
    buy_rate: float | None
    base_pool: list[ShopEntry]           # 固定商品
    rotating_pool: list[ShopEntry]       # 轮换商品
    rotating_slots: int = 0
    refresh_on: str | list[str] | None   # 刷新时机（"long_rest" 等）
    level_scaling: bool = False

@dataclass(slots=True)
class ShopEntry:
    item_id: str
    count: str = "1"                     # 支持骰子表达式
    min_player_level: int = 0
    restock: bool = True
```

查询：`get_by_area(area_id)`；`get_merchants()`；`get_by_faction(faction_id)`

---

### 3. ItemRegistry — 物品模板

```python
@dataclass(slots=True)
class ItemTemplate:
    id: str
    name / description: str
    type: str                            # "weapon","armor","consumable","accessory"
    rarity: str                          # "common","uncommon","rare","very_rare","legendary"
    base_price: int | None
    slot: str                            # 装备槽（或空字符串）
    tags: list[str]
    weight: float | None
    requires_attunement: bool = False
    weapon_data: WeaponData | None
    armor_data: ArmorData | None
    consumable_data: ConsumableData | None
    accessory_data: AccessoryData | None

@dataclass(slots=True)
class WeaponData:
    damage_dice: str                     # "1d8", "2d6", ...
    damage_type: str                     # "slashing","piercing","bludgeoning","fire",...
    properties: list[str]               # ["finesse","two_handed","thrown",...]
    range: int = 1
    proficiency: str                     # "simple","martial"
    slot: str = "main_hand"
    versatile_dice: str = ""

@dataclass(slots=True)
class ArmorData:
    armor_type: str                      # "light","medium","heavy","shield"
    base_ac: int
    dex_cap: int | None
    str_requirement: int | None
    stealth_disadvantage: bool = False
    proficiency: str
```

查询：`get_equippable()`；`get_by_type()`；`get_by_rarity()`；`get_weapons(properties)`；`get_by_price_range(min,max)`；`get_by_tags(tags)`

---

### 4. SkillRegistry — 技能/法术/状态效果

```python
@dataclass(slots=True)
class SkillTemplate:
    id: str
    name / description: str
    category: str                        # "spell","combat_skill","exploration_skill"
    spell_level: int | None              # 法术等级（1-9，非法术为 None）
    school: str                          # "evocation","illusion","necromancy",...
    tags: list[str]
    effect: SkillEffect
    cost: SkillCost
    requirements: dict                   # 使用前提
    usable_in: list[str]                 # ["combat","exploration","dialogue"]

@dataclass(slots=True)
class SkillEffect:
    type: str                            # "damage","heal","status","utility"
    target: str                          # "single","aoe","self","ally"
    range / area_size: int | None
    dice: str | None                     # "2d6","1d4+3"
    damage_type: str | None
    save: str | None                     # 豁免属性 "dex","con",...
    save_dc_stat: str | None             # DC 基准属性
    half_on_save: bool = False
    applies_status: str | None           # 施加状态效果的 ID
    status_duration: int | None
    concentration: bool = False
    upcast_dice: str | None              # 提环施放额外骰

@dataclass(slots=True)
class StatusEffectTemplate:
    id: str
    name / description: str
    category: str                        # "debuff","buff","dot","crowd_control"
    stackable: bool = False
    tick_damage: str | None              # 每回合伤害骰
    tick_damage_type: str | None
    tick_heal: str | None
    modifiers: dict | None               # stat 修改器
    prevents_action: bool = False        # 禁止行动（如石化）
    disadvantage_on: list[str]
    duration: int | None                 # None=永久，int=回合数
    save_end_of_turn: str | None         # 每回合豁免机会
    save_dc: int | None
    advantage_on_attacks_against: bool = False
```

查询：`get_spells()`；`get_spells_by_level()`；`get_spells_by_school()`；`get_status_effect(effect_id)`；`list_status_effects()`

---

### 5. ClassRegistry — 职业/种族/背景

```python
@dataclass(slots=True)
class ClassTemplate:
    id: str
    name / description: str
    hit_die: str | int | None            # "d10", 10
    hp_per_level: int | None
    subclass_level: int | None           # 选择子职业的等级（通常3级）
    spellcasting_ability: str            # "int","wis","cha"
    level_features: dict[str, list[Feature]]  # {"1": [...], "3": [...]}
    class_resources_schema: dict         # 职业资源模板（怒气/Ki/优越骰等）
    starting_equipment: list[str]
    armor_proficiency / weapon_proficiency / save_proficiency: list[str]
    spellcasting: SpellcastingConfig | None
    subclass_options: list[str]

@dataclass(slots=True)
class Feature:
    id: str
    name / description: str
    type: str = "passive"                # "passive","active","resource"
    skill_id: str | None                 # 关联的技能 ID
    resource_config: ResourceConfig | None

@dataclass(slots=True)
class SpellcastingConfig:
    stat: str                            # 法术施法属性
    cantrips_known: dict[str,int]        # {level: count}
    spell_slots: dict[str, dict[str,int]]  # {level: {slot_level: count}}
    spells_known: dict[str,int] | None
    prepared_formula: str | None
```

---

### 6. MonsterRegistry — 怪物模板

```python
@dataclass(slots=True)
class MonsterTemplate:
    id: str
    name / description: str
    hp / max_hp: int | None
    ac: int | None
    cr: float | None                     # 挑战等级
    creature_type: str                   # "humanoid","beast","undead",...
    abilities: dict[str,int]             # D&D 六维属性
    resistances / immunities / vulnerabilities: list[str]
    attacks: list[MonsterAttack]
    gold_drop: str = "0"                 # 骰子表达式
    loot_table: list[LootEntry]          # {item_id, chance, count}
    ai_personality: str = "aggressive"  # "aggressive","defensive","cowardly"
    flee_threshold: float = 0.0          # HP 低于此比例时逃跑（0=从不）
    flee_chance: float = 0.5             # 达到阈值后每回合逃跑概率
    xp_reward: int = 0
    tactics_notes: str                   # LLM AI 战术提示
    preferred_terrain: list[str]
    group_size: str = "solo"             # "solo","pair","group"
```

---

### 7. MapRegistry — 区域/地图

```python
@dataclass(slots=True)
class AreaTemplate:
    id: str
    name / description / region: str
    base_danger: float | None
    connections: list[Connection]        # 连接到其他区域
    sub_locations: dict[str, SubLocationTemplate]
    default_sub_location: str
    discoveries: list[Discovery]         # 可被探索发现的事物
    hostile_pool: list[HostileTemplate] | None
    encounter_table: list[EncounterEntry]
    encounter_slot_capacity: int = 1
    is_starting_area: bool = False
    terrain_type: str

@dataclass(slots=True)
class SubLocationTemplate:
    id / name / description: str
    tags: list[str]
    type: str = "visit"                  # "visit","shop","home","dungeon"
    available_hours: tuple[int,int] | None  # (开门时, 关门时)
    resident_npcs: list[str]             # NPC id 列表
    interactables: list[InteractableTemplate]
    hostile_config: HostileConfig | None
    rooms: dict[str, RoomTemplate]       # 内部房间
    default_room: str

@dataclass(slots=True)
class InteractableTemplate:
    id / name / description: str
    type: str = "inspect"                # "inspect","container","puzzle","npc_trigger"
    visibility_dc: int | None            # 被动感知 DC
    checks: list[CheckPath]              # 多步骤检定路径
    reward: dict | None
    one_time: bool = False
    container_data: ContainerData | None # 容器特化数据（含陷阱/战利品）

@dataclass(slots=True)
class Connection:
    target: str                          # 目标区域 ID
    type: str = "travel"
    travel_slots: int = 1                # 旅行消耗时段数
    description: str
    blocked: bool = False
```

查询：`starting_area()`；`get_adjacent(area_id)`；`get_connection(from_id, to_id)`；`resolve_encounter_map_category()`

---

### 8. QuestRegistry — 里程碑/章节

```python
@dataclass(slots=True)
class MilestoneTemplate:
    id: str
    title / description: str
    chapter_id: str
    tags: list[str]
    prerequisites: list[str]             # 前置里程碑 ID 列表
    next_milestones: list[str]           # 解锁的下一里程碑
    completion_value: int = 10           # 贡献章节完成度的点数
    sequence: int = 0                    # 排序序号
    narrative_context: str               # LLM 叙事上下文提示
    key_elements: list[str]              # 关键叙事元素
    involved_npcs: list[str]
    involved_locations: list[str]
    success_conditions: list[MilestoneCondition]
    failure_conditions: list[MilestoneCondition]
    failure_fallback: str | None         # 失败时跳转到的里程碑 ID
    rewards: dict                        # {xp, gold, items}
```

查询：`get_milestone(id)`；`get_chapter_milestones(chapter_id)`；`get_milestone_graph() → dict[str, list[str]]`

---

### 9. LoreRegistry — 世界知识/规则

```python
@dataclass(slots=True)
class LoreEntry:
    id: str
    title / content: str
    tags: list[str]
    scope: str = "global"               # "global","chapter","area","faction"
    scope_id: str | None

@dataclass(slots=True)
class WorldRule:
    id: str
    title / description: str
    tags: list[str]
    scope: str = "global"
    scope_id: str | None
    priority: int = 0                   # 注入 LLM prompt 的优先级
```

查询：`get_rules_for_context(chapter_id, area_id, faction_ids)` — 合并全局+章节+区域+派系规则并排序

---

### 10. BattleMapRegistry — 战斗地图

```python
@dataclass(slots=True, frozen=True)    # frozen=True → 完全不可变
class BattleMapVariant:
    name: str
    width / height: int
    terrain: tuple[str, ...]            # 地形标签
    player_spawn: tuple[tuple[int,int], ...]
    enemy_spawn: tuple[tuple[int,int], ...]
    tags: tuple[str, ...]

@dataclass(slots=True, frozen=True)
class BattleMapTemplate:
    category: str                        # "outdoor","dungeon","town",等
    variants: tuple[BattleMapVariant, ...]
```

查询：`select_variant(category, rng)` — 随机选 variant；`select_by_tags(tags, rng)` — 按标签过滤后随机

---

## 四、跨 Registry 依赖关系

```
Tags ←───────────────── 所有 Registry（tags 字段校验）
Maps ←── Characters（resident_npcs）
Maps ←── Monsters（encounter_table, hostile_pool）
Maps ←── Items（discoveries/interactables 奖励）
Characters ←── Classes（class_id）
Characters ←── Factions（faction_id）
Characters ←── Items（inventory）
Quests ←── Characters（involved_npcs）
Quests ←── Maps（involved_locations）
Lore ←── Maps/Factions/Chapters（scope_id）
Factions ←── Characters（leader_id, member_ids）
```

**加载顺序保证**：Tags → Maps/Classes/Skills/Lore → Characters/Items/Monsters/Factions/Quests

---

## 五、设计模式

| 模式 | 说明 |
|------|------|
| **`@dataclass(slots=True)`** | 全量使用，节省内存，防止意外属性添加 |
| **`frozen=True`（BattleMap）** | 最高不变性保证，可哈希，用作字典键 |
| **`load_issues` 收集** | 加载时收集警告而不抛出异常，允许部分数据缺失 |
| **向后兼容默认值** | 所有字段有默认值，旧数据文件不需要迁移 |
| **双字段别名** | `area_id`/`current_area`、`faction`/`faction_id` 同时存在，兼容不同版本的数据格式 |
| **骰子表达式字符串** | `count: str = "1"` 支持 "1d4+2" 等表达式，运行时解析 |
