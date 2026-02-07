# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

AI 驱动的互动式 RPG 游戏后端，具备智能记忆管理和知识图谱能力。采用 Flash-Only v2 架构——单一 Flash 模型完成意图分析、操作执行和叙述生成。

**技术栈**: FastAPI + Firestore + Google Gemini 3 + NetworkX

## 构建与运行命令

```bash
# 安装依赖
pip install -r requirements.txt

# 运行 FastAPI 服务器（开发模式，默认 stdio 传输自动启动 MCP 子进程）
uvicorn app.main:app --reload --port 8000

# 运行测试
pytest -v                                        # 全部测试
pytest tests/test_spreading_activation.py -v     # 单个测试文件

# MCP 服务器（独立启动，通常不需要——stdio 模式自动管理）
python -m app.mcp.game_tools_server                                         # Game Tools (stdio)
python -m app.mcp.game_tools_server --transport streamable-http --port 9101 # Game Tools (HTTP)
python -m app.combat.combat_mcp_server                                      # Combat (stdio)
python -m app.combat.combat_mcp_server --transport streamable-http --port 9102 # Combat (HTTP)

# 使用启动脚本
bash 启动服务/run_fastapi.sh
bash 启动服务/run_mcp_services.sh    # 一键启动 MCP (HTTP 模式)
```

### E2E 测试

需先启动 MCP 服务（HTTP 模式）：

```bash
bash 启动服务/run_e2e_tests.sh              # 运行所有阶段
bash 启动服务/run_e2e_tests.sh phase1       # 单阶段: 基础连通性
bash 启动服务/run_e2e_tests.sh --check      # 只检查前置条件

# 手动运行
PYTHONPATH=. \
  MCP_TOOLS_TRANSPORT=streamable-http MCP_TOOLS_ENDPOINT=http://127.0.0.1:9101/mcp \
  MCP_COMBAT_TRANSPORT=streamable-http MCP_COMBAT_ENDPOINT=http://127.0.0.1:9102/mcp \
  pytest tests/test_fastapi_to_mcp.py -v -s
```

### 交互式开发 CLI

```bash
python -m app.tools.game_master_cli              # 完整游戏管理
python -m app.tools.game_master_cli --setup-demo # 初始化演示世界
python -m app.tools.flash_natural_cli            # Flash 服务测试
python -m app.tools.gm_natural_cli               # GM 叙述测试
python -m app.tools.init_world_cli               # 世界初始化
```

## 架构

### Flash-Only v2 数据流

这是核心请求处理流程，入口为 `AdminCoordinator.process_player_input_v2()`：

```
玩家输入
    ↓
1. 收集基础上下文（世界状态、会话状态、场景、队伍）
    ↓
2. Flash 一次性分析（intent + operations + memory seeds）
    ↓
3. 并行：记忆召回  |  顺序：执行 Flash 操作
    ↓
4. 组装完整上下文
    ↓
5. 导航后同步队友位置
    ↓
6. Flash GM 生成叙述
    ↓
7. 队友响应（显式交互用更高模型，被动用 Flash）
    ↓
8. 分发事件到队友图谱
    ↓
CoordinatorResponse → 前端
```

### 核心系统

1. **游戏编排器** (`app/services/admin/`)
   - `admin_coordinator.py`: 主编排器，协调所有子系统（单例，`AdminCoordinator.get_instance()`）
   - `flash_cpu_service.py`: Flash 意图分析和操作执行
   - `world_runtime.py`: 世界状态运行时
   - `event_service.py` / `event_llm_service.py`: 事件系统
   - `state_manager.py`: 会话状态管理

2. **NPC AI 系统** — 三层模型 (`NPCTierConfig`)
   - **Passerby**: 轻量路人交互（无 thinking）
   - **Secondary**: 次要角色（medium thinking）
   - **Main**: 主要角色（low thinking）
   - NPC 实例池化：`instance_manager.py`，上下文窗口自动管理与淘汰

3. **队伍系统** (`app/services/party_service.py`, `teammate_response_service.py`)
   - 队友每回合自动决策是否响应
   - 队友位置随玩家导航自动同步

4. **MCP 工具层** (`app/mcp/`)
   - `game_tools_server.py`: 游戏 MCP 服务器
   - `app/combat/combat_mcp_server.py`: 战斗 MCP 服务器
   - 工具模块在 `app/mcp/tools/`：graph、narrative、navigation、npc、party、passerby、time

