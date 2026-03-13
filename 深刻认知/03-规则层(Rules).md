# L3 规则层 (Rules Layer)

> 路径：`backend/app/game_core/rules/`
> 职责：将 Command 转换为 StateDelta 的纯计算引擎，~13,684 行 handler 代码。

---

## 一、整体结构

```
rules/
├── engine.py          — RulesEngine（命令路由器）
├── base.py            — CommandHandler ABC + StaticCommandHandler
├── models.py          — Command / ValidationResult / ExecuteResult / DiceRoll
├── handler_utils.py   — 骰子/类型强制/结果构造工具函数
├── defaults.py        — DEFAULT_RULE_HANDLER_TYPES（23 个 handler 注册表）
├── combat_units.py    — 战斗单位工厂（玩家/同伴/怪物 unit dict）
├── battle_grid.py     — 方格地图（Dijkstra/A*/LoS/地形）
├── battle_ai.py       — 怪物回合决策引擎（纯函数）
├── reward_utils.py    — 奖励处理（gold/xp/items → StateChange 列表）
├── proficiency.py     — 熟练加值计算
└── handlers/
    ├── combat.py          — CombatHandler（9 命令）
    ├── skill_check.py     — SkillCheckHandler（4 命令）
    ├── navigation.py      — NavigationHandler（5 命令）
    ├── inventory.py       — InventoryHandler（6 命令）
    ├── economy.py         — EconomyHandler（3 命令）
    ├── growth.py          — GrowthHandler（5 命令）
    ├── rest.py            — RestHandler（4 命令）
    ├── world_state.py     — WorldStateHandler（14 命令）
    ├── spell.py           — SpellHandler（3 命令）
    ├── spell_preparation.py
    ├── spell_resolver.py
    ├── spell_effects.py
    ├── spell_concentration.py
    ├── status_effect.py   — StatusEffectHandler（5 命令）
    ├── companion.py       — CompanionHandler（4 命令）
    ├── encounter.py       — EncounterHandler（3 命令）
    ├── discovery.py       — DiscoveryHandler（2 命令）
    ├── board.py           — BoardHandler（4 命令）
    ├── receptionist.py    — ReceptionistHandler（2 命令）
    ├── container.py       — ContainerHandler（5 命令）
    ├── interactable.py    — InteractableHandler（1 命令）
    ├── hostile_area.py    — HostileAreaHandler（3 命令）
    ├── crime.py           — CrimeHandler（2 命令）
    └── planner.py         — PlannerHandler × 5（15+ 命令）
```

---

## 二、核心数据结构

### Command — 行动指令

```python
@dataclass
class Command:
    type: str                          # 命令类型标识符
    params: dict[str, Any]            # 命令特定参数
    source: str = "system"            # "system"|"engine"|"narrative_planner"|"user"
    context: dict[str, Any] | None    # 可选元数据
```

### ValidationResult

```python
@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""      # 失败原因
```

### DiceRoll — 骰子追踪

```python
@dataclass
class DiceRoll:
    purpose: str          # "attack_roll", "damage", "saving_throw"...
    dice: str             # "1d20", "2d6+2"
    result: int           # 单次骰子面值
    modifiers: list[dict] # 已应用的修改器
    total: int            # 最终结果
    critical: bool | None # True=自然20, False=自然1, None=正常
```

### ExecuteResult — 执行结果

```python
@dataclass
class ExecuteResult:
    executed: bool                # 命令是否成功执行
    delta: StateDelta | None      # 状态变更（失败时为 None）
    narrative_hints: list[str]    # 叙事层钩子提示
    rolls: list[DiceRoll]        # 所有骰子记录
    time_cost: float              # 消耗时间（小时，0.1667≈10分钟）
    errors: list[str]             # 失败原因列表
    is_dry_run: bool
    metadata: dict[str, Any]      # 自动标准化的结果元数据

    # 类方法
    @classmethod error(reason) -> ExecuteResult
    @classmethod not_implemented(name) -> ExecuteResult
```

---

## 三、RulesEngine — 命令路由器

```python
class RulesEngine:
    def register(handler: CommandHandler) -> None
        # 将 handler.command_types 中所有类型映射到 handler

    def register_if_missing(handler) -> list[str]
        # 避免覆盖已有路由

    def execute(command, state, world) -> ExecuteResult
        # 1. 查找 handler
        # 2. handler.validate() → 纯校验，无副作用
        # 3. 若有效 → handler.compute() → 返回 delta
        # 4. 验证失败立即返回，不执行

    def dry_run(command, state, world) -> ExecuteResult
        # 同 execute，但 is_dry_run=True，可多次调用

    def batch_execute(commands: list[Command]) -> list[ExecuteResult]
        # 顺序执行，每条命令能看到前面命令的状态变更
```

---

## 四、Handler 基类

