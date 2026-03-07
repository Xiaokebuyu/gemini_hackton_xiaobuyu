# P10-AIOsiris完整LLM版修正设计

## 1. 目标

这份设计不是在现有 `AIOsirisHook` 上继续打补丁，而是明确给出一套能够逐步落到架构文档目标态的完整修正方案。

目标只有一个：

- 让 AI Osiris 真正成为 **P30 的专职因果判定 LLM**，在每个时间格结算中读取 `summary + snapshot + rules_context`，输出结构化 consequences，经规则引擎验证执行，并把玩家可感知后果交给 `SceneBus -> GM narration`。

这份设计按三阶段实施，但目标态从现在就固定，不再边做边改协议。

## 2. 当前实现审计

### 2.1 已有基础

当前实现并不是空白，已经有 4 层壳：

1. `AIOsirisHook` 已正确挂在 P30。
2. Hook 会构造：
   - `summary`
   - `snapshot`
   - `rules_context`
3. Hook 具备 consequence 白名单、归一化、`execute_command()` 执行壳。
4. 当环境中存在 LLM provider 时，runtime 已会注入 `AgenticAIOsirisEvaluator`，不是永远只跑 deterministic fallback。

也就是说，当前系统已经有：

- Hook 位置
- 输入壳
- 输出安全壳
- LLM 注入点

真正缺的是“脑子”和“闭环”。

### 2.2 当前最大差距

#### A. LLM evaluator 过薄

当前 `AgenticAIOsirisEvaluator` 本质上只是：

- 单次 prompt
- 单个 `submit_consequences` tool
- 把 tool args 直接转成 `AIOsirisDecision`

它没有做到：

1. 针对文档定义的 consequence schema 做强校验
2. 针对不同 consequence 类型做语义级修正/补全
3. 把 `visible_change` 落到 `SceneBus`
4. 使用文档要求的 medium thinking
5. 形成专职的 Osiris 模型配置和观测面

#### B. deterministic fallback 过于骨架化

当前 `BasicAIOsirisEvaluator` 只做两件事：

1. quest 变化时写 `osiris_last_quest_change_chapter`
2. `quest_started` 时写 `osiris_ack_quest_started`

它完全没有体现文档要求的：

- 关系变化
- 任务推进
- 延迟事件
- 社会后果
- rumor / danger / approval / disposition 推理

#### C. SceneBus 闭环缺失

架构文档要求：

- Osiris 的可见后果写入 `SceneBus`
- P80 GM 读取 SceneBus 再叙述

当前实现没有这条链。现在只有一个技术性 SSE：

- `ai_osiris_applied`

这意味着当前 Osiris 输出对玩家来说更像“后台调试事件”，不是世界叙事的一部分。

#### D. skip 语义和文档不一致

文档规定：

- 只有纯 `0 cost` trivial action 才能跳过

当前实现是：

- 只要 `change_log` 没碰到 `_MEANINGFUL_SLICES` 就跳过

这是一种实现方便的近似，不是文档定义。

#### E. 输入虽然存在，但可推理性不足

当前 `summary/snapshot` 已有信息，但距离“供因果 LLM 使用的规范输入”还差几层：

1. `duration_minutes` 固定写死 60
2. `nearby_npcs` 偏 area 级，不够 scene-local
3. `state_changes` 仍接近底层 delta 视角
4. 缺少更强的“本格类型”语义，如：
   - normal_turn
   - travel_tick
   - rest_tick
   - conversation_tick
5. 缺少“玩家行为被谁看到”的置信度分层

#### F. 模型策略与文档预期不一致

文档对 Osiris 明确要求：

- `Flash + medium thinking`

当前系统的 Gemini adapter 已被统一降到 `LOW`，所以现有 Osiris LLM 路径和文档目标不一致。

### 2.3 当前完成度判断

如果按“P30 接线和执行壳”算，当前完成度约 `35%-40%`。