5. **知识图谱** (`app/services/memory_graph.py`, `spreading_activation.py`)
   - 扩散激活算法查找相关概念
   - 世界级和角色级两个图谱作用域
   - `memory_graphizer.py`: 对话自动入图

6. **战斗系统** (`app/combat/`)
   - D&D 风格 d20 判定
   - `combat_engine.py`: 核心战斗逻辑
   - `ai_opponent.py`: 基于性格的敌人 AI

7. **世界数据处理** (`app/tools/worldbook_graphizer/`)
   - `unified_pipeline.py`: 统一世界数据提取管线（支持 Batch API）
   - 从小说/Lorebook 提取地图、角色、知识图谱等结构化数据

### 路由结构

单一路由器 `game_v2_router` 挂载于 `/api/game`（`app/routers/game_v2.py`）：

- 会话: `POST /{world_id}/sessions`, `GET /{world_id}/sessions/{session_id}`
- 导航: `POST .../navigate`, `POST .../sub-location/enter`, `POST .../sub-location/leave`
- 时间: `GET .../time`, `POST .../time/advance`
- 游戏: `POST .../input`（主入口）, `POST .../scene`, `GET .../context`
- 战斗: `POST .../combat/start`, `POST .../combat/action`, `POST .../combat/resolve`

### 依赖注入与单例

- `app/dependencies.py`: `@lru_cache()` 实现单例
- `get_coordinator()` → `AdminCoordinator.get_instance()`
- `get_graph_store()` → `GraphStore()`
- 所有路由通过 `Depends()` 获取服务实例

### LLM 提示词

所有系统提示存放在 `app/prompts/`，使用 `{variable}` 模板变量：
- `flash_analysis.md`: Flash 意图分析（输出严格 JSON）
- `flash_cpu_system.md`: Flash CPU 系统提示
- `flash_context_curation.md`: 上下文选择策略
- `flash_gm_narration.md`: GM 叙述生成
- `teammate_decision.md` / `teammate_response.md`: 队友决策与响应
- `travel_narration.md`: 旅行叙述

### Firestore 结构

```
worlds/{world_id}/
  graphs/{graph_type}/nodes/{node_id}/, edges/{edge_id}/
  characters/{character_id}/nodes/, edges/, instances/
  maps/{map_id}/locations/{location_id}/, graphs/
  sessions/{session_id}/state/, events/

users/{user_id}/
  sessions/{session_id}/
    messages/{message_id}/
    topics/{topic_id}/threads/{thread_id}/insights/{insight_id}/
    archived_messages/{message_id}/
```

## 关键配置

环境变量（`.env`）：

**必需：**
- `GEMINI_API_KEY`: Gemini API 密钥
- `GOOGLE_APPLICATION_CREDENTIALS`: Firebase 凭证路径（默认: `./firebase-credentials.json`）

**Gemini 模型（均可通过环境变量覆盖，见 `app/config.py`）：**
- `GEMINI_FLASH_MODEL` / `GEMINI_MAIN_MODEL`: 主力模型（默认: `gemini-3-flash-preview`）
- `ADMIN_FLASH_MODEL`: Admin 层模型（默认: `gemini-3-flash-preview`）
- `ADMIN_FLASH_THINKING_LEVEL`: 思考级别（默认: `high`）
- `NPC_PASSERBY_MODEL` / `NPC_SECONDARY_MODEL` / `NPC_MAIN_MODEL`: NPC 三层模型

**MCP 传输配置：**
- `MCP_TOOLS_TRANSPORT` / `MCP_TOOLS_ENDPOINT`: Game Tools（默认 stdio）
- `MCP_COMBAT_TRANSPORT` / `MCP_COMBAT_ENDPOINT`: Combat（默认 stdio）
- `MCP_TOOL_TIMEOUT_SECONDS`: MCP 工具超时（默认: `20`）
- `MCP_NPC_TOOL_TIMEOUT_SECONDS`: NPC 工具超时（默认: `90`）

**其他：**
- `FIRESTORE_DATABASE`: 数据库名称（默认: `(default)`）
- `INSTANCE_POOL_MAX_INSTANCES`: NPC 实例池上限（默认: `20`）
- `INSTANCE_POOL_CONTEXT_WINDOW_SIZE`: 实例上下文窗口（默认: `200000`）

## 代码规范

- Python 4 空格缩进
- Pydantic 模型放在 `app/models/`，路由放在 `app/routers/`，服务放在 `app/services/`
- 提交信息：简短，通常中文，偶尔使用 Conventional Commit 前缀
- 遵循 PEP 8，保持与现有代码风格一致
