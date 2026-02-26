# 物品数据 Schema 规格（BG3 / D&D 5e 对齐版）

创建时间：2026-02-25
状态：定稿，待实施

> 本文档定义物品系统的**完整数据结构规格**，对齐博德之门 3（D&D 5e SRD）。
> 方向概览见 [物品类基础系统设计.md](物品类基础系统设计.md)。

---

## 一、通用字段（所有物品共享）

```jsonc
{
  "id": "string",                     // 唯一标识（snake_case）
  "name": "string",                   // 显示名称
  "description": "string",            // LLM 生成的叙述描述（自由发挥）
  "type": "weapon|armor|accessory|consumable|misc",
  "subtype": "string",                // 见 §二 枚举表
  "rarity": "common|uncommon|rare|very_rare|legendary",
  "price_cp": 0,                      // 铜币整数（1gp=100cp, 1sp=10cp）
  "weight": 0.0,                      // 磅（lb），数值型
  "slot": "string|null",              // 装备槽位，见 §三
  "proficiency": "string|null",       // 需要的熟练项，见 §四
  "enchantment": 0,                   // +N 魔法加值（0=非魔法）
  "requires_attunement": false,       // 是否需要调谐

  // 类型专属数据块（仅出现对应 type 的那个，其余省略或 null）
  "weapon_data": { ... },             // type=weapon 时
  "armor_data": { ... },              // type=armor 时
  "consumable_data": { ... },         // type=consumable 时

  // 结构化效果列表（所有类型均可拥有）
  "special_effects": [ ... ]
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | Y | 全局唯一，snake_case，如 `weapon_longsword_1` |
| name | string | Y | 游戏内显示名 |
| description | string | Y | 叙述性描述，LLM 自由发挥 |
| type | enum | Y | 五大类型之一 |
| subtype | string | Y | 类型内细分，见 §二 |
| rarity | enum | Y | 稀有度五级 |
| price_cp | int | Y | 统一铜币价格。无法定价用 0 |
| weight | float | Y | 磅。极轻物品用 0.1，无重量用 0 |
| slot | string/null | N | 可装备物品必填，消耗品/杂物=null |
| proficiency | string/null | N | 需要熟练才能有效使用的类别 |
| enchantment | int | Y | 默认 0。+1/+2/+3 影响攻击/伤害/AC |
| requires_attunement | bool | Y | 默认 false |

---

## 二、type → subtype 枚举

| type | subtype 枚举 | 说明 |
|------|-------------|------|
| weapon | `simple_melee`, `simple_ranged`, `martial_melee`, `martial_ranged` | D&D 四分类 |
| armor | `light`, `medium`, `heavy`, `shield` | D&D 护甲分类 |
| accessory | `helmet`, `gloves`, `boots`, `cloak`, `amulet`, `ring` | BG3 饰品六类 |
| consumable | `potion`, `scroll`, `throwable`, `food`, `coating`, `camp_supply` | 消耗品六类（camp_supply 预留） |
| misc | `material`, `quest`, `junk`, `key` | 非功能性物品 |

---

## 三、装备槽位（13 槽）

```
helmet          头盔
armor           护甲（身体）
gloves          手套
boots           靴子
cloak           披风
amulet          项链
ring_1          戒指 1
ring_2          戒指 2
weapon_main     主手武器
weapon_off      副手（双持武器/火炬等）
shield          盾牌（与 weapon_off 互斥）
ranged          远程武器槽
camp_slot       营地物品槽（预留，暂不实现）
```

**互斥规则**：
- `shield` 和 `weapon_off` 互斥（盾牌占副手）
- 双手武器（`two_handed` 属性）占 `weapon_main` 并禁用 `weapon_off` + `shield`
- `ring_1` / `ring_2` 同一戒指不能戴两个

**subtype → slot 默认映射**：

| subtype | 默认 slot |
|---------|----------|
| simple_melee / martial_melee | weapon_main |
| simple_ranged / martial_ranged | ranged |
| light / medium / heavy | armor |
| shield | shield |
| helmet | helmet |
| gloves | gloves |
| boots | boots |
| cloak | cloak |
| amulet | amulet |
| ring | ring_1（ring_1 已占则 ring_2） |

---

## 四、proficiency 枚举

```
simple_weapons          简单武器
martial_weapons         军用武器
light_armor             轻甲
medium_armor            中甲
heavy_armor             重甲
shields                 盾牌
null                    无需熟练（消耗品/饰品/杂物）
```

**不熟练惩罚**（D&D 5e 规则）：
- 武器：攻击检定不加熟练加值
- 护甲：攻击检定、力量/敏捷豁免检定劣势，无法施法
- 盾牌：同护甲

---

## 五、weapon_data（武器专属）

```jsonc
{
  "damage_dice": "1d8",              // 基础伤害骰
  "damage_type": "slashing",         // 伤害类型
  "versatile_dice": "1d10",          // 灵巧（双手握持）伤害，null=不可双手
  "properties": ["versatile"],       // 武器属性列表
  "range_normal": 5,                 // 正常射程（ft），近战=5
  "range_long": null                 // 远程长射程，近战=null
}
```

### damage_type 枚举

**物理**：`slashing`（挥砍）、`piercing`（穿刺）、`bludgeoning`（钝击）

**元素**：`fire`（火）、`cold`（冰）、`lightning`（闪电）、`thunder`（雷鸣）、`acid`（强酸）、`poison`（毒素）

**魔法**：`radiant`（光耀）、`necrotic`（黯蚀）、`force`（力场）、`psychic`（心灵）

### weapon properties 枚举

| 属性 | 说明 |
|------|------|
| `finesse` | 可用敏捷代替力量进行攻击/伤害 |
| `light` | 可用于双持（副手攻击） |
| `heavy` | 小型生物使用有劣势 |
| `two_handed` | 必须双手持握 |
| `versatile` | 可单手或双手，双手用 versatile_dice |
| `thrown` | 可投掷，用 range_normal/range_long |
| `reach` | 近战射程 +5ft（共 10ft） |
| `loading` | 每轮只能攻击一次（除非有专长） |
| `ammunition` | 需要弹药 |

### 标准武器参考（D&D 5e SRD）

| 武器 | subtype | damage_dice | damage_type | properties | price_cp |
|------|---------|-------------|-------------|------------|----------|
| 匕首 | simple_melee | 1d4 | piercing | finesse, light, thrown | 200 |
| 短棍 | simple_melee | 1d6 | bludgeoning | versatile(1d8) | 10 |
| 长矛 | simple_melee | 1d6 | piercing | thrown, versatile(1d8) | 100 |
| 短弓 | simple_ranged | 1d6 | piercing | ammunition | 2500 |
| 长剑 | martial_melee | 1d8 | slashing | versatile(1d10) | 1500 |
| 战斧 | martial_melee | 1d8 | slashing | versatile(1d10) | 1000 |
| 巨剑 | martial_melee | 2d6 | slashing | heavy, two_handed | 5000 |
| 长弓 | martial_ranged | 1d8 | piercing | ammunition, heavy, two_handed | 5000 |
| 手弩 | martial_ranged | 1d6 | piercing | ammunition, light, loading | 7500 |

---

## 六、armor_data（护甲专属）

```jsonc
{
  "base_ac": 16,                     // 基础 AC（非加值！）
  "max_dex_bonus": 0,                // heavy=0, medium=2, light=null(无上限)
  "stealth_disadvantage": true,      // 穿着此甲隐匿有劣势
  "strength_requirement": 13         // 不满足则移速 -10ft，0=无要求
}
```

### 标准护甲参考（D&D 5e SRD）

**轻甲**（max_dex_bonus=null）：

| 护甲 | base_ac | stealth_disadv | str_req | price_cp |
|------|---------|----------------|---------|----------|
| 软皮甲 | 11 | false | 0 | 500 |
| 皮甲 | 11 | false | 0 | 1000 |
| 镶嵌皮甲 | 12 | false | 0 | 4500 |

**中甲**（max_dex_bonus=2）：

| 护甲 | base_ac | stealth_disadv | str_req | price_cp |
|------|---------|----------------|---------|----------|
| 兽皮甲 | 12 | false | 0 | 1000 |
| 链甲衫 | 13 | false | 0 | 5000 |
| 鳞甲 | 14 | true | 0 | 5000 |
| 胸甲 | 14 | false | 0 | 40000 |
| 半身板甲 | 15 | true | 0 | 75000 |

**重甲**（max_dex_bonus=0）：

| 护甲 | base_ac | stealth_disadv | str_req | price_cp |
|------|---------|----------------|---------|----------|
| 环甲 | 14 | true | 0 | 3000 |
| 锁子甲 | 16 | true | 13 | 7500 |
| 夹板甲 | 17 | true | 15 | 20000 |
| 板甲 | 18 | true | 15 | 150000 |

**盾牌**：

| 盾牌 | 效果 | price_cp |
|------|------|----------|
| 盾牌 | AC +2（on_equip effect） | 1000 |

> 盾牌不使用 armor_data，而是通过 special_effects `{trigger:"on_equip", effect_type:"ac_bonus", params:{value:2}}` 实现。

---

## 七、consumable_data（消耗品专属）

```jsonc
{
  "uses": 1,                         // 使用次数
  "consumed_on_use": true,           // 用完后从背包移除
  "spell_level": null                // 卷轴专属：对应法术环级
}
```

---

## 八、special_effects 系统

所有物品均可携带 `special_effects` 数组。每个效果是一个结构化对象：

```jsonc
{
  "trigger": "on_equip",             // 触发时机
  "effect_type": "ac_bonus",         // 效果类型
  "params": { "value": 1 }           // 效果参数
}
```

### trigger 枚举

| trigger | 说明 | 适用场景 |
|---------|------|---------|
| `on_equip` | 装备时生效，卸下时消失 | 护甲、饰品的被动加成 |
| `on_hit` | 命中目标时触发 | 武器附魔（额外伤害、施加状态） |
| `on_crit` | 暴击时触发 | 暴击专属效果 |
| `on_use` | 主动使用（消耗品） | 药水、卷轴、投掷物 |
| `on_damaged` | 受到伤害时触发 | 反伤护甲、火盾 |

### effect_type 完整列表

| effect_type | 适用 trigger | params | 说明 | 示例 |
|-------------|-------------|--------|------|------|
| `damage_bonus` | on_hit, on_crit | `{dice?, bonus?, damage_type}` | 额外伤害 | 火焰附魔 `{dice:"1d4", damage_type:"fire"}` |
| `heal` | on_use | `{dice, bonus?}` | 恢复 HP | 治疗药水 `{dice:"2d4", bonus:2}` |
| `ac_bonus` | on_equip | `{value}` | AC 加值 | 防护戒指 `{value:1}` |
| `ability_bonus` | on_equip | `{ability, value}` | 属性值加成 | 巨力腰带 `{ability:"strength", value:2}` |
| `saving_throw_bonus` | on_equip | `{saves, value}` | 豁免加值 | `{saves:"all", value:1}` 或 `{saves:"wisdom", value:2}` |
| `skill_bonus` | on_equip | `{skill, value}` | 技能检定加值 | 隐匿斗篷 `{skill:"stealth", value:2}` |
| `resistance` | on_equip | `{damage_type}` | 伤害抗性（半伤） | 抗火斗篷 `{damage_type:"fire"}` |
| `condition_immunity` | on_equip | `{condition}` | 状态免疫 | 免疫中毒 `{condition:"poisoned"}` |
| `apply_condition` | on_hit, on_use | `{condition, dc?, save?, duration_rounds?}` | 施加状态 | 毒刃 `{condition:"poisoned", dc:13, save:"constitution"}` |
| `remove_condition` | on_use | `{condition}` | 移除状态 | 解毒剂 `{condition:"poisoned"}` |
| `cast_spell` | on_use | `{spell_id, dc?}` | 施放法术 | 火球卷轴 `{spell_id:"fireball", dc:15}` |
| `speed_bonus` | on_equip | `{value}` | 移动速度加值（ft） | 疾行靴 `{value:10}` |
| `temp_hp` | on_use, on_equip | `{dice?, bonus?}` | 临时生命值 | `{dice:"1d6", bonus:4}` |

### condition 枚举（D&D 5e 状态列表）

```
blinded         目盲
charmed         魅惑
deafened        耳聋
frightened      恐惧
grappled        擒抱
incapacitated   失能
invisible       隐形
paralyzed       麻痹
petrified       石化
poisoned        中毒
prone           倒地
restrained      束缚
stunned         震慑
unconscious     昏迷
exhaustion      力竭（1-6 级）
```

### ability 枚举

```
strength        力量
dexterity       敏捷
constitution    体质
intelligence    智力
wisdom          感知
charisma        魅力
```

---

## 九、完整示例

### 魔法武器

```json
{
  "id": "weapon_longsword_flame_1",
  "name": "烈焰长剑 +1",
  "description": "剑刃上流淌着永不熄灭的火焰，据说是远古火元素锻造师的杰作。握柄处的红宝石在战斗中会随主人的心跳一起脉动。",
  "type": "weapon",
  "subtype": "martial_melee",
  "rarity": "rare",
  "price_cp": 400000,
  "weight": 3,
  "slot": "weapon_main",
  "proficiency": "martial_weapons",
  "enchantment": 1,
  "requires_attunement": true,
  "weapon_data": {
    "damage_dice": "1d8",
    "damage_type": "slashing",
    "versatile_dice": "1d10",
    "properties": ["versatile"],
    "range_normal": 5,
    "range_long": null
  },
  "special_effects": [
    {
      "trigger": "on_hit",
      "effect_type": "damage_bonus",
      "params": { "dice": "1d6", "damage_type": "fire" }
    }
  ]
}
```

### 重甲

```json
{
  "id": "armor_plate_mail",
  "name": "板甲",
  "description": "由重叠的金属板组成的全身铠甲，是骑士和重装战士的标志性装备。穿戴需要旁人协助，但提供的防护无与伦比。",
  "type": "armor",
  "subtype": "heavy",
  "rarity": "common",
  "price_cp": 150000,
  "weight": 65,
  "slot": "armor",
  "proficiency": "heavy_armor",
  "enchantment": 0,
  "requires_attunement": false,
  "armor_data": {
    "base_ac": 18,
    "max_dex_bonus": 0,
    "stealth_disadvantage": true,
    "strength_requirement": 15
  },
  "special_effects": []
}
```

### 魔法饰品

```json
{
  "id": "accessory_cloak_elvenkind",
  "name": "精灵斗篷",
  "description": "用月光精灵的丝线织就的斗篷，穿戴者的身影会与周围环境融为一体。",
  "type": "accessory",
  "subtype": "cloak",
  "rarity": "uncommon",
  "price_cp": 500000,
  "weight": 1,
  "slot": "cloak",
  "proficiency": null,
  "enchantment": 0,
  "requires_attunement": true,
  "special_effects": [
    {
      "trigger": "on_equip",
      "effect_type": "skill_bonus",
      "params": { "skill": "stealth", "value": 5 }
    }
  ]
}
```

### 消耗品（药水）

```json
{
  "id": "potion_healing",
  "name": "治疗药水",
  "description": "红色的液体散发着微微的光芒，喝下后能感受到温暖的治愈力量在体内流淌。",
  "type": "consumable",
  "subtype": "potion",
  "rarity": "common",
  "price_cp": 5000,
  "weight": 0.5,
  "slot": null,
  "proficiency": null,
  "enchantment": 0,
  "requires_attunement": false,
  "consumable_data": {
    "uses": 1,
    "consumed_on_use": true,
    "spell_level": null
  },
  "special_effects": [
    {
      "trigger": "on_use",
      "effect_type": "heal",
      "params": { "dice": "2d4", "bonus": 2 }
    }
  ]
}
```

### 杂物（任务物品）

```json
{
  "id": "misc_goblin_ear",
  "name": "哥布林的耳朵",
  "description": "讨伐哥布林后割下的证明。冒险者公会按件收购，每只耳朵可以换取少量赏金。",
  "type": "misc",
  "subtype": "quest",
  "rarity": "common",
  "price_cp": 100,
  "weight": 0.1,
  "slot": null,
  "proficiency": null,
  "enchantment": 0,
  "requires_attunement": false,
  "special_effects": []
}
```

### 食物

```json
{
  "id": "food_ration_standard",
  "name": "标准口粮",
  "description": "用盐腌制的干肉搭配硬面饼，是冒险者旅途中最常见的食物。味道乏善可陈，但足以果腹恢复体力。",
  "type": "consumable",
  "subtype": "food",
  "rarity": "common",
  "price_cp": 50,
  "weight": 2.0,
  "slot": null,
  "proficiency": null,
  "enchantment": 0,
  "requires_attunement": false,
  "consumable_data": {
    "uses": 1,
    "consumed_on_use": true,
    "spell_level": null
  },
  "special_effects": [
    {
      "trigger": "on_use",
      "effect_type": "heal",
      "params": { "dice": "1d4", "bonus": 0 }
    }
  ]
}
```

```json
{
  "id": "food_tavern_feast",
  "name": "酒馆丰盛大餐",
  "description": "热气腾腾的炖肉配上新鲜面包和一大杯麦酒，是冒险者在城镇中最期待的犒赏。",
  "type": "consumable",
  "subtype": "food",
  "rarity": "common",
  "price_cp": 200,
  "weight": 0,
  "slot": null,
  "proficiency": null,
  "enchantment": 0,
  "requires_attunement": false,
  "consumable_data": {
    "uses": 1,
    "consumed_on_use": true,
    "spell_level": null
  },
  "special_effects": [
    {
      "trigger": "on_use",
      "effect_type": "heal",
      "params": { "dice": "1d6", "bonus": 2 }
    },
    {
      "trigger": "on_use",
      "effect_type": "temp_hp",
      "params": { "bonus": 2 }
    }
  ]
}
```

---

## 十、与旧 Schema 的迁移对照

| 旧字段 | 新字段 | 迁移规则 |
|--------|--------|---------|
| `properties.ac_bonus: 4` | `armor_data.base_ac: 16` | 查 SRD 表换算（不是简单加 10） |
| `properties.price: "25 gp"` | `price_cp: 2500` | 解析字符串 → 整数铜币 |
| `properties.weight: "heavy"` | `weight: 55` | 查 SRD 表换为磅数 |
| `properties.damage: "varies"` | `weapon_data.damage_dice: "1d8"` | 查 SRD 表填入具体骰子 |
| `effects: ["恢复生命值"]` | `special_effects: [{trigger,type,params}]` | 自由文本 → 结构化效果 |
| 无 slot | `slot: "armor"` | 按 subtype 映射 |
| 无 proficiency | `proficiency: "heavy_armor"` | 按 type+subtype 映射 |

> 迁移工作主要在提取管线（worldbook_graphizer）中完成：更新 prompt 模板让 LLM 按新 schema 输出。已有的 41 个物品需要一次性转换。

---

## 十一、价格换算速查

```
1 pp = 1000 cp
1 gp = 100 cp
1 sp = 10 cp
1 cp = 1 cp
```

常用示例：`"25 gp"` = 2500 cp, `"10 sp"` = 100 cp, `"5 sp"` = 50 cp

---

## 变更日志

| 日期 | 变更 |
|------|------|
| 2026-02-25 | 创建。对齐 BG3 / D&D 5e SRD，完全机械化 special_effects，description 保留 LLM 自由发挥。预留 camp_slot/camp_supply。 |
| 2026-02-25 | food 从预留改为正式：subtype=food 的消耗品，通过 heal/temp_hp 效果回血。新增标准口粮 + 酒馆大餐示例。 |
