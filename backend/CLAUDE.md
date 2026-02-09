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

### 世界数据提取（统一管线）

```bash
# 从酒馆卡片 JSON 一步生成全部结构化文件（Batch API 模式，推荐）
python -m app.tools.init_world_cli extract \
    --input "worldbook.json" \
    --output data/goblin_slayer/structured/ \
    --model gemini-3-pro-preview \
    --thinking-level none \
    --relabel-edges --enrich-entities

# 直接调用模式（实时返回，无 Batch 等待，但无成本优惠）
python -m app.tools.init_world_cli extract \
    --input "worldbook.json" \
    --output data/goblin_slayer/structured/ \
    --model gemini-3-pro-preview \
    --direct --relabel-edges --enrich-entities

# 主要选项：
#   --model              Gemini 模型（默认 gemini-3-pro-preview）
#   --thinking-level     思考级别：high/medium/low/lowest/none（Batch API 不支持时用 none）
#   --direct             直接 LLM 调用而非 Batch API
#   --relabel-edges      LLM 重标注 unknown 边类型
#   --enrich-entities    提取 D&D 实体数据（怪物/物品/技能）
#   --mainlines FILE     指定已有的 mainlines.json（否则自动生成）
```

### 交互式开发 CLI

```bash
python -m app.tools.game_master_cli              # 完整游戏管理
python -m app.tools.game_master_cli --setup-demo # 初始化演示世界
python -m app.tools.flash_natural_cli            # Flash 服务测试
python -m app.tools.gm_natural_cli               # GM 叙述测试
python -m app.tools.init_world_cli               # 世界初始化（查看所有子命令）
```

## 架构

> **注意**: 根目录下的 `README.md`、`ARCHITECTURE.md`、`MEMORY_GATEWAY_INTEGRATION.md` 内容已过时，不应作为架构参考。以本文件为准。

### Flash-Only v2 数据流

这是核心请求处理流程，入口为 `AdminCoordinator.process_player_input_v2()`：

```
玩家输入
    ↓
1. 收集基础上下文（世界状态、会话状态、场景、队伍）
    ↓
2. StoryDirector 预评估（机械条件 → auto_fired_events + 语义条件 → pending_flash）
    ↓
3. Flash 一次性分析（intent + operations + memory seeds + Flash 条件评估）
    ↓
4. StoryDirector 后评估（合并结果 → fired_events + chapter_transition）
    ↓
5. 并行：记忆召回  |  顺序：执行 Flash 操作
    ↓
6. 组装完整上下文
    ↓
7. 导航后同步队友位置
    ↓
8. Flash GM 生成叙述
    ↓
9. 队友响应（显式交互用更高模型，被动用 Flash）
    ↓
10. 分发事件到队友图谱
    ↓
CoordinatorResponse → 前端
```

### 核心系统

1. **游戏编排器** (`app/services/admin/`)
   - `admin_coordinator.py`: 主编排器，协调所有子系统（单例，`AdminCoordinator.get_instance()`）
   - `flash_cpu_service.py`: Flash 意图分析和操作执行
   - `world_runtime.py`: 世界状态运行时
   - `state_manager.py`: 会话状态管理，内存快照 + StateDelta 增量追踪（`app/models/state_delta.py`）

2. **故事导演与事件系统** (`app/services/admin/`)
   - `story_director.py`: 两阶段事件评估（pre_evaluate → Flash → post_evaluate）
     - 返回 `PreDirective`（auto_fired_events + pending_flash_conditions + pacing_action）
     - 返回 `StoryDirective`（fired_events + chapter_transition + side_effects）
   - `condition_engine.py`: 纯机械条件引擎（8 种结构化条件 + FLASH_EVALUATE 语义条件）
     - 条件类型：LOCATION / NPC_INTERACTED / TIME_PASSED / ROUNDS_ELAPSED / PARTY_CONTAINS / EVENT_TRIGGERED / OBJECTIVE_COMPLETED / GAME_STATE
     - 支持 AND/OR/NOT 嵌套逻辑
   - `event_service.py`: 结构化事件入图 + EventBus 发布
   - `event_llm_service.py`: 自然语言事件 3 步管线（parse → encode → transform_perspective）
   - 节奏控制（PacingConfig）：subtle_environmental → npc_reminder → direct_prompt → forced_event

3. **NPC AI 系统** — 三层模型 (`NPCTierConfig`) + 双层认知
   - **Passerby**: 轻量路人交互（无 thinking，无上下文窗口）
   - **Secondary**: 次要角色（medium thinking，共享上下文窗口）
   - **Main**: 主要角色（low thinking，完整 200K 上下文窗口 + 扩散激活记忆召回）
   - **双层认知架构**（`instance_manager.py` `NPCInstance`）：
     - 层1 同步工作记忆：`ContextWindow`（`context_window.py`）— 实时对话上下文，200K token 容量
     - 层2 潜意识记忆图谱：`FlashService` + `MemoryGraph` — 长期语义记忆，扩散激活检索
     - 当工作记忆达 90% 阈值 → `MemoryGraphizer.graphize()` 将旧消息转为图谱节点 → 释放 token
     - 记忆注入：`build_context_with_injection()` 将召回的图谱节点作为"相关记忆"注入系统提示
   - 实例池化：`InstanceManager`，LRU 淘汰（默认 20 实例），淘汰前强制图谱化

