# P3-1: Buff/Debuff 管线闭合

> 状态：设计完成，可施工
> 创建：2026-03-05
> 关联：P3 §六.1（Buff/Debuff 管线断裂）
> 前置调查：P3 审计 + 本轮深入调查（修正了 P3 的错误结论）

---

## 一、P3 审计修正

P3 将 `get_effect_modifiers()` / `get_disadvantage_checks()` / `get_advantage_on_attacks_against()` 标记为"死方法"。**深入调查发现这三个方法均已有消费者：**

| 方法 | 消费者 | 位置 |
|------|--------|------|
| `get_effect_modifiers()` | CombatHandler._resolve_monster_responses | combat.py:1058 |
| `get_advantage_on_attacks_against()` | CombatHandler._compute_attack_resolution | combat.py:581 |
| `get_disadvantage_checks()` | SkillCheckHandler._compute_skill_check | skill_check.py:159 |
| `get_disadvantage_checks()` | SkillCheckHandler._compute_saving_throw | skill_check.py:204 |
| `is_action_prevented()` | CombatHandler + SpellHandler | combat.py:57, spell.py:109 |

**读取端完整、消费端在位。真正的断裂在效果实例创建层——行为字段从未被写入效果 dict，读取端永远读到空值。**

---

## 二、断裂点精确定位

### 2.1 效果创建不传播模板行为字段（核心断点）

两条效果创建路径都只传播 `modifiers` / `periodic` / `tags`，不传播行为字段：

```
StatusEffectTemplate (skills.json):
  disadvantage_on: ["attack"]            <-- 字段已定义（当前数据未填）
  advantage_on_attacks_against: true     <-- 字段已定义（当前数据未填）
  prevents_action: true                  <-- 字段已定义（intoxicated 已填）
  save_end_of_turn / save_dc             <-- 字段已定义（当前数据未填）
  cure_conditions                        <-- 字段已定义（当前数据未填）
  modifiers: {"ac": 2}                   <-- 字段已定义（当前数据未填）
          |
          | (NOT propagated)
          v
路径 A: spell_effects.py::build_spell_effect_instance()
路径 B: StatusEffectHandler::_build_effect_payload()
          |
          | (字段缺失)
          v
ActiveEffect runtime dict:
  disadvantage_checks: (缺失)     --> get_disadvantage_checks() 返回 []
  advantage_on_attacks_against: (缺失) --> get_advantage_on_attacks_against() 返回 False
  modifiers: {}                   --> get_effect_modifiers() 返回 {}
```

**关键认知：读取 API 没问题，写入 API 没问题，Settlement tick 没问题。唯一的断点是创建实例时模板字段丢失。**

### 2.2 数据未填充

6 个 StatusEffectTemplate 的描述文本与实际字段不一致：

| 效果 ID | 描述 | 缺失字段 |
|---------|------|---------|
| `poisoned` | "属性检定具有劣势" | 无 `disadvantage_on`、无 `modifiers` |
| `shielded` | "AC提升2点" | 无 `modifiers: {"ac": 2}` |
| `blinded` | "攻击劣势，针对其攻击有优势" | 无 `disadvantage_on`、无 `advantage_on_attacks_against` |
| `blessed` | "攻击和豁免+1d4加成" | 无 `modifiers` |
| `silenced` | "无法施法" | 设计上不阻止行动，只阻止需要言语的法术（保持现状） |
| `intoxicated` | "无法采取行动" | `prevents_action: true` 已正确填充 |

### 2.3 玩家攻击加值不受效果影响

combat.py:584-585:
```python
attack_total = roll_result + strength_mod + prof
# 缺少: + effect_mods.get("attack", 0)
```

### 2.4 怪物 AC 不受怪物自身效果影响

combat.py:586:
```python
target_ac = state.areas.participant_ac(participant)
# 缺少: + participant 的 active_effects 中的 ac 修正
```

### 2.5 `get_advantage_on_attacks_against()` 语义错位

combat.py:581:
```python
adv = state.player.get_advantage_on_attacks_against()
```
该方法语义为"对我的攻击具有优势"（如致盲/束缚/俯卧时，攻击我的人有优势）。当前用作玩家自己攻击时的优势——**语义反转**。

正确行为：
- 玩家攻击怪物 → 检查**怪物**的 `advantage_on_attacks_against` → 玩家获得优势
- 怪物攻击玩家 → 检查**玩家**的 `advantage_on_attacks_against` → 怪物获得优势