如果按“文档里真正定义的 AI Osiris”算，当前完成度更接近 `20%`。

准确描述是：

- 现在已经有 Hook 和执行框架
- 但还没有完整的 LLM 因果引擎

## 3. 目标态定义

目标态必须严格满足以下 8 条。

### 3.1 P30 单次调用，单次结构化决策

每个 settlement tick：

1. 由 `AIOsirisHook` 构造输入
2. 调用一次 Osiris LLM provider
3. 得到单个 `AIOsirisDecision`
4. 验证并执行 consequences
5. 将可见后果写入 `SceneBus`

不复用 `AgenticExecutor` 的多轮 agent loop。

Osiris 不是 GM/NPC/队友；它是单次结构化因果判定器。

### 3.2 输入三件套固定

输入模型固定为：

1. `summary`
2. `snapshot`
3. `rules_context`

其中：

- `summary` 负责“本格发生了什么”
- `snapshot` 负责“当前世界是什么状态”
- `rules_context` 负责“这个世界如何运作”

任何未来增强都只能在这三件套内部加字段，不能额外让 evaluator 自己去检索世界。

### 3.3 输出固定为 consequences 数组

允许的 consequence 类型仍固定为 10 个：

1. `set_flag`
2. `modify_disposition`
3. `modify_approval`
4. `advance_quest`
5. `schedule_event`
6. `create_rumor`
7. `modify_location`
8. `add_knowledge`
9. `modify_completion`
10. `adjust_danger`

任何非这 10 类输出一律视为非法 consequence。

### 3.4 Osiris 只判不直接改玩家硬状态

Osiris 仍然不允许直接改：

- HP
- gold
- inventory
- player location

它只允许修改“世界对玩家的看法”和“世界侧状态”。

### 3.5 visible consequence 必须进入 SceneBus

目标态里不再依赖 `ai_osiris_applied` 作为主要玩家可见反馈。

规则如下：

1. consequence 若属于玩家可感知变化，必须转译成 `SceneEntry`
2. 这些 `SceneEntry` 以 `visibility="system"` 写入 `SceneBus`
3. P80 `GmNarrationHook` 再读取这些内容进行叙述

`ai_osiris_applied` 只保留给调试或后台观测，不再承担产品语义。

### 3.6 skip 规则按文档收口

目标态只允许以下情况跳过：

- 本格全部 action 都是 trivial 且总成本为 0

例如：

- 查看背包
- 查看地图
- 纯装备切换

只要涉及：

- NPC 互动
- 状态变化
- 时间消耗
- 社会可见动作

就不能跳过。

### 3.7 Osiris 使用独立模型策略

Osiris 不能简单继承当前“所有 agent 统一 LOW thinking”的配置。

目标态要求：

- model family: `Flash`
- thinking level: `MEDIUM`

原因很直接：

- Osiris 的价值不是写得像人，而是推理跨系统后果
- 这类任务比 NPC 对话更依赖推理稳定性

### 3.8 NarrativePlanner 必须能看到 Osiris 当格输出

P30 和 P35 的关系保持文档定义：

1. P30 先执行 Osiris
2. Osiris 产生的 flags / quests / rumors / directives / danger 等进入状态和 SceneBus
3. P35 NarrativePlanner 基于这一轮最新状态继续做主动叙事规划

## 4. 总体架构设计

### 4.1 分层

完整 LLM 版拆成 4 层：

1. **Hook 层**
   - `AIOsirisHook`
   - 负责输入构造、调用 provider、执行 consequences、写 SceneBus

2. **Provider 层**
   - `AIOsirisProvider`
   - 负责把三件套送给模型，并返回结构化 decision

3. **Validation/Normalization 层**
   - consequence schema 校验
   - command 级别修复与拒绝
   - visible consequence 转 SceneEntry

4. **Execution 层**
   - `SettlementContext.execute_command()`
   - 规则引擎校验执行

