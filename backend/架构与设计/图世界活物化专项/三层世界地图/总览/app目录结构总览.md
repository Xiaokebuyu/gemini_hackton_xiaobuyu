# app/ 目录结构总览

> 更新时间：2026-02-24
> 口径：以当前仓库 `app/` 实际代码为准
> 架构：V4 Runtime Pipeline 三层架构（L1 编排 → L2 Agentic → L3 世界引擎）

---

## 顶层文件

| 文件 | 用途 |
|------|------|
| `main.py` | FastAPI 入口，启动/关闭钩子（MCP 探测、NPC 实例刷盘、CORS） |
| `config.py` | `Settings` 配置类，所有环境变量（Gemini 模型、MCP 传输、Firestore、NPC 分层等） |
| `dependencies.py` | `@lru_cache` 单例注入（`get_coordinator()` 等） |

---

## `agentic/` — L1 Agentic 基础设施（LLM 工具调用引擎）

| 文件 | 用途 |
|------|------|
| `agentic_executor.py` | **统一 Agent 执行器**，封装 LLM agentic 循环 + 工具录制 + SSE 推送，GM/NPC/队友共用 |
| `immersive_tools.py` | **30+ 沉浸式工具** + `AgenticContext` + `FEELING_MAP` 情感翻译 + `bind_tool()` 绑定机制 |
| `role_registry.py` | 按 `(role, traits)` 映射工具集：gm / npc / teammate 三种角色 |
| `prompt_loader.py` | 加载 `flash_agentic_system.md` 模板提示词 |
| `passerby_llm.py` | 路人 NPC 轻量响应（无工具、无 thinking） |
| `memory_graphizer.py` | 对话/事件 → 图谱节点（Flash LLM 结构化提取） |
| `event_llm_service.py` | 自然语言事件 3 步管线（parse → encode → transform_perspective） |
| `teammate/response_service.py` | 队友每回合自动决策 + AgenticExecutor 响应生成 |

---

## `runtime/` — V4 运行时层（会话状态 + 上下文组装）

| 文件 | 用途 |
|------|------|
| `game_runtime.py` | 全局单例，管理 `WorldInstance` 缓存（每世界懒加载 + 锁） |
| `world_instance.py` | 世界静态数据注册表（地图/角色/章节/怪物/物品，启动时并行批量加载） |
| `session_runtime.py` | **会话运行时门面**，统一管理状态/队伍/历史/角色；`restore()` / `persist()` / `advance_time()` |
| `context_assembler.py` | 分层上下文组装：L0 常量 → L1 玩家 → L2 地点 → L3 事件 → L4 叙事 → 记忆 |
| `event_machine.py` | 事件状态机 + 奖励发放 + tick 编排（调用 `BehaviorEngine.tick()`） |
| `memory_ops.py` | 记忆操作辅助 |
| `narrative_ops.py` | 叙事操作辅助 |
| `stat_ops.py` | 属性修改操作 |
| `models/world_constants.py` | 世界常量（阵营/地理） |
| `models/area_state.py` | 区域定义模型 |
| `models/layered_context.py` | 分层上下文结构 |

---

## `services/` — 业务逻辑层

### `services/admin/` — 管线编排

| 文件 | 用途 |
|------|------|
| `admin_coordinator.py` | **入口协调器单例**，委托到 PipelineOrchestrator，暴露 `process_player_input_v3()` |
| `pipeline_orchestrator.py` | **V4 三阶段编排**（A 上下文 → B Agentic → C 后处理）+ NPC interact + 私聊流 |
| `event_service.py` | 世界级事件持久化（GM 事件 + 自然语言事件入图） |
| `state_manager.py` | 会话状态内存快照 + StateDelta 增量追踪 |
| `world_runtime.py` | 薄壳：`start_session()` + `get_current_location()` |

### 其他服务

| 文件 | 用途 |
|------|------|
| `llm_service.py` | **Gemini 3 API 封装**，`generate_text()` / `agentic_chat()`（工具调用）/ thinking 支持 |
| `mcp_client_pool.py` | **MCP 连接池单例**，per-server 调用锁 + 健康检查 + 自动重连 + 工具级超时 |
| `passerby_service.py` | 路人 NPC 管理（生成/对话/清理） |
| `session_history.py` | 按轮次消息记录（Firestore 持久化） |
| `area_navigator.py` | 区域导航 + 子地点切换 |
| `world_api.py` | 统一世界操作接口 |
| `image_generation_service.py` | 图片生成服务 |

