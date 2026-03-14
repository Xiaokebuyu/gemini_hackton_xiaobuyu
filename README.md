<p align="center">
  <h1 align="center">AI CRPG Engine</h1>
  <p align="center">
    <strong>AI 驱动的类博德之门 3 CRPG 游戏引擎</strong>
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/python-3.13-blue?logo=python&logoColor=white" alt="Python 3.13" />
    <img src="https://img.shields.io/badge/LLM-Gemini_Flash-4285F4?logo=google&logoColor=white" alt="Gemini Flash" />
    <img src="https://img.shields.io/badge/tests-3043_passed-brightgreen" alt="Tests" />
    <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License" />
    <img src="https://img.shields.io/badge/react-19-61DAFB?logo=react&logoColor=white" alt="React 19" />
  </p>
  <p align="center">
    <a href="./README_EN.md">English</a> | 中文
  </p>
</p>

---

全栈 CRPG 引擎，基于**六边形纯 Python 内核**（零外部依赖）构建，集成**三角色 LLM Agent 系统**（GM / NPC / 队友）、**D&D 5e 规则子集**和**实时 SSE 流式叙事**。所有 LLM 功能均有确定性降级——无 API Key 时游戏完整运行不中断。

## 概览

| 指标 | 数值 |
|------|------|
| 后端模块数 | 203 |
| 内核模块数（零依赖） | 167 |
| 命令类型 | ~96 |
| Settlement Hook | 25 |
| 内容层 dataclass | 45+ |
| 测试通过 | 3,043 |
| 后端代码量 | ~74,000 行 |
| 测试代码量 | ~78,000 行 |
| 前端代码量 | ~8,800 行 |
| SSE 事件类型 | 40+ |
| Zustand Store | 12 |

## 架构

```
┌──────────────────────────────────────────────────────────────────┐
│                    Frontend · React 19 + Zustand + Tailwind       │
│            12 Stores · useGameStream（SSE 消费层）                  │
└────────────────────────────┬─────────────────────────────────────┘
                             │ HTTP / SSE
┌────────────────────────────▼─────────────────────────────────────┐
│                    Application Layer · FastAPI                     │
│    deps.py (DI) · GeminiAdapter · AgentOrchestration · Narrators  │
│    WorldKnowledgeGraph · AIOsirisEvaluator · ViewBuilders         │
└────────────────────────────┬─────────────────────────────────────┘
                             │ Port Protocols
┌────────────────────────────▼─────────────────────────────────────┐
│                   game_core/ · 六边形内核                           │
│                                                                    │
│    L1 State ──► L2 Content ──► L3 Rules ──► L4 Orchestration      │
│                                   │               │                │
│                               L5 Narrative ◄─────┘                │
│                                   │                                │
│                               L6 Planning                          │
│                                                                    │
│                            Adapters / Ports                        │
└──────────────────────────────────────────────────────────────────┘
```

内核 `game_core/` 包含 167 个 Python 模块，**不 import 任何外部库**。LLM、数据库、SSE 输出全部通过 Port Protocol 注入——更换任何外部服务无需修改内核代码。

### Tick 生命周期

```
Player Input
    │
    ▼
ActionDispatcher → Command（~96 种类型）
    │
    ▼
RulesEngine.execute() → StateDelta
    │
    ▼
StateContainer.apply()                 ← 唯一写入路径
    │
    ▼  (accumulated_time >= 1.0)
Settlement Hook 优先级链  P20 → P90
    ├─ P30  AIOsiris           因果判断（LLM）
    ├─ P35  NarrativePlanner   叙事规划
    ├─ P45  PassivePerception  被动感知自动发现
    ├─ P50  EventCondition     事件触发评估
    ├─ P60  NpcSchedule        NPC 日程移动
    ├─ P63  Campfire           长休篝火叙事
    ├─ P65  Relationship       关系跃迁 + 自动驱逐
    ├─ P75  PrivateChatTrigger 私聊触发
    └─ P80  GmNarration        GM 叙事生成
    │
    ▼
SSE Event Stream → Frontend
```

## 核心设计

### 1. 三角色 LLM Agent 系统

| 角色 | 工具 | 能改状态？ | 信息可见性 |
|------|------|:---------:|-----------|
| **GM** | narrate, comment, suggest_options, describe_environment, pass_turn | 否（仅 SSE） | 全局（L0–L7） |
| **NPC** | speak, emote, update_feeling, remember, offer_quest, offer_trade, refuse | 是（倾向值、任务） | 受限（无法访问 quests/plan） |
| **Teammate** | speak, emote, express_opinion, leave_party | 是（倾向值、离队） | 受限（无法访问任务细节） |