### 4.2 不再复用 AgenticExecutor

目标态不应使用 `AgenticExecutor.run_agentic(role="osiris")`。

原因：

1. Osiris 没有多轮工具交互需求
2. 它要的是稳定 JSON / tool payload，不是开放式对话
3. 它和 NPC/GM 的失败模式不同
4. 它需要独立 thinking 和观测策略

因此建议保留专用 provider 接口：

```python
class AIOsirisProvider(Protocol):
    async def evaluate(
        self,
        summary: dict[str, Any],
        snapshot: dict[str, Any],
        rules_context: dict[str, Any],
    ) -> AIOsirisDecision: ...
```

`AgenticAIOsirisEvaluator` 可以继续存在，但建议升级并重命名为：

- `GeminiAIOsirisProvider`

### 4.3 Provider 使用专用 LLM 配置

需要引入一层 Osiris 专用配置，而不是直接复用全局 `GeminiLlmAdapter` 默认行为。

建议新增：

- `GeminiLlmAdapter.generate_with_profile(profile="osiris", ...)`

或者新增一个更直接的 provider：

- `GeminiAIOsirisProvider`

它内部显式指定：

- model
- temperature
- thinking level
- response schema / tool declaration

## 5. 输入模型修正

### 5.1 summary 目标结构

目标态 `summary` 至少应包含：

1. `time_slot`
2. `location`
3. `tick_kind`
4. `time_cost`
5. `actions`
6. `state_changes`
7. `changed_slices`
8. `change_count`

新增关键字段：

- `tick_kind`
  - `normal`
  - `travel`
  - `rest`
  - `conversation`
  - `combat_resolution`

- `time_cost`
  - 当前格实际成本，而不是固定 `duration_minutes=60`

- `actions[].visibility_scope`
  - `private`
  - `local`
  - `public`

- `actions[].witnessed_by`
  - 只保留当前 scene/area 内真实可见角色

- `actions[].tags`
  - 保留当前已有 tag enrich
  - 补强 faction / sacred / guild / crime / stealth / witness context

### 5.2 snapshot 目标结构

目标态 `snapshot` 要覆盖：

1. `player`
2. `party`
3. `nearby_npcs`
4. `faction_standings`
5. `active_flags`
6. `current_chapter`
7. `chapter_completion`
8. `time`
9. `location`
10. `pending_events`
11. `danger`
12. `active_dynamic_quests`
13. `scene_presence`

新增重点：

- `pending_events`
  - 让 Osiris 知道已有延迟后果队列，避免重复 schedule

- `danger`
  - area/location 级风险状态

- `scene_presence`
  - 当前 SceneBus / 当前 location 的在场角色，而不是只看 area

- `active_dynamic_quests`
  - 给 Osiris 更直接的任务上下文，而不只靠 milestone/flags 猜

### 5.3 rules_context 目标结构

目标态 `rules_context` 保持现有字段，并新增：

1. `command_schema`
2. `semantic_constraints`
3. `visibility_rules`
4. `trigger_condition_schema`
5. `relationship_stage_guide`

其中：

- `command_schema` 用于明确每个 consequence 类型所需 params
- `semantic_constraints` 明确哪些 target 合法、哪些 target 禁止
- `visibility_rules` 明确何种 consequence 需要进入 SceneBus
- `trigger_condition_schema` 明确 `schedule_event` 的合法条件

## 6. 输出与校验设计

### 6.1 Decision 结构

目标态的 `AIOsirisDecision` 固定包含：

1. `consequences`
2. `reasoning`
3. `visible_change`
4. `metadata`

`metadata` 至少应含：

1. `status`
2. `provider`
3. `model`
4. `thinking_level`
5. `token_usage`
6. `latency_ms`
7. `raw_consequence_count`
8. `normalized_consequence_count`

### 6.2 consequence 归一化流程