---

## `world/` — L3 世界引擎（11 个子系统）

| 子目录 | 用途 |
|--------|------|
| `graph/` | **世界图谱核心**：`WorldGraph`（NetworkX 内存图 + 4 索引）、`GraphBuilder`（从 WorldInstance 构建）、`WorldNode`/`WorldEvent` 模型、快照序列化 |
| `events/` | **事件状态机**：`BehaviorEngine.tick()` 心跳驱动、`ConditionEvaluator` 条件判定、`ActionExecutor` 动作执行、`EventBus` 发布订阅、`EventPropagator` 涟漪传播 |
| `memory/` | **记忆系统**：`activation.py` 扩散激活算法、`graph_writer.py` 图谱写入、`recall.py` 记忆召回、`recorder.py` 记忆记录 |
| `npc/` | **NPC 实例池**：`InstanceManager`（LRU 20 上限）+ 双层认知（`ContextWindow` 200K 工作记忆 + MemoryGraph 潜意识）、`NPCReactor` 相关度推荐、`visibility.py` 可见性管理 |
| `combat/` | **D&D 战斗系统**：`CombatEngine` 回合制引擎、`AIOpponent` 敌人 AI、骰子/法术/技能/空间/效果/怪物注册表 |
| `narrative/` | **叙事系统**：`NarrativeService` 主线/章节/目标进度追踪、`narrative_parser.py` YAML 解析 |
| `party/` | **队伍系统**：`PartyService` 创建/解散/成员管理 + 位置同步 + 事件分发 |
| `player/` | **玩家角色**：`CharacterService` 角色创建/种族加成/点购验证、`ability_check.py` d20 判定、D&D 常量/装备/属性 |
| `intent/` | **意图系统**：`IntentExecutor` 预执行高置信度机械意图（导航/检查/使用物品） |
| `scene/` | **场景总线**：`SceneBus` 当轮场景事件日志（speech/action/engine_result），每轮清空 |
| `time/` | **时间管理**：`GameTime` 日/时/分推进、`TimePeriod`（黎明/白天/黄昏/夜晚） |

---

## `mcp/` — MCP 工具服务器

| 文件 | 用途 |
|------|------|
| `game_tools_server.py` | 游戏 MCP 服务器（FastMCP），注册 8 个工具模块，支持 stdio/HTTP 传输 |
| `tools/character_tools.py` | 角色工具 |
| `tools/inventory_tools.py` | 背包工具 |
| `tools/narrative_tools.py` | 叙事工具 |
| `tools/navigation_tools.py` | 导航工具 |
| `tools/npc_tools.py` | NPC 工具 |
| `tools/party_tools.py` | 队伍工具 |
| `tools/passerby_tools.py` | 路人工具 |
| `tools/time_tools.py` | 时间工具 |

---

## `combat/`（顶层）— 战斗 MCP 服务器

| 文件 | 用途 |
|------|------|
| `combat_mcp_server.py` | 战斗 MCP 服务器，暴露 start/action/result 工具 |
| `llm_orchestrator_example.py` | LLM 编排示例（离线） |
| `test_combat_cli.py` | 战斗 CLI 测试（离线） |

---

## `models/` — 24 个 Pydantic 数据模型

| 分类 | 文件 | 说明 |
|------|------|------|
| 核心协议 | `admin_protocol.py` | IntentType / CoordinatorResponse / AgenticResult |
| | `game.py` | GameSessionState |
| | `session.py` | Session / SessionCreate |
| 角色 | `player_character.py` | D&D 角色表（种族/职业/属性/技能） |
| | `character_creation.py` | 角色创建选项 |
| | `character_profile.py` | 角色简介 |
| 队伍 | `party.py` | Party / PartyMember / TeammateRole / TeammateResponseDecision |
| 事件 | `event.py` | Event / EventType / EventContent |
| 叙事 | `narrative.py` | Mainline / Chapter / ChapterObjective / NarrativeProgress |
| 图谱 | `graph_scope.py` | GraphScope（6 种作用域寻址：world/chapter/area/location/character/camp） |
| | `graph_nodes.py` | ChapterNode / AreaNode / LocationNode / CharacterNode / EventNode2 |
| | `graph_elements.py` | EventGroupNode / EventNode / ExtractedElements |
| | `graph.py` | 图谱基础模型 |
| | `graph_schema.py` | 图谱 Schema 定义 |
| NPC | `npc_instance.py` | NPCConfig / NPCInstanceState / GraphizeTrigger |
| | `context_window.py` | WindowMessage / ContextWindowState（200K token 容量） |
| 状态 | `state_delta.py` | GameState / StateDelta（增量变更追踪） |
| | `activation.py` | SpreadingActivationConfig（扩散激活配置） |
| 其他 | `game_action.py` | 游戏动作模型 |
| | `flash.py` | Flash 模型定义 |
| | `message.py` | 消息模型 |
| | `passerby.py` | 路人模型 |
| | `topic.py` | 话题模型 |

