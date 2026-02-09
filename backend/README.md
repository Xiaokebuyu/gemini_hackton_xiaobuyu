**中文** | [English](README_EN.md)

# AI RPG Game Engine Backend

AI 驱动的互动式 TRPG 游戏后端，具备智能记忆管理、知识图谱和动态叙事编排能力。

采用 **Flash-Only v2 架构**——单一 Gemini Flash 模型在一次调用中完成意图分析、操作执行和叙述生成，兼顾低延迟与叙事连贯性。

## 技术栈

| 层 | 技术 |
|---|------|
| Web 框架 | FastAPI 0.109 |
| 数据存储 | Google Cloud Firestore |
| AI 模型 | Google Gemini 3 Flash / Pro |
| 图算法 | NetworkX 3.2 |
| 工具协议 | MCP (Model Context Protocol) 1.25 |
| 令牌计数 | tiktoken |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境

创建 `.env` 文件：

```env
GEMINI_API_KEY=your_gemini_api_key
GOOGLE_APPLICATION_CREDENTIALS=./firebase-credentials.json
```

将 Firebase 服务账号凭证放在项目根目录，命名为 `firebase-credentials.json`。

### 3. 运行服务

```bash
# 开发模式（默认 stdio 传输，MCP 子进程自动启动）
uvicorn app.main:app --reload --port 8000

# 或使用启动脚本
bash 启动服务/run_fastapi.sh
```

服务启动后访问：
- API: http://localhost:8000/api/game/worlds
- 文档: http://localhost:8000/docs
- 健康检查: http://localhost:8000/health

### 4. 初始化世界数据

```bash
# 从酒馆卡片 JSON 提取结构化世界数据
python -m app.tools.init_world_cli extract \
    --input "worldbook.json" \
    --output data/goblin_slayer/structured/ \
    --model gemini-3-pro-preview \
    --direct --relabel-edges --enrich-entities

# 加载到 Firestore
python -m app.tools.init_world_cli load \
    --world "goblin_slayer" \
    --data-dir data/goblin_slayer/structured/
```

## 项目结构

```
backend/
├── app/
│   ├── main.py                    # FastAPI 入口，生命周期管理
│   ├── config.py                  # 环境变量与模型配置
│   ├── dependencies.py            # 依赖注入（单例）
│   ├── models/                    # Pydantic 数据模型（24 个）
│   │   ├── admin_protocol.py      #   Flash 协议（IntentType, FlashOperation, AnalysisPlan）
│   │   ├── game.py                #   游戏状态（GamePhase, SceneState, PlayerInput/Response）
│   │   ├── narrative.py           #   叙事模型（Mainline, Chapter, StoryEvent, Condition）
│   │   ├── graph.py               #   图节点/边（MemoryNode, MemoryEdge）
│   │   ├── graph_scope.py         #   6 种图作用域统一寻址
│   │   ├── npc_instance.py        #   NPC 实例（双层认知模型）
│   │   ├── state_delta.py         #   增量状态变更
│   │   └── ...
│   ├── routers/
│   │   └── game_v2.py             # 统一路由，挂载于 /api/game
│   ├── services/
│   │   ├── admin/                 # 核心编排层（12 个文件）
│   │   │   ├── admin_coordinator.py   # 主编排器（入口）
│   │   │   ├── flash_cpu_service.py   # Flash 意图分析 + 操作执行
│   │   │   ├── story_director.py      # 两阶段事件评估
│   │   │   ├── condition_engine.py    # 8 种结构化条件 + FLASH_EVALUATE
│   │   │   ├── state_manager.py       # 内存快照 + StateDelta
│   │   │   ├── world_runtime.py       # 世界状态运行时
│   │   │   ├── event_service.py       # 结构化事件入图
│   │   │   ├── event_llm_service.py   # 自然语言事件 3 步管线
│   │   │   └── recall_orchestrator.py # 多作用域记忆召回
│   │   ├── memory_graph.py        # NetworkX 图容器（索引 + 查询）
│   │   ├── spreading_activation.py # 扩散激活算法
│   │   ├── memory_graphizer.py    # 对话自动入图（LLM 提取）
│   │   ├── graph_store.py         # Firestore 图持久化（GraphScope 路径解析）
│   │   ├── instance_manager.py    # NPC 实例池（LRU，双层认知）
│   │   ├── context_window.py      # 工作记忆滑动窗口（200K tokens）
│   │   ├── mcp_client_pool.py     # MCP 连接池（健康检查 + 自动重连）
│   │   ├── party_service.py       # 队伍管理
│   │   ├── teammate_response_service.py # 队友并发响应
│   │   └── ...
│   ├── mcp/                       # MCP 工具服务器
│   │   ├── game_tools_server.py   #   Game Tools（9 个工具模块）
│   │   └── tools/                 #   graph, narrative, navigation, npc, party,
│   │                              #   passerby, time, character, inventory
│   ├── combat/                    # D&D 风格战斗系统
│   │   ├── combat_engine.py       #   回合制战斗引擎
│   │   ├── combat_mcp_server.py   #   战斗 MCP 服务器
│   │   ├── ai_opponent.py         #   性格驱动的敌人 AI
│   │   ├── dice.py                #   d20 骰子系统
│   │   └── models/                #   Combatant, Action, CombatSession
│   ├── prompts/                   # LLM 提示词模板（10 个）
│   │   ├── flash_analysis.md      #   意图分析（严格 JSON 输出）
│   │   ├── flash_gm_narration.md  #   GM 叙述生成
│   │   ├── teammate_response.md   #   队友响应
│   │   └── ...
│   └── tools/                     # CLI 工具
│       ├── init_world_cli.py      #   世界数据提取 + 加载
│       ├── game_master_cli.py     #   交互式 GM 测试
│       └── worldbook_graphizer/   #   统一提取管线（支持 Batch API）
├── tests/                         # 46+ 测试文件
├── data/                          # 世界数据（Goblin Slayer）
├── 启动服务/                      # 启动脚本
└── requirements.txt
```

