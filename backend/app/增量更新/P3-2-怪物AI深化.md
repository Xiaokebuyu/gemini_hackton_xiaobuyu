# P3-2: 怪物战斗 AI 深化

> 状态：设计完成，可施工
> 创建：2026-03-05
> 关联：P3 §六.2（怪物战斗 AI 管线）
> 前置调查：修正了 P3 的错误结论

---

## 一、P3 审计修正

P3 将 10 个 MonsterTemplate 字段标记为"未消费"。深入调查发现 6 个已消费：

| 字段 | 消费者 | 位置 |
|------|--------|------|
| `ai_personality` | `_decide_monster_action()` | combat.py:1077 |
| `flee_threshold` | `_decide_monster_action()` | combat.py:1078 |
| `resistances` | 玩家攻击伤害计算 | combat.py:614 |
| `immunities` | 玩家攻击伤害计算 | combat.py:611 |
| `vulnerabilities` | 玩家攻击伤害计算 | combat.py:617 |
| `attacks[0]` | 怪物反击（name/damage_dice/hit_bonus） | combat.py:1105-1108 |

**真正的 gap 是 4 个可行深化 + 4 个需要新系统的远期目标。**

---

## 二、当前怪物回合逻辑

```
_resolve_monster_responses()
  for each alive monster:
    1. _decide_monster_action(ai_personality, hp_ratio, flee_threshold)
       → aggressive + hp <= threshold + hp >= 10%: 继续攻击
       → aggressive + hp < 10%: 逃跑
       → defensive/cowardly + hp <= threshold: 逃跑
       → else: 攻击
    2. if flee → alive=False, fled=True
    3. if attack:
       a. 取 attacks[0]（永远只用第一个攻击）
       b. _roll_monster_attack(name, damage_dice, hit_bonus, player_ac)
       c. 累积伤害
```

**问题：**
- flee 是确定性的（flee_chance 字段存在但未消费）
- 永远只用第一个攻击（怪物有多个攻击时忽略其余）
- 怪物 damage_type 已有数据但攻击时不应用（不检查玩家防御）
- player_ac 已含效果修正（get_effect_modifiers 在 1058 行），但怪物攻击无优劣势考虑

---

## 三、可行深化（本轮目标）

### Phase 1: flee_chance 概率化

**当前**：hp_ratio <= flee_threshold → 必定逃跑（aggressive 除外）
**目标**：hp_ratio <= flee_threshold → flee_chance 概率逃跑

```python
def _decide_monster_action(self, ai_personality, hp_ratio, flee_threshold, flee_chance):
    if flee_threshold <= 0.0 or hp_ratio > flee_threshold:
        return "attack"
    if ai_personality == "aggressive" and hp_ratio >= 0.1:
        return "attack"
    # 概率逃跑
    if random.random() < flee_chance:
        return "flee"
    return "attack"
```

**变更**：combat.py `_decide_monster_action` + `_resolve_monster_responses` 传入 flee_chance。
**复杂度**：极小（3 行改动）。

### Phase 2: 多攻击选择

**当前**：`attacks[0]` 固定取第一个。
**目标**：根据 ai_personality 选择攻击。

选择策略（简洁版，不做完整 AI 决策树）：

```python
def _select_attack(self, attacks, ai_personality):
    if len(attacks) <= 1:
        return attacks[0]
    if ai_personality == "aggressive":
        # 选伤害最高的攻击
        return max(attacks, key=lambda a: _estimate_damage(a.damage_dice))
    if ai_personality == "defensive":
        # 选命中率最高的攻击
        return max(attacks, key=lambda a: a.hit_bonus)
    # cowardly: 选射程最远的攻击
    return max(attacks, key=lambda a: a.range)
```

`_estimate_damage(dice_str)` 是一个简单的骰子期望值计算（如 "2d6" → 7）。

**变更**：combat.py 新增 `_select_attack()` + `_estimate_damage()`，`_resolve_monster_responses` 替换 `attacks[0]`。
**复杂度**：小（~20 行新增）。

