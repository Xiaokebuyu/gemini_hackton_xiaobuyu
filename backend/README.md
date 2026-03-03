**中文** | [English](README_EN.md)

# AI CRPG Game Engine Backend

AI 驱动的 CRPG 游戏后端（类博德之门 3 风格），采用**六边形纯 Python 内核**架构——`game_core` 层零外部依赖，所有外部能力（LLM、持久化、图谱）通过适配器端口注入，兼顾可测性与可扩展性。

## 技术栈

| 层 | 技术 |
|---|------|
| Web 框架 | FastAPI 0.109 + uvicorn |
| 数据存储 | Google Cloud Firestore / 本地 JSON |
| AI 模型 | Google Gemini Flash / Pro（`google-genai`）|
| 图算法 | NetworkX 3.2（世界知识图谱）|
| 令牌计数 | tiktoken |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境

创建 `.env` 文件：

```env
GOOGLE_API_KEY=your_gemini_api_key
# 可选：Firestore（不配置则退回本地 JSON 持久化）
GOOGLE_APPLICATION_CREDENTIALS=./firebase-credentials.json
```

### 3. 运行服务

```bash
# 开发模式
uvicorn app.main:app --reload --port 8000
```

服务启动后访问：
- API: http://localhost:8000/api/game/worlds
- 文档: http://localhost:8000/docs
- 健康检查: http://localhost:8000/health

### 4. 初始化世界数据

世界数据以结构化 JSON 形式存放于 `data/<world_id>/structured_new/`，服务启动时通过 `world_data_loader` 自动加载。

## 项目结构

