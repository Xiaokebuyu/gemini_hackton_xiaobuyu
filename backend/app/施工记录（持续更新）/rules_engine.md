# ❷ 规则引擎层施工记录

**设计文档**：规则引擎层设计规范（粗读）.md
**代码路径**：`app/game_core/rules/`
**Phase**：1（引擎）+ 4A（具体 Handler）

## 模块状态

| 组件 | 状态 | 说明 |
|------|------|------|
| `Command` | [完成] | type/params/source/context |
| `ValidationResult` | [完成] | ok/reason |
| `ExecuteResult` | [完成] | success/delta/narrative_hints/rolls/time_cost/errors |
| `DiceRoll` | [完成] | purpose/dice/result/modifiers/total/critical |
| `CommandHandler` ABC | [完成] | command_types(plural) + validate + compute |
| `StaticCommandHandler` | [完成] | 共享 stub 基类，返回 "not implemented" |
| `RulesEngine` | [完成] | register/execute/validate/dry_run/batch_execute |

### Handler 实施状态

| # | Handler | command_types | 状态 | 复杂度 |
|---|---------|---------------|------|--------|
| 1 | `CombatHandler` | attack, defend, disengage, dash, shove, flee, use_combat_item, offhand_attack, start_combat, stand_up | [完成] | 高 |
| 2 | `SkillCheckHandler` | skill_check, saving_throw, contest, investigate | [完成] | 低 |
| 3 | `NavigationHandler` | move_area, enter_sub_location, leave_sub_location | [完成] | 中 |
| 4 | `InventoryHandler` | pick_up, drop, equip, unequip, use_item, consume_resource | [完成] | 中 |
| 5 | `EconomyHandler` | trade_buy, trade_sell, refresh_shop | [完成] | 低 |
| 6 | `GrowthHandler` | add_xp, level_up, apply_asi, choose_subclass, create_character | [完成] | 中 |
| 7 | `RestHandler` | rest_short, rest_long, night_watch, set_camp | [完成] | 中 |
| 8 | `CrimeHandler` | steal, lockpick | [完成] | 低 |
| 9 | `EncounterHandler` | encounter_check, generate_loot, clear_hostile | [完成] | 中 |
| 10 | `ContainerHandler` | open_container, disarm_trap, take_from_container, take_all, interact_object | [完成] | 中 |
| 11 | `WorldStateHandler` | set_flag, modify_disposition, modify_approval, advance_quest, schedule_event, create_rumor, modify_location, add_knowledge, modify_completion, adjust_danger | [完成] | 高 |
| 12 | `StatusEffectHandler` | apply_effect, remove_effect, remove_effect_by_type, tick_effects, tick_combat_effects | [完成] | 中 |
| 13 | `SpellHandler` | cast_spell, prepare_spells, break_concentration | [完成] | 高 |

## 决策记录

### [D-R01] StaticCommandHandler 共享 stub 基类

所有 13 个 Handler 在骨架阶段继承 `StaticCommandHandler`：
- `COMMAND_TYPES` 类属性定义处理的指令类型
- `validate()` 返回 `ok=False, reason="not implemented"`
- `compute()` 返回 `ExecuteResult.not_implemented()`

好处：所有 Handler 可安全注册到 RulesEngine，未实现的指令返回明确错误而非 panic。

### [D-R02] batch_execute 顺序应用 delta

`RulesEngine.batch_execute()` 在循环中逐条执行 Command，成功后立即 `state.apply(delta)`。
后续 Command 看到前序 Command 的状态变更。这是设计文档要求的顺序语义。

### [D-R03] Handler 只读约定

`CommandHandler.validate()` 和 `compute()` 参数为 `state: StateContainer`（不是 StateSnapshot）。
**约定**：Handler 只读 state，不直接调用写入 API。所有变更通过返回 StateDelta 表达。
引擎负责 `state.apply(delta)` — Handler 本身无权写状态。

### [D-R04] SkillCheckHandler MVP 仅锚定 PlayerSlice

`skill_check` / `saving_throw` 本轮统一读取 `state.player` 作为数值来源。
`contest` 对非玩家目标不尝试推断运行时属性，要求调用方显式提供 `target_bonus`。

这样保持规则层可用，同时避免在 NPC 运行时模型未定稿前引入错误耦合。

### [D-R05] saving_throw 已从统一熟练升级为条件化熟练

~~MVP 阶段统一按 `ability modifier + player.proficiency_bonus`。~~

已深化：`PlayerSlice` 新增 `save_proficiencies: list[str]`（如 `[“dex”, “cha”]`）。
`SkillCheckHandler._compute_saving_throw` 现在只在 `ability in save_proficiencies` 时加熟练加值。

- 支持 restore/serialize/snapshot 全链路
- 空列表时所有豁免不加熟练（兼容既有无 save_proficiencies 的存档数据）
- 创建角色时应由 `GrowthHandler.create_character` 根据职业设置初始豁免熟练

### [D-R06] WorldStateHandler 采用来源敏感的兼容收权

`WorldStateHandler` 现在对 `modify_location` 做双模式处理：

- 玩家位置兼容模式：`{area_id, location_id?}`
- 区域属性模式：`{area_id, key, value}`

其中玩家位置兼容模式仅允许 `engine/system` 调用，用于保留角色创建等既有流程；
`ai_osiris` 被显式禁止直接修改玩家位置，但仍允许通过区域属性模式修改世界环境态。

### [D-R07] schedule_event 继续以 trigger_tick 为运行时契约

虽然验证层接受受限的 `trigger_condition` 输入：

- `absolute_tick`
- `time_slots_elapsed`

但在 `WorldStateHandler.compute()` 阶段会统一编译回 `trigger_tick`，
与 `ScheduledEventHook` 的当前消费契约保持一致，不引入完整条件树。

### [D-R08] modify_completion 沿用归一化章节进度模型

`QuestSlice.chapter_completion` 当前使用 `0.0 ~ 1.0` 表示章节完成度。
因此 `modify_completion.delta` 的校验范围固定为：

- 最小 `-0.20`
- 最大 `+0.50`

与设计文档中的百分比范围语义保持一致，但落到当前运行时是归一化浮点增量。

### [D-R09] StatusEffectHandler 先锚定 PlayerSlice.active_effects

本轮 `StatusEffectHandler` 作为稳定扩展点只处理玩家的 `active_effects`：

- `apply_effect`：按 `effect_id` 覆盖/刷新
- `remove_effect`：按 `effect_id` 移除
- `remove_effect_by_type`：按 `effect_type`（兼容 `effect_id` 别名）批量移除
- `tick_effects`：统一做逐格倒计时，并处理最小的 `periodic.damage/heal`

当前不引入 NPC 效果容器，也不实现复杂豁免/专注联动；先把规则层和 P20 Hook 的最小闭环补齐。

### [D-R10] NavigationHandler 先只处理静态 MapRegistry

本轮 `NavigationHandler` 只接静态地图模板：

- `move_area`：切换 `current_area` 并清空 `current_location`
- `enter_sub_location`：只验证 `MapRegistry.sub_locations`
- `leave_sub_location`：只负责退出当前子地点

明确不接动态子区域、被动感知扫描、遭遇触发和敌对检测；先把玩家位置移动的稳定入口补齐。

### [D-R11] InventoryHandler 的 use_item 只做最小治疗模型

本轮 `InventoryHandler` 补齐了全部 5 条指令，但 `use_item` 仍保持受控 MVP：

- 只识别 `heal_amount / heal / restore_hp`
- 识别到治疗效果时消耗 1 个物品并恢复 HP
- 其他模板返回 `success=True + status="no_effect"`，不消耗物品

这样能保证默认管线可用，同时不提前承诺复杂物品效果系统。

### [D-R12] GrowthHandler 采用显式升级边界

本轮 `GrowthHandler` 的成长链明确拆分为两步：

- `add_xp`：只增加经验并报告 `available_level`
- `level_up`：显式执行升级（等级/HP/熟练加值/特性）

这样不让经验增长隐式触发升级，避免在当前阶段引入更复杂的链式命令和升级选择流程。

另外：

- `apply_asi` 只在 4/8/12/16/19 级开放
- `choose_subclass` 默认最低等级为 6，除非类模板显式声明 `subclass_level`
- `create_character` 继续保持与当前运行时流程兼容

### [D-R13] EncounterHandler 先落为确定性遭遇/掉落边界

本轮 `EncounterHandler` 补齐了三个命令，但仍有意保持为”稳定扩展点”：

- `encounter_check` 使用确定性阈值：
  - `danger * period_multiplier >= 1.0` 视为触发
  - 不引入随机数，也不接 `DynamicSubAreaManager`
- 触发时只往 `AreaSlice.hostile_tracking` 写入最小 hostile runtime 状态
- `generate_loot` ~~只处理确定性金币与确定性掉落~~ 已深化为概率掉落：
  - 金币自动入账
  - 物品掉落现使用 `random.random() < chance` 概率判定
  - `chance=1.0`（或缺省）的物品仍保证掉落
  - 物品只出现在 metadata 中，不自动入包
- `clear_hostile` 采用幂等语义：
  - 未找到目标 hostile 时返回 `success=True + status=”noop”`

`encounter_check` 仍保持确定性阈值，仅 `generate_loot` 引入了概率。

### [D-R14] EconomyHandler 先做最小真实交易，刷新库存使用确定性轮转

本轮 `EconomyHandler` 已补齐：

- `refresh_shop`
- `trade_buy`
- `trade_sell`

当前商店刷新不使用随机抽取，而是按 `absolute_tick` 对可选 rotating 库存做确定性轮转。
这样测试和默认运行结果都稳定可预测。

另外：

- `trade_buy` 在 `shop_state` 缺失时可从商人模板自动初始化库存
- `trade_sell` 支持最小 buyback 行写回
- buyback 行会在下一次 `refresh_shop` 时被新的基础库存覆盖，这是有意的 MVP 取舍

### [D-R15] RestHandler 先锚定 PlayerSlice 做最小真实恢复

本轮 `RestHandler` 已补齐：

- `rest_short`
- `rest_long`
- `night_watch`
- `set_camp`

其中：

- `rest_short` 只恢复 `recovery="short_rest"` 的职业资源，不恢复法术位
- `rest_long` 会恢复 HP/法术位/职业资源，并清除可被长休移除的效果
- `rest_long` 时间消耗固定为 8.0（D&D 5e 标准长休时长），不依赖当前时段计算
- `rest_long` 本轮不自动串联 `night_watch` 或 `refresh_shop`
- `set_camp` 本轮只往 `areas.<area_id>.properties.active_camp` 写运行时营地信息，不生成真实营地子地点
- `night_watch` 只返回结构化结果，不直接改状态

### [D-R16] CrimeHandler 先只支持容器相关的最小犯罪交互

本轮 `CrimeHandler` 已补齐：

- `steal`
- `lockpick`

当前边界明确收窄为“容器相关”：

- `steal` 只支持从已打开、未上锁的容器偷取物品
- `target_npc` 模式会明确返回 `NPC theft unsupported in MVP`
- `lockpick` 只处理容器锁状态

所有检定本轮都采用确定性的被动值：

- 潜行：`10 + stealth bonus`
- 撬锁：`10 + sleight_of_hand bonus`

暂不引入犯罪值、通缉或守卫响应系统。

### [D-R17] ContainerHandler 先只处理已初始化容器

本轮 `ContainerHandler` 已补齐：

- `open_container`
- `disarm_trap`
- `take_from_container`
- `take_all`

当前容器系统仍是最小运行时版本：

- 只操作 `AreaSlice.container_states`
- 不从内容模板自动初始化容器
- 只处理已经存在于 runtime state 的容器

同时：

- `open_container` 会处理最小陷阱/上锁分支，但不自动撬锁
- `disarm_trap` 与 `CrimeHandler.lockpick` 一样使用确定性的被动值
- `take_all` 可以一次性转移全部物品和金币

### [D-R18] SpellHandler 先锚定 PlayerSlice 做最小真实施法

本轮 `SpellHandler` 已补齐：

- `cast_spell`
- `prepare_spells`
- `break_concentration`

当前边界已从“仅玩家自身”推进到“玩家自身 + 战斗内单体目标”的最小法术语义：

- 法术模板统一从 `SkillRegistry` 读取，不新增 `SpellRegistry`
- `break_concentration` 会清空 `player.concentration`，并同时清理：
  - `player.active_effects` 中的专注关联效果
  - `AreaSlice.hostile_tracking.participants[*].active_effects` 中的专注目标效果
- `prepare_spells` 支持：
  - 准备制职业的显式上限
  - 受限的 `prepared_formula`（仅加法表达式）
  - 缺少职业模板时的 fallback 上限
- `cast_spell` 当前完整支持：
  - 自身治疗
  - 自身增益/控制（以 `active_effects` 形式落地）
- 战斗内单体非自身目标：
  - `damage` 直接落到 `participants[*].hp/alive`
  - `control` 直接落到 `participants[*].active_effects`
  - 若专注控制替换旧专注，会同步清掉旧目标上的效果
  - 若伤害击倒最后一个目标，会同步把当前遭遇标为 `cleared`
- 对当前未建模的目标形状或效果类型：
  - 返回结构化 `unsupported_target` / `unsupported_effect`
  - 不消耗法术位或职业资源