归一化分 4 步：

1. **schema validation**
   - 是否是 10 个合法类型之一
   - 是否有必需参数

2. **semantic validation**
   - 目标实体是否存在
   - 数值范围是否合法
   - `modify_location` 是否触碰 player location 禁区
   - `schedule_event` 是否有合法 trigger

3. **normalization**
   - 参数别名收口
   - 空字符串/None 归一化
   - 默认值补齐

4. **execution gate**
   - 通过者进入 `execute_command`
   - 失败者计入 invalid/failed telemetry

### 6.3 visible consequence 转 SceneBus

新增一个内部转换器：

- `OsirisVisibleConsequenceRenderer`

职责：

1. 读取已执行成功的 consequence
2. 按 consequence 类型决定是否可见
3. 生成 `SceneEntry(visibility="system")`

示例：

- `create_rumor` -> system scene entry
- `adjust_danger` -> system scene entry
- `advance_quest` -> system scene entry
- `modify_disposition` 通常不直接可见，除非带有强可见标签

这样 P80 GM 叙述看到的是“世界发生了什么”，而不是技术性 command log。

## 7. Prompt 与 LLM 设计

### 7.1 Prompt 目标

当前 prompt 可以保留大方向，但要收紧 3 点：

1. 明确只输出跨系统涟漪，不重复事实
2. 明确优先生成小而可信的 consequence，不贪多
3. 明确 visible 与 invisible consequence 的判断标准

### 7.2 输出协议

保留 `submit_consequences` tool 方案，但需要把 schema 提升到真正可执行级别。

新增字段建议：

- `visibility_hint`
  - `visible`
  - `hidden`

- `confidence`
  - `low`
  - `medium`
  - `high`

- `reason`
  - 单条 consequence 的局部理由

Hook 不一定把这些字段直接透传给 command，但会用它们做：

- SceneBus 渲染
- telemetry
- 调试

### 7.3 模型策略

目标态固定：

- model: `gemini-2.5-flash` 或当前等价 Flash 型号
- thinking: `MEDIUM`
- temperature: 偏低

理由：

- Osiris 不追求文风
- 追求 consequence 稳定性和结构正确率

### 7.4 不关闭 thinking

完整 LLM 版 Osiris 不建议关闭 thinking。

原因：

1. 它的核心价值就是因果推理
2. 这类任务比 NPC 回复更依赖中间推理稳定性
3. 文档已经明确选择了 `medium thinking`

## 8. 三阶段实施路线

## Phase 1：输入/校验/观测回正

目标：

- 不改系统大契约
- 先把 Hook 壳和输入、skip、SceneBus、观测面做对

实现内容：

1. `should_skip()` 改成按 trivial + 0 cost 判定
2. 补 `tick_kind`
3. 把 `nearby_npcs` 改成 scene-local 优先
4. 补 `pending_events/danger/active_dynamic_quests`
5. 新增 `OsirisVisibleConsequenceRenderer`
6. 把 `visible_change` 真正落到 SceneBus
7. 完善 metadata / telemetry

这阶段完成后：

- 即使还用现有 LLM evaluator，Osiris 也不再只是后台调试器

## Phase 2：专用 LLM provider 回正

目标：

- 把当前薄的 `AgenticAIOsirisEvaluator` 升级成真正可用的 `GeminiAIOsirisProvider`

实现内容：

1. 独立 Osiris provider
2. 独立 model/thinking 配置
3. consequence schema 强化
4. semantic normalization
5. provider 级 token / latency / parse telemetry
6. fallback 策略：
   - LLM error -> noop
   - parse failed -> noop + telemetry
   - partial invalid -> valid 部分继续执行

这阶段完成后：

- P30 会真正跑“专用 LLM 因果推理”

## Phase 3：文档目标态收口

目标：

- 尽可能贴近 `AI-Osiris设计规范`

实现内容：