当前代码恰好反了：玩家攻击时检查了自己的标志。怪物攻击时根本没检查。

---

## 三、反屎山原则

针对这次任务的具体约束：

1. **"接线"不"改造"** — 读取 API 正确、写入 API 正确、tick 机制正确。只修复创建层的字段传播。不重构已有代码。
2. **数据驱动** — 主要修复是填充 StatusEffectTemplate 数据。数据正确后，已有代码路径自动生效。
3. **一个合并函数，两个调用点** — 模板字段合并是一个纯函数，被 spell_effects 和 status_effect_handler 两处调用。不建 utility 类、不建抽象层。
4. **不加新文件** — 所有改动在已有文件中完成。
5. **Phase 3 可选** — save_end_of_turn 是独立增强，不影响 Phase 1-2 的正确性。不做 scope creep。

---

## 四、实施方案

### Phase 1: 数据补全 + 模板字段传播

**目标：让已有读取端代码能读到有效值。**

#### 1a. 填充 StatusEffectTemplate 数据

更新 `data/goblin_slayer/v2/skills.json`，在 `status_effects` 中补全缺失字段：

| 效果 | 补充字段 |
|------|---------|
| `poisoned` | `"disadvantage_on": ["attack", "ability_check"]` |
| `shielded` | `"modifiers": {"ac": 2}` |
| `blinded` | `"disadvantage_on": ["attack"]`, `"advantage_on_attacks_against": true` |
| `blessed` | `"modifiers": {"attack": 1, "saving_throw": 1}` |
| `silenced` | 不改（D&D 沉默不给劣势，阻止言语法术应通过法术系统检查） |
| `intoxicated` | `"cure_conditions": ["damage"]`（受伤苏醒） |

同步补充 `cure_conditions` 到合适的效果：
- `poisoned`: `"cure_conditions": ["long_rest"]`
- `blinded` / `silenced`: 无（靠持续时间到期）

#### 1b. 模板字段合并函数

在 `spell_effects.py` 新增模块级纯函数：

```python
def merge_status_effect_template(
    effect_dict: dict[str, Any],
    template: StatusEffectTemplate | None,
) -> None:
    """将 StatusEffectTemplate 行为字段合并到效果实例 dict。

    效果实例已有的值优先（允许 per-spell 覆盖模板默认值）。
    就地修改 effect_dict。
    """
    if template is None:
        return
    if "disadvantage_checks" not in effect_dict and template.disadvantage_on:
        effect_dict["disadvantage_checks"] = list(template.disadvantage_on)
    if "advantage_on_attacks_against" not in effect_dict and template.advantage_on_attacks_against:
        # 注意：StatusEffectTemplate 中字段名是 advantage_on_attacks_against
        # 但当前 dataclass 没有这个字段 —— 需要先补到 dataclass（见 1d）
        effect_dict["advantage_on_attacks_against"] = True
    if "prevents_action" not in effect_dict and template.prevents_action:
        effect_dict["prevents_action"] = True
    if "save_end_of_turn" not in effect_dict and template.save_end_of_turn:
        effect_dict["save_end_of_turn"] = template.save_end_of_turn
    if "save_dc" not in effect_dict and template.save_dc is not None:
        effect_dict["save_dc"] = template.save_dc
    if "cure_conditions" not in effect_dict and template.cure_conditions:
        effect_dict["cure_conditions"] = list(template.cure_conditions)
    # modifiers: 模板提供默认值，实例值优先
    if not effect_dict.get("modifiers") and template.modifiers:
        effect_dict["modifiers"] = dict(template.modifiers)
```

#### 1c. 两条创建路径接入合并

**路径 A: spell_effects.py**

`build_spell_effect_instance()` 新增 `status_effect_template` 参数：

```python
def build_spell_effect_instance(
    spell_id: str,
    effect_type: str,
    template: Any,
    effect: Any,
    status_effect_template: StatusEffectTemplate | None = None,  # 新增
) -> tuple[dict[str, Any] | None, bool]:
    ...  # 现有逻辑不变
    effect_instance = { ... }  # 现有构建不变
    merge_status_effect_template(effect_instance, status_effect_template)  # 新增
    return (effect_instance, concentration)
```

调用方适配（`apply_self_target()` 和 `apply_combat_target()`）：
- 新增 `world` 参数（WorldInstance）
- 在调用 `build_spell_effect_instance()` 前，用 `applies_status` 查询模板：

