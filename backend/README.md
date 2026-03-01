**中文** | [English](README_EN.md)

# AI RPG Game Engine Backend

AI 驱动的 CRPG 游戏后端（类博德之门 3），采用五层六边形架构的纯 Python 内核。

## 技术栈

| 层 | 技术 |
|---|------|
| Web 框架 | FastAPI |
| 游戏内核 | 纯 Python（零外部依赖） |
| 持久化 | 本地文件 / Firestore（可切换） |
| AI 模型 | Google Gemini（适配器层，内核不依赖） |

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 运行开发服务器
uvicorn app.main:app --reload --port 8000

# 运行测试
pytest --ignore=tests/test_api_shell.py --ignore=tests/test_interaction_service.py -v
```

服务启动后：
- API: http://localhost:8000/api/game/worlds
- 文档: http://localhost:8000/docs
- 健康检查: http://localhost:8000/health

## 项目结构

```
backend/
├── app/
│   ├── main.py                    # 瘦入口（include 4 个 router）
│   ├── deps.py                    # 共享依赖：app 实例、运行时、会话加载
│   ├── api_models.py              # Pydantic 请求/响应模型
│   ├── world_seed.py              # 世界种子数据（WORLD_CATALOG）
│   ├── interaction_service.py     # 应用层交互编排（presence → precheck → execute）
│   ├── interaction_views.py       # 交互快照构建器
│   ├── routers/
│   │   ├── sessions.py            #   会话生命周期（6 路由）
│   │   ├── character.py           #   角色创建（3 路由）
│   │   ├── panels.py              #   面板查询（3 路由）
│   │   └── gameplay.py            #   导航 + 流式动作执行（5 路由）
│   └── game_core/                 # ⬡ 五层六边形纯 Python 内核
│       ├── content/               #   ❶ 内容层：10 个 ContentRegistry
│       ├── state/                 #   ❸ 状态层：10 个 StateSlice
│       ├── rules/                 #   ❷ 规则层：13 个 CommandHandler
│       ├── orchestration/         #   ❹ 编排层：TickCoordinator + 11 个 SettlementHook
│       ├── narrative/             #   ❺ 叙事层：AgenticExecutor + RoleToolRegistry
│       ├── adapters/              #   适配器：4 端口 Protocol + 实现
│       ├── bootstrap.py           #   运行时组装（DefaultRuntime）
│       └── runtime.py             #   会话生命周期管理（GameRuntime）
├── tests/                         # 57 个测试文件，744+ passed
└── pyproject.toml
```

## 核心架构

### Tick 生命周期

```
TickCoordinator.process(input_payload)
  ↓
PipelineOrchestrator
  ContextAssembler → ActionDispatcher → RulesEngine
  ↓
StateDelta 原子应用
  ↓
累积时间 ≥ 1.0 → SettlementHooks（按优先级执行）
  ↓
PipelineResult（含 SSE 事件）
```

### 五层六边形

| 层 | 职责 | 规模 |
|---|------|------|
| ❶ 内容层 | 世界静态数据（地图、角色、技能、物品等） | 10 registry |
| ❷ 规则层 | 无状态命令处理（combat、navigation、spell 等） | 13 handler |
| ❸ 状态层 | 会话运行时状态（player、area、quests、relations 等） | 10 slice |
| ❹ 编排层 | Tick 协调、Pipeline、Settlement hooks | 11 hook |
| ❺ 叙事层 | AI 叙事执行器（LLM 集成点） | agentic executor |
| 适配器 | 4 端口 Protocol（Input/Output/Persistence/Presentation） | 4 port |

**隔离红线**：`game_core/` 不依赖 FastAPI、LLM SDK 或任何外部服务。所有外部能力通过适配器端口注入。

### 真流式 SSE

流式路由（`action/stream`、`input/stream`、`interact/stream`）使用 `asyncio.Queue` + `create_task` 实现真流式推送——Settlement hook 每执行完一个，其 SSE 事件立即到达客户端。

## API 端点

### 世界与会话

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/api/game/worlds` | 列出世界 |
| GET | `/api/game/{world_id}/sessions` | 列出会话 |
| POST | `/api/game/{world_id}/sessions` | 创建会话 |
| POST | `.../sessions/{sid}/resume` | 恢复会话 |
| DELETE | `.../sessions/{sid}` | 删除会话 |

### 角色

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `.../character-creation/options` | 创建选项 |
| POST | `.../character` | 创建角色 |
| GET | `.../character/panel` | 角色面板 |

### 面板查询

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `.../inventory/panel` | 物品面板 |
| GET | `.../map/panel` | 地图面板 |
| GET | `.../quests/panel` | 任务面板 |

### 游戏执行

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `.../navigate` | 导航（JSON） |
| POST | `.../action/stream` | 结构化动作（SSE 流） |
| POST | `.../input/stream` | 文本命令（SSE 流） |
| POST | `.../interact/stream` | 目标交互（SSE 流） |

## 运行测试

```bash
# 全部核心测试
pytest --ignore=tests/test_api_shell.py --ignore=tests/test_interaction_service.py -v

# 单个测试文件
pytest tests/test_hook_resilience.py -v

# 包含 API shell 测试（较慢）
pytest -v
```

## License

MIT