```
backend/
├── app/
│   ├── main.py                        # FastAPI 入口（路由挂载）
│   ├── deps.py                        # 单例构建与依赖注入（组合根）
│   ├── api_models.py                  # 请求/响应 Pydantic 模型
│   ├── interaction_service.py         # 应用层：presence 验证 + 执行编排
│   ├── interaction_views.py           # Snapshot 视图构建器（商店/任务/对话等）
│   ├── agent_orchestration.py         # Agentic 会话编排服务
│   ├── llm_gemini.py                  # Gemini LLM 适配器
│   ├── memory_retriever_impl.py       # 图谱记忆检索适配器
│   ├── world_knowledge_graph.py       # 世界知识图谱（NetworkX）
│   ├── world_data_loader.py           # 世界结构化数据加载器
│   ├── world_seed.py                  # 世界目录注册
│   ├── evaluators.py                  # 条件评估器
│   ├── narrators.py                   # 叙述生成器
│   ├── routers/
│   │   ├── sessions.py                # 会话管理路由
│   │   ├── character.py               # 角色创建与面板路由
│   │   ├── panels.py                  # 背包/地图/任务面板路由
│   │   └── gameplay.py                # 游戏动作与流式路由
│   └── game_core/                     # ★ 六边形纯 Python 内核（零外部依赖）
│       ├── runtime.py                 # GameRuntime（世界加载 + Session 工厂）
│       ├── bootstrap.py               # 默认组件装配
│       ├── state/                     # 状态层
│       │   ├── base.py                # StateContainer + StateDelta
│       │   ├── delta.py               # StateChange 应用逻辑
│       │   └── slices/                # 10 个 StateSlice
│       │       ├── time.py            #   游戏时间
│       │       ├── player.py          #   玩家位置/属性
│       │       ├── area.py            #   当前区域与子地点
│       │       ├── quests.py          #   任务状态
│       │       ├── scene.py           #   场景描述
│       │       ├── relations.py       #   NPC 关系阶段 + 好感度
│       │       ├── flags.py           #   全局标志位（schemaless）
│       │       ├── events.py          #   事件历史
│       │       ├── party.py           #   队伍成员
│       │       └── narrative_plan.py  #   叙事计划状态
│       ├── content/                   # 内容层
│       │   ├── world.py               # WorldInstance（聚合所有 Registry）
│       │   └── registries/            # 10 个 ContentRegistry
│       │       ├── characters.py      #   NPC 定义
│       │       ├── items.py           #   道具/装备
│       │       ├── skills.py          #   技能/法术
│       │       ├── classes.py         #   职业
│       │       ├── monsters.py        #   怪物
│       │       ├── maps.py            #   地图与区域
│       │       ├── factions.py        #   势力
│       │       ├── lore.py            #   世界观条目
│       │       ├── quests.py          #   任务定义
│       │       └── tag.py             #   标签系统
│       ├── rules/                     # 规则层
│       │   ├── engine.py              # RulesEngine（分发 Command → Handler）
│       │   ├── models.py              # Command / ExecuteResult / Roll
│       │   ├── handler_utils.py       # Handler 共用工具
│       │   └── handlers/              # 13+ CommandHandler
│       │       ├── combat.py          #   战斗（攻击/防御/位移/逃跑等）
│       │       ├── skill_check.py     #   技能检定/豁免/对抗
│       │       ├── navigation.py      #   区域移动与子地点进出
│       │       ├── inventory.py       #   背包（拾取/丢弃/装备/使用）
│       │       ├── economy.py         #   贸易（买/卖）
│       │       ├── growth.py          #   成长（经验/升级/ASI/子职业）
│       │       ├── rest.py            #   休息（短/长）
│       │       ├── crime.py           #   犯罪（盗窃/开锁）
│       │       ├── encounter.py       #   遭遇战触发
│       │       ├── container.py       #   容器操作
│       │       ├── world_state.py     #   世界状态变更（标志/调度/传言）
│       │       ├── status_effect.py   #   状态效果
│       │       ├── spell.py           #   施法系统（准备/施放/专注/效果）
│       │       ├── discovery.py       #   探索发现
│       │       ├── hostile_area.py    #   危险区域进入
│       │       ├── interactable.py    #   可交互物体
│       │       └── proficiency.py     #   熟练度检定
│       ├── orchestration/             # 编排层
│       │   ├── tick_coordinator.py    # TickCoordinator（Session tick 生命周期）
│       │   ├── pipeline.py            # PipelineOrchestrator（三阶段管线）
│       │   ├── action_dispatcher.py   # ActionDispatcher（action_type → Command）
│       │   ├── context_assembler.py   # ContextAssembler（L1-L7 上下文组装）
│       │   ├── defaults.py            # 默认注册表（50+ action, 14 Hook）
│       │   ├── event_engine.py        # 事件触发引擎
│       │   ├── interaction.py         # 交互 presence/precondition 验证
│       │   ├── npc_interaction.py     # NPC 交互编排
│       │   ├── private_chat.py        # 私聊流编排
│       │   ├── settlement.py          # SettlementContext
│       │   ├── scene_bus.py           # SceneBus（场景信号总线）
│       │   ├── shared_context.py      # SharedContext（一次 tick 共享数据）
│       │   ├── models.py              # PipelineResult / SSEEvent / StructuredAction
│       │   └── hooks/                 # 14 个 SettlementHook
│       │       ├── encounter.py       #   遭遇战触发
│       │       ├── event_condition.py #   事件条件评估
│       │       ├── relationship.py    #   关系阶段推进
│       │       ├── gm_narration.py    #   GM 叙述生成（LLM）
│       │       ├── ai_osiris.py       #   AI Osiris 死亡/复活系统
│       │       ├── narrative_planner.py #  叙事计划更新
│       │       ├── npc_schedule.py    #   NPC 日程调度
│       │       ├── private_chat_trigger.py # 私聊触发
│       │       ├── scheduled_event.py #   定时事件
│       │       ├── status_effect.py   #   状态效果结算
│       │       ├── time_advance.py    #   时间推进
│       │       ├── dynamic_sub_area_expiry.py # 动态子区域过期
│       │       └── scene_reset.py     #   场景重置
│       ├── narrative/                 # 叙事层（LLM 集成）
│       │   ├── executor.py            # AgenticExecutor（单次 + 多轮 LLM 循环）
│       │   ├── registry.py            # RoleToolRegistry（角色工具注册）
│       │   ├── gm_tools.py            # GM 工具集
│       │   ├── character_tools.py     # NPC / 队友工具集
│       │   ├── instance_manager.py    # NPC 实例管理器
│       │   ├── context.py             # AgentContext
│       │   ├── context_builder.py     # 上下文构建器
│       │   ├── context_window.py      # 上下文窗口管理
│       │   ├── memory_retriever.py    # 记忆检索 Protocol
│       │   ├── models.py              # AgentResult / ToolResult
│       │   ├── role_proxy.py          # 角色代理
│       │   └── tools.py               # AgentTool 基类
│       ├── adapters/                  # 适配器层（端口协议 + 默认实现）
│       │   ├── inbound.py             # InputPort + FastAPIInputPort（归一化）
│       │   ├── outbound.py            # OutputPort Protocol
│       │   ├── persistence.py         # PersistencePort Protocol
│       │   ├── session_store.py       # Session 持久化（Firestore / 本地）
│       │   ├── local_persistence.py   # 本地 JSON 持久化
│       │   ├── firestore_persistence.py # Firestore 持久化
│       │   ├── llm.py                 # LlmPort Protocol
│       │   ├── memory_graph_port.py   # 记忆图谱端口
│       │   └── presentation.py        # SSE 格式化工具
│       └── planning/                  # 规划层（动态子区域生成）
│           ├── planner.py             # 区域规划器
│           ├── dynamic_sub_area.py    # 动态子区域
│           └── models.py              # 规划数据模型
├── tests/                             # 测试（基线 290+ passed）
├── data/                              # 世界数据（Goblin Slayer 等）
└── requirements.txt
```