**Agent 行为由系统架构保证，不靠 prompt 约束：**

- **工具路由** — `OfferTradeTool.applicable_traits = ["merchant"]`：只有商人 NPC 的工具集中才有交易工具。LLM 无法调用不存在的工具。
- **协议约束** — 每轮最多 1 次 speak + 1 次 emote；有可见输出即终止循环。
- **状态隔离** — `RoleStateProxy` 在代码层面拦截 NPC 对 `quests` / `narrative_plan` 的访问。
- **优雅降级** — 每个 LLM 集成点都有确定性 fallback。无 API Key = 游戏照常运行。

### 2. L0–L7 分层上下文工程

每次 Agent 调用按需装配 7 层上下文，按角色权限过滤：

| 层 | 内容 | GM | NPC | Teammate |
|----|------|:--:|:---:|:--------:|
| L0 | 世界观与派系 | 全量 | 全量 | 全量 |
| L1 | 章节进度与里程碑 | 全量 | 不可见 | 仅可用里程碑 |
| L2 | 区域环境 | 全量 | 全量 | 全量 |
| L3 | 位置详情 | 全量 | 全量 | 全量 |
| L4 | 动态状态（倾向值、关系） | 全局 | 仅自身 | 自身+队伍 |
| L5 | 场景总线（最近对话） | 全量 | 按可见性过滤 | 按可见性过滤 |
| L6 | 记忆召回（知识图谱） | 不使用 | BFS top-5 | BFS top-5 |
| L7 | 引擎结果（骰点、命令） | 全量 | 不可见 | 不可见 |

### 3. 三层记忆架构

```
短期记忆 ─── ContextWindow（per-actor FIFO，32K token）
                │  溢出 → write_episode()
                ▼
长期记忆 ─── Per-actor 知识图谱（NetworkX）
                │  LLM 提取 [主体, 关系, 客体, 权重] 三元组
                │  BFS 扩散激活（decay=0.8）→ top-5 注入 system prompt
                ▲
                │  合并查询
全局知识 ─── 静态知识图谱
                │  从内容层 seed（11 种边关系）
                +
外部指令 ─── Directive 队列（规划层注入）
                NPC 消费指令但不知道来源
```

### 4. StateDelta 原子状态

所有状态变更描述为 `StateDelta`（`StateChange` 列表），通过唯一入口 `apply()` 写入。无 setter，无直接修改。

```python
delta = StateDelta(changes=[
    StateChange("player", "set", "hp", 45),
    StateChange("relations", "add", "npc_dispositions.goblin_chef.approval", -10),
], reason="combat_attack")
state.apply(delta)  # 唯一写入路径
```

收益：完整审计链路、25 个 Hook 安全并发读、dry-run 能力、快照回放。

### 5. Narrative Planner — 涌现叙事引擎

没有中央编剧。`NarrativePlannerHook`（P35）在每次结算时驱动 `PlannerDispatcher`，将世界状态变化转化为 `PlannerEvent`，分发到 6 个独立子系统，各子系统产出 `Directive`（18 种类型），最终转化为 RulesEngine 命令执行——叙事从系统规则的交互作用中**涌现**，而非预编排。

```
Settlement Hook 链执行
    │ 产生状态变更
    ▼
collect_planner_events()              ← 从 change_log / action_log 提取语义事件
    │ 5 个优先级大类，去重排序
    ▼
PlannerDispatcher.dispatch()
    │ 分发到匹配的子系统
    ▼
┌─────────────────────────────────────────────────────────────────┐
│  QuestManager        任务创建 / 公告发布 / 退役                    │
│  NpcDirector         NPC 行为指令 / 动态能力分配                   │
│  NarrativeWeaver     生命周期 GC / 升级安全网 / 临时 NPC 解散       │
│  WorldBuilder        环境植入 / 动态子区域 / 遭遇植入               │
│  PacingController    节奏控制 / 停滞升级                           │
│  ItemDesigner        任务奖励设计 / 商店策展                       │
└─────────────────────────────────────────────────────────────────┘
    │ 产出 Directive
    ▼
PlannerDispatcher.apply_directive()
    │ 转化为 RulesEngine Command
    ▼
StateContainer.apply(delta)           ← 状态变更传播到下一个 Hook
```

#### 18 种 Directive 类型