- 当前仍明确不做：
  - 多目标 / AoE
  - 敌方豁免、抗性、完整法术命中判定
  - 敌方效果的完整 tick / settlement 驱动

### [D-R19] CombatHandler 不新增 combat slice，先借 hostile_tracking 挂最小战斗事实

本轮 `CombatHandler` 已补齐：

- `start_combat`
- `defend`
- `disengage`
- `dash`
- `flee`
- `use_combat_item`
- `attack`
- `shove`
- `offhand_attack`

其中当前明确采用”稳定扩展点”边界：

- 不新增 `combat` slice
- 最小战斗运行时挂在 `AreaSlice.hostile_tracking`
- `start_combat` 只允许 `engine/system`（不注册到 `DEFAULT_ACTION_COMMAND_TYPES`）
- `start_combat` 会写入：
  - `combat_active`
  - `combat_round`
  - `surprise_state`
  - `participants`
  - `player_flags`
- `defend / disengage / dash` 只更新玩家战斗旗标
- ~~`flee` 使用确定性的被动值，不掷骰~~
- ~~`attack` 做最小确定性命中与伤害结算~~
- 已深化：`attack`、`shove`、`flee` 现在使用 d20 骰子（`random.randint(1, 20)`），
  与 `SkillCheckHandler` 对齐。每次攻击/推搡/逃跑均产出 `DiceRoll` 追踪记录，
  包含暴击（nat 20）/大失败（nat 1）判定。
- `use_combat_item` 本轮只支持战斗中的自身治疗消耗品
- `offhand_attack` 使用 d20 攻击检定，命中时固定 1 点伤害
- 仍保持 MVP 边界：
  - 不引入完整战斗引擎
  - 不引入先攻/怪物回合
  - 不新增 `combat` slice
  - 伤害公式仍为 `max(1, proficiency + str_mod)`，不解析武器模板

### [D-R20] DEFAULT_ACTION_COMMAND_TYPES 补注册

本轮补注册了两个已有 handler 但此前遗漏的 action→command 映射：

- `refresh_shop`：EconomyHandler 已实现，但此前未注册到默认 dispatcher
- `night_watch`：RestHandler 已实现，但此前未注册到默认 dispatcher

`start_combat` **不注册**：该命令仅允许 `engine/system` 来源，不是玩家可触发的动作。

### [D-R21] InteractionService.shop_refresh 改走 TickCoordinator

此前 `InteractionService._execute_shop_refresh` 直接调用 `rules_engine.execute()` + `state.apply()`，
绕过了 `TickCoordinator` 的 tick 生命周期（时间累积、settlement hooks、持久化）。

已修复：改为通过 `_execute_structured_action()` 走完整 pipeline，
与 `_execute_pipeline_action()` 保持一致的执行路径。

## 填充 TODO（推荐实施顺序）

**第一批（AIOsirisHook 硬依赖）**：
- [x] `SkillCheckHandler`：d20 判定 + 属性调整值 + 熟练加值
- [x] `WorldStateHandler`：10 个 command_type 路由到对应 Slice 操作

**第二批（核心游玩循环）**：
- [x] `NavigationHandler`：区域移动 + 子区域进出
- [x] `InventoryHandler`：物品拾取/丢弃/装备
- [x] `StatusEffectHandler`：效果施加/移除/tick

**第三批**：
- [x] `GrowthHandler`：经验/升级/ASI/子职业/角色创建
- [x] 其余 Handler 按需实施
- [x] `CombatHandler` 依赖 `SpellHandler`（当前先完成稳定扩展点；完整法术攻击委托延后）

## D-R22: 共享工具模块提取（P1 结构性清理）

**日期**：2026-02-28
**新文件**：`rules/handler_utils.py`（~190 行）

**提取内容**：
- 类型强转：`coerce_int`（8 handler）、`coerce_float`（3）、`coerce_non_empty_string`（3）、`get_non_empty_string`（13）、`normalize_tags`（4）
- 骰子工具：`roll_d20`、`resolve_roll`、`build_dice_roll`（combat + skill_check 共用）
- 结果构建：`handler_success`（10 handler）、`handler_success_no_delta`（8）、`handler_failure`

**保留本地的变体**：
- `encounter.py._coerce_int`（bool→int 语义不同）
- `encounter.py._coerce_float`（同上）
- `world_state.py._success`（variadic 签名）
- `inventory.py._normalize_tags`（去重变体）

**`omit_empty_delta` 参数**：
- `True`（默认）：container, crime, rest, encounter
- `False`：economy, growth, inventory, navigation, spell, status_effect, combat

**测试影响**：combat/skill_check 的 `_patch_rolls` 改为 monkeypatch `handler_utils.roll_d20`

## D-R23: spell.py _compute_cast_spell 方法拆分

**日期**：2026-02-28
**文件**：`rules/handlers/spell.py`

**原方法**：379 行 → 拆分为 5 个方法：
- `_compute_cast_spell`（主入口，~80 行）
- `_apply_self_target`（自身目标分支）
- `_apply_combat_target`（战斗目标分支 → 委托 _apply_combat_damage / _apply_combat_control）
- `_build_cast_result`（状态变更 + metadata 组装）

**共享状态**：通过 `ctx: dict[str, Any]` 传递可变上下文，避免引入新 dataclass。

## D-R24: stand_up — CombatHandler 扩展

**日期**：2026-02-28

从 `player.active_effects` 移除所有 `effect_id == "prone"` 的效果。

- `time_cost=0`（设计文档：消耗半移动力，非时间成本）
- 无 prone 时返回 `status="not_prone"` + no delta
- 有 prone 时通过 `StateChange("player", "set", "active_effects", ...)` 整体替换
- 注册：`("stand_up", "stand_up")`

## D-R25: consume_resource — InventoryHandler 扩展

**日期**：2026-02-28

消耗职业资源（愤怒充能、神术引导等）。

- `time_cost=0`（设计文档：自由动作）
- params: `{resource_key: str, amount?: int}`，amount 默认 1
- 通过 `StateChange("player", "modify", f"class_resources.{key}", updated)` 更新
- 资源不存在 → `handler_failure(errors=["unknown resource"])`
- 余量不足 → `handler_failure(errors=["insufficient resource"])`
- 注册：`("use_resource", "consume_resource")`

## D-R26: investigate — SkillCheckHandler 扩展

**日期**：2026-02-28

主动搜索。掷感知/调查检定，发现隐藏内容。

- `time_cost=1/6`
- params: `{skill?: str, target_id?: str}`，skill 默认 "perception"
- 区域搜索目标从 `area.properties.search_targets` 读取
- 已发现目标记录在 `area.properties.discoveries`
- 无搜索目标 → `status="nothing_to_find"`
- 全部已发现 → `status="already_discovered"`
- 掷骰成功 → `status="discovered"` + StateChange 更新 discoveries
- 掷骰失败 → `status="found_nothing"`
- 注册：`("search_area", "investigate")`

## D-R27: interact_object — ContainerHandler 扩展

**日期**：2026-02-28

与可交互物件交互（公告板、机关、门等）。

- `time_cost=1/6`
- params: `{object_id: str, action?: str}`，action 默认 "examine"
- 物件数据从 `area.properties.interactables` 读取
- 物件不存在 → `ExecuteResult.error("object not found")`
- 存在 → 返回 `status="examined"` + 描述/类型信息
- 如需检定（`requires_check=True`）→ metadata 附带 `check_skill` / `check_dc`
- MVP 不执行实际检定，由玩家后续 `skill_check` 完成
- 注册：`("interact_object", "interact_object")`

## D-R28: tick_combat_effects — NPC 战斗效果 ticking

**日期**：2026-02-28

战斗参与者（NPC/怪物）的 `active_effects` 此前只能被 SpellHandler 施加，但永远不会被 tick。

- 新增 `tick_combat_effects` 命令到 StatusEffectHandler
- 提取 `_tick_effect_list()` 共享方法，player tick 和 combat tick 复用同一逻辑
- 遍历所有 `area.hostile_tracking`，只处理 `combat_active == True` 的战斗
- 对每个存活参与者：periodic damage/heal、duration 递减、到期移除
- HP ≤ 0 因 periodic damage → 标记 `alive = False`
- StatusEffectHook (P20) 扩展：player tick 之后追加 `tick_combat_effects`
- 有变化时发射 `combat_effects_ticked` SSE
- 10 新测试 in `tests/test_npc_effect_ticking.py`。基线 429→439

## D-R29: batch_execute delta 累积修复

**日期**：2026-03-02
**文件**：`rules/engine.py`

**问题**：`RulesEngine.batch_execute()` 的循环没有调用 `state.apply(result.delta)`，
后续命令看不到前一条命令的状态变更，与设计规范 §2.1 及施工记录 D-R02 的语义不符。

**注意**：`StateContainer.apply()` 是原地变更（返回 `None`），不需要重新赋值，
正确写法是 `state.apply(result.delta)`（设计规范中的 `state = state.apply(...)` 为伪代码）。

**修复**：
```python
if result.success and result.delta is not None:
    state.apply(result.delta)  # accumulate: each command sees prior changes
```

**影响**：仅 `batch_execute` 路径。AI Osiris P30 Hook 接入时依赖此语义。
**测试**：3 新测试 in `tests/test_rules_engine.py`（add_xp→level_up 链式场景）。

## D-R30: spell_resolver.coerce_int 对齐 handler_utils

**日期**：2026-03-02
**文件**：`rules/handlers/spell_resolver.py`、`rules/handlers/spell.py`

**问题**：
1. `spell_resolver.py` 自定义了 `coerce_int`，与 `handler_utils.coerce_int` 行为不同：
   - 原版 `spell_resolver.coerce_int(3.5)` → `3`（静默截断）
   - `handler_utils.coerce_int(3.5)` → `None`（拒绝非整数 float，对法术位等级更安全）
   - D-R22 未将此列为有意保留的本地变体
2. `spell.py` 另有 `_coerce_int` 静态方法，只是对 `coerce_int` 的透传包装，无价值。

**修复**：
- `spell_resolver.py`：删除本地 `coerce_int` 定义，改为 `from handler_utils import coerce_int`
- `spell.py`：删除 `_coerce_int` 静态方法，将 2 处 `self._coerce_int(...)` 改为直接调 `coerce_int(...)`

**测试影响**：行为仅对非整数 float 输入（如 `slot_level=3.5`）有差异，无现有测试覆盖此边界。
现有 spell handler 测试基线全部通过，无回归。

## D-R31: validate/compute 路径中 assert 替换为正确返回路径

**日期**：2026-03-02
**文件**：`rules/handlers/combat.py`、`rules/handlers/status_effect.py`、`rules/handlers/spell.py`

**问题**：9 处 `assert X is not None`（或 `assert bool(...)`）分布在 validate/compute/helper 方法中：
- Python `-O` 模式下 assert 被禁用，会导致 NPE 崩溃而非受控失败
- `validate()` 方法里的 assert 会 raise `AssertionError` 而不是返回 `ValidationResult(ok=False, ...)`，破坏 Handler 契约
- 逻辑上这些不变量确实成立（前序 validate 已经保证），但表达方式不符合防御性编程规范

**替换规则**：
- `validate()` 路径 → `return ValidationResult(ok=False, reason="...")`
- `compute()` 路径 → `return ExecuteResult.error("...")`
- helper 方法（`_build_effect_payload`）→ 直接删 assert（validate 已保证不变量，下游 state.apply 会在异常值时自然报错）

**修改清单**：

| 文件 | 位置 | 原 assert | 替换为 |
|------|------|-----------|--------|
| combat.py | `_validate_direct_resolution_command` | `assert resolved is not None` | `if resolved is None: return ValidationResult(ok=False, reason="active combat sub_area_id is required")` |
| combat.py | `_compute_flag_command` | `assert resolved is not None` | `if resolved is None: return ExecuteResult.error("active combat not found")` |
| combat.py | `_compute_flee` | `assert resolved is not None` | 同上 |
| combat.py | `_compute_use_combat_item` | `assert resolved is not None` | 同上 |
| combat.py | `_compute_direct_resolution_command` | `assert resolved is not None` | 同上 |
| combat.py | `_compute_direct_resolution_command` | `assert target_resolution is not None` | `if target_resolution is None: return ExecuteResult.error(f"unknown combat target: {target}")` |
| combat.py | `_compute_direct_resolution_command` | `assert bool(participant.get("alive", False))` | `if not bool(...): return ExecuteResult.error(f"target is not alive: {target}")` |
| status_effect.py | `_build_effect_payload` | `assert duration_ticks is not None` | 删除（validate 已确保） |
| spell.py | `_compute_cast_spell` | `assert template is not None` | `if template is None: return ExecuteResult.error("internal: spell template not found")` |

**测试影响**：无功能变化，837 passed，无回归。

## D-F-B: SpellHandler 消费端对齐 SkillEffect/SkillCost typed struct

**日期**：2026-03-02
**文件**：`rules/handlers/spell_resolver.py`、`rules/handlers/spell_effects.py`（类型签名），`orchestration/hooks/ai_osiris.py`（漏网消费端修复）

**背景**：❶ 内容层 `SkillTemplate.effect`/`.cost` 从 `dict[str, Any]` 迁移为 `SkillEffect`/`SkillCost` typed dataclass。