```python
applies_status = read_non_empty_string(effect, template, "applies_status")
se_template = None
if applies_status and world.has_registry("skills"):
    se_template = world.skills.get_status_effect(applies_status)
effect_instance, conc = build_spell_effect_instance(
    spell_id, effect_type, template, effect,
    status_effect_template=se_template,
)
```

SpellHandler._compute_cast_spell() 已有 world 参数，只需透传。

**路径 B: StatusEffectHandler._build_effect_payload()**

在构建 payload 后，查询模板并合并：

```python
def _build_effect_payload(self, cmd: Command, world: WorldInstance) -> dict[str, Any]:
    ...  # 现有构建不变
    payload = { ... }
    # 查询 StatusEffectTemplate 并合并行为字段
    if world.has_registry("skills"):
        se_template = world.skills.get_status_effect(payload["effect_id"])
        merge_status_effect_template(payload, se_template)
    return payload
```

`_build_effect_payload` 的调用方 `_compute_apply_effect` 已有 world 参数。

#### 1d. StatusEffectTemplate dataclass 补字段（如需）

检查 `StatusEffectTemplate` 是否已有 `advantage_on_attacks_against` 字段。如果没有，需要补：

```python
@dataclass(slots=True)
class StatusEffectTemplate:
    ...
    advantage_on_attacks_against: bool = False  # 新增（如缺失）
```

同步更新 `_parse_status_effect_template()` 解析逻辑。

### Phase 2: 战斗修正接入

**目标：让已有的效果修正值影响战斗计算结果。**

#### 2a. 玩家攻击加值含效果修正

combat.py `_compute_attack_resolution()`：

```python
# 现有:
attack_total = roll_result + strength_mod + prof
# 改为:
effect_mods = state.player.get_effect_modifiers()
attack_bonus_from_effects = effect_mods.get("attack", 0)
attack_total = roll_result + strength_mod + prof + attack_bonus_from_effects
```

同步在 modifiers 列表中新增：
```python
modifiers=[
    {"name": "str", "value": strength_mod},
    {"name": "proficiency", "value": prof},
    {"name": "effects", "value": attack_bonus_from_effects},  # 新增
],
```

#### 2b. 怪物 AC 含效果修正

新增模块级纯函数（combat.py）：

```python
def _participant_effect_ac_mod(participant: Mapping[str, Any]) -> int:
    """从战斗参与者的 active_effects 中提取 AC 修正。"""
    total = 0
    for effect in participant.get("active_effects", []):
        mods = effect.get("modifiers")
        if isinstance(mods, Mapping):
            raw = mods.get("ac", 0)
            try:
                total += int(raw)
            except (TypeError, ValueError):
                pass
    return total
```

使用（`_compute_attack_resolution` + `_compute_shove_resolution`）：
```python
target_ac = state.areas.participant_ac(participant) + _participant_effect_ac_mod(participant)
```

#### 2c. 修正 advantage 语义

**玩家攻击怪物时**（`_compute_attack_resolution`）：
```python
# 旧（错误）:
adv = state.player.get_advantage_on_attacks_against()
# 新: 检查目标怪物是否有让攻击者获得优势的效果
target_effects = participant.get("active_effects", [])
adv = any(bool(e.get("advantage_on_attacks_against")) for e in target_effects)
```

**怪物攻击玩家时**（`_resolve_monster_responses`）：
```python
# 已有: player_ac 计算
# 新增: 怪物攻击玩家时，检查玩家是否有 advantage_on_attacks_against
monster_adv_on_player = state.player.get_advantage_on_attacks_against()
# 传递给 _roll_monster_attack 或直接影响骰投
```

`_roll_monster_attack()` 需要新增 `advantage` 参数来支持优势骰投。

### Phase 3: save_end_of_turn（可选，独立增强）

**目标：效果持续期间，每次 tick 尝试豁免结束。**

在 `StatusEffectHandler._compute_tick_effects()` 中，对每个效果：

```python
for effect in updated_effects:
    # ... 现有 periodic/duration 处理 ...

    # save_end_of_turn: 如果效果有此字段，进行豁免检定
    save_ability = effect.get("save_end_of_turn")
    save_dc = effect.get("save_dc")
    if save_ability and save_dc is not None:
        save_mod = state.player.get_modifier(save_ability)
        roll, _, _ = resolve_roll()
        if roll + save_mod >= save_dc:
            # 豁免成功，移除此效果
            expired_ids.append(effect.get("instance_id"))
```

