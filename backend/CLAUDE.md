# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

AI 驱动的互动式 RPG 游戏后端，具备智能记忆管理功能。使用 MCP（Model Context Protocol）为 LLM 提供上下文感知的记忆能力。

**技术栈**: FastAPI + Firestore + Google Gemini + NetworkX

## 构建与运行命令

```bash
# 安装依赖
pip install -r requirements.txt

# 运行 FastAPI 服务器（开发模式）
uvicorn app.main:app --reload --port 8000

# 使用启动脚本
bash 启动服务/run_fastapi.sh

# 运行 MCP 服务器（两个独立服务）
python -m app.mcp.game_tools_server                                      # Game Tools (stdio)
python -m app.mcp.game_tools_server --transport streamable-http --port 9101  # Game Tools (HTTP)
python -m app.combat.combat_mcp_server                                   # Combat (stdio)
python -m app.combat.combat_mcp_server --transport streamable-http --port 9102  # Combat (HTTP)

# 一键启动所有 MCP 服务（HTTP 模式）
bash 启动服务/run_mcp_services.sh

# 运行测试
pytest -v
pytest tests/test_spreading_activation.py -v
```

### MCP 服务启动流程

默认情况下 FastAPI 使用 **stdio 传输**，自动以子进程方式启动 MCP 服务器，无需额外配置。

如需使用 **HTTP 传输**（独立进程、可共享），按以下顺序操作：

```bash
# 1. 启动 MCP 服务（后台）
bash 启动服务/run_mcp_services.sh

# 2. 设置环境变量（脚本会输出这些）
export MCP_TOOLS_TRANSPORT=streamable-http
export MCP_TOOLS_ENDPOINT=http://127.0.0.1:9101/mcp
export MCP_COMBAT_TRANSPORT=streamable-http
export MCP_COMBAT_ENDPOINT=http://127.0.0.1:9102/mcp

# 3. 启动 FastAPI
bash 启动服务/run_fastapi.sh
```

### E2E 测试

```bash
# 前置条件：MCP 服务已启动（HTTP 模式）
bash 启动服务/run_e2e_tests.sh              # 运行所有测试
bash 启动服务/run_e2e_tests.sh phase1       # 阶段1: 基础连通性
bash 启动服务/run_e2e_tests.sh phase2       # 阶段2: Game Tools MCP
bash 启动服务/run_e2e_tests.sh phase3       # 阶段3: Combat MCP
bash 启动服务/run_e2e_tests.sh phase4       # 阶段4: 队伍系统
bash 启动服务/run_e2e_tests.sh phase5       # 阶段5: 路人与事件
bash 启动服务/run_e2e_tests.sh --check      # 只检查前置条件

# 手动运行（不使用脚本）
PYTHONPATH=. \
  MCP_TOOLS_TRANSPORT=streamable-http MCP_TOOLS_ENDPOINT=http://127.0.0.1:9101/mcp \
  MCP_COMBAT_TRANSPORT=streamable-http MCP_COMBAT_ENDPOINT=http://127.0.0.1:9102/mcp \
  pytest tests/test_fastapi_to_mcp.py -v -s
```

### 交互式开发 CLI

```bash
# 主游戏循环测试（推荐）
python play_cli.py [world_id]
python play_cli.py goblin_slayer

# 完整游戏管理工具
python -m app.tools.game_master_cli
python -m app.tools.game_master_cli --setup-demo

# 其他测试 CLI
python -m app.tools.game_cli           # 游戏循环测试
python -m app.tools.flash_natural_cli  # Flash 服务测试
python -m app.tools.pro_chat_cli       # Pro 服务测试
```

## 架构

### 核心系统

1. **游戏编排器** (`app/services/admin/`)
   - `admin_coordinator.py`: 主编排器，协调所有子系统（单例，`AdminCoordinator.get_instance()`）
   - `world_runtime.py`: 世界状态运行时
   - `flash_cpu_service.py`: 快速路由和解析（Flash 模型）
   - `pro_dm_service.py`: 主叙述 AI 服务（Pro 模型）
   - `event_service.py` / `event_llm_service.py`: 事件系统
   - `state_manager.py`: 会话状态管理

2. **多层 NPC AI 系统**
   - **Passerby Service**: 轻量 NPC 交互（gemini-3-flash-preview）
   - **Pro Service**: 主角色 AI，扩展上下文（gemini-3-pro-preview）
   - **GM Service**: 世界叙事和事件生成
   - 上下文缓存和实例池化提升性能