4. **队伍系统** (`app/services/party_service.py`, `teammate_response_service.py`)
   - 队友每回合自动决策是否响应
   - 队友位置随玩家导航自动同步

5. **MCP 工具层** (`app/mcp/`, `app/services/mcp_client_pool.py`)
   - `game_tools_server.py`: 游戏 MCP 服务器
   - `app/combat/combat_mcp_server.py`: 战斗 MCP 服务器
   - 工具模块在 `app/mcp/tools/`：graph、narrative、navigation、npc、party、passerby、time
   - **MCPClientPool 单例**（`mcp_client_pool.py`）：
     - 每服务器调用锁（防止 stdio 交错）+ 连接锁（防止并发重连）
     - 健康检查 + 自动重连 + 30 秒冷却（超时错误豁免冷却）
     - 工具级超时：默认 20s，`npc_respond` 90s

6. **知识图谱** (`app/services/memory_graph.py`, `spreading_activation.py`)
   - 扩散激活算法查找相关概念
   - **GraphScope 统一寻址**（`app/models/graph_scope.py`）：6 种作用域
     - `world` → 世界级知识 | `chapter(cid)` → 章节 | `area(cid, aid)` → 区域
     - `location(cid, aid, lid)` → 具体地点 | `character(char_id)` → 角色个人 | `camp` → 队伍营地
   - `GraphStore`（`app/services/graph_store.py`）通过 `_get_base_ref_v2()` 将 scope 映射到 Firestore 路径
   - `memory_graphizer.py`: 对话自动入图

7. **战斗系统** (`app/combat/`)
   - D&D 风格 d20 判定
   - `combat_engine.py`: 核心战斗逻辑
   - `ai_opponent.py`: 基于性格的敌人 AI

8. **世界数据处理** (`app/tools/worldbook_graphizer/`)
   - `unified_pipeline.py`: 统一世界数据提取管线（支持 Batch API）
   - 从小说/Lorebook 提取地图、角色、知识图谱等结构化数据

### 路由结构

单一路由器 `game_v2_router` 挂载于 `/api/game`（`app/routers/game_v2.py`）：

- 世界: `GET /worlds`
- 会话: `POST /{world_id}/sessions`, `GET /{world_id}/sessions`, `GET .../sessions/{session_id}`, `POST .../sessions/{session_id}/resume`
- 角色创建: `GET .../character-creation/options`, `POST .../character`, `GET .../character`
- 导航: `GET .../location`, `POST .../navigate`, `GET .../sub-locations`, `POST .../sub-location/enter`, `POST .../sub-location/leave`
- 时间: `GET .../time`, `POST .../time/advance`, `POST .../advance-day`
- 游戏: `POST .../input`（主入口）, `POST .../input/stream`（SSE）, `POST .../scene`, `GET .../context`
- 对话: `POST .../dialogue/start`, `POST .../dialogue/end`, `POST .../private-chat/stream`（SSE）
- 战斗: `POST .../combat/trigger`, `POST .../combat/start`, `POST .../combat/action`, `POST .../combat/resolve`
- 队伍: `POST .../party`, `GET .../party`, `POST .../party/add`, `DELETE .../party/{character_id}`, `POST .../party/load`
- 叙事: `GET .../narrative/progress`, `GET .../narrative/flow-board`, `GET .../narrative/current-plan`, `GET .../narrative/available-maps`, `POST .../narrative/trigger-event`
- 事件: `POST /{world_id}/events/ingest`, `POST /{world_id}/events/ingest-natural`
- 路人: `GET .../passersby`, `POST .../passersby/spawn`, `POST .../passersby/dialogue`
- 历史: `GET .../history`

### 依赖注入与单例

- `app/dependencies.py`: `@lru_cache()` 实现单例
- `get_coordinator()` → `AdminCoordinator.get_instance()`
- `get_graph_store()` → `GraphStore()`
- `MCPClientPool.get_instance()` → 异步单例，双重检查锁
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
  graphs/world/nodes/, edges/                                    ← GraphScope.world()
  chapters/{cid}/graph/nodes/, edges/                            ← GraphScope.chapter(cid)
  chapters/{cid}/areas/{aid}/graph/nodes/, edges/                ← GraphScope.area(cid, aid)
  chapters/{cid}/areas/{aid}/locations/{lid}/graph/nodes/, edges/ ← GraphScope.location(cid, aid, lid)
  characters/{char_id}/nodes/, edges/, instances/                ← GraphScope.character(char_id)
  camp/graph/nodes/, edges/                                      ← GraphScope.camp()
  maps/{map_id}/locations/{location_id}/
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
