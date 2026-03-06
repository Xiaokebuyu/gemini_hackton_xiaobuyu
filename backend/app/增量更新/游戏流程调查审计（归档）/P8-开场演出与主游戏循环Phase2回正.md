# Phase 2 开场演出与主游戏循环回正

## 本轮计划落地

1. 开场生成从模板流改回 GM Agent 流
   - `opening/stream` 不再直接依赖 `opening_views.py` 里的固定文案 builder 作为主路径
   - 开场主路径改为：构建 opening 专用上下文 → 调用 `AgenticExecutor.run(role="gm")` → 从 GM 工具结果提取 `gm_narration`、`gm_comment`、`dialogue_options`
   - `scene_change`、`character_enter`、`status_update` 仍由编排层基于当前场景快照确定性生成
   - `opening_views.py` 退化为降级 fallback，不再是默认开场生成器

2. 开场上下文补成正式的 GM opening context
   - 新增 opening 专用上下文组装入口，语义对齐文档中的 L0-L7 开场上下文
   - 上下文至少包含：
     - 世界常量与世界书
     - 当前章节/动态任务/开场线索
     - 起始区域与地点细节
     - 当前在场 NPC / 队友 / 可去地点
     - 当前时间段与玩家角色关键信息
     - SceneBus 当前可见内容（开场前通常为空或仅含 bootstrap 信息）
   - opening context 不复用“动作后反应”的简化 GM prompt，而使用单独的 opening prompt

3. 开场 SSE 契约收口为“6 个主事件 + 失败降级”
   - 视觉主序列固定为：
     1. `scene_change`
     2. `gm_narration`
     3. `gm_comment`（可选）
     4. `character_enter` × N
     5. `status_update`
     6. `dialogue_options`
   - `location_overview` 若仍需保留，只作为场景状态同步事件，不再参与开场演出节奏定义
   - opening 失败时保持文档里的降级原则：
     - 仍然推送 `scene_change`
     - 仍然推送 `status_update`
     - 仍然给出可操作 `dialogue_options`
     - 玩家可以继续操作，只是开场文本退化

4. 前端把 opening 真正编排成演出，而不是“收到就立刻渲染完”
   - `gm_narration` / `gm_comment` 改为前端本地打字机 reveal，而不是整段一次性落盘
   - `character_enter` 改成队列式依次入场，按固定节奏 stagger
   - `status_update` 与 `dialogue_options` 延后展示，避免和前序文本/立绘同时挤进画面
   - `scene_change` 仍保留现有遮罩转场，但 opening 期间由统一 choreography 驱动
   - opening 演出结束前，不允许普通 explore 选项或其它 overlay 抢占焦点

5. `PipelineOrchestrator` 恢复显式 A/B/C 三阶段语义
   - Stage A：`ContextAssembler` + `ActionDispatcher` + `RulesEngine` + 前置 `EventEngine.check_conditions()`
   - Stage B：主循环动作后的 Agent round，顺序固定为 `GM -> nearby NPC -> teammate`
   - Stage C：后置 `EventEngine.check_conditions()` + `after_agents` 扩展点 + 事件汇总
   - `TickCoordinator` 只保留：
     - 调用 pipeline
     - 应用 delta
     - 时间累积
     - settlement
     - 持久化边界
   - 不再把 B 阶段主体放在 `TickCoordinator.agent_round_hooks` 里当“外接 runner”

6. EventEngine 调用时机按文档补齐
   - 主管线恢复两次检查：
     - A6：规则执行后立即做前置条件检查
     - C1：Agent round 结束后再做一次后置条件检查
   - P50 `EventConditionHook` 继续保留，用于跨格 settlement 检查
   - 目标是恢复文档里的“格内即时触发 + 格边结算兜底”双层语义，而不是只在 P50 才检查事件