| 类别 | Directive | 效果 |
|------|-----------|------|
| **任务** | `create_quest`, `publish_bulletin`, `retire_quest`, `update_quest`, `set_task_monitor` | 动态创建/发布/退役任务 |
| **NPC** | `direct_npc`, `spawn_quest_npc`, `assign_capability`, `revoke_capability` | 指令注入 NPC 实例 / 动态分配能力 |
| **世界** | `plant_environmental`, `fill_area`, `plant_encounter`, `discover_room`, `fill_room` | 运行时改变环境 / 植入遭遇 |
| **节奏** | `escalate`, `adjust_pacing` | 升级叙事张力 / 冻结节奏 |
| **物品** | `design_reward`, `curate_shop` | LLM 驱动的奖励设计 / 商店策展 |

#### 关键机制

- **Directive → NPC 实例实时注入**：`NpcDirector` 产出 `direct_npc` 后，如果该 NPC 的 `InstanceManager` 实例存在，指令直接注入其 `directive_queue`——下次对话时 NPC 会自然地执行该行为，而不知道指令来源。
- **动态能力分配**：`assign_capability` 可以在运行时给任何 NPC 添加功能（如临时变成商人），通过 `CapabilityDescriptor` 注入 system prompt，到期自动回收。
- **停滞升级安全网**：`NarrativeWeaver` 监控 `ticks_since_milestone_progress`，阈值 `[4, 7, 10, 13, 16]` 逐级触发 `escalate`，防止玩家卡关。
- **事件去重与忙碌语义**：`PlannerDispatcher` 用 `dedupe_key` 去重，子系统执行中标记 busy，新事件入队（最大深度 3），避免重复处理和无限递归。

## 游戏系统

### 战斗

D&D 5e 回合制战斗：d20 攻击骰、AC 对比、伤害骰、优势/劣势、暴击。`BattleGrid` 实现 Dijkstra 寻路（地形加权移动力）、A* 搜索（Manhattan 启发式）、Bresenham 视线检测。怪物 AI 基于性格驱动决策树（激进/防御/胆怯），含目标评分系统。

### 法术

法术位系统：专注追踪、豁免检定、AOE 多目标、提环施放、状态效果（眩晕/麻痹/中毒/流血，均有实际机制效果）。

### 探索

区域制导航（含子地点和房间）。容器、陷阱（技能检定解除）、隐藏发现（被动感知自动检测）、运行时动态生成临时子区域。

### 关系系统

NPC 四维倾向值：认可、信任、恐惧、浪漫。六阶段关系，含正向和负向路径：

```
stranger → acquaintance → friend → close_friend → intimate
                 ↓              ↓            ↓           ↓
                cold ──────► hostile ──────► enemy
                               ↑ 自动驱逐队友
```

### 经济与成长

NPC 商店（库存消耗、刷新周期）。XP 升级（1–20）、职业特性树、3 级选子职业、里程碑级 ASI。

## 技术栈

### 后端

| 组件 | 技术 |
|------|------|
| 语言 | Python 3.13 |
| 框架 | FastAPI + uvicorn |
| LLM | Google Gemini Flash (`google-genai`) |
| 知识图谱 | NetworkX 3.2 |
| 持久化 | Google Cloud Firestore / 本地 JSON |
| 测试 | pytest（3,043 tests） |

### 前端

| 组件 | 技术 |
|------|------|
| 语言 | TypeScript |
| 框架 | React 19 + Vite |
| 状态管理 | Zustand 5（12 个 store） |
| 样式 | Tailwind CSS |
| 实时通信 | SSE (Server-Sent Events) |
| 动画 | Framer Motion |

## 项目结构

```
.
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI 入口
│   │   ├── deps.py                    # 依赖注入根
│   │   ├── llm_gemini.py             # Gemini LLM 适配器
│   │   ├── agent_orchestration.py    # 三角色 Agent 编排
│   │   ├── narrators.py             # GM 结算叙事
│   │   ├── evaluators.py            # AI-Osiris 因果评估
│   │   ├── world_knowledge_graph.py  # NetworkX 知识图谱
│   │   ├── interaction_service.py    # 交互校验与编排
│   │   ├── routers/                  # API 路由
│   │   │
│   │   └── game_core/               # ★ 六边形内核（零外部依赖）
│   │       ├── state/               #   L1: 10 个 StateSlice + StateDelta
│   │       ├── content/             #   L2: 11 个 Registry + 45+ dataclass
│   │       ├── rules/               #   L3: RulesEngine + 27 个 Handler（~96 命令）
│   │       ├── orchestration/       #   L4: TickCoordinator + 25 个 Settlement Hook
│   │       ├── narrative/           #   L5: AgenticExecutor + L0-L7 ContextBuilder
│   │       ├── planning/            #   L6: 6 个子系统 + 18 种 Directive
│   │       └── adapters/            #   Port Protocol + null 实现
│   │
│   ├── tests/                       # 3,043 测试，~78,000 行
│   └── data/                        # 世界数据（结构化 JSON）
│
└── frontend/
    └── src/
        ├── pages/                   # GamePage（主路由）
        ├── game/                    # 游戏 UI 组件
        ├── hooks/                   # useGameStream（SSE 消费层）
        ├── stores/                  # 12 个 Zustand Store
        ├── types/                   # SSE 事件类型定义（40+）
        └── lib/                     # SSE 客户端
```