3. **队伍系统** (`app/services/party_service.py`, `teammate_response_service.py`)
   - 玩家与 NPC 队友组队
   - 队友每回合根据上下文自动决策是否响应
   - 显式队友交互用 Pro 模型，被动响应用 Flash 模型
   - 队友位置随玩家导航自动同步

4. **MCP 记忆网关** (`app/mcp/`)
   - `game_tools_server.py`: 游戏 MCP 服务器
   - `combat_mcp_server.py`: 战斗 MCP 服务器（在 `app/combat/`）
   - MCP 工具模块在 `app/mcp/tools/`：graph、narrative、navigation、npc、passerby、time
   - 热记忆（会话）-> 温记忆（归档话题）-> 冷记忆（向量索引，占位）

5. **知识图谱** (`app/services/memory_graph.py`, `spreading_activation.py`)
   - 记忆节点（人物、地点、事件、概念）
   - 扩散激活算法查找相关概念
   - 两个作用域：世界级和角色级图谱

6. **战斗系统** (`app/combat/`)
   - D&D 风格机制，d20 判定
   - `combat_engine.py`: 核心战斗逻辑
   - `ai_opponent.py`: 基于性格的敌人 AI
   - 通过 `combat_mcp_server.py` 暴露 MCP 接口

7. **游戏循环** (`app/routers/game_v2.py`)
   - 统一 Pro-First v2 接口
   - 会话、导航、对话、战斗、队伍与事件派发

### Pro-First v2 数据流

```
玩家输入
   |
   v
1. 收集基础上下文（世界状态、会话状态、场景、队伍）
   |
   v
2. Flash 一次性分析（intent + operations + memory seeds）
   |
   v
3. 并行：记忆召回  |  顺序：执行 Flash 操作
   |               |
   v               v
4. 组装完整上下文
   |
   v
5. 导航后同步队友位置（如有导航）
   |
   v
6. Pro 生成叙述
   |
   v
7. 队友响应（显式交互→Pro，被动→Flash）
   |
   v
8. 分发事件到队友图谱
   |
   v
CoordinatorResponse → 前端
```

### 依赖注入与单例

- `app/dependencies.py`: FastAPI 依赖注入入口
- `get_coordinator()` → `AdminCoordinator.get_instance()`（单例）
- `get_graph_store()` → `GraphStore()`（单例）
- 所有路由通过 `Depends()` 获取服务实例

### LLM 提示词

所有 LLM 系统提示存放在 `app/prompts/`：
- `flash_analysis.md`: Flash 意图分析
- `flash_cpu_system.md`: Flash CPU 系统提示
- `intent_parse.md`: 意图解析
- `pro_dm_system.md`: Pro DM 系统提示
- `teammate_decision.md`: 队友决策
- `teammate_response.md`: 队友响应
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

**MCP 传输配置：**
- `MCP_TOOLS_TRANSPORT`: Game Tools 传输方式（默认: `stdio`）
- `MCP_TOOLS_ENDPOINT`: Game Tools 端点（默认: `stdio://game_tools_server`）
- `MCP_COMBAT_TRANSPORT`: Combat 传输方式（默认: `stdio`）
- `MCP_COMBAT_ENDPOINT`: Combat 端点（默认: `stdio://combat_mcp_server`）

**Gemini 模型（均可通过环境变量覆盖，见 `app/config.py`）：**
- `GEMINI_FLASH_MODEL`: Flash 模型（默认: `gemini-3-flash-preview`）
- `GEMINI_PRO_MODEL`: Pro 模型（默认: `gemini-3-pro-preview`）
- `ADMIN_FLASH_MODEL` / `ADMIN_PRO_MODEL`: Admin 层模型
- `ADMIN_FLASH_THINKING_LEVEL` / `ADMIN_PRO_THINKING_LEVEL`: 思考级别（默认: `low`）

**其他：**
- `FIRESTORE_DATABASE`: 数据库名称（默认: `(default)`）
- `MEMORY_WINDOW_TOKENS`: 窗口大小（默认: `120000`）
- `MEMORY_INSERT_BUDGET_TOKENS`: 插入预算（默认: `20000`）
- `INSTANCE_POOL_MAX_INSTANCES`: NPC 实例池上限（默认: `20`）

## 代码规范

- Python 4 空格缩进，模块级文档字符串
- Pydantic 模型放在 `app/models/`
- 路由放在 `app/routers/`，服务放在 `app/services/`
- 提交信息：简短，通常中文，偶尔使用 Conventional Commit 前缀
- 无格式化配置；遵循 PEP 8，保持与现有代码风格一致