---

## `routers/` — FastAPI 路由

`game_v2.py` 是唯一路由器，挂载于 `/api/game`，包含 37 个端点：

| 分组 | 端点 |
|------|------|
| 世界 | `GET /worlds` |
| 会话 | `POST /{world_id}/sessions`, `GET /{world_id}/sessions`, `GET .../sessions/{session_id}`, `POST .../sessions/{session_id}/resume` |
| 角色 | `GET .../character-creation/options`, `POST .../character`, `GET .../character` |
| 导航 | `GET .../location`, `POST .../navigate`, `GET .../sub-locations`, `POST .../sub-location/enter`, `POST .../sub-location/leave` |
| 时间 | `GET .../time`, `POST .../time/advance`, `POST .../advance-day` |
| 游戏 | `POST .../input`（主入口）, `POST .../input/stream`（SSE）, `POST .../scene`, `GET .../context` |
| NPC 交互 | `POST .../interact/stream`（SSE：NPC→GM 观察→队友旁观→对话选项） |
| 对话 | `POST .../dialogue/start`, `POST .../dialogue/end`, `POST .../private-chat/stream`（SSE 私聊） |
| 战斗 | `POST .../combat/trigger`, `POST .../combat/start`, `POST .../combat/action`, `POST .../combat/resolve` |
| 队伍 | `POST .../party`, `GET .../party`, `POST .../party/add`, `DELETE .../party/{character_id}`, `POST .../party/load` |
| 叙事 | `GET .../narrative/progress`, `GET .../narrative/flow-board`, `GET .../narrative/current-plan`, `GET .../narrative/available-maps` |
| 事件 | `POST /{world_id}/events/ingest`, `POST /{world_id}/events/ingest-natural` |
| 路人 | `GET .../passersby`, `POST .../passersby/spawn`, `POST .../passersby/dialogue` |
| 历史 | `GET .../history` |

---

## `tools/` — 离线工具 & CLI

| 文件/目录 | 用途 |
|-----------|------|
| `init_world_cli.py` | 世界数据提取 CLI（酒馆卡片 → 结构化文件，支持 Batch API） |
| `game_master_cli.py` | 交互式 GM 测试 |
| `gm_natural_cli.py` | GM 叙述测试 |
| `worldbook_graphizer/` | 统一世界数据提取管线（`unified_pipeline.py` + 地图/图谱/NPC 分类器） |
| `world_initializer/` | 世界初始化器（角色/地图/图谱预填充加载） |
| `batch/` | Gemini Batch API 管理 |
| `graph_*.py` | 图谱导入/索引/合并/审查工具 |

---

## `prompts/` — LLM 提示词模板

| 文件 | 用途 |
|------|------|
| `flash_agentic_system.md` | V4 Agentic 系统提示（工具调用 + 叙述生成） |
| `flash_cpu_system.md` | Flash CPU 系统提示（遗留，主路径已不依赖） |
| `teammate_agentic_system.md` | 队友 Agentic 系统提示 |
| `teammate_decision.md` | 队友决策提示 |
| `teammate_response.md` | 队友响应提示 |
| `travel_narration.md` | 旅行叙述提示 |
| `opening_narration.md` | 开场叙述提示 |
| `session_resume.md` | 会话恢复提示 |

---

## `utils/` — 工具函数

空包占位。

## `static/` — 静态资源

`agentic_trace_viewer.html` — Agentic 调用链可视化调试页。
