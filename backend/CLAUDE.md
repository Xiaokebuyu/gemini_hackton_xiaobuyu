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

# 带环境验证的快速启动
./run.sh

# 运行 MCP 服务器（stdio 传输）
python run_mcp_server.py

# 运行 MCP 服务器（HTTP/SSE 传输）
python run_mcp_server.py --transport streamable-http --port 8080

# 运行测试
pytest -v
pytest tests/test_spreading_activation.py -v

# 集成测试（需要 Firestore/Gemini 凭证）
PYTHONPATH=backend pytest tests/test_integration.py -v -s
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
   - `admin_coordinator.py`: 主编排器，协调所有子系统
   - `world_runtime.py`: 世界状态运行时
   - `flash_cpu_service.py`: 快速路由和解析
   - `pro_dm_service.py`: 主角色 AI 服务

2. **多层 NPC AI 系统**
   - **Passerby Service**: 轻量 NPC 交互（gemini-3-flash-preview）
   - **Pro Service**: 主角色 AI，扩展上下文（gemini-3-pro-preview）
   - **GM Service**: 世界叙事和事件生成
   - 上下文缓存和实例池化提升性能

3. **MCP 记忆网关** (`app/mcp/`)
   - `game_tools_server.py`: 游戏 MCP 服务器
   - 热记忆（会话）-> 温记忆（归档话题）-> 冷记忆（向量索引，占位）

4. **知识图谱** (`app/services/memory_graph.py`, `spreading_activation.py`)
   - 记忆节点（人物、地点、事件、概念）
   - 扩散激活算法查找相关概念
   - 两个作用域：世界级和角色级图谱

5. **战斗系统** (`app/combat/`)
   - D&D 风格机制，d20 判定
   - `combat_engine.py`: 核心战斗逻辑
   - `ai_opponent.py`: 基于性格的敌人 AI
   - 通过 `combat_mcp_server.py` 暴露 MCP 接口

6. **游戏循环** (`app/routers/game.py`, `app/routers/game_master.py`)
   - 会话和场景状态管理
   - 带可见性规则的事件派发

### 数据流

```
用户 -> 编排器 -> 主 LLM（带窗口上下文）
                      |
                [需要记忆？]
                      |
              MCP: memory_request
                      |
           路由器 -> 检索器 -> 拼装器
                      |
              LLM 获取 insert_messages + 窗口
                      |
              MCP: memory_commit -> Firestore
```

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
- `GEMINI_API_KEY`: 必需
- `GOOGLE_APPLICATION_CREDENTIALS`: Firebase 凭证路径
- `FIRESTORE_DATABASE`: 数据库名称（默认: "(default)"）
- `MEMORY_WINDOW_TOKENS`: 窗口大小（默认: 120000）
- `MEMORY_INSERT_BUDGET_TOKENS`: 插入预算（默认: 20000）
- `INSTANCE_POOL_MAX_INSTANCES`: NPC 实例池上限（默认: 20）
- `THINKING_ENABLED`: 启用扩展思考（默认: true）
- `THINKING_LEVEL`: 思考级别 lowest/low/medium/high（默认: medium）

Gemini 模型配置在 `app/config.py`：
- `gemini-3-flash-preview`: Flash 服务（快速路由、NPC 交互）
- `gemini-3-pro-preview`: Pro 服务（主角色 AI、深度对话）

## 代码规范

- Python 4 空格缩进，模块级文档字符串
- Pydantic 模型放在 `app/models/`
- 路由放在 `app/routers/`，服务放在 `app/services/`
- 提交信息：简短，通常中文，偶尔使用 Conventional Commit 前缀
- 无格式化配置；遵循 PEP 8，保持与现有代码风格一致