## 核心架构

### Flash-Only v2 数据流

入口：`AdminCoordinator.process_player_input_v2()`

```
玩家输入
    │
    ▼
┌─────────────────────────────────────────────┐
│ 1. 收集基础上下文                              │
│    世界状态 · 会话状态 · 场景 · 队伍 · 章节    │
└────────────────────┬────────────────────────┘
                     ▼
┌─────────────────────────────────────────────┐
│ 2. StoryDirector 预评估                       │
│    机械条件 → auto_fired_events              │
│    语义条件 → pending_flash_conditions        │
└────────────────────┬────────────────────────┘
                     ▼
┌─────────────────────────────────────────────┐
│ 3. Flash 一次性分析                            │
│    intent + operations + memory_seeds        │
│    + Flash 条件评估 + context_package         │
└────────────────────┬────────────────────────┘
                     ▼
         ┌───────────┴───────────┐
         ▼                       ▼
┌─────────────────┐   ┌─────────────────────┐
│ 4a. 记忆召回     │   │ 4b. 执行 Flash 操作  │
│ 扩散激活检索     │   │ MCP 工具调用         │
│ 多作用域合并     │   │ 状态更新             │
└────────┬────────┘   └──────────┬──────────┘
         └───────────┬───────────┘
                     ▼
┌─────────────────────────────────────────────┐
│ 5. StoryDirector 后评估                       │
│    合并结果 → fired_events + chapter_transition│
└────────────────────┬────────────────────────┘
                     ▼
┌─────────────────────────────────────────────┐
│ 6. Flash GM 生成叙述（2-4 句）                 │
│    完整上下文 + 执行摘要 + 记忆                 │
└────────────────────┬────────────────────────┘
                     ▼
┌─────────────────────────────────────────────┐
│ 7. 队友并发响应                                │
│    每个队友独立决策是否响应 → 生成回复           │
└────────────────────┬────────────────────────┘
                     ▼
┌─────────────────────────────────────────────┐
│ 8. 事件分发到队友图谱（视角转换）               │
└────────────────────┬────────────────────────┘
                     ▼
             CoordinatorResponse → 前端
```

### 六大核心系统

#### 1. NPC 双层认知架构

NPC 拥有两层独立的认知系统：

| 层 | 名称 | 实现 | 容量 | 用途 |
|----|------|------|------|------|
| 层1 | 同步工作记忆 | `ContextWindow` | 200K tokens | 实时对话上下文 |
| 层2 | 潜意识记忆图谱 | `MemoryGraph` + 扩散激活 | 无限 | 长期语义记忆 |

**自动图谱化**：当工作记忆达 90% → `MemoryGraphizer` 用 LLM 将旧消息提取为图谱节点 → 释放 token 空间。

**三层 NPC 模型**（`NPCTierConfig`）：

| 层级 | 思考 | 上下文窗口 | 记忆图谱 | 适用场景 |
|------|------|-----------|---------|---------|
| Passerby | 无 | 无 | 无 | 路人、群众 |
| Secondary | medium | 共享 | 有 | 次要角色 |
| Main | low | 完整 200K | 完整 + 扩散激活 | 主要角色 |

实例通过 `InstanceManager` 管理，LRU 淘汰（默认 20 实例），淘汰前强制图谱化保存。

#### 2. 知识图谱与记忆检索

**GraphScope 统一寻址**——6 种层级作用域：

```
world                          → 世界级知识
chapter(cid)                   → 章节叙事
area(cid, aid)                 → 区域信息
location(cid, aid, lid)        → 具体地点
character(char_id)             → 角色个人记忆
camp                           → 队伍共享知识
```