**修改**：
- `spell_resolver.py`：`resolve_effect_payload()` 直接返回 `template.effect`（不再转 dict）；`resolve_effect_type`/`resolve_action_type`/`resolve_resource_cost` 改用 `source_get()`（支持 Mapping + getattr 两路），不再硬写 `cost.get()`
- `spell_effects.py`：函数签名 `effect: dict[str, Any]`/`Mapping[str, Any]` 改为 `effect: Any`；函数体不变（`read_*` helpers 已通过 `source_get` 支持 dataclass）
- `ai_osiris.py`（❹ 层）：`skill.effect.get("type")` → `getattr(skill.effect, "type", None)`

**测试**：884 passed，无回归。

## 合规性排查结论（2026-03-02）

**结论：规则引擎层与设计规范 §3.1~§3.13 完全对齐。**

| 项目 | 数量 | 状态 |
|------|------|------|
| 设计规范 command_types | 56 | 全部实现 |
| 缺失命令 | 0 | 无缺漏 |
| D-R 扩展命令 | 5 | 全部有施工记录 |
| 未授权扩展 | 0 | 无 |

D-R 扩展（均有记录）：stand_up(D-R24)、consume_resource(D-R25)、investigate(D-R26)、interact_object(D-R27)、tick_combat_effects(D-R28)。
`create_character`（GrowthHandler）属于运行时初始化命令，不在玩家命令集，设计规范未单独列出但与系统流程兼容。

---

## [F-D] NavigationHandler 连接校验 + travel_slots

**日期**：2026-03-02

**背景**：`_validate_move_area()` 未检查连接存在性（玩家可跨越不相邻区域移动）；`_compute_move_area()` `time_cost` 硬编码 1.0。

**改动**（`navigation.py`）：

1. `_validate_move_area()` 新增连接存在性检查：
   - 仅在 `current_area` 非空时检查
   - `target_area not in world.maps.get_adjacent(current_area)` → `ValidationResult(ok=False, "no connection from ... to ...")`
   - `current_area` 为空时跳过检查（游戏初始化阶段向后兼容）

2. `_compute_move_area(cmd, state, world)` 新增 `world` 参数：
   - `conn = world.maps.get_connection(current_area, target_area)`
   - `time_cost = float(conn.travel_slots) if conn is not None else 1.0`

**测试**：`test_rules_engine.py` 新增 4 个测试（连接不存在拒绝 / 连接存在通过 / travel_slots time_cost / 无 current_area 跳过检查）；`test_navigation_handler.py` 补充 connections 使已有测试仍通过

---

## [F-E] GrowthHandler 升级初始化 class_resources

**日期**：2026-03-02

**背景**：`_compute_level_up()` 已处理特性字符串，但不初始化/更新 `class_resources`；`PlayerSlice.class_resources` 和 `RestHandler` 已就绪，唯缺初始化触发。

**改动**（`growth.py`）：

1. `_compute_level_up()` 追加 resource_changes，metadata 新增 `updated_resources`

2. 新增 `_resolve_resource_changes(class_template, target_level, state) -> tuple[list[StateChange], list[str]]`：
   - 读取 `class_template.class_resources_schema`
   - 对每个 resource：调用 `_resolve_resource_max_at_level()` 找对应等级的 max
   - 首次解锁：`current = max = new_max`
   - max 增加：`current += gain, max = new_max`
   - max 未变：跳过

3. 新增 `_resolve_resource_max_at_level(max_at_level, target_level) -> int | None`：
   - 找 `max_at_level` 中 `<= target_level` 的最大键对应值
   - 无合适键则返回 None（该等级尚未解锁）

**测试**：`test_rules_engine.py` 新增 3 个测试（首次解锁初始化 / max 增量追加 / 已到最高档无变更）


### [D-R32] F-A InventoryHandler equip 验证 + AC 重算

**日期**：2026-03-02

**背景**：`_validate_equip` 有 `del world`，完全不验证物品类型与装备槽的兼容性；`_compute_equip/_compute_unequip` 不重算 AC。

**改动**（`inventory.py`）：

1. **`_validate_equip`**：移除 `del world`，增加 type↔slot 约束检查（需 world.items registry）
   - 新增 `_allowed_slots(template)` 静态方法：armor→{"chest"}，shield→{"off_hand"}，weapon→{weapon_data.slot}，其余→None（宽松）

2. **`compute()` 分发**：`equip`/`unequip` 现在将 world 参数传入对应 `_compute_*`

3. **`_compute_equip`/`_compute_unequip`**：签名添加 world 参数，装备/卸甲后调用 `_compute_ac()` 重算；AC 变化时追加 `StateChange("player", "set", "ac", new_ac)`

4. **新增 `_compute_ac(equipment, state, world)`** 静态方法：
   - chest slot → ArmorData（light/medium/heavy AC 公式）
   - off_hand slot → 检测 shield（ArmorData.armor_type=="shield"），叠加 base_ac
   - 无 items registry → 返回 None（不更新 AC）

**AC 公式**：
- unarmored：`10 + DEX_mod`
- light：`10 + base_ac + DEX_mod`
- medium：`10 + base_ac + min(DEX_mod, 2)`
- heavy：`10 + base_ac`
- shield：在上述结果基础上 `+= shield.base_ac`

**测试**：`test_inventory_handler.py` 新增 6 个测试（TestInventoryHandlerEquipFA 类）


### [D-R32] F-C：CombatHandler 怪物反击 + CombatAI + XP 分发（2026-03-02）

**目标**：关闭战斗生命周期循环——玩家 attack 后怪物自动决策并反击，击杀后分发 XP。

**handler_utils.py**：新增 `roll_damage_dice(dice_str) -> int`（解析 NdM/NdM+B/NdM-B）

**combat.py 改动**：

1. **方法签名**：`_compute_direct_resolution_command(cmd, state)` → 加 `world`；`_compute_attack_resolution(...)` → 加 `world`

2. **四个新私有方法**：
   - `_decide_monster_action(ai_personality, hp_ratio, flee_threshold) -> str`
   - `_roll_monster_attack(name, damage_dice, hit_bonus, player_ac) -> tuple[bool, int, DiceRoll]`
   - `_resolve_monster_responses(participants, state, world) -> tuple[updated_parts, changes, rolls, metadata]`
   - `_compute_combat_xp(participants, world) -> int`

3. **整合在 `_compute_attack_resolution` 末尾**：
   - 若 `combat_active and not combat_cleared`：调用 `_resolve_monster_responses`
   - 若 `combat_cleared`：调用 `_compute_combat_xp` + 追加 `StateChange("player", "set", "xp", ...)`
   - metadata 新增 `monster_responses` / `xp_awarded`

**设计决策**：
- 无 `attacks` 定义的怪物 → action="hold"，不发起攻击（不消耗骰子）
- `flee_threshold=0.0`（默认）→ 怪物从不主动逃跑
- `aggressive` 仅在 `hp < 10%` 时才逃跑；`defensive/cowardly` 在 `hp < threshold` 时逃跑
- 玩家 AC 暂用无装甲公式：`10 + DEX_mod`（装备系统深化后消费 equipped_ac）

**测试**：`tests/test_rules_engine.py` 新增 6 个战斗测试；`tests/test_content_registries.py` 新增 3 个（F-C content）


### [D-R33] 设计变形修复：base_ac 语义 + get_equippable + ConsumableData.effect（2026-03-02）

**背景**：F-A 实现后发现三处代码与设计文档存在语义偏差。

**Fix 1：`ArmorData.base_ac` 语义对齐（`items.py` + `inventory.py`）**

- **问题**：设计规范 §6.3 公式 `轻甲: base_ac + DEX_mod`（无 `10 +` 前缀），含义是 `base_ac` 为绝对值（如皮甲=12）；原实现存增量（`ac_bonus=2`），公式 `10 + base_ac + DEX_mod`，当前数值相同但语义不对齐。
- **`_build_armor_data`**：非盾牌护甲改存绝对值 `base_ac = 10 + ac_bonus`；盾牌保持加成值 `base_ac = ac_bonus`（设计规范盾牌是"当前 AC + 加成"）。
- **`_compute_ac`**：去掉 `10 +` 前缀（`base_ac + DEX_mod`），与设计规范公式完全对齐。
- **数值不变**：所有现有 AC 测试零改（旧 `10+2+1=13`，新 `12+1=13`）。
- **测试**：`test_item_builds_armor_data_from_type_and_subtype` 更新 2 处 `base_ac` 断言（`2→12`，`4→14`）。

**Fix 2：`get_equippable()` 扩展条件（`items.py`）**

- **问题**：仅依赖 `item.slot` 判断可装备性，而 equip 验证用 sub-struct；有 `weapon_data` 但无 `slot` 的武器会被遗漏。
- **改动**：条件改为 `item.slot or item.weapon_data is not None or item.armor_data is not None`。
- **测试零改**：现有测试数据的 equippable 物品都有 `item.slot`，结果集不变。

**Fix 3：`resolve_item_heal_amount` 优先读 `ConsumableData.effect`（`handler_utils.py`）**

- **问题**：`consumable_data.effect` 字段填充后无消费端，`resolve_item_heal_amount()` 仍读 `item.heal_amount`。
- **改动**：优先走 `consumable_data.effect["params"]["amount"]` 路径，fallback 到 `heal_amount`。
- **测试零改**：heal 值相同，HP delta 断言全部不变。

---

### [D-S-AOE] SpellHandler 法术 DC + 豁免检定 + 多目标（2026-03-02）

**背景**：F-B 已添加 `SkillEffect.save`/`save_dc_stat`/`half_on_save` 字段；F-C 已添加 `MonsterTemplate.abilities`。现在实现消费端。

**改动文件**：`spell_resolver.py` / `spell_effects.py` / `spell.py`

#### 法术 DC（`spell_resolver.py`）

新增 `resolve_spell_dc(state, world) -> int`：
- `DC = 8 + state.player.proficiency_bonus + state.player.get_modifier(resolve_spellcasting_ability(state, world))`
- 在 `_compute_cast_spell` 初始化 ctx 时计算并存入 `ctx["spell_dc"]`

#### 豁免检定（`spell_effects.py apply_combat_damage`）

- 签名加 `world: WorldInstance`（`apply_combat_target` 同步透传）
- 在 damage 算完后，检查 `effect.save`：
  - 从 `world.monsters.get(target_monster_id).abilities` 取存档属性分
  - `save_mod = (ability_score - 10) // 2`
  - `roll_dice("1d20") + save_mod >= spell_dc` → 通过
  - `half_on_save=True` → 半伤；`half_on_save=False` → 0 伤
  - ctx 追加 `save_rolls` 记录（target_monster_id, save_ability, roll, mod, total, dc, succeeded）
- 结果元数据：`save_dc` + `save_rolls` 仅在有存档时追加

#### 多目标/AOE（`spell.py` + `spell_resolver.py`）

- `resolve_cast_targets()` 现在接受多元素 list → `{"mode": "combat", "targets": [...]}`
- `_compute_cast_spell`：`len == 1` 走原有路径；`len > 1` 循环调用 `apply_combat_target`
  - `ctx["damage_total"]` 改为累加（多目标求和）
  - `apply_combat_damage` 用 `ctx["pending_hostile_payloads"].get(sub_area_id, hostile_payload)` 链式读取（与 `apply_combat_control` 对齐）
  - 所有目标都失败时才 early exit，否则已命中目标的变更保留

**测试基线**：898 passed（+14，含 F-C 的 10 个新增）；spell handler 24 通过（新增 5 个 DC/豁免/多目标测试）

---

### [D-R34] BattleGrid — SRPG 战旗网格基础设施（2026-03-10）

**设计文档**：`app/增量更新/战斗系统战旗化重构.md` §2.3, §3.1
**新文件**：`app/game_core/rules/battle_grid.py`（~240 行）
**测试文件**：`tests/test_battle_grid.py`（32 个测试，全部通过）

**架构定位**：❷ 规则引擎层工具模块（纯计算，无状态，不 import 应用层），与 `handler_utils.py`、`models.py` 同级。

**主要组件**：

| 组件 | 说明 |
|------|------|
| `TerrainType` | `@dataclass(slots=True, frozen=True)`，8 字段（code/name/move_cost/ac_bonus/range_bonus/blocks_los/speed_penalty） |
| `TERRAIN_REGISTRY` | 8 种地形：G(草)/F(林)/H(丘)/S(沼)/W(水)/R(石)/B(墙)/M(山) |
| `BattleGrid` | `@dataclass(slots=True)`，宽/高/terrain；terrain存储为 `[row][col]`（row-major） |

**关键设计决策**：

- **API 参数顺序 vs 内部存储**：公开 API 均为 `(col, row)` 与 unit position `[col, row]` 一致；内部索引必须 `terrain[row][col]`（row-major），不混淆
- **未知地形码 fallback**：`at()` 对未知 code 返回 grass（宽松模式，不 raise）；越界同样 fallback 而非 crash
- **地形不可通行判断**：`move_cost == 0` → 不可通行（W/B/M 三种）；`is_passable()` 越界返回 False
- **`side` 参数语义**：告诉算法"谁在移动"——只有对立方 alive 单位才阻挡通行（己方不阻挡，死亡单位不阻挡）
- **寻路**：`reachable_cells()` 用 Dijkstra（带地形消耗的加权 BFS）；`shortest_path()` 用 A*（曼哈顿启发，含终点 alive 敌方不阻挡）
- **LoS**：Bresenham 算法，首尾格不检查（只检查中间格的 `blocks_los`）
- **`from_map_data()`**：JSON 格式 `{width, height, terrain: [行字符串…]}`，尺寸不符时 `raise ValueError`