1. `schedule_event` 深化
2. 与 EventEngine 的触发语义梳理
3. 与 NarrativePlanner 的当格协作调优
4. 跨格行为场景：
   - travel tick
   - rest tick
   - crime discovery delay
   - faction rumor diffusion
5. Osiris visible consequence -> GM narration 文风闭环

这阶段完成后：

- Osiris 才能算接近文档里的目标形态

## 9. 失败与降级策略

### 9.1 LLM 失败

若 provider 报错：

- 不阻断 settlement
- 记录 `ai_osiris_error`
- metadata 标记 `status=llm_error`
- 不写 SceneBus visible consequence

### 9.2 解析失败

若模型返回不可解析内容：

- 记 `llm_parse_failed`
- 本格按 noop 处理

### 9.3 consequence 部分非法

若返回 5 条 consequence，其中 2 条非法：

- 合法的继续执行
- 非法的记 invalid telemetry
- 不做全局失败

### 9.4 超时

Osiris 超时不应阻塞整个 tick 过久。

建议：

- provider 设置短超时
- 超时视为 noop

## 10. 测试计划

### 10.1 Hook 级

1. trivial 0-cost action 跳过
2. NPC 交互/有时间成本 action 不跳过
3. visible consequence 写入 SceneBus
4. invalid consequence 被拒绝但不拖垮整格

### 10.2 Provider 级

1. prompt 组装正确
2. tool payload 解析正确
3. text JSON fallback 正确
4. medium thinking 配置正确
5. LLM error / parse failure / timeout 都能降级

### 10.3 Integration 级

1. P30 -> P35：NarrativePlanner 能看到 Osiris 当格输出
2. P30 -> P80：visible consequence 经 SceneBus 被 GM 叙述
3. `schedule_event` 在未来 tick 触发
4. rumor / disposition / approval / danger 等 consequence 经规则引擎正确落状态

### 10.4 Live smoke

至少覆盖：

1. 偷窃被目击 -> rumor / disposition / delayed discovery
2. 在公会完成关键检定 -> guild approval / quest advance
3. rest tick -> 同伴/世界侧延迟后果
4. travel tick -> danger / encounter preparation

## 11. 风险

### 11.1 成本上升

引入 medium thinking 后，P30 成本会上升。

缓解方式：

1. 严格 `should_skip`
2. 控制 `summary/snapshot` 输入大小
3. consequence 数量上限

### 11.2 模型不稳定输出

LLM 天然会有 schema 偏差。

缓解方式：

1. 强 schema
2. 归一化层
3. 不合法 consequence 丢弃
4. noop 降级

### 11.3 SceneBus 叙述重复

若 Osiris visible consequence 和其他 Hook 同时写 SceneBus，P80 可能重复讲。

缓解方式：

1. Osiris scene tags 独立命名
2. GM narration prompt 做去重约束

## 12. 验收标准

只有同时满足以下条件，才算“完整 LLM 版 Osiris 达标”：

1. P30 运行时默认走专用 LLM provider，而不是 deterministic skeleton
2. provider 使用独立 Flash + medium thinking 配置
3. `should_skip()` 语义符合文档
4. 10 类 consequence 都有 schema + semantic validation
5. visible consequence 能进入 SceneBus，并被 P80 GM narration 消费
6. NarrativePlanner 能看到 Osiris 当格输出
7. `schedule_event` 在真实多格流程中可工作
8. LLM 失败不会拖垮 settlement

## 13. 执行建议

建议严格按三阶段推进，但从现在开始就遵守目标态契约。

也就是说：

- 第一阶段不是随便做一个“能跑就行”的版本
- 而是只实现目标态中的一部分，同时不制造未来要推翻的协议债务

下一轮直接从 Phase 1 开始：

1. `should_skip` 回正
2. `summary/snapshot` 扩充
3. visible consequence -> SceneBus
4. telemetry 补齐

这是最小但正确的起点。