**扩散激活算法**（`spreading_activation.py`）：
1. 从种子节点出发，激活值 = 1.0
2. 沿边传播，每步衰减（0.9×）+ 跨视角/跨章节额外衰减
3. 高度数节点惩罚（> 10 条边）
4. 超过阈值的节点进入结果子图

**RecallOrchestrator** 并行加载多作用域图谱 → 合并 → 扩散激活 → 返回相关记忆。

#### 3. 故事导演与事件系统

**两阶段评估**：

| 阶段 | 时机 | 评估内容 | 输出 |
|------|------|---------|------|
| 预评估 | Flash 分析前 | 8 种机械条件 | `PreDirective`（auto_fired_events + pending_flash） |
| 后评估 | Flash 执行后 | 合并所有结果 | `StoryDirective`（fired_events + chapter_transition） |

**8 种结构化条件**：LOCATION / NPC_INTERACTED / TIME_PASSED / ROUNDS_ELAPSED / PARTY_CONTAINS / EVENT_TRIGGERED / OBJECTIVE_COMPLETED / GAME_STATE，支持 AND/OR/NOT 嵌套。

**节奏控制**（PacingConfig）：`subtle_environmental` → `npc_reminder` → `direct_prompt` → `forced_event`，确保剧情推进不突兀。

#### 4. 战斗系统

D&D 5e 风格的回合制战斗，**纯逻辑无 LLM**：

- **d20 判定**：攻击掷骰 + 加值 vs 目标 AC
- **先攻顺序**：d20 + DEX 修正
- **距离系统**：engaged / close / near / far / distant
- **AI 对手**：性格驱动（aggressive / defensive / tactical），影响目标选择和逃跑阈值
- **状态效果**：中毒、优势/劣势等

#### 5. MCP 工具层

两个 MCP 服务器通过 `MCPClientPool` 单例管理：

| 服务器 | 端口 | 工具模块 |
|--------|------|---------|
| Game Tools | 9101 | graph, narrative, navigation, npc, party, passerby, time, character, inventory |
| Combat | 9102 | combat engine, enemy templates, ability checks |

**传输支持**：stdio（默认）/ streamable-http / sse
**健康检查**：ping 探活 + 自动重连 + 30s 冷却
**工具超时**：默认 20s，`npc_respond` 90s

#### 6. 队伍系统

- 队友每回合并发决策是否响应
- 位置随玩家导航自动同步
- 事件按视角转换后分发到各队友图谱
- 支持角色：LEADER / SUPPORT / SCOUT / TANK 等

## API 端点

所有端点挂载于 `/api/game`：

### 世界与会话

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/worlds` | 列出所有世界 |
| POST | `/{world_id}/sessions` | 创建会话 |
| GET | `/{world_id}/sessions` | 列出会话 |
| POST | `/{world_id}/sessions/{sid}/resume` | 恢复会话 |

### 游戏核心

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `.../input` | 主入口（JSON 响应） |
| POST | `.../input/stream` | 主入口（SSE 流式） |
| POST | `.../scene` | 更新场景 |
| GET | `.../context` | 获取游戏上下文 |

### 角色与导航

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `.../character-creation/options` | 创建选项 |
| POST | `.../character` | 创建角色 |
| GET | `.../location` | 当前位置 |
| POST | `.../navigate` | 导航 |
| POST | `.../sub-location/enter` | 进入子位置 |

### 对话与战斗

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `.../dialogue/start` | 开始 NPC 对话 |
| POST | `.../dialogue/end` | 结束对话 |
| POST | `.../private-chat/stream` | 私聊（SSE） |
| POST | `.../combat/trigger` | 触发战斗 |
| POST | `.../combat/action` | 战斗行动 |
| POST | `.../combat/resolve` | 结算战斗 |

### 队伍与叙事

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `.../party` | 创建队伍 |
| POST | `.../party/add` | 添加队友 |
| GET | `.../narrative/progress` | 叙事进度 |
| POST | `.../narrative/trigger-event` | 触发事件 |

### 其他

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `.../time` | 游戏时间 |
| POST | `.../time/advance` | 推进时间 |
| GET | `.../passersby` | 场景路人 |
| POST | `.../passersby/dialogue` | 与路人交谈 |
| GET | `.../history` | 会话历史 |
| POST | `/{world_id}/events/ingest` | 结构化事件入图 |
| POST | `/{world_id}/events/ingest-natural` | 自然语言事件 |

## Firestore 数据结构

```
worlds/{world_id}/
├── graphs/world/nodes/, edges/                                     ← GraphScope.world()
├── chapters/{cid}/graph/nodes/, edges/                             ← GraphScope.chapter()
├── chapters/{cid}/areas/{aid}/graph/nodes/, edges/                 ← GraphScope.area()
├── chapters/{cid}/areas/{aid}/locations/{lid}/graph/nodes/, edges/ ← GraphScope.location()
├── characters/{char_id}/nodes/, edges/, instances/, dispositions/  ← GraphScope.character()
├── camp/graph/nodes/, edges/                                       ← GraphScope.camp()
├── maps/{map_id}/locations/{location_id}/
├── sessions/{session_id}/state/, events/
└── mainlines/{mainline_id}/...
```

## 配置

### 必需环境变量

| 变量 | 说明 |
|------|------|
| `GEMINI_API_KEY` | Gemini API 密钥 |
| `GOOGLE_APPLICATION_CREDENTIALS` | Firebase 凭证路径 |

### 模型配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GEMINI_FLASH_MODEL` | `gemini-3-flash-preview` | 主力 Flash 模型 |
| `ADMIN_FLASH_MODEL` | `gemini-3-flash-preview` | Admin 层模型 |
| `ADMIN_FLASH_THINKING_LEVEL` | `high` | Flash 思考级别 |
| `NPC_PASSERBY_MODEL` | (同 flash) | 路人 NPC |
| `NPC_SECONDARY_MODEL` | (同 flash) | 次要 NPC |
| `NPC_MAIN_MODEL` | (同 flash) | 主要 NPC |