### Phase 3: 怪物 damage_type 反向应用

**当前**：玩家攻击怪物时检查 resistances/immunities/vulnerabilities ✅。怪物攻击玩家时**不检查**。

**目标**：怪物攻击时，将 `MonsterAttack.damage_type` 应用于玩家防御。

问题：PlayerSlice 当前没有 resistances/immunities/vulnerabilities 字段。需要决定：

**方案 A**：通过 active_effects 的 tags 模拟（如 "fire_resistance" 效果 → 减半火焰伤害）
**方案 B**：PlayerSlice 新增 resistances 字段（来自种族/装备）

**推荐方案 A**：不需要新增 state 字段，与 Buff/Debuff 系统自然集成。检查逻辑：

```python
# 怪物攻击后，检查玩家是否有对应伤害类型的抗性效果
player_effects = state.player.get_active_effects()
damage_type = attack.damage_type
for effect in player_effects:
    tags = effect.get("tags", [])
    if f"{damage_type}_immunity" in tags:
        damage = 0; break
    elif f"{damage_type}_resistance" in tags:
        damage = max(1, damage // 2); break
    elif f"{damage_type}_vulnerability" in tags:
        damage *= 2; break
```

**变更**：combat.py `_resolve_monster_responses` 中 `_roll_monster_attack` 返回后添加伤害类型修正。
**复杂度**：小（~15 行新增）。需要定义 tag 命名约定（`{type}_resistance`）。

### Phase 4: 怪物攻击时的优劣势（依赖 P3-1）

**当前**：怪物攻击不考虑任何优劣势。
**目标**：
- 玩家有 `advantage_on_attacks_against` 效果 → 怪物获得优势
- 怪物有 action-preventing 效果 → 跳过行动

这依赖 P3-1（Buff/Debuff 管线闭合）先完成。

**变更**：combat.py `_resolve_monster_responses` + `_roll_monster_attack` 新增 advantage 参数。
**复杂度**：小。

---

## 四、远期目标（不在本轮范围）

| 特性 | 需要的新系统 | 为什么推迟 |
|------|------------|----------|
| 怪物施法（spells） | SpellHandler 需支持怪物施法者 + 法术选择 AI | 需要大量新代码 |
| 特殊能力（ability_refs） | 需要 AbilityHandler + 能力解析管线 | 未设计 |
| 战术笔记（tactics_notes） | LLM Agent 集成 | 需要 AgenticCombatAI |
| 地形偏好（preferred_terrain） | 需要位置/距离模型 | 需战术战斗系统 |
| 移动速度（speed） | 需要位置/距离模型 | 需战术战斗系统 |

---

## 五、文件变更清单

| Phase | 文件 | 变更 |
|-------|------|------|
| 1 | combat.py | `_decide_monster_action` 新增 flee_chance 参数 + 概率逻辑 |
| 1 | combat.py | `_resolve_monster_responses` 传入 flee_chance |
| 2 | combat.py | 新增 `_select_attack()` + `_estimate_damage()` |
| 2 | combat.py | `_resolve_monster_responses` 替换 attacks[0] |
| 3 | combat.py | `_resolve_monster_responses` 怪物伤害后检查玩家效果抗性 |
| 4 | combat.py | `_resolve_monster_responses` + `_roll_monster_attack` 优劣势 |
| all | tests/ | 针对性测试 |

**总复杂度：中（~50 行新增/改动，不需要新文件或架构变更）**

---

## 六、测试策略

- `test_flee_chance_probabilistic`: mock random → 验证 flee_chance 控制逃跑概率
- `test_aggressive_selects_highest_damage`: 多攻击时 aggressive 选最高伤害
- `test_cowardly_selects_longest_range`: 多攻击时 cowardly 选最远射程
- `test_monster_damage_type_vs_player_resistance`: 玩家有 fire_resistance 效果时火焰伤害减半
- `test_monster_advantage_on_blinded_player`: 依赖 P3-1，玩家致盲时怪物获得优势