## 核心架构

### 六边形架构分层

```
┌─────────────────────────────────────────────────────────┐
│  应用层（app/）                                           │
│  FastAPI 路由 → InteractionService → InteractionViews    │
├─────────────────────────────────────────────────────────┤
│  适配器层（game_core/adapters/）                          │
│  FastAPIInputPort · LlmPort · PersistencePort · OutputPort│
├─────────────────────────────────────────────────────────┤
│  编排层（game_core/orchestration/）                       │
│  TickCoordinator → PipelineOrchestrator                  │
│  ActionDispatcher · ContextAssembler · SettlementHooks   │
├─────────────────────────────────────────────────────────┤
│  规则层（game_core/rules/）                               │
│  RulesEngine → 16 CommandHandler                         │
├─────────────────────────────────────────────────────────┤
│  内容层（game_core/content/）                             │
│  WorldInstance + 10 ContentRegistry                      │
├─────────────────────────────────────────────────────────┤
│  状态层（game_core/state/）                               │
│  StateContainer + 10 StateSlice + StateDelta             │
└─────────────────────────────────────────────────────────┘
```

### 请求数据流

```
玩家输入（HTTP）
    │
    ▼
FastAPIInputPort.process_action()
    │  归一化为 execution 指令（resolved / rejected）
    ▼
InteractionService
    │  presence 验证（NPC/board 是否在场）
    │  precondition 验证（意图、道具、任务）
    ▼
TickCoordinator.process()
    │
    ├─► PipelineOrchestrator
    │       │
    │       ├─► ContextAssembler（L1-L7 上下文组装）
    │       │
    │       ├─► ActionDispatcher（action_type → Command）
    │       │
    │       └─► RulesEngine.execute(Command, state, world)
    │               │  调用对应 CommandHandler
    │               └─► StateDelta（状态变更描述）
    │
    ├─► after_engine 钩子（PipelineHook）
    │
    └─► SettlementHooks（按 priority 顺序）
            ScheduledEventHook → StatusEffectHook → AIOsirisHook
            → NarrativePlannerHook → EncounterHook → EventConditionHook
            → NpcScheduleHook → RelationshipHook → PrivateChatTriggerHook
            → TimeAdvanceHook → DynamicSubAreaExpiryHook
            → GmNarrationHook（LLM 叙述）→ SceneBusResetHook
    │
    ▼
PipelineResult → SSE 事件流 → 前端
```

### 十大状态切片（StateSlice）

| 切片 | 文件 | 职责 |
|------|------|------|
| TimeSlice | `time.py` | 游戏时间（轮/小时/天） |
| PlayerSlice | `player.py` | 玩家位置、属性、HP、法术位 |
| AreaSlice | `area.py` | 当前区域与子地点状态 |
| QuestSlice | `quests.py` | 任务状态（active/completed/retired） |
| SceneSlice | `scene.py` | 当前场景描述与 NPC 列表 |
| RelationsSlice | `relations.py` | NPC 关系阶段 + 好感度数值 |
| FlagSlice | `flags.py` | 全局标志位（schemaless dict） |
| EventsSlice | `events.py` | 近期事件历史 |
| PartySlice | `party.py` | 队伍成员列表 |
| NarrativePlanSlice | `narrative_plan.py` | 叙事计划与节拍状态 |

### 十大内容注册表（ContentRegistry）

| 注册表 | 内容 |
|--------|------|
| CharacterRegistry | NPC 定义（属性、技能、初始状态） |
| ItemRegistry | 道具/装备（类型、效果、价格） |
| SkillRegistry | 技能/法术（效果、消耗、射程） |
| ClassRegistry | 职业（特性、成长曲线） |
| MonsterRegistry | 怪物（CR、掉落、行为） |
| MapRegistry | 地图与区域（连通关系、子地点） |
| FactionRegistry | 势力（声望阈值、立场） |
| LoreRegistry | 世界观条目（百科） |
| QuestRegistry | 任务定义（阶段、条件、奖励） |
| TagRegistry | 标签系统（分类与查询） |