### MCP 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MCP_TOOLS_TRANSPORT` | `stdio` | Game Tools 传输方式 |
| `MCP_COMBAT_TRANSPORT` | `stdio` | Combat 传输方式 |
| `MCP_TOOL_TIMEOUT_SECONDS` | `20` | 默认工具超时 |
| `MCP_NPC_TOOL_TIMEOUT_SECONDS` | `90` | NPC 工具超时 |

### 实例池配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `INSTANCE_POOL_MAX_INSTANCES` | `20` | NPC 实例池上限 |
| `INSTANCE_POOL_CONTEXT_WINDOW_SIZE` | `200000` | 上下文窗口 token 数 |
| `INSTANCE_POOL_GRAPHIZE_THRESHOLD` | `0.8` | 图谱化触发阈值 |

## 运行测试

```bash
# 全部测试
pytest -v

# 单个测试文件
pytest tests/test_spreading_activation.py -v

# E2E 测试（需先启动 MCP HTTP 服务）
bash 启动服务/run_mcp_services.sh
bash 启动服务/run_e2e_tests.sh
```

## 世界数据提取

从 SillyTavern 酒馆卡片 JSON 一步生成全部结构化文件：

```bash
# Batch API 模式（推荐，50% 成本优惠）
python -m app.tools.init_world_cli extract \
    --input "worldbook.json" \
    --output data/goblin_slayer/structured/ \
    --model gemini-3-pro-preview \
    --thinking-level none \
    --relabel-edges --enrich-entities

# 直接调用模式（实时返回）
python -m app.tools.init_world_cli extract \
    --input "worldbook.json" \
    --output data/goblin_slayer/structured/ \
    --model gemini-3-pro-preview \
    --direct --relabel-edges --enrich-entities
```

输出文件：maps.json, characters.json, world_map.json, character_profiles.json, world_graph.json, prefilled_graph.json, chapters_v2.json, monsters.json, items.json, skills.json

## 交互式开发工具

```bash
python -m app.tools.game_master_cli              # 完整游戏管理 REPL
python -m app.tools.game_master_cli --setup-demo # 初始化演示世界
python -m app.tools.flash_natural_cli            # Flash 服务测试
python -m app.tools.gm_natural_cli               # GM 叙述测试
```

## 设计亮点

1. **Flash-Only 架构**：单一模型一次调用完成意图分析 + 操作规划 + 条件评估，减少延迟和多模型编排复杂度
2. **双层认知 NPC**：工作记忆（实时对话）+ 长期记忆图谱（语义检索），自动图谱化实现无限对话容量
3. **GraphScope 统一寻址**：6 种层级作用域覆盖世界→章节→区域→地点→角色→队伍，一套 API 操作所有图谱
4. **扩散激活检索**：基于图论的记忆召回，支持跨视角/跨章节衰减，比向量相似度更好地捕捉叙事因果关系
5. **两阶段事件系统**：机械条件（确定性）+ 语义条件（LLM 判断），兼顾可控性和灵活性
6. **节奏控制引擎**：从环境暗示到强制推进的 4 级渐进升级，确保剧情自然流动
7. **视角感知事件分发**：同一事件按"参与者/目击者/旁观者"视角转换后写入不同角色图谱
8. **MCP 工具抽象**：游戏逻辑通过 MCP 协议暴露，支持 stdio/HTTP/SSE 三种传输，方便独立测试和扩展

## License

MIT