7. 主循环里的 NPC/队友反应回归文档约束
   - `/input/stream`、`/action/stream` 的动作后反应按 B 阶段执行
   - `GM` 仍是默认第一观察者
   - nearby NPC 只处理“对玩家动作作出场景级反应”，不替代 `/interact/stream` 专用对话管线
   - 队友仍是可选插话，不要求每轮发言
   - `/interact/stream` 与 `/private_chat/stream` 继续保持独立专用管线，不并入主循环动作管线

8. 开场与主循环的选项来源统一到工具化协议
   - 开场首组选项优先来自 GM `suggest_options`
   - 主循环动作后的 `dialogue_options` 继续来自既有工具化选项链路
   - 保持 `dispatch` 为前端唯一可执行语义，不再把开场选项当作纯静态按钮文案

9. 主循环结果流顺序收口
   - `/action/stream`、`/input/stream` 的公共顺序目标为：
     1. 规则执行结果事件（如 `dice_roll` / `action_result`）
     2. B 阶段 Agent 反应（`gm_narration` / `gm_comment` / `npc_response` / `teammate_response`）
     3. C 阶段事件触发结果
     4. `dialogue_options` 或 `location_overview`
     5. `stream_end`
   - 避免 `location_overview`、旧选项或 overlay snapshot 抢先打断 narrative 顺序

## 明确延后

1. `AdminCoordinator` 单例类本体
   - 本轮不强行补一个名义上的 `AdminCoordinator`
   - 继续保持当前扁平路由入口，但把生命周期职责收口到现有 coordinator/runtime/pipeline 组件

2. 会话级 `TickCoordinator` 常驻内存缓存
   - 当前仍允许“每次请求从 SaveStore 恢复 runtime”
   - 单用户 shell 下先不引入 coordinator cache
   - 多用户并发性能问题单独作为后续专题处理

3. CompanionInstance 级事件分发系统
   - 本轮不补独立“同伴事件分发器”或 `CompanionInstance`
   - 先把文档里要求的 B/C 阶段语义收口到现有队友反应路径

4. PipelineHook 生态化扩展
   - `after_engine` / `after_agents` 扩展点保留
   - 但本轮不追求把所有现有后置逻辑都改造成独立 PipelineHook 类

5. 更复杂的 opening 视觉资产
   - 不引入新视频、复杂粒子系统、独立 CG 页面
   - 以当前背景、遮罩、立绘、文字面板完成开场演出编排

6. 多用户会话隔离与 ownership
   - Phase 2 不处理 `user_id`、会话归属与多端同时在线问题
   - 继续按当前单用户 shell 假设运行

## 目标契约

1. 新游戏开场
   - `POST /character` → `phase=opening_ready`
   - 前端进入 `/play` 自动触发 `POST /opening/stream`
   - `opening/stream` 负责：
     - 运行 opening planner bootstrap（如需要）
     - 生成 GM 开场叙述
     - 按固定顺序推送 opening SSE
     - 完成后 session 进入 `active`

2. 开场失败降级
   - GM 开场生成失败不阻塞进入游戏
   - 至少仍要让玩家拿到：
     - 背景切换
     - HUD
     - 首组可操作选项
   - 前端必须把这视为“可玩但叙述降级”，不是初始化失败

3. 主循环动作入口
   - `/input/stream` 与 `/action/stream` 共用 `TickCoordinator.process()`
   - `TickCoordinator.process()` 内部执行顺序为：
     - Pipeline Stage A
     - Pipeline Stage B
     - Pipeline Stage C
     - 累积时间
     - settlement
     - 应用层持久化

4. 专用对话入口
   - `/interact/stream` 继续走 6 步 NPC 交互管线
   - `/private_chat/stream` 继续走 4 步私聊管线
   - 它们仍然属于同一时间格生命周期，但不与主动作管线混成一套 prompt/agent 逻辑

5. 前端开场表现
   - 玩家首次进入游戏看到的是“已编排的开场演出”，不是普通游戏页瞬时填充完数据
   - 开场结束后才进入常规 explore/dialogue 状态
   - 继续存档恢复则不重复播放完整开场，只按 `resume` 语义恢复
