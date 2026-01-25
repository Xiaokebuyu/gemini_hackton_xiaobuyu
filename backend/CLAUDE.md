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

## 架构

### 核心系统

1. **MCP 记忆网关** (`app/mcp/`)
   - `memory_gateway.py`: 主编排器，提供 `session_snapshot`、`memory_request`、`memory_commit` 工具
   - `message_stream.py`: 滑动窗口管理（默认 120k tokens）
   - `truncate_archiver.py`: 归档旧消息，生成带 embedding 的见解
   - 热记忆（会话）-> 温记忆（归档话题）-> 冷记忆（向量索引，占位）

2. **知识图谱** (`app/services/memory_graph.py`, `spreading_activation.py`)
   - 记忆节点（人物、地点、事件、概念）
   - 扩散激活算法查找相关概念
   - 两个作用域：世界级和角色级图谱

3. **战斗系统** (`app/combat/`)
   - D&D 风格机制，d20 判定
   - `combat_engine.py`: 核心战斗逻辑
   - `ai_opponent.py`: 基于性格的敌人 AI
   - 通过 `combat_mcp_server.py` 暴露 MCP 接口

4. **游戏循环** (`app/services/game_loop_service.py`, `app/routers/game.py`)
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

Gemini 模型配置在 `app/config.py`：
- `gemini-2.5-flash-lite`: 路由和解析
- `gemini-3-flash-preview`: 带思考的主生成模型

## 代码规范

- Python 4 空格缩进，模块级文档字符串
- Pydantic 模型放在 `app/models/`
- 路由放在 `app/routers/`，服务放在 `app/services/`
- 提交信息：简短，通常中文，偶尔使用 Conventional Commit 前缀
- 无格式化配置；遵循 PEP 8，保持与现有代码风格一致