**测试覆盖分布**（32 个）：
- 地形注册表：3 个（8 种 code 全在 / frozen / 不可通行零消耗）
- 基础访问：6 个（边界内外 / 查表 / 未知 fallback / 通行性 / 占用检查）
- 距离与范围：3 个（曼哈顿 / cells_in_range / 近边界裁剪）
- BFS 可达性：6 个（开放草地 / 地形消耗 / 不可通行阻挡 / 敌方阻挡 / 友方不阻挡 / 死敌不阻挡）
- A* 最短路径：5 个（直线 / 绕墙 / 无路 / 偏好低消耗 / 敌方绕路）
- LoS：4 个（无阻挡 / 墙阻挡 / 山阻挡 / 同格）
- 构建：4 个（正常解析 / 行数不符 / 列数不符 / 地形码往返正确）

**不改动任何现有文件**：Phase 1 纯增量，下一步（Phase 2）将在 `CombatHandler` 接入网格坐标。

---

### [D-R35] combat_units.py — 单位构建工厂 + start_combat v2 payload

**日期**：2026-03-10
**设计文档**：`app/增量更新/战斗系统战旗化重构.md` §2.2, §2.5, §2.6, §3.5.3, §3.5.4

#### 新建文件

**`app/game_core/rules/combat_units.py`**（~270 行）

❷ 规则引擎层纯工具模块，只 import stdlib + game_core 内部类型。