```python
class CommandHandler(ABC):
    @property command_types: list[str]       # 本 handler 处理的命令类型

    def validate(cmd, state, world) -> ValidationResult
    def compute(cmd, state, world) -> ExecuteResult

class StaticCommandHandler(CommandHandler):
    # 所有具体 handler 的基类，提供 COMMAND_TYPES 类变量支持
    COMMAND_TYPES: tuple[str, ...]
```

---

## 五、工具模块

### handler_utils.py — 通用工具

```python
# 类型强制
coerce_int(v) → int | None
coerce_float(v) → float | None
get_non_empty_string(params, key) → str | None
normalize_tags(raw) → list[str]
resolve_item_heal_amount(template) → int | None  # ConsumableData.effect 或旧 heal_amount

# 骰子
roll_damage_dice("2d6+3") → int   # regex 解析 + random，最小值 1
roll_d20() → int
resolve_roll(roll_fn, advantage, disadvantage) → (selected, all_rolls, notation)
    # advantage: 2d20 取高
    # disadvantage: 2d20 取低
build_dice_roll(purpose, dice, result, modifiers, total) → DiceRoll

# 结果构造
handler_success(handler_name, command_type, changes, metadata, time_cost, rolls, hints)
handler_success_no_delta(...)
handler_failure(handler_name, command_type, errors)
```

### combat_units.py — 战斗单位工厂

```python
build_player_unit(state, world?) → dict
build_companion_unit(character_id, member_data, template) → dict | None
build_monster_unit(monster_id, template, index) → dict

# 标签驱动攻击属性选择
compute_attack_ability_mod(stats, attack_tags) → int
    # RANGED → DEX, FINESSE → max(STR,DEX), 默认 → STR

roll_initiative(unit) → (total, dex)    # d20 + DEX，DEX 作决胜局
build_turn_order(units) → (ordered_ids, initiative_rolls)
assign_positions(units, grid_w, grid_h) # 友方左侧 (0-1)，敌方右侧 (w-2~w-1)
resolve_surprise(units, base_state, stealth_total) → units
```

### battle_grid.py — 方格地图

```python
# 地形类型（9种）
TerrainType: G(草地)/F(森林)/H(山丘)/S(沼泽)/W(水域)/R(石地)/B(墙壁)/M(山脉)/D(浅水)
    # 每种地形有: move_cost, ac_bonus, range_bonus, blocks_los, speed_penalty, damage_immunities

# 环境修改器
EnvironmentModifiers: hit_modifier, ranged_hit_modifier, max_visibility, damage_type_modifiers
compute_environment_modifiers(weather, time_of_day)
    # rain: 命中-1, 火焰×0.5, 雷电×1.5
    # fog: 命中-2, 能见度≤3
    # night: 远程命中-2, 能见度≤4

class BattleGrid:
    distance(a, b) → int            # Manhattan 距离
    cells_in_range(center, r)       # 范围内所有格
    reachable_cells(start, mp, ...)  # Dijkstra 加权 BFS（敌方格阻挡）
    shortest_path(start, goal, ...)  # A* + Manhattan 启发式
    line_of_sight(start, end) → bool # Bresenham，只有中间格才阻挡
    from_map_data(map_data) → BattleGrid
```

### battle_ai.py — 怪物 AI

```python
class MonsterDecision:
    move_to: tuple | None
    action: str           # "attack"|"flee"|"defend"|"hold"
    target_id: str | None
    attack_index: int

decide_monster_turn(unit, grid, all_units) → MonsterDecision
    # 决策树：
    # 1. 逃跑检定（HP 比例 + 性格随机化）
    #    aggressive: flee_threshold×10%/20%
    #    defensive:  flee_threshold×25%/50%
    #    cowardly:   flee_threshold×50%/80%
    # 2. 选择攻击（性格驱动）
    #    aggressive → 最大伤害; defensive → 最高命中; cowardly → 最远射程
    # 3. 目标评分 = 10/(d+1) + 低HP奖励5 + 射程内奖励3 + 掩护惩罚-2
    # 4. 移动 → 攻击 / 靠近 / 坚守
```

---

## 六、全部命令类型（按 Handler 分组）