## 快速开始

### 前置条件

- Python 3.13+
- Node.js 18+

### 后端

```bash
cd backend

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 配置环境变量（可选——没有 API Key 游戏也能完整运行）
cat > .env << 'EOF'
GOOGLE_API_KEY=your_gemini_api_key
EOF

# 启动服务
uvicorn app.main:app --reload --port 8000
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

### 验证

| URL | 说明 |
|-----|------|
| http://localhost:8000/health | 健康检查 |
| http://localhost:8000/docs | API 文档（Swagger） |
| http://localhost:5173 | 前端页面 |

### 运行测试

```bash
cd backend
PYTHONPATH=. pytest --ignore=tests/test_api_shell.py --ignore=tests/test_interaction_service.py -v
```

## API 端点

### 会话管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/api/game/worlds` | 世界列表 |
| POST | `/api/game/{world_id}/sessions` | 创建会话 |
| POST | `.../sessions/{sid}/resume` | 恢复会话 |
| DELETE | `.../sessions/{sid}` | 删除会话 |

### 游戏玩法（SSE 流式）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `.../act` | 结构化行动 |
| POST | `.../navigate` | 区域导航 |
| POST | `.../interact` | NPC / 公告栏交互 |
| POST | `.../private_chat` | 私聊 |
| POST | `.../text_input` | 自由文本输入 |
| GET | `.../scene` | 场景状态 |

### 面板

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `.../character` | 角色面板 |
| GET | `.../inventory` | 背包面板 |
| GET | `.../quests` | 任务面板 |
| GET | `.../map` | 地图面板 |

### 战斗

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `.../encounter` | 遭遇决策（战/逃/偷袭） |
| POST | `.../combat` | 战斗行动 |
| GET | `.../combat` | 战斗状态 |

## 配置

| 变量 | 必需 | 说明 |
|------|:----:|------|
| `GOOGLE_API_KEY` | 否 | Gemini API Key。没有此 Key 时所有 LLM 功能降级为确定性 fallback，游戏完整可玩。 |
| `GOOGLE_APPLICATION_CREDENTIALS` | 否 | Firebase 凭据路径。没有时回退到本地 JSON 持久化。 |

> **没有 API Key？没问题。** 完整游戏循环——战斗、探索、交易、任务、NPC 日程——无需任何外部服务即可运行。LLM 在此基础上增加更丰富的叙事、NPC 个性和动态剧情规划。

## 设计亮点

1. **六边形内核** — `game_core/` 零外部 import。3,043 个测试在无 API Key、无数据库的 CI 环境中全部通过。
2. **架构级 Agent 安全** — 工具路由（`applicable_traits`）、协议约束（max 1 speak/emote）、状态隔离（`RoleStateProxy`）、确定性降级。行为边界由代码保证，不是 prompt。
3. **L0–L7 上下文工程** — 7 层上下文按需装配，角色可见性矩阵。每个 Agent 只看到其角色权限内的信息。
4. **三层记忆** — FIFO 工作记忆（32K）+ NetworkX 知识图谱（BFS 扩散激活）+ Directive 指令注入。NPC 跨 session 维持持久记忆。
5. **StateDelta 不变性** — 所有变更描述为 delta，通过唯一 `apply()` 入口写入。完整审计链路、安全并发读、dry-run 能力。
6. **Settlement Hook 链** — 25 个 Hook，优先级 P20–P90。新功能 = 在合适优先级插入一个 Hook，核心循环零修改。
7. **涌现叙事** — 6 个规划子系统 + 18 种 Directive 类型。无中央编剧——叙事从独立 Hook/Planner 的交互作用中涌现。
8. **双路径 SSE 流式** — 离散事件（drain 缓冲）+ 连续文本（直接透传）。per-session `asyncio.Lock` + Queue 驱动并发模型。
9. **D&D 5e 战斗引擎** — 方格战斗：Dijkstra/A* 寻路、Bresenham 视线、性格驱动怪物 AI、优势/劣势骰。
10. **生产级降级** — 每个 LLM 集成点都有确定性 fallback。系统设计为用 AI 增强，而非依赖 AI。

## License

MIT