| 函数 | 说明 |
|------|------|
| `build_player_unit(state)` | player 单位，main_hand 装备→武器攻击，无装备→徒手攻击(1d1)，speed=3 |
| `build_companion_unit(char_id, member_data, template)` | combat_capable=False→None；HP 优先 member_data→template.base_hp→10 |
| `build_monster_unit(monster_id, template, index)` | unit_id=f"{mid}_{index}"；speed=max(2, feet//10)；空 attacks→默认 Slam |
| `resolve_surprise(units, state, stealth_total)` | "player_surprise"→enemy d20+WIS_mod vs stealth_total；"enemy_surprise"→all ally；"none"→全 False；原地赋值返回 units |
| `roll_initiative(unit)` | (d20+DEX_mod, DEX) 用于排序 + 平局 |
| `build_turn_order(units)` | 过滤 alive+not fled，按 (initiative, dex) 降序；返回 (turn_order list, initiative_rolls dict) |
| `assign_positions(units, w, h)` | ally 左侧 col 0-1，enemy 右侧 col w-2~w-1，原地赋值 |
| `build_default_grid(width=8, height=6)` | 全草地默认网格 dict |

Unit dict 包含 24 个字段（unit_id/side/source/monster_id/character_id/name/hp/max_hp/ac/stats/speed/position/alive/fled/active_effects/attacks/ai_personality/flee_threshold/flee_chance/action_used/move_used/disengaged/dashed/defending/reaction_used/surprised/proficiency_bonus）。

#### 修改文件

**`app/game_core/rules/handlers/combat.py`**

- 新增 import：`combat_units` 7 个函数
- 重写 `_compute_start_combat` 后半段（L302 以后）：
  1. 构建 units 列表（player + companions + monsters）
  2. 未知怪物模板→内联 fallback unit
  3. 默认 8×6 草地网格
  4. 分配初始位置
  5. 突袭检定（`last_stealth_result.roll+modifier` 或 `cmd.params.stealth_total`）
  6. 先攻检定 + 排序
  7. `combat_round = 0 if any(surprised) else 1`（新语义；旧逻辑：`0 if surprise_state != "none"`）
  8. 向后兼容 `participants` 从 enemy units 导出（v1 格式）
  9. combat_seed 注入 version/grid/units/turn_order/initiative_rolls/current_turn_index/current_unit_id/environment
- `_build_participants` 加 `# Legacy (v1) — remove after Phase 4` 注释

**`tests/test_combat_handler.py`**

- `test_start_combat_creates_minimal_combat_payload`：加 `stealth_total: 100` 使敌方必定 surprised（新语义下 combat_round 取决于是否有 surprised 单位，非纯粹 surprise_state）；补加 v2 字段断言（version/units/grid/turn_order）

#### 关键设计决策

| # | 决策 | 原因 |
|---|------|------|
| 1 | `combat_round` 由 `any(surprised)` 决定而非 `surprise_state != "none"` | 若 enemy_surprise 但所有 ally 通过感知→无人 surprised→应为 round=1；D&D 规则语义更准确 |
| 2 | 仍输出 `participants`（v1 向后兼容） | attack/defend 等命令依赖此字段，Phase 3-4 迁移后删除 |
| 3 | `area.py` 不改 | `_normalize_hostile_payload` 的 dict-spread 自动保留 version/grid/units 等新字段 |
| 4 | `stealth_total` 优先从 `cmd.params` 读（覆盖 existing_payload 推导值） | 主调方可显式指定，测试可控 |

**测试覆盖**（24 新测试 in `tests/test_combat_units.py`）：
- PlayerUnit：3 个（基础字段 / 徒手 / 武器）
- CompanionUnit：4 个（basic / non-combat-none / hp-fallback / attacks）
- MonsterUnit：4 个（basic / speed-conversion / default-attack / with-attacks）
- Surprise：4 个（player_surprise-all-fail / player_surprise-none-fail / enemy_surprise / none）
- TurnOrder：3 个（降序 / DEX 平局 / 排除死亡fled）
- Positions：3 个（sides / all-have / single）
- DefaultGrid：3 个（dimensions / all-grass / custom-size）

测试基线：1983 passed（+71 vs 前次基线 1912）。

---

### [D-R36] v2 SRPG 回合命令：combat_move / combat_end_turn / combat_disengage / combat_dash

**日期**：2026-03-10
**设计文档**：`app/增量更新/战斗系统战旗化重构.md` §2.7, §2.8, §四 Phase 3

#### 修改文件

**`app/game_core/rules/handlers/combat.py`**（~275 行新增）

- 新增 import：`from app.game_core.rules.battle_grid import BattleGrid`
- `COMMAND_TYPES` 追加 4 个 v2 命令：`combat_move`、`combat_end_turn`、`combat_disengage`、`combat_dash`
- `validate()` 末尾增加 v2 路由（`{"combat_move", "combat_end_turn", "combat_disengage", "combat_dash"} → _validate_v2_command`）
- `compute()` 增加 4 个 v2 命令路由
- 新增辅助方法：
  - `_find_unit(units, unit_id)` — 按 unit_id 查找
  - `_is_unit_active(unit)` — alive + not fled
  - `_resolve_v2_combat(params, state, world=None)` — 复用 `_resolve_active_combat` 并过滤 `version==2`
  - `_validate_v2_command(cmd, state, world)` — 通用验证：v2 payload 存在 + 当前回合检查
- 新增命令方法：
  - `_compute_combat_move` — 可达性（BattleGrid.reachable_cells）+ 机会攻击（随机 d20 + 伤害）+ position 更新
  - `_compute_combat_end_turn` — 推进 index、wrap 时 round++、跳过死亡/突袭单位、重置下一单位回合状态
  - `_compute_combat_disengage` — 设置 disengaged=True + action_used=True
  - `_compute_combat_dash` — 设置 dashed=True + action_used=True

**`app/game_core/orchestration/defaults.py`**

- `DEFAULT_ACTION_COMMAND_TYPES` 追加 4 行：`combat_move / combat_end_turn / combat_disengage / combat_dash`

#### 关键设计决策

| # | 决策 | 原因 |
|---|------|------|
| 1 | 新命令名 `combat_move/end_turn/disengage/dash` | 与 v1 命令并存，不破坏旧逻辑 |
| 2 | `combat_move` 中 opportunity attack 用 `random.randint` + `roll_damage_dice` | 复用 combat.py 已有模式 |
| 3 | `reaction_used` 在 `round_advanced` 时重置所有单位 | D&D 5e：反应每轮恢复一次 |
| 4 | `defending` 在 `combat_end_turn` 重置时清除 | 设计说 AC+2 "到下回合"，轮次结束时清除 |
| 5 | BattleGrid 从 `payload["grid"]` 通过 `from_map_data` 重建 | grid 字典格式兼容（width/height/terrain） |
| 6 | 被击杀的移动者不更新 position | 符合 D&D：被机会攻击击倒后停在原地 |
| 7 | v2 命令要求 `payload["version"] == 2` | 明确区分新旧代码路径 |

#### 新建测试文件

**`tests/test_combat_v2_turn.py`**（14 个测试，全部通过）

| 组 | 测试 | 验证点 |
|----|------|--------|
| move | test_combat_move_updates_position | 移动到可达格→position 更新 + move_used=True |
| move | test_combat_move_unreachable_fails | 目标不可达→error |
| move | test_combat_move_already_moved_fails | move_used=True→validation 失败 |
| move | test_combat_move_dash_doubles_speed | dashed=True→effective speed 翻倍 |
| opp | test_opportunity_attack_triggers | 离开敌方近战范围→敌方免费攻击 |
| opp | test_opportunity_attack_disengage_prevents | disengaged=True→不触发机会攻击 |
| opp | test_opportunity_attack_reaction_used_limit | reaction_used→不触发 |
| turn | test_combat_end_turn_advances_to_next_unit | current_turn_index 推进 |
| turn | test_combat_end_turn_wraps_round | 越界→round++ + index=0 |
| turn | test_combat_end_turn_skips_dead_unit | 跳过 alive=False 的单位 |
| turn | test_combat_end_turn_skips_surprised_in_round_0 | round 0 跳过 surprised 单位 |
| turn | test_combat_end_turn_clears_surprised_entering_round_1 | 进入 round 1 清除所有 surprised |
| disengage | test_combat_disengage_sets_flags | disengaged=True + action_used=True |
| dash | test_combat_dash_sets_flags | dashed=True + action_used=True |

**测试基线**：1997 passed（+14 vs D-R35 的 1983）。

### [D-R37] v2 SRPG 攻击命令：combat_attack / combat_defend（2026-03-10）

**设计文档**：`app/增量更新/战斗系统战旗化重构.md` §2.7, §四 Phase 4

#### 改动文件

| 文件 | 内容 |
|------|------|
| `combat.py` | `COMMAND_TYPES` 追加 `combat_attack`/`combat_defend`；`validate()` 路由扩展；`compute()` 路由新增；`_validate_v2_command` 扩展（action_used 检查 + target 参数校验）；新增 `_compute_combat_attack`（~110 行）、`_compute_combat_defend`（~15 行） |
| `defaults.py` | `DEFAULT_ACTION_COMMAND_TYPES` 追加 `combat_attack`/`combat_defend` |
| `tests/test_combat_v2_attack.py` | 新建 — 14 个测试 |

#### combat_attack 核心流程

1. 目标解析 + 同侧检查
2. 攻击选择（attack_index 参数）
3. BattleGrid 距离检查（weapon_range + terrain range_bonus）
4. LoS 检查（仅远程 range > 1）
5. 目标 AC = base + terrain_ac_bonus（目标格）+ defending_bonus + active_effects
6. 攻击骰 d20 + hit_bonus；critical=d20==20；auto_miss=d20==1
7. 命中时：伤害骰（暴击双骰）+ 怪物/玩家伤害减免
8. action_used = True
9. 战斗结束检测（所有 enemy 死/逃 → combat_cleared=True + XP + kill_count）
10. 玩家 HP 同步（unit_id="player" 被攻击时同步 PlayerSlice.hp）

#### 关键设计决策

| # | 决策 | 原因 |
|---|------|------|
| 1 | 新命令名 combat_attack/combat_defend | 与 v1 attack/defend 并存，不修改 v1 |
| 2 | 距离用曼哈顿距离 | BattleGrid.distance() 一致 |
| 3 | 地形 AC 加成取目标位置 | forest +2, stone +1 |
| 4 | 地形 range_bonus 取攻击者位置 | hill +1 |
| 5 | 暴击双骰 | D&D 5e 简化版 |
| 6 | combat.random / handler_utils.random 同一模块对象 | 测试中 mock.patch.object(random, "randint", seq_fn) 统一拦截 |

#### 测试覆盖

| 组 | 测试 | 验证点 |
|----|------|--------|
| attack | test_combat_attack_melee_hit | 命中→伤害+action_used |
| attack | test_combat_attack_melee_miss | 未命中→伤害=0 |
| attack | test_combat_attack_melee_out_of_range | 距离>range→error |
| attack | test_combat_attack_ranged_hit | 远程命中成功 |
| attack | test_combat_attack_ranged_blocked_los | LoS 被墙阻挡→error |
| attack | test_combat_attack_terrain_ac_bonus | 目标在森林→AC+2 |
| attack | test_combat_attack_range_bonus_from_hill | 攻击者在丘陵→range+1 |
| attack | test_combat_attack_defending_target_ac_bonus | 目标 defending→AC+2 |
| attack | test_combat_attack_action_already_used_fails | action_used=True→validation 失败 |
| attack | test_combat_attack_friendly_fire_fails | 攻击同侧→error |
| attack | test_combat_attack_kills_last_enemy_clears_combat | 击杀最后敌人→cleared |
| attack | test_combat_attack_player_hp_sync | 攻击玩家→PlayerSlice HP 同步 |
| attack | test_combat_attack_critical_double_damage_dice | d20=20→暴击双骰 |
| defend | test_combat_defend_sets_flags | defending=True+action_used=True |

**测试基线**：2024 passed（+14 vs D-R36 的 1997；不计 8 skipped 和 5 pre-existing failures）。

---

### [D-R38] combat_npc_turn：怪物 AI 自主回合命令（Phase 5）

**日期**：2026-03-10

#### 背景

Phase 5 实现怪物 AI 决策模块（`battle_ai.py`，由 Agent 1 完成）与 `combat_npc_turn` engine-only 命令，使怪物在自己的回合自主执行移动+行动。

#### 改动范围

| 文件 | 改动 |
|------|------|
| `app/game_core/rules/handlers/combat.py` | 新增 `combat_npc_turn` 到 COMMAND_TYPES + validate/compute 路由 + `_validate_combat_npc_turn` + `_compute_combat_npc_turn` + import `decide_monster_turn` |
| `app/game_core/orchestration/defaults.py` | 追加 `("combat_npc_turn", "combat_npc_turn")` 到 DEFAULT_ACTION_COMMAND_TYPES |
| `tests/test_combat_v2_npc.py` | 新建，6 个集成测试 |

#### 关键设计决策

| # | 决策 | 原因 |
|---|------|------|
| 1 | `combat_npc_turn` 是 engine-only（source=engine/system） | 与 start_combat/advance_combat_round 一致 |
| 2 | 内联移动+攻击逻辑，不反向调用 combat_move/combat_attack | 一次原子操作完成 NPC 回合，避免跨命令路由 |
| 3 | 不自动 end_turn | 调用方（编排层）负责后续 combat_end_turn |
| 4 | 机会攻击逻辑与 combat_move 一致 | 复用相同 grid.distance 检测 |
| 5 | 攻击解析复用 _participant_effect_ac_mod + _apply_player_damage_resistance | 与 combat_attack 一致的 AC 和伤害计算 |
| 6 | flee 时 alive=False + fled=True | 与旧 flee 命令语义一致，战斗结束检测可识别 |

#### 测试覆盖

| 测试 | 验证点 |
|------|--------|
| test_npc_turn_attacks_adjacent_target | 近战怪相邻目标→攻击+action_used |
| test_npc_turn_moves_then_attacks | 远距离目标→移动接近+攻击 |
| test_npc_turn_flees_when_low_hp | 低 HP 胆小怪→fled=True |
| test_npc_turn_player_source_rejected | source="player"→validation 失败 |
| test_npc_turn_kills_last_enemy_clears | 击杀最后敌人→combat_cleared |
| test_npc_turn_player_hp_sync | 攻击玩家→PlayerSlice HP 同步 |

**测试基线**：2059 passed（+6 vs D-R37 的 2053；不计 8 skipped 和 5 pre-existing failures）。

---

### [D-R39] Phase 6：队友参战 LLM AI — companion_combat_ai.py + 决策覆盖（2026-03-10）

**设计文档**：`app/增量更新/战斗系统战旗化重构.md` §四 Phase 6
**计划文件**：`/home/xiaokebuyu/.claude/plans/cozy-dazzling-locket.md`

#### 架构决策

| # | 决策 | 原因 |
|---|------|------|
| 1 | LLM 决策在应用层预计算，通过 `cmd.params["decision"]` 传入 combat_npc_turn | rules 层保持同步纯函数，不引入 async / 应用层依赖 |
| 2 | `validate_decision()` 放 battle_ai.py（❷ 规则层） | 纯函数验证，复用 BattleGrid.reachable_cells |
| 3 | LLM 模块新建 `app/companion_combat_ai.py` | agent_orchestration.py 已 2216 行，不写 god file |
| 4 | 无 LLM 时（NullLlmProvider）自动降级规则 AI | 与项目现有降级模式一致 |

#### 新建文件

**`app/companion_combat_ai.py`**（~175 行，应用层）

| 函数 | 说明 |
|------|------|
| `build_battlefield_summary(grid_data, units, current_unit_id)` | 生成战场文字摘要（地形网格 + 单位列表 + 攻击清单 + 速度）|
| `_build_system_prompt(unit, summary, character_name, personality_hint, approval)` | 构建 LLM system prompt（含角色性格、好感度定性描述、严格 JSON 格式要求）|
| `_parse_decision_response(text)` | 解析 LLM 响应：直接 JSON parse → 正则提取 `{...}` → 验证 action 字段 → None |
| `decide_companion_combat_turn(unit, grid_data, all_units, character_name, personality_hint, approval, llm_provider)` | 主入口：LLM 调用 + 解析，任何异常 → return None → 调用方使用规则 AI |

#### 修改文件（Agent 1 负责，D-R39 联合记录）

**`app/game_core/rules/battle_ai.py`**

- 新增 `validate_decision(decision, unit, grid, all_units) -> bool`
- 验证：move_to 可达性 / action 合法枚举 / 攻击目标存活+对立阵营 / 射程+LoS

**`app/game_core/rules/handlers/combat.py`**（`_compute_combat_npc_turn`）

- 新增决策覆盖逻辑：读取 `cmd.params.get("decision")` → MonsterDecision → validate_decision → 覆盖或 fallback
- metadata 新增 `decision_source`：`"override"` | `"rules_ai"`

#### 测试文件

**`tests/test_companion_combat_ai.py`**（新建，7 个测试）

| 测试 | 验证点 |
|------|--------|
| test_valid_llm_response_returns_decision | LLM 返回合法 JSON → 返回 dict（action/target_id/attack_index/move_to 全部对齐）|
| test_invalid_json_returns_none | LLM 返回非 JSON 文本 → 返回 None |
| test_llm_exception_returns_none | LLM 抛异常 → 返回 None（优雅降级）|
| test_battlefield_summary_format | 摘要含网格尺寸/地形行/两侧单位/← 你 标记/攻击信息/速度 |
| test_parse_decision_response_extracts_embedded_json | 嵌在散文中的 JSON 块可被正则提取 |
| test_parse_decision_response_returns_none_for_missing_action | 缺少 action 字段 → None |
| test_parse_decision_response_returns_none_for_empty_string | 空字符串 → None |

---

### [D-R39] Phase 6a — validate_decision + combat_npc_turn 决策覆盖

**日期**：2026-03-10

**改动范围**：
- `app/game_core/rules/battle_ai.py`：新增 `validate_decision()` 公共函数（约 60 行）
- `app/game_core/rules/handlers/combat.py`：import 补充 `MonsterDecision, validate_decision`；`_compute_combat_npc_turn()` 插入决策覆盖逻辑；metadata 新增 `decision_source` 字段

**设计决策**：
- `validate_decision()` 放规则层（`battle_ai.py`），纯函数无副作用，复用 `BattleGrid.reachable_cells / distance / line_of_sight`
- 决策覆盖通过 `cmd.params["decision"]` 传入，应用层预计算，规则层只做验证。保持 rules 层同步纯函数，不引入 async / 应用层依赖
- 有效覆盖 → `decision_source="override"`；fallback（无效/异常/未传）→ `decision_source="rules_ai"`
- `effective_speed = speed * 2` 当 `unit.dashed=True`（与 dash 行动语义一致）

**validate_decision 验证逻辑**：
1. action 必须是 `{"attack", "flee", "defend", "hold"}` 之一
2. `move_to` 非 None 时：目标格必须在 `reachable_cells()` 返回的可达集合内
3. `action="attack"` 时：target 存活且属于对立阵营、attack_index 合法、攻击后位置到目标距离 ≤ range + terrain_bonus、远程需 LoS

**新增测试**：
- `tests/test_battle_ai.py`：+3 个（test_validate_decision_valid, test_validate_decision_invalid_move, test_validate_decision_invalid_target）
- `tests/test_combat_v2_npc.py`：+2 个（test_npc_turn_with_valid_decision_override, test_npc_turn_with_invalid_decision_fallback）

**测试基线**：2066 passed（+7 vs D-R38 的 2059）。

---

### [D-R40] Phase 7（Agent 2）：环境修正 + start_combat 地图选择（2026-03-10）

**设计文档**：`app/增量更新/战斗系统战旗化重构.md` §四 Phase 7
**计划文件**：`/home/xiaokebuyu/.claude/plans/cozy-dazzling-locket.md`

#### 改动范围

| 文件 | 内容 |
|------|------|
| `app/game_core/rules/battle_grid.py` | 新增 `EnvironmentModifiers` dataclass（frozen）+ `compute_environment_modifiers()` 函数 |
| `app/game_core/rules/handlers/combat.py` | import 新增 `compute_environment_modifiers`, `assign_positions_from_spawns`；`start_combat` 地图选择逻辑；`_compute_combat_attack` + `_compute_combat_npc_turn` 攻击环境修正 |
| `tests/test_combat_v2_env.py` | 新建，11 个测试 |

#### EnvironmentModifiers（battle_grid.py）

`compute_environment_modifiers(weather, time_of_day)` 映射：

| 条件 | 效果 |
|------|------|
| weather="rain" | hit_modifier=-1 |
| weather="fog" | hit_modifier=-2, max_visibility=3 |
| time_of_day="night" | ranged_hit_modifier=-2 |
| 其他 | 无修正 |

多条件叠加（night+rain → hit=-1, ranged_hit=-2）。

#### start_combat 地图选择（8a）

原来固定 `build_default_grid() + assign_positions()`，现改为：
1. 读取 `cmd.params["map_category"]` 或 `cmd.params["map_tags"]`
2. 若 world 存在且有 `battle_maps` registry → 按 category 或 tags 选 variant
3. 选中时用 variant 尺寸/地形构建 grid，并调用 `assign_positions_from_spawns()`
4. 未选中时 fallback 到 `build_default_grid() + assign_positions()`（保留后向兼容）

#### 攻击环境修正（8b）

在 `_compute_combat_attack` 和 `_compute_combat_npc_turn` 两处的 `atk_total = atk_roll + hit_bonus` 之后追加：
- `atk_total += env_mods.hit_modifier`
- 若 `weapon_range > 1`：`atk_total += env_mods.ranged_hit_modifier`
- 浓雾 LoS 限制：`fog_blocked = env_mods.max_visibility is not None and distance > env_mods.max_visibility`
- `hit = (atk_total >= total_ac or critical) and not auto_miss and not fog_blocked`

#### 关键设计决策

| # | 决策 | 原因 |
|---|------|------|
| 1 | `EnvironmentModifiers` 放 `battle_grid.py` | 与地形系统同层，纯计算无副作用，无循环依赖 |
| 2 | 浓雾 LoS 用 `fog_blocked` gate 而非 `has_los = False` | `_compute_combat_attack` 无 `has_los` 变量；两处结构不同，用 `fog_blocked` 统一处理 |
| 3 | start_combat fallback 保留旧路径 | 无 world / 无 battle_maps registry 时不报错，向后兼容 |
| 4 | fog_blocked 不改 critical 标记 | 浓雾中暴击骰但因 LoS 受阻而未命中，骰子记录仍反映真实 d20 值 |

#### 测试覆盖（11 个测试，test_combat_v2_env.py）

| 测试 | 验证点 |
|------|--------|
| test_environment_modifiers_defaults | clear/day → 零修正，无 visibility cap |
| test_environment_modifiers_rain | rain → hit_modifier=-1 |
| test_environment_modifiers_fog_visibility | fog → hit_modifier=-2, max_visibility=3 |
| test_environment_modifiers_night_ranged | night → ranged_hit_modifier=-2 |
| test_environment_modifiers_night_and_rain_stack | night+rain 叠加 |
| test_environment_modifiers_night_and_fog_stack | night+fog 叠加 |
| test_environment_modifiers_is_frozen | frozen dataclass 不可变 |
| test_combat_attack_with_rain_penalty | rain 使 borderline 攻击未命中 |
| test_combat_attack_without_rain_penalty_hits | 对照组：clear 天气相同骰命中 |
| test_combat_attack_ranged_night_penalty | 夜间远程攻击 -2 惩罚 |
| test_combat_attack_fog_blocks_distant_ranged | 浓雾超出 max_visibility → nat20 也未命中 |

**测试基线**：2111 passed（+11 vs D-R39 的 2100；5 个 pre-existing failures 不变）。

---

### [D-R40] Phase 7a — BattleMapRegistry + assign_positions_from_spawns

**日期**：2026-03-10

**改动范围**：
- `app/game_core/content/registries/battle_maps.py`（新建，~190 行）：`BattleMapVariant` + `BattleMapTemplate` frozen dataclasses + `BattleMapRegistry(ContentRegistry)` 含 `load/get/list_all/select_variant/select_by_tags` + load 验证
- `data/goblin_slayer/v2/battle_maps.json`（新建，~120 行）：cave/ruins/plains/woodland/hills 5 类各 2 变体，标准 8×6 尺寸，spawn 3+3 点
- `app/game_core/content/registries/__init__.py`：新增 `BattleMapRegistry/Template/Variant` 导出
- `app/game_core/content/world.py`：新增 `battle_maps` TYPE_CHECKING import + `battle_maps` property
- `app/game_data_loader_v2.py`：新增 `"battle_maps"` key 加载
- `app/game_core/rules/combat_units.py`：新增 `assign_positions_from_spawns()` 函数（~20 行），就地分配 spawn 点位，超出则回绕

**设计决策**：
- `BattleMapVariant` 字段用 `tuple[str, ...]` / `tuple[tuple[int,int], ...]` 而非 list（frozen dataclass 需不可变容器）
- `BattleMapRegistry` 继承 `ContentRegistry`，JSON 顶层 key = category，value = `{"variants": [...]}`，与现有 10 个 registry 的单文件模式一致
- `assign_positions_from_spawns()` 保留 `assign_positions()` 不变，新函数处理模板 spawn 点；超出回绕用 `idx % len(spawns)` 实现
- `world.battle_maps` property 仅在 `battle_maps` registry 已注册时有效（`has_registry()` 调用前置守卫在 combat.py 中，此处不重复）
- `WorldInstance.load_all()` 已有兜底循环加载未分组 registry，battle_maps 无需加入有序分组
- terrain 合法字符集 `GFHSWRBM` 在注册表内静态定义，load 阶段逐格验证，非法字符记录 `_load_issues`

**测试文件**：
- `tests/test_battle_maps.py`（新建，25 个测试）：覆盖 load/select_variant/select_by_tags/spawn 无重叠/terrain 维度/边界验证
- `tests/test_combat_units.py`：追加 `TestAssignPositionsFromSpawns` 4 个测试（basic/wraps/in-place/tuple input）

**测试基线**：2100 passed（+34 vs D-R39 的 2066；5 个已知 pre-existing 失败不变）。

---

### [D-R41] Phase 8：Planner → 战斗联动（WorldBuilder plant_encounter + 导航激活）（2026-03-10）

**设计文档**：`app/增量更新/战斗系统战旗化重构.md` §四 Phase 8
**计划文件**：`/home/xiaokebuyu/.claude/plans/cozy-dazzling-locket.md`

#### 改动范围

| 文件 | 内容 |
|------|------|
| `app/game_core/planning/world_builder.py` | `_HANDLES` 扩展 `plant_encounter`；`apply_directive` 新增路由；新增 `_apply_plant_encounter` 方法（~55 行） |
| `app/game_core/rules/handlers/navigation.py` | `_compute_enter_sub_location` 追加 planted 检测逻辑（~45 行） |
| `app/narrators.py` | 主 prompt 追加 `plant_encounter` 指令示例；`WORLD_BUILDER_AGENT_PROMPT` 更新（两处文案） |
| `tests/test_world_builder_encounter.py` | 新建，5 个测试 |
| `tests/test_navigation_planted.py` | 新建，4 个测试 |

#### plant_encounter 指令语义

Planner 通过 `plant_encounter` 指令将怪物遭遇预植入子地点（`status="planted"`）。玩家导航到该子地点时，NavigationHandler 自动将状态转换为 `status="spotted"` 并在 ExecuteResult.metadata 中写入 `encounter_spotted` 字段，复用现有 enter_hostile → start_combat 流程。

#### 关键设计决策

| # | 决策 | 原因 |
|---|------|------|
| 1 | planted → spotted 在 navigation handler 内完成 | enter_sub_location 是玩家进入子地点的唯一入口 |
| 2 | encounter_spotted 信息放 metadata，不在 handler 创建 SSEEvent | 规则层不持有 SSEEvent 类型，SSE 构造由上层负责 |
| 3 | monster_ids 验证 soft-fail（跳过不存在的怪物） | SettlementContext 可能无 world，且 start_combat 已有 fallback |
| 4 | expiry_ticks 在导航检测时校验 | 避免新增 hook，复用导航时的一次性检查 |
| 5 | _apply_plant_encounter 调用 upsert_hostile 直接写状态 + record_change 记账 | 与 _apply_plant_environmental/_apply_fill_area 的状态直写模式一致 |
| 6 | plant_encounter 不发 SSE | 遭遇在玩家到达时激活，种植时静默 |

#### 测试覆盖（9 个测试）

| 测试 | 验证点 |
|------|--------|
| test_plant_encounter_creates_hostile_tracking | 合法 payload → hostile_tracking 创建，status="planted" |
| test_plant_encounter_missing_area_fails | 不存在的 area_id → return False |
| test_plant_encounter_missing_monsters_fails | monster_ids 空/缺失 → return False |
| test_plant_encounter_no_sse_emitted | 不发 SSE（sse_collector 为空） |
| test_plant_encounter_metadata_stored | threat_level/surprise_modifier/map_tags 正确存储 |
| test_enter_sub_location_detects_planted | planted entry → metadata 含 encounter_spotted + status 变 "spotted" |
| test_enter_sub_location_no_planted | 无 planted entry → 正常导航，无 encounter_spotted |
| test_enter_sub_location_planted_expired | 过期 planted → 不激活，标记 expired/cleared |
| test_enter_sub_location_planted_cleared_skipped | cleared=True → 不再激活 |

**测试基线**：2123 passed（+9 vs D-R40b 的 2111+補 的 2114；5 个已知 pre-existing 失败不变）。

---

### [D-R42] Phase 9：旧战斗代码清理（2026-03-10）

#### 背景

combat.py 在 Phase 2-8 完成后已完全迁移至 v2（SRPG 战旗），v1 死代码已在前序 checkpoint 中清除。本次审计确认清理状态并补齐遗漏的 import 清理。

#### 改动清单

| 文件 | 改动 |
|------|------|
| `app/game_core/rules/handlers/combat.py` | 删除未使用的 `resolve_roll` import（v1 遗留） |

#### 确认已完成的清理（前序 checkpoint 中完成）

- v1 COMMAND_TYPES 条目全部删除（`attack`/`defend`/`disengage`/`dash`/`shove`/`flee`/`use_combat_item`/`offhand_attack`/`stand_up`/`advance_combat_round`）
- v1 类常量 `_FLAG_COMMANDS`/`_DIRECT_RESOLUTION_COMMANDS` 已删除
- v1 验证器 6 个（`_validate_flag_command`、`_validate_advance_combat_round`、`_validate_flee`、`_validate_use_combat_item`、`_validate_direct_resolution_command`、`_validate_stand_up`）已删除
- v1 计算方法 8 个已删除
- v1 支撑函数 8 个（含 `_build_participants`、`_resolve_monster_responses` 等）已删除
- `_apply_player_damage_resistance` 重命名为 `_apply_damage_resistance`
- `defaults.py` 无 v1 条目（`attack`/`defend`/`disengage`/`dash`/`shove`/`flee`/`use_combat_item`/`offhand_attack`/`stand_up` 已不存在）
- `result_semantics.py` 无 v1 战斗命令特定分支

#### Combat SSE 补充（Phase 9 Part B，2026-03-10）

| 文件 | 改动 |
|------|------|
| `app/game_core/orchestration/combat_sse.py` | 新建；`extract_combat_sse(action_type, metadata)` → 8 种战斗 action 转 SSEEvent |
| `app/game_core/orchestration/tick_coordinator.py` | `process()` 在 `_append_pipeline_action` 后插入 `extract_combat_sse` 调用，结果 extend 到 `result.sse_events` |
| `tests/test_combat_sse.py` | 新建；11 个纯同步测试覆盖所有 SSE 分支 |
| `tests/test_combat_handler.py` | 清理 v1 测试（保留 8 个 start_combat / dispatcher 测试） |

SSE 事件类型映射：
- `start_combat` → `combat_started`（含 grid/units/turn_order/current_unit_id/environment）
- `combat_move` → `unit_moved`（含 unit_id/from/to）
- `combat_attack` / `combat_npc_turn` → `unit_attacked`（命中时）；target_alive=False 追加 `unit_defeated`；combat_cleared=True 追加 `combat_ended`
- `combat_end_turn` → `turn_changed`（含 next_unit_id/combat_round/round_advanced）
- `combat_defend` / `combat_disengage` / `combat_dash` — 在 SSE 类型集合中但当前不生成事件（无需暴露给前端）

#### 架构状态

combat.py 当前 1254 行，仅含 v2 SRPG 命令：`start_combat` + 7 个战旗命令（`combat_move`/`combat_end_turn`/`combat_disengage`/`combat_dash`/`combat_attack`/`combat_defend`/`combat_npc_turn`）。combat_sse.py 提供标准化 SSE 转换，TickCoordinator 自动注入。

---

### [P23-E] 轨道 E：战斗 AI + 数据（2026-03-11）

**范围**：W3-5（队友 ai_personality）、W4-4（preferred_terrain 消费）、W6-8（近战 AI 改进 B1-08/09/10）

#### E-1: W3-5 队友 ai_personality 传入

| 文件 | 改动 |
|------|------|
| `app/game_core/content/registries/characters.py` | `CharacterTemplate` 新增 `ai_personality: str \| None = None`；`_build_template()` 解析该字段 |
| `app/game_core/rules/combat_units.py` | `build_companion_unit()` 的 `ai_personality` 参数从 `None` 改为 `template.ai_personality or "aggressive"` |
| `data/goblin_slayer/v2/characters.json` | 为 7 个 `combat_capable: true` 角色添加 `ai_personality`：priestess=protective, goblin_slayer=aggressive, high_elf_archer=cowardly, dwarf_shaman=defensive, lizard_priest=aggressive, town_guard=defensive, north_gate_warden=defensive |

设计决策：`high_elf_archer` 设为 `cowardly`（AI策略"保持远程距离"，选最远距离武器，是弓手正确行为而非负面标签）。

#### E-2: W4-4 消费 preferred_terrain

| 文件 | 改动 |
|------|------|
| `app/game_core/rules/combat_units.py` | `build_monster_unit()` 末尾追加 `unit["preferred_terrain"] = list(template.preferred_terrain)` |
| `app/game_core/rules/battle_ai.py` | `_find_move_toward_target()` 近战和远程路径均新增 preferred_terrain 加权（name→code map，匹配则 score -1.0） |

传递链：`MonsterTemplate.preferred_terrain` → `build_monster_unit()` → `unit["preferred_terrain"]` → `_find_move_toward_target()` 消费。

#### E-3: W6-8 近战 AI 改进

| 文件 | 改动 | 对应问题 |
|------|------|---------|
| `app/game_core/rules/battle_ai.py` | `_find_move_toward_target()` 近战路径候选格评分：`cover_bonus=-1.0`（`ac_bonus > 0`）+ `swamp_penalty=+2.0`（`move_cost >= 3`） | B1-08 偏好掩体，B1-09 回避沼泽 |
| `app/game_core/rules/battle_ai.py` | `_evaluate_targets()` `_score()` 追加 `cover_penalty=-2.0`（目标在 `ac_bonus >= 2` 地形时）| B1-10 目标掩体惩罚 |

评分公式（近战候选格）：`score = move_cost + cover_bonus + swamp_penalty + preferred_bonus`，越低越优先。B1-10 阈值为 `ac_bonus >= 2`（森林），石地（ac_bonus=1）不触发。

#### 测试

新建 `tests/test_track_e_combat_ai.py`（11 个纯同步测试）：覆盖 ai_personality 传递、preferred_terrain 字段、近战掩体偏好、沼泽回避、目标掩体惩罚。

---

### [P23-轨道 F] 地形 + 地图深化（F-1 ~ F-5）（2026-03-11）

**计划文件**：`/home/xiaokebuyu/.claude/plans/cozy-dazzling-locket.md`（轨道 F 章节）
**问题单**：W4-5（补地图）、W4-2（speed_penalty）、W4-7（浅水地形）、W4-8（夜间视野）、W4-3（天气伤害修正）

#### 改动范围

| 文件 | 内容 |
|------|------|
| `app/game_core/rules/battle_grid.py` | 多处增量修改（见下） |
| `app/game_core/content/registries/battle_maps.py` | `_VALID_TERRAIN_CHARS` 新增 `"D"` |
| `data/goblin_slayer/v2/battle_maps.json` | 新增 5 类地图（各 2 变体） |
| `tests/test_battle_grid.py` | 更新既有测试 + 新增 20 个针对性测试 |
| `tests/test_battle_maps.py` | 更新既有测试 + 新增 9 个新地图分类测试 |
| `tests/test_combat_v2_env.py` | 更新 2 个既有测试（night max_visibility 语义变更） |

#### F-1：补 5 类缺失地图（W4-5）

新增 5 个 category 至 `battle_maps.json`，共 10 变体，全部 8×6，spawn 点均在可通行格：

| Category | 变体 1 | 变体 2 | 标签 |
|----------|--------|--------|------|
| `swamp` | 沼泽荒地（G+S） | 泥沼密林（F+S） | swamp/outdoor |
| `town_street` | 小镇广场（R+B+G） | 狭窄巷道（B+R） | town_street/outdoor/urban/narrow |
| `temple` | 神殿大厅（R+B，带柱） | 祭坛走廊（全 R + 外 B 墙） | temple/indoor |
| `bridge` | 木桥（G 桥 + W） | 石桥（R 桥 + B 栏 + W） | bridge/outdoor/linear |
| `camp` | 营地（G+B+R） | 驿站（F+G+R+B） | camp/outdoor |

`select_by_tags()` 可通过各 category 标签名检索到对应变体。

#### F-2：speed_penalty 消耗（W4-2）

修改 `BattleGrid.reachable_cells()`，在计算进入格子的移动消耗时追加 `speed_penalty`：
```python
# 改前：
new_cost = cost + self.move_cost(nc, nr)
# 改后：
terrain = self.at(nc, nr)
new_cost = cost + terrain.move_cost + terrain.speed_penalty
```

沼泽（S）总消耗 = 3（move_cost）+ 2（speed_penalty）= 5，以此实现 W4-2 要求的"进入沼泽后几乎耗尽移动力"语义。

**设计决策**：`speed_penalty` 视为额外移动消耗叠加（不修改 `move_cost` 字段本身），简洁且与 `A*` 路径代价保持一致（A* 仍用 `move_cost`，用于大致路径规划，两者语义不冲突）。

#### F-3：浅水地形 "D"（W4-7）

`TerrainType` 新增 `damage_immunities: frozenset[str] = frozenset()` 字段（`frozen=True` dataclass，用 frozenset 保持不可变性）。

新地形注册：
```python
"D": TerrainType(code="D", name="shallow_water", move_cost=2, ac_bonus=0,
                 range_bonus=0, blocks_los=False, speed_penalty=0,
                 damage_immunities=frozenset({"fire"}))
```

特性：可通行（move_cost=2）、无 AC/range 加成、不遮挡 LoS、站在浅水中对火焰伤害免疫。

**轨道 D 消费**：`combat.py` 在伤害计算时读取目标单位所在格的 `damage_immunities`，如 `damage_type in target_terrain.damage_immunities` 则伤害归零。

`battle_maps.py` `_VALID_TERRAIN_CHARS` 更新为 `frozenset("GFHSWRBMD")`。

#### F-4：夜间视野 max_visibility=4（W4-8）

`compute_environment_modifiers()` 在 `time_of_day="night"` 时新增 `max_visibility` 设置：
```python
if time_of_day == "night":
    ranged_hit_mod += -2
    if max_vis is None or max_vis > 4:
        max_vis = 4  # 夜间视野 4 格；若浓雾更紧（3），保留浓雾值
```

浓雾（max_vis=3）比夜间（4）更严格，通过 `or max_vis > 4` 条件确保浓雾+夜间时取更紧的 3。

**设计决策**：保持 `-2 flat`（固定值）而非 `-10%`（设计文档 B3-03），与 D&D 5e 固定修正风格一致。设计文档偏差在本施工记录中说明。

**既有测试更新**：`test_environment_modifiers_night_ranged` 和 `test_environment_modifiers_night_and_rain_stack` 的 `max_visibility is None` 断言更新为 `== 4`。

#### F-5：天气伤害修正 damage_type_modifiers（W4-3）

`EnvironmentModifiers` 新增字段：
```python
damage_type_modifiers: tuple[tuple[str, float], ...] = ()
```

helper 方法：
```python
def get_damage_modifier(self, damage_type: str) -> float:
    for dt, mod in self.damage_type_modifiers:
        if dt == damage_type:
            return mod
    return 1.0
```

`compute_environment_modifiers()` 在 `weather="rain"` 时设置：
```python
damage_mods.extend([("fire", 0.5), ("thunder", 1.5)])
```

**frozen dataclass + immutable container**：`damage_type_modifiers` 用 `tuple[tuple[str, float], ...]` 而非 `dict`，完全满足 frozen dataclass 的不可变约束，同时允许 `get_damage_modifier()` 高效线性查找。

**轨道 D 消费**：`combat.py` 在命中后伤害计算阶段调用 `env_mods.get_damage_modifier(damage_type)`，乘以原始伤害值后再应用到目标 HP。

#### 测试基线

+20 新测试（`test_battle_grid.py`）+ +9 新测试（`test_battle_maps.py`）= 29 个新测试；更新 2 个既有测试（`test_combat_v2_env.py`）。所有 84 个 battle_grid/battle_maps 相关测试全部通过。

### [D-P23-B] P23 轨道 B：BoardHandler + ReceptionistHandler 奖励发放

**日期**：2026-03-11

**改动**：

**`board.py`**：
- 新增模块级 `_build_reward_changes(state, rewards)` — 将 quest rewards（gold/xp/items）转为 StateChange 列表
- `_compute_board_complete_quest()` — 完成时追加 reward_changes + 设置 `rewards_claimed=True` + 在 metadata 写 `reward_summary`

**`receptionist.py`**：
- `_compute_report_quest(cmd, state)` 新增 state 参数 — 读取 quest rewards，检查 rewards_claimed 防重，发奖后设 rewards_claimed flag
- `validate()` 放宽 receptionist_accept_quest：quest 不在公告板但 status=="available" 也可接取（B-6）
- `_compute_accept_quest()` 对应调整：board_id 可为 None（accepted via dynamic_quests fallback）

**StateChange 路径**：gold/xp 用绝对值 set（handler 层计算增量），inventory 用整个 snapshot set（与 InventoryHandler 一致）

### [D-P23-D] P23 轨道 D：战斗流程全面升级

**日期**：2026-03-11

**改动**：

**`app/game_core/rules/battle_grid.py`**（部分来自轨道 F 交叉依赖）：
- `EnvironmentModifiers` 新增 `damage_type_modifiers: tuple[tuple[str, float], ...]` 字段
- 新增 `get_damage_modifier(damage_type)` 方法（查表，默认 1.0）
- `compute_environment_modifiers()` — rain 时 fire×0.5/thunder×1.5；night 时 max_visibility=4（if None or 4 < existing）

**`app/game_core/rules/handlers/combat.py`**：
- D-5 (W3-7)：`_validate_start_combat()` 新增 MAX_ENEMY_UNITS=5 校验
- D-7 (W4-3)：`_compute_combat_attack()` 在命中后应用 `env_mods.get_damage_modifier(damage_type)` 天气乘数
- D-7 (W4-3)：同上应用地形免疫（`target_terrain.damage_immunities`，shallow_water 免火焰）
- D-8 (W4-8)：远程攻击超出 `env_mods.max_visibility` 自动未命中（夜间/浓雾）
- D-10 (W4-6)：`_compute_combat_attack()` metadata 追加 `target_name/target_side/target_source`
- D-4 (B1-02)：`_compute_combat_attack()` 和 `_compute_combat_npc_turn()` metadata 追加 `player_defeated`
- D-7/D-8：`_compute_combat_npc_turn()` 对应同步升级

**`app/game_core/orchestration/combat_sse.py`**：
- D-10 (W4-6)：`unit_defeated` SSE 事件追加 `name/side/source` 字段

**`app/deps.py`**：
- 新增 `_llm_provider` 模块级变量（在 `_build_game_runtime()` 中赋值）
- 新增 `get_llm_provider()` 函数（供 router 层调用）
- 恢复 `planner_system_factory = None` 初始化（Track A 部分改动遗漏）

**`app/combat_helpers.py`**（新建）：
- `COMBAT_ACTION_TO_COMMAND` — 前端 action→v2 command 映射表（无 FastAPI 依赖，可单测）
- `get_current_unit(payload)` — 从 v2 payload 提取当前回合单位
- `compute_companion_decision(session, unit, payload, llm_provider)` — 队友 LLM AI 决策
- `restore_fallen_companions(session, payload, save_fn)` — 战后恢复队友 HP 到 50%

**`app/routers/combat.py`**：
- D-1 (W3-1)：从 `combat_helpers.COMBAT_ACTION_TO_COMMAND` 引入映射，`combat_action()` 在执行前转换 action_type→v2 command
- D-2 (W3-2)：新增 `/combat/move` 和 `/combat/end_turn` 端点 + `CombatMoveRequest` 模型
- D-3 (W3-3)：新增 `_auto_advance_npc_turns()` — 玩家行动后自动推进所有 NPC 回合直到玩家回合；移除死调用 `advance_combat_round`
- D-6 (W4-1)：`_compute_companion_decision()` 路由器包装 — 注入 LLM provider，委托 combat_helpers
- D-9 (W3-6)：`_restore_fallen_companions()` 路由器包装 — 胜利后恢复倒地队友 HP 到 50%；委托 combat_helpers

**新增测试**（`tests/test_combat_track_d.py`，21 个）：
- D-1: `COMBAT_ACTION_TO_COMMAND` 覆盖率 + 未知 action passthrough
- D-2: `CombatMoveRequest` 模型 + move endpoint 验证
- D-3: `get_current_unit` 3 个边界用例
- D-4: `player_defeated` metadata 设置 / 未设置
- D-5: 超 5 敌人被拒绝 / 恰好 5 个被接受
- D-7: rain fire×0.5 / thunder×1.5 / clear 无修正（3 测试）
- D-8: night max_visibility=4 阻断距离 5 远程 / 允许距离 3 远程
- D-10: `unit_defeated` SSE name/side/source（3 场景）
- D-9: fallen companion 恢复到 50% / 存活队友不受影响
- D-6: `compute_companion_decision` 无 LLM 返回 None

**设计决策**：
- 映射逻辑放在 `combat_helpers.py`（无 FastAPI 依赖）而非 `routers/combat.py`，确保可单元测试
- `_restore_fallen_companions` 直接调用 `party.add_member()` 而非 StateChange（受控例外：router 层编排写入）
- `shove` → `combat_attack`，`flee` → `combat_disengage`（handler 通过 params 区分语义）
- 夜间视野 max_visibility=4 仅限远程攻击（is_ranged）；近战不受视野限制

---

## [D-S504] S5-04 NPC Tool Call 约束守护

**完成时间**：2026-03-11

### 问题

NPC 角色约束（merchant/receptionist/temple_keeper/guard）仅靠 prompt 注入，tool call 层面缺少参数校验。任意 NPC 均可调用 `offer_quest`，且不校验 quest_id 合法性。

### 解决方案："放过嘴，管住手"

**两步**：
1. 在 `npc_interaction.py` 中把 `role_data` 注入到 `AgentContext.metadata["role_data"]`
2. 在 `OfferQuestTool.execute()` 中校验 `quest_id` 合法性

### 具体实现

**`app/game_core/orchestration/npc_interaction.py`**：
- 新增 `_extract_role_data` 到 import（与 `context_builder.py` 同层，合法）
- `execute_interaction()` 的 `npc_metadata` 构建块后追加：
  ```python
  role_data = _extract_role_data(npc_tags, npc_id, self._state, self._world)
  if role_data is not None:
      npc_metadata["role_data"] = role_data
  ```

**`app/game_core/narrative/character_tools.py`**：
- `OfferQuestTool` 新增 `applicable_traits = ["receptionist"]`（第一道防线：RoleToolRegistry 过滤）
- `OfferQuestTool.execute()` 新增第二道防线：当 `metadata.role_data.role == "receptionist"` 时，校验 quest_id 是否在 `bulletins` 中；不在则 fallback 检查 `dynamic_quests` 是否 `status == "available"`；两者都不符合则返回 `quest_not_available`
- `OfferTradeTool.execute()` 补 `logger.warning` 当 shop_data 为空时

### 影响分析

- 只有 `guild_girl` 有 `receptionist` tag，其他 NPC 不受影响
- 无 `role_data` 的 NPC 完全跳过校验（向后兼容）
- OfferTradeTool 是只读展示，warning 不影响返回值

### 测试

`tests/test_s504_tool_constraints.py`：8 个测试，全部通过

---

## [D-P29a] P29 Batch A 关键管线修复（A4/A8b/A8c/A9e）

**日期**：2026-03-13

### 问题

Live 测试暴露了四个相互关联的系统性缺陷：

1. **A4**：`planner_fill_area` 创建动态子地点时，`resident_npcs` 存入 dict 但没有生成 `npc_presence` StateChange，导致 NPC 没有真正进入区域的 `npc_locations`。
2. **A8b**：Planner 创建任务时，objective 有 `type`（如 `talk_to`/`reach_location`）但没有 `condition` 字段，导致 `QuestObjectiveTrackingHook` 无法自动追踪完成状态。
3. **A8c**：`_objective_target_to_params()` 中 `location_entered`/`location_visited` 分支：若 target 的 `area_id` 和 `location_id` 都为 None（如只有 `sub_location_id`），返回 `None` 而非 passthrough，导致条件参数丢失。
4. **A9e**：`planner_create_quest` 没有 `step_index` 参数，无法将任务关联到 milestone outline 的某个步骤；`QuestObjectiveTrackingHook` 完成时也没有回写 outline step completed 状态。

### 实施

**`app/game_core/rules/handlers/planner.py`**：

1. **A4 - `_compute_sub_area_command()`**：
   - `temporary.append(created)` 之后，遍历 `created["resident_npcs"]`
   - 为每个有效 NPC ID 追加 `StateChange("areas", "set", f"npc_presence.{npc_id_str}", {...})`
   - source="planner"，location_id = sub_area_id（动态子地点的 id）
   - 返回值 metadata 增加 `npc_presence_count` 字段

2. **A8b - `_compute_create_quest()` objectives 归一化阶段**：
   - 在 `normalized_objectives.append(entry)` 之前，对每个 dict objective 检查：有 `type` 但无有效 `condition`
   - 用 `_objective_to_condition_type(obj_type)` + `_objective_target_to_params()` 自动生成 condition
   - 无法生成时向 `entry["metadata_warnings"]` 添加说明（不报错）

3. **A8c - `_objective_target_to_params()`**：
   - `location_entered`/`location_visited` 分支中，`if area_id is None and location_id is None: return None` 改为 passthrough
   - 改为 `return {str(key): value for key, value in target.items()}`

4. **A9e - `_compute_create_quest()` + `NarrativePlanSlice`**：
   - `_compute_create_quest()` 读取 `step_index = coerce_int(cmd.params.get("step_index"))`
   - 如果 `step_index >= 0` 且 state 有 `narrative_plan` slice，追加 `StateChange("narrative_plan", "set", "milestone_outline.step_quest_id", {"step_index": step_index, "quest_id": quest_id})`
   - `NarrativePlanSlice` 增加 `set_outline_step_quest_id(step_index, quest_id)` 方法
   - `apply_state_change` 增加 `milestone_outline.step_quest_id` 路径处理

**`app/game_core/orchestration/hooks/quest_objective_tracking.py`**：

- 当所有 objectives 完成、quest status 推进时，检查 `quest["metadata"]["step_index"]`
- 若有有效 step_index 且 state 有 `narrative_plan`，调用 `context.state.narrative_plan.mark_outline_step_completed(step_index)`（直接写入，受控例外）

### 测试

`tests/test_planner_command_handlers.py` 新增 11 个测试（共 19 个）：

- `test_fill_area_with_resident_npcs_emits_npc_presence_changes`
- `test_fill_area_without_resident_npcs_emits_no_npc_presence`
- `test_fill_area_resident_npc_source_is_planner`
- `test_create_quest_auto_generates_condition_for_talk_objective`
- `test_create_quest_auto_generates_condition_for_location_objective`
- `test_create_quest_preserves_explicit_condition_unchanged`
- `test_objective_target_to_params_passthrough_location_with_only_sub_location`
- `test_objective_target_to_params_normal_location_preserved`
- `test_create_quest_with_step_index_writes_back_to_outline`
- `test_create_quest_without_step_index_does_not_modify_outline`
- `test_quest_objective_tracking_hook_marks_outline_step_on_completion`

全部 19 个测试通过。


---

### [D-P29-A5e] PlannerWorldHandler interactable 管线修复

**实施日期**：2026-03-13

**变更文件**：`app/game_core/rules/handlers/planner.py`

**问题**：`_compute_sub_area_command()` 构建 `created` dict 时，`interactables` 字段调用 `_normalize_string_list()`，该函数通过 `coerce_non_empty_string()` 把 dict 元素转成字符串（如 `"{'id': 'altar', 'name': '...'}"`），导致下游 `InteractableHandler._find_dynamic_interactable()` 无法按 id 匹配。

**修复**：
- 新增 `_normalize_interactable_list()` 函数：保留 dict 元素，plain string 元素仍走 `coerce_non_empty_string()`
- `_compute_sub_area_command()` 中 `"interactables"` 字段从 `_normalize_string_list` 改为 `_normalize_interactable_list`
- `"resident_npcs"`、`"tags"` 等其他字段保持使用 `_normalize_string_list`（不含 dict，无需改动）

---

### [D-precious-wishing-meteor-P2] PlannerWorldHandler 验证加固（Phase 2）

**实施日期**：2026-03-13

**变更文件**：`app/game_core/rules/handlers/planner.py`

**背景**：planner LLM 生成的 directive 会触发约 70 处 `raise ValueError/KeyError`，导致 narrative_planner hook 崩溃。Phase 2 修复 PlannerWorldHandler 中已确认的 4 条崩溃路径，让错误回路走 `ExecuteResult.error` 而非 exception。

**修复内容**：

**2a. planner_fill_location 容量检查移至 compute()**
- `validate()` 的容量投影基于 validate 时刻的快照；若状态在 validate 后变化，`upsert_scoped_interactable_overlays` 仍可能 raise
- 修复：`_compute_fill_location()` 中用与 `upsert` 相同的遍历逻辑重新计算最终 slot 数，超出 4 则返回 `ExecuteResult.error("scene interactable overlay capacity exceeded")`，不依赖 state 层的 raise

**2b. planner_fill_area interactables/resident_npcs 列表防御**
- `_compute_sub_area_command()` 中 `created` dict 的 interactables 和 resident_npcs 字段增加 `isinstance(…, list)` 兜底，确保非 list 输入（如 None 或 string）coerce 为空 list

**2c. planner_plant_encounter area_id 非空守卫**
- `_compute_plant_encounter()` 开头改为 `coerce_non_empty_string()`，若为空/None 则返回 `ExecuteResult.error("area_id must be a non-empty string")`，而非塞入空字符串进 hostile_tracking entry

**2d. planner_fill_room Mapping 防御检查**
- `_compute_fill_room()` 在 emit StateChange 前添加 `isinstance(room_dict, Mapping)` 断言；当前 room_dict 始终是 dict，但明确守卫未来 refactor 引入的类型变化

**测试**：`tests/test_planner_command_handlers.py` 新增 9 个测试（共 32 个）：
- `test_fill_location_compute_rejects_overflow_when_4_existing`
- `test_fill_location_compute_allows_update_of_existing_id_when_full`
- `test_fill_location_compute_rejects_when_mix_of_new_and_existing_overflows`
- `test_fill_area_interactables_non_list_coerced_to_empty`
- `test_fill_area_interactables_string_coerced_to_empty`
- `test_plant_encounter_compute_rejects_empty_area_id`
- `test_plant_encounter_compute_rejects_none_area_id`
- `test_plant_encounter_compute_succeeds_with_valid_area_id`
- `test_fill_room_compute_produces_mapping_entry`

全部 32 个测试通过（含 9 个新增）。

**测试**：见 narrative.md [D-P29-A5e] — `tests/test_p29_a5e_interactable_pipeline.py`

---

### [D-R39] precious-wishing-meteor Phase 3：PlannerNpcHandler + PlannerQuestHandler 验证加固

**文件**：`app/game_core/rules/handlers/planner.py`

**根因**：三条崩溃路径未在 handler 层拦截，导致合法 delta 流入 state 层触发 raise。

**3a. planner_spawn_quest_npc room_id/location_id 一致性**
- `_compute_spawn_quest_npc()` 在调用 `resolve_npc_room()` 后，若返回了 `resolved_room_id` 但 `location_id` 为 None/空，则清除 `resolved_room_id = None`
- 原因：`AreaSlice.apply_state_change()` 强制要求 room_id 与 location_id 同时存在或同时缺失
- 修复位置：`resolve_npc_room()` 调用之后，`name = ...` 赋值之前

**3b. planner_publish_bulletin area_id 空字符串守卫**
- `_compute_publish_bulletin()` 中 `_resolve_area_id()` 可能因 `location.area_id` 为空白字符串而返回空字符串（非 None）
- 修复：在 compute() 中对返回值再次调用 `coerce_non_empty_string()`，若结果为 None 返回 `ExecuteResult.error("area_id must be a non-empty string")`
- `validate()` 的检查已覆盖主路径，但 compute() 的守卫提供第二道防线

**3c. planner_direct_npc expires_at_tick 向下 clamp**
- `_compute_direct_npc()` 中新增：`if expires_at_tick < current_tick: expires_at_tick = current_tick`
- 防止 LLM 传入过去时间点（负数情形已被上方 `< 0` 的现有检查拒绝；此处处理"小于当前 tick 但非负"的情形）

**测试**：`tests/test_planner_command_handlers.py` 新增 9 个测试（共 41 个）：
- `test_spawn_quest_npc_clears_room_when_location_id_absent`
- `test_spawn_quest_npc_room_cleared_means_area_apply_succeeds`
- `test_spawn_quest_npc_with_location_retains_resolved_room`
- `test_publish_bulletin_compute_rejects_empty_area_id_string`
- `test_publish_bulletin_compute_succeeds_with_valid_area_id`
- `test_direct_npc_expires_at_tick_clamped_when_less_than_current`
- `test_direct_npc_expires_at_tick_negative_value_clamped`
- `test_direct_npc_expires_at_tick_future_value_preserved`
- `test_direct_npc_expires_at_tick_equal_current_tick_preserved`

全部 41 个测试通过（含 9 个新增）。

---

## D-P4: AreaSlice apply_state_change 韧性化

**日期**：2026-03-13
**文件**：`app/game_core/state/slices/area.py`
**新测试**：`tests/test_area_slice_resilience.py`（23 个测试）

### 背景

`apply_state_change()` 中有约 15 个 `raise ValueError`，分布在 planner 可达的路径上。
当 Planner LLM 生成格式不合规的 directive 时，这些 raise 会让整个 `narrative_planner` hook 崩溃，LLM 看不到错误反馈。

Phase 1 已在 `execute_command()` 加了安全网 catch；Phase 4 从根本上把这些 raise 软化为 `logger.warning + return`（跳过），防止误用崩溃。

### 改动（15 处 + 1 个 capacity break）

**新增**（文件顶部）：
```python
import logging
logger = logging.getLogger(__name__)
```

**npc_presence 路径（4 个 raise → warning + return）**：
- 不支持的 operation（不在 `{"set", "modify"}`）
- value 不是 Mapping
- value 中缺少 area_id
- room_id 非空但 location_id 为空

**board_bulletins 路径（3 个 raise → warning + return）**：
- operation 不是 `"append"`
- value 不是 Mapping
- value 中 area_id 为空字符串

**hostile_tracking 路径（3 个 raise → warning + return）**：
- apply_state_change 中 operation 不在 `{"set", "modify"}`
- apply_state_change 中 value 不是 Mapping
- `upsert_hostile()` 中 area_id 为空（直接在方法体内修改，而非 apply_state_change）

**temporary_sub_areas 路径（3 个 raise → warning / continue）**：
- operation 不在 `{"set", "modify"}`
- value 不是 list
- 列表中的单个 entry 不是 Mapping → `continue`（跳过该条目，而非崩溃，其余有效条目仍应用）

**scoped_interactable_overlays 路径（2 个 raise → warning + return，1 个 capacity raise → break）**：
- operation 不在 `{"set", "modify"}`
- value 不是 list
- `upsert_scoped_interactable_overlays()` 中容量超限 → `break`（截断而不是 raise）

### 保留的 raise（Category C）

以下 raise 属于编程错误探测，不是 planner 可达路径，**未修改**：
- `line 1089`（现在偏移后的行）：`"area change path must include area id"`
- `line 1191`（兜底）：`"unsupported area state change"`
- `container_states` / `interactable_states` 路径的 raise（非 planner 路径）
- `mark_cleared` / `remove_item_from_container` 的 raise（战斗路径）
- `board_bulletins`（整体替换，非 `.` 前缀路径）已有单独守卫，保持不变

### 测试设计（23 个）

分 5 个 TestCase 类：
- `TestNpcPresenceResilience`（5）
- `TestBoardBulletinsResilience`（4）
- `TestHostileTrackingResilience`（5）
- `TestTemporarySubAreasResilience`（4）
- `TestScopedInteractableOverlaysResilience`（5）

每个 TestCase 包含：
- 各无效输入场景（warning 消息验证 + 不触发状态变更）
- 有效输入场景（仍然正常工作）
- capacity truncation 场景（不 raise，只 break）

全部 23 个测试通过，原有 28 个 area_slice 测试全部通过，无回归。

---

## D-P5: PlayerSlice + QuestSlice 韧性化

**日期**：2026-03-13
**文件**：`app/game_core/state/slices/player.py`、`app/game_core/state/slices/quests.py`
**新测试**：`tests/test_player_quest_slice_resilience.py`（14 个测试）

### 背景

Phase 1 的 `execute_command()` 安全网已防止崩溃传播；Phase 5 从根本上把 planner 可达路径上的 `raise` 软化为 `logger.warning + return/fallback`，与 Phase 4（AreaSlice）保持一致。

### 改动

**两个文件共同变更**：
- 新增 `import logging` 和 `logger = logging.getLogger(__name__)` 到文件顶部（位于 local imports 之后）

**`player.py`（2 处）**：

1. **`apply_state_change` inventory 路径（L601 附近）**：
   - `raise ValueError("inventory change must be a list")` → `logger.warning(...) + return`
   - 非 list 输入时跳过整个 inventory 替换，旧 inventory 保持不变

2. **`_coerce_stack` 静态方法（末尾）**：
   - `raise ValueError(f"invalid item stack: {item!r}")` → `logger.warning(...) + return ItemStack(item_id="", count=0, tags=[])`
   - 无效 item 降级为空 ItemStack，而非让整个 inventory 替换失败

**`quests.py`（4 处，均在 `_apply_nested_dynamic_change`）**：

1. `path_parts` 为空 → `logger.warning(...) + return`
2. list 容器中的 key 无法转 int → `logger.warning(...) + return`
3. list 容器 index 越界 → `logger.warning(...) + return`
4. container 既非 dict 也非 list → `logger.warning(...) + return`

### 保留的 raise

- `quests.py:215` `raise ValueError(f"unsupported quest state change: ...")` — 顶层路由兜底，编程错误探测，不在 planner 正常路径上，**未修改**
- `quests.py:224` `raise ValueError(f"...missing dynamic quest...")` — 要求 quest 存在，非 LLM 格式错误，**未修改**

### 测试设计（14 个）

- PlayerSlice inventory 非 list：3 个（warn + 不变 / 不 raise / 含 list 中无效 item 使用 fallback）
- `_coerce_stack` 无效输入：4 个（int / string / None → 空 ItemStack / list 中混入无效 item）
- QuestSlice `_apply_nested_dynamic_change`：5 个（空 path / bad index / 越界 / 非 dict-list / 不 raise）
- 通过 `apply_state_change` 的集成路径：2 个（bad index / valid change 仍工作）

全部 14 个测试通过，原有测试基线无回归（3022 passed）。