注意：这需要在 StatusEffectHandler 中引入 `resolve_roll`（已在 combat/skill_check 中使用的模块级函数）。

---

## 五、文件变更清单

| Phase | 文件 | 变更 |
|-------|------|------|
| 1a | `data/goblin_slayer/v2/skills.json` | 补全 StatusEffectTemplate 缺失字段 |
| 1b | `app/game_core/rules/handlers/spell_effects.py` | 新增 `merge_status_effect_template()` 函数 |
| 1c | `app/game_core/rules/handlers/spell_effects.py` | `apply_self_target` / `apply_combat_target` 新增 world 参数 + 模板查询 |
| 1c | `app/game_core/rules/handlers/spell.py` | `_compute_cast_spell` 透传 world 到 apply_* |
| 1c | `app/game_core/rules/handlers/status_effect.py` | `_build_effect_payload` 合并模板字段 |
| 1d | `app/game_core/content/registries/skills.py` | StatusEffectTemplate 补 `advantage_on_attacks_against` 字段（如缺失） |
| 2a | `app/game_core/rules/handlers/combat.py` | attack_total 加效果修正 |
| 2b | `app/game_core/rules/handlers/combat.py` | 新增 `_participant_effect_ac_mod()` + 使用 |
| 2c | `app/game_core/rules/handlers/combat.py` | 修正 advantage 语义 + 怪物攻击玩家优势 |
| 3 | `app/game_core/rules/handlers/status_effect.py` | tick_effects 增加 save_end_of_turn 逻辑 |
| all | `tests/` | 针对性测试 |

---

## 六、预估复杂度与风险

| Phase | 复杂度 | 风险 |
|-------|--------|------|
| Phase 1 | 中 | 改两条效果创建路径的函数签名（参数增加）。需要确保所有调用方同步更新。风险可控——调用方固定，通过 Grep 穷举。 |
| Phase 2 | 小 | 一行级修改 + 一个 8 行纯函数 + advantage 语义修正。CombatHandler 已有集成点。 |
| Phase 3 | 中 | tick_effects 增加分支。需要引入 resolve_roll 到 StatusEffectHandler。独立于 Phase 1-2，可拆开施工。 |

**总体：中等。不需要架构变更，不需要新文件，不需要新的 handler/hook。**

---

## 七、测试策略

### Phase 1 测试
- `test_build_spell_effect_instance_merges_template`: 验证 `merge_status_effect_template` 正确合并 disadvantage_checks / advantage_on_attacks_against / prevents_action / modifiers / cure_conditions
- `test_merge_template_instance_priority`: 验证效果实例已有值优先于模板值
- `test_apply_effect_command_merges_template`: 验证 StatusEffectHandler 的 apply_effect 命令正确合并模板字段
- `test_data_status_effects_have_behavioral_fields`: 数据校验——确保 skills.json 中每个 StatusEffectTemplate 的行为字段与描述一致

### Phase 2 测试
- `test_player_attack_includes_effect_bonus`: 玩家有 attack +1 效果时，attack_total 增加
- `test_monster_ac_includes_effect_mod`: 怪物有 AC +2 效果时，攻击需要更高骰值
- `test_advantage_checks_target_not_player`: 验证 blinded 怪物被攻击时玩家获得优势
- `test_monster_advantage_on_blinded_player`: 验证 blinded 玩家被攻击时怪物获得优势

### Phase 3 测试
- `test_save_end_of_turn_removes_effect_on_success`: 豁免成功时效果被移除
- `test_save_end_of_turn_keeps_effect_on_failure`: 豁免失败时效果保留

---

## 八、P3 文档同步更新

施工后需更新 P3 文档 §四.2 的错误结论：

| 旧结论 | 修正 |
|--------|------|
| `get_effect_modifiers()` 0 调用 | 实际有 1 处调用（combat.py:1058），但覆盖面不全 |
| `get_disadvantage_checks()` 0 调用 | 实际有 2 处调用（skill_check.py:159,204） |
| `get_advantage_on_attacks_against()` 0 调用 | 实际有 1 处调用（combat.py:581），但语义错位 |

将 §六.1 的问题描述从"CombatHandler 从不调用"修正为"效果创建层不传播模板行为字段 + 数据未填充"。