### 叙事层（AgenticExecutor）

`AgenticExecutor` 支持两种模式：

- **单次模式** `run()`：执行预构建的 `tool_calls` 列表，适合结构化动作后处理
- **多轮 Agentic 循环** `run_agentic()`：LLM 自主选择工具调用，适合 GM 叙述、NPC 对话

`RoleToolRegistry` 按角色类型（gm / npc / teammate）分配工具集，LLM 只能看到对应角色的工具。

`InstanceManager` 管理 NPC 实例（上下文窗口 + 记忆检索），支持 LRU 淘汰。

## API 端点

### 世界与会话

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/api/game/worlds` | 列出所有世界 |
| GET | `/api/game/{world_id}/sessions` | 列出会话 |
| POST | `/api/game/{world_id}/sessions` | 创建会话 |
| POST | `/api/game/{world_id}/sessions/{sid}/load` | 加载/恢复会话 |
| DELETE | `/api/game/{world_id}/sessions/{sid}` | 删除会话 |

### 角色

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/game/{world_id}/character-creation/options` | 创建选项（职业/种族） |
| POST | `/api/game/{world_id}/sessions/{sid}/character` | 创建角色 |
| GET | `/api/game/{world_id}/sessions/{sid}/character` | 角色面板 |

### 面板（状态查询）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `.../inventory` | 背包面板 |
| GET | `.../map` | 地图面板 |
| GET | `.../quests` | 任务面板 |

### 游戏动作（均为 SSE 流式）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `.../navigate` | 导航动作（move_area / enter / leave） |
| POST | `.../action/stream` | 结构化动作（含 GM 叙述 SSE） |
| POST | `.../input/stream` | 文本指令（命令别名解析 + SSE） |
| POST | `.../interact/stream` | NPC / 任务板交互（SSE） |
| POST | `.../private_chat/stream` | 私聊流（SSE） |

## 配置

### 必需环境变量

| 变量 | 说明 |
|------|------|
| `GOOGLE_API_KEY` 或 `GEMINI_API_KEY` | Gemini API 密钥（无此密钥时 LLM 功能降级） |

### 可选环境变量

| 变量 | 说明 |
|------|------|
| `GOOGLE_APPLICATION_CREDENTIALS` | Firebase 凭证路径（不设则使用本地 JSON 持久化） |
| `GEMINI_FLASH_MODEL` | Flash 模型名称 |
| `GEMINI_PRO_MODEL` | Pro 模型名称 |

## 运行测试

```bash
# 在 backend/ 目录下运行全部测试
PYTHONPATH=. pytest --ignore=tests/test_api_shell.py --ignore=tests/test_interaction_service.py -v

# 运行单个测试文件
PYTHONPATH=. pytest tests/test_game_core_scaffold.py -v

# 按名称筛选
PYTHONPATH=. pytest tests/test_encounter_handler.py -k "test_name" -v
```

测试基线：290+ passed（`asyncio_mode = "auto"`）。

## 设计亮点

1. **六边形纯 Python 内核**：`game_core/` 零外部依赖，任何数据库/LLM/框架均可替换，单元测试不需 mock 基础设施
2. **StateDelta 不可变状态变更**：所有状态修改通过 `StateDelta` 描述，`StateContainer.apply()` 统一执行，便于回放、审计和撤销
3. **SettlementHook 链**：14 个有序 Hook 组成可插拔的后处理管线，每个 Hook 独立职责（关系/遭遇/叙述/时间等），新功能只需追加 Hook
4. **ActionDispatcher 注册表**：50+ action_type 到 CommandHandler 的映射集中管理，运行时可动态注册，无需修改 Pipeline 代码
5. **FastAPIInputPort 归一化**：适配器层将 HTTP 请求归一化为 `execution` 指令（resolved/rejected），game_core 不感知 HTTP 细节
6. **AgenticExecutor 双模式**：支持单次工具执行和多轮 LLM 自主循环，GM 叙述与 NPC 对话均可独立扩展
7. **RoleToolRegistry 角色隔离**：GM/NPC/队友工具集独立注册，LLM 上下文按角色裁剪，避免越权调用
8. **ContextAssembler L1-L7**：七层上下文（世界观/区域/场景/玩家/事件/规则结果/NPC 记忆）按需组装，叙述 LLM 始终获得最小充分上下文

## License

MIT
