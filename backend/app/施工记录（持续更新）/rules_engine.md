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