| Handler | 命令数 | 命令列表 |
|---------|--------|---------|
| **CombatHandler** | 9 | start_combat, combat_move, combat_end_turn, combat_disengage, combat_dash, combat_attack, combat_defend, combat_npc_turn, combat_finalize_status |
| **SkillCheckHandler** | 4 | skill_check, saving_throw, contest, investigate |
| **NavigationHandler** | 5 | move_area, enter_sub_location, leave_sub_location, enter_room, leave_room |
| **InventoryHandler** | 6 | pick_up, drop, equip, unequip, use_item, consume_resource |
| **EconomyHandler** | 3 | trade_buy, trade_sell, refresh_shop |
| **GrowthHandler** | 5 | add_xp, level_up, apply_asi, choose_subclass, create_character |
| **RestHandler** | 4 | rest_short, rest_long, night_watch, set_camp |
| **WorldStateHandler** | 14 | set_flag, remove_flag, modify_disposition, modify_approval, advance_quest, schedule_event, create_rumor, modify_location, add_knowledge, modify_completion, adjust_danger, schedule_npc_move, transition_event_state, change_relationship_stage |
| **SpellHandler** | 3 | cast_spell, prepare_spells, break_concentration |
| **StatusEffectHandler** | 5 | apply_effect, remove_effect, remove_effect_by_type, tick_effects, tick_combat_effects |
| **CompanionHandler** | 4 | recruit_companion, dismiss_companion, force_leave_companion, restore_companion_after_combat |
| **EncounterHandler** | 3 | encounter_check, generate_loot, clear_hostile |
| **DiscoveryHandler** | 2 | discover, passive_scan |
| **BoardHandler** | 4 | browse_board, board_accept_quest, board_complete_quest, board_retire_quest |
| **ReceptionistHandler** | 2 | receptionist_accept_quest, receptionist_report_quest |
| **ContainerHandler** | 5 | open_container, disarm_trap, take_from_container, take_all, interact_object |
| **InteractableHandler** | 1 | interact_object_v2 |
| **HostileAreaHandler** | 3 | enter_hostile, record_hostile_stealth_choice, mark_hostile_spotted |
| **CrimeHandler** | 2 | steal, lockpick |
| **PlannerQuestHandler** | 5 | planner_create_quest, planner_update_quest, planner_delete_quest, planner_activate_milestone, planner_complete_milestone |
| **PlannerNpcHandler** | 2 | planner_add_npc_location, planner_update_npc_location |
| **PlannerWorldHandler** | 2 | planner_plant_environmental, planner_schedule_rumor |
| **PlannerItemHandler** | 2 | planner_create_item, planner_place_item |
| **PlannerRuntimeHandler** | 2 | planner_capture_state_checkpoint, planner_record_timeline_event |

**合计：~96 个命令类型，23 个 Handler**

---

## 七、关键 Handler 实现细节

### combat_attack 执行流程

```
1. 校验：战斗激活 + 攻击者存活/行动可用 + 目标在范围内且有 LoS
2. 获取战斗 unit dict（不修改 state，只读）
3. 掷 d20 + 能力修正（RANGED→DEX, FINESSE→max(STR,DEX), 默认→STR）
4. vs 目标 AC（含状态效果 AC 修正）
5. 命中 → 掷伤害骰 + 能力修正
6. 应用地形/天气环境修正（EnvironmentModifiers）
7. 应用目标抗性（resistances/immunities）
8. 应用玩家特有 DR（职业特性）
9. 生成 StateChange("areas", "set", "combat[sub_area_id].units[unit_id].hp", new_hp)
10. 返回 ExecuteResult(rolls=[attack_roll, damage_roll], metadata={hit,damage,...})
```

### cast_spell 执行流程

```
1. 校验：法术已准备 + 有对应等级法术位 + 目标合法
2. 消耗法术位 → StateChange("player", "set", "spell_slots.N.current", ...)
3. 处理专注（如需 → 先 break_concentration）
4. 分发到 spell_effects.py：
   - apply_combat_target(): damage/crowd_control（含骰子伤害+豁免）
   - apply_self_target(): heal/buff/control/utility
5. 提环施放：upcast_dice 额外加骰
```

### 技能检定（skill_check）

```python
# D&D 优劣势机制
resolve_roll(roll_fn, advantage, disadvantage):
    if advantage:   2d20 取高
    if disadvantage: 2d20 取低
    else:           1d20
→ result + ability_modifier + proficiency_bonus(if proficient) vs DC
```

---

## 八、错误处理策略

1. **验证失败立即返回** — `ExecuteResult(executed=False, errors=[reason])`，不执行 compute
2. **无回滚机制** — `batch_execute()` 累积状态，但失败项不回滚前面的变更
3. **Planner 命令门控** — `source="narrative_planner"` 才能执行 planner_* 命令
4. **骰子永远安全** — `roll_damage_dice()` 最小值 1，不会产生非法值

---

## 九、设计模式

| 模式 | 说明 |
|------|------|
| **纯函数 validate** | 无副作用，可反复调用（dry_run 复用） |
| **快照式变更** | list/dict 变更全量替换（`inventory = new_list`），不原地修改 |
| **Handler 互不通信** | 每个 handler 独立工作，RulesEngine 不在 handler 间协调 |
| **时间代价跟踪** | `time_cost` 字段支持 tick 生命周期的小时计费 |
| **向后兼容字段** | handler 同时识别新旧字段名（如 `heal_amount` vs `consumable_data.effect.dice`）|
| **延迟模式** | 部分命令（passive_scan）在 metadata 中标记 `deferred_to_hook`，实际由 Settlement Hook 执行 |
