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
| 1 | `CombatHandler` | attack, defend, disengage, dash, shove, flee, use_combat_item, offhand_attack, start_combat | [完成] | 高 |
| 2 | `SkillCheckHandler` | skill_check, saving_throw, contest | [完成] | 低 |
| 3 | `NavigationHandler` | move_area, enter_sub_location, leave_sub_location | [完成] | 中 |
| 4 | `InventoryHandler` | pick_up, drop, equip, unequip, use_item | [完成] | 中 |
| 5 | `EconomyHandler` | trade_buy, trade_sell, refresh_shop | [完成] | 低 |
| 6 | `GrowthHandler` | add_xp, level_up, apply_asi, choose_subclass, create_character | [完成] | 中 |
| 7 | `RestHandler` | rest_short, rest_long, night_watch, set_camp | [完成] | 中 |
| 8 | `CrimeHandler` | steal, lockpick | [完成] | 低 |
| 9 | `EncounterHandler` | encounter_check, generate_loot, clear_hostile | [完成] | 中 |
| 10 | `ContainerHandler` | open_container, disarm_trap, take_from_container, take_all | [完成] | 中 |
| 11 | `WorldStateHandler` | set_flag, modify_disposition, modify_approval, advance_quest, schedule_event, create_rumor, modify_location, add_knowledge, modify_completion, adjust_danger | [完成] | 高 |
| 12 | `StatusEffectHandler` | apply_effect, remove_effect, remove_effect_by_type, tick_effects | [完成] | 中 |
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

### [D-R05] saving_throw 暂按统一熟练实现

当前仓内没有独立的“各豁免熟练项”状态结构。
MVP 阶段 `saving_throw` 统一按：

`ability modifier + player.proficiency_bonus`

后续如引入精细化职业/专长熟练表，再替换为真实豁免熟练判定。

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

本轮 `EncounterHandler` 补齐了三个命令，但仍有意保持为“稳定扩展点”：

- `encounter_check` 使用确定性阈值：
  - `danger * period_multiplier >= 1.0` 视为触发
  - 不引入随机数，也不接 `DynamicSubAreaManager`
- 触发时只往 `AreaSlice.hostile_tracking` 写入最小 hostile runtime 状态
- `generate_loot` 只处理确定性金币与确定性掉落：
  - 金币自动入账
  - 物品只出现在 metadata 中，不自动入包
- `clear_hostile` 采用幂等语义：
  - 未找到目标 hostile 时返回 `success=True + status="noop"`

这样默认规则链已可依赖这三个命令，后续再在这个边界上深化真实遭遇和完整掉落系统。

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

其中当前明确采用“稳定扩展点”边界：

- 不新增 `combat` slice
- 最小战斗运行时挂在 `AreaSlice.hostile_tracking`
- `start_combat` 只允许 `engine/system`
- `start_combat` 会写入：
  - `combat_active`
  - `combat_round`
  - `surprise_state`
  - `participants`
  - `player_flags`
- `defend / disengage / dash` 只更新玩家战斗旗标
- `flee` 使用确定性的被动值，不掷骰
- `use_combat_item` 本轮只支持战斗中的自身治疗消耗品
- `attack` 现在会做最小确定性命中与伤害结算，并直接回写 `participants`
- `offhand_attack` 现在会做最小确定性命中与固定弱伤害结算
- `shove` 现在会做最小确定性对抗，并在成功时把当前遭遇的 `blocking` 置为 `False`
- 三者仍保持 MVP 边界：
  - 不引入完整战斗引擎
  - 不引入先攻/怪物回合
  - 不新增 `combat` slice

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
