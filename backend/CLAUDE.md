# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

AI 驱动的互动式 RPG 游戏后端，具备智能记忆管理和知识图谱能力。采用 V4 Runtime Pipeline 架构——分层运行时 + Agentic 工具调用 + 结构化上下文组装。

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

### V4 Runtime Pipeline 数据流

核心请求处理流程，入口为 `AdminCoordinator.process_player_input_v3()` → `PipelineOrchestrator.process()`：

```
玩家输入
    ↓
A 阶段: 上下文组装
  1. GameRuntime.get_world() → WorldInstance（静态数据注册表）
  2. SessionRuntime.restore() → 加载状态/队伍/历史/角色
  3. ContextAssembler.assemble() → 结构化上下文快照
  4. AreaRuntime.check_events() → 机械条件驱动事件状态机
    ↓
B 阶段: Agentic 会话
  5. AgenticExecutor.run() → 模型自主工具调用 + 叙述生成
     - 工具来源：RoleRegistry 沉浸式工具(role="gm") + GM extra_tools（MCP 依赖）
     - 引擎排除：ENGINE_TOOL_EXCLUSIONS 过滤已执行操作对应的工具
    ↓
C 阶段: 后处理
  6. AreaRuntime.check_events() → 后检查事件（agentic 操作可能改变状态）
  6b. 自动时间推进 → session.advance_time(10)（引擎已推进时间时跳过：move_area/rest）
  7. CompanionInstance 事件分发 → 同伴接收已完成事件
  8. TeammateResponseService.process_round() → 队友响应（使用 AgenticExecutor + RoleRegistry 沉浸式工具）
  9. SessionRuntime.persist() → 统一持久化
    ↓
CoordinatorResponse → 前端
```

**NPC 直接交互管线** (`POST .../interact/stream`)：
```
Setup → NPC Agentic Response → GM Observer(可[PASS]) → Teammate Observer → Dialogue Options → Persist
```
- NPC 通过 AgenticExecutor 使用 role="npc" 沉浸式工具响应
- GM 默认 [PASS]（空 narration），仅在需要环境渲染时介入
- 对话选项由 LLM 生成 4 个结构化选项（失败时回退通用选项）

**私聊管线** (`POST .../private-chat/stream`)：
```
Setup → InstanceManager 双层认知 → NPC Agentic Response → Dialogue Options → Persist
```
- 走 PipelineOrchestrator，完整 SessionRuntime + AgenticExecutor + SceneBus + SessionHistory
- 系统提示来自 InstanceManager（保留双层认知：上下文窗口 + 记忆图谱化）
- GM/队友观察在私密模式下跳过
- SSE 事件格式与 /interact/stream 统一（interact_start/npc_response/dialogue_options/complete）

### 核心系统

1. **V4 Runtime 层** (`app/runtime/`)
   - `game_runtime.py`: 全局运行时单例，管理 WorldInstance 缓存
   - `world_instance.py`: 世界静态数据注册表（地图/角色/章节/知识图谱，启动时加载）
   - `session_runtime.py`: 会话运行时，统一管理状态/队伍/历史/角色；`advance_time(minutes)` 通过 TimeManager 推进时间并触发时段/日期事件；Player 持久化双路径：主路径 WorldGraph 快照 + 兜底路径 CharacterStore，脏标记仅在至少一条路径成功后清除
   - `area_runtime.py`: 区域生命周期管理，事件状态机 + 章节转换检查
   - `context_assembler.py`: 结构化上下文组装（替代旧 `_build_context`）
   - `companion_instance.py`: 同伴系统实例，轻量事件日志 + 记忆摘要

2. **管线编排** (`app/services/admin/`)
   - `pipeline_orchestrator.py`: V4 薄编排层（A/B/C 三阶段 + NPC /interact 交互流 + /private-chat 私聊流）
   - `admin_coordinator.py`: 入口协调器（单例），委托核心逻辑到 PipelineOrchestrator
   - `flash_cpu_service.py`: Flash MCP 工具调用（`execute_request`：SPAWN_PASSERBY/NPC_DIALOGUE/START_COMBAT/ADD_TEAMMATE/REMOVE_TEAMMATE/DISBAND_PARTY/ABILITY_CHECK + `call_combat_tool`）+ agentic 系统提示加载
   - `world_runtime.py`: 世界状态运行时（薄壳：仅 `start_session` + `get_current_location`；SessionRuntime/FlashCPU 已解耦，不再依赖）
   - `state_manager.py`: 会话状态管理，内存快照 + StateDelta 增量追踪

   **Agentic 基础设施**（Phase 4b+4c 完成）：
   - `app/world/agentic_executor.py`: 统一 Agent 执行器（GM/NPC/队友共用），封装 LLM agentic 循环 + 工具录制/SSE 推送 + `exclude_tools` 引擎排除
   - `app/world/immersive_tools.py`: 30 个沉浸式工具 + AgenticContext + FEELING_MAP 情感翻译层 + bind 机制；GM 工具含 `heal_player`/`damage_player`/`add_xp`/`update_disposition`/`create_memory` 等 16 个（时间由 Pipeline 自动推进，不再有 `update_time` 工具）
   - `app/world/gm_extra_tools.py`: 8 个 GM MCP 依赖工具工厂（`npc_dialogue`/`start_combat`/`ability_check`/`add_teammate` 等）+ `ENGINE_TOOL_EXCLUSIONS` 引擎排除映射
   - `app/world/role_registry.py`: 按 (role, traits) 映射工具集 — gm={base,gm}, npc={base}, teammate={base,teammate}
   - GM/NPC/队友三条路径均使用 AgenticExecutor：
     - GM: `PipelineOrchestrator.process()` B-stage → AgenticExecutor + RoleRegistry(gm) + GM extra_tools
     - NPC: `/interact/stream` → AgenticExecutor + RoleRegistry(npc)
     - 队友: `TeammateResponseService` → AgenticExecutor + RoleRegistry(teammate)

3. **事件系统** (`app/services/admin/`)
   - `event_service.py`: 结构化事件入图 + EventBus 发布
   - `event_llm_service.py`: 自然语言事件 3 步管线（parse → encode → transform_perspective）
   - 事件状态机由 `AreaRuntime.check_events()` 驱动（替代旧 StoryDirector）

4. **NPC AI 系统** — 三层模型 (`NPCTierConfig`) + 双交互路径
   - **Passerby**: 轻量路人交互（无 thinking，无上下文窗口）
   - **Secondary**: 次要角色（medium thinking，共享上下文窗口）
   - **Main**: 主要角色（low thinking，完整 200K 上下文窗口 + 扩散激活记忆召回）
   - **双层认知架构**（`instance_manager.py` `NPCInstance`）：
     - 层1 同步工作记忆：`ContextWindow`（`context_window.py`）— 实时对话上下文，200K token 容量
     - 层2 潜意识记忆图谱：`FlashService` + `MemoryGraph` — 长期语义记忆，扩散激活检索
     - 当工作记忆达 90% 阈值 → `MemoryGraphizer.graphize()` 将旧消息转为图谱节点 → 释放 token
     - 记忆注入：`build_context_with_injection()` 将召回的图谱节点作为"相关记忆"注入系统提示
   - 实例池化：`InstanceManager`，LRU 淘汰（默认 20 实例），淘汰前强制图谱化
   - **直接交互路径**：`POST .../interact/stream`（SSE），NPC 通过 AgenticExecutor 使用沉浸式工具响应，支持 GM 观察、队友旁观、对话选项生成
   - **NPCReactor**（`app/services/npc_reactor.py`）：NPC 相关度推荐器，模板反应，不再使用 LLM

5. **队伍系统** (`app/services/party_service.py`, `teammate_response_service.py`)
   - 队友每回合自动决策是否响应
   - 队友位置随玩家导航自动同步
   - 队友使用 `AgenticExecutor` + RoleRegistry 沉浸式工具（4b 迁移完成）

6. **MCP 工具层** (`app/mcp/`, `app/services/mcp_client_pool.py`)
   - `game_tools_server.py`: 游戏 MCP 服务器
   - `app/combat/combat_mcp_server.py`: 战斗 MCP 服务器
   - 工具模块在 `app/mcp/tools/`：graph、narrative、navigation、npc、party、passerby、time
   - **MCPClientPool 单例**（`mcp_client_pool.py`）：
     - 每服务器调用锁（防止 stdio 交错）+ 连接锁（防止并发重连）
     - 健康检查 + 自动重连 + 30 秒冷却（超时错误豁免冷却）
     - 工具级超时：默认 20s，`npc_respond` 90s

7. **知识图谱** (`app/services/memory_graph.py`, `spreading_activation.py`)
   - 扩散激活算法查找相关概念
   - **GraphScope 统一寻址**（`app/models/graph_scope.py`）：6 种作用域
     - `world` → 世界级知识 | `chapter(cid)` → 章节 | `area(cid, aid)` → 区域
     - `location(cid, aid, lid)` → 具体地点 | `character(char_id)` → 角色个人 | `camp` → 队伍营地
   - `GraphStore`（`app/services/graph_store.py`）通过 `_get_base_ref_v2()` 将 scope 映射到 Firestore 路径
   - `memory_graphizer.py`: 对话自动入图

8. **战斗系统** (`app/combat/`)
   - D&D 风格 d20 判定
   - `combat_engine.py`: 核心战斗逻辑
   - `ai_opponent.py`: 基于性格的敌人 AI

9. **世界数据处理** (`app/tools/worldbook_graphizer/`)
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
- NPC 交互: `POST .../interact/stream`（SSE，NPC 直接交互：NPC回复→GM观察→队友旁观→对话选项）
- 对话: `POST .../dialogue/start`, `POST .../dialogue/end`, `POST .../private-chat/stream`（SSE，私聊走 Pipeline：NPC Agentic 回复→对话选项→持久化，GM/队友跳过）
- 战斗: `POST .../combat/trigger`, `POST .../combat/start`, `POST .../combat/action`, `POST .../combat/resolve`
- 队伍: `POST .../party`, `GET .../party`, `POST .../party/add`, `DELETE .../party/{character_id}`, `POST .../party/load`
- 叙事: `GET .../narrative/progress`, `GET .../narrative/flow-board`, `GET .../narrative/current-plan`, `GET .../narrative/available-maps`
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
- `flash_agentic_system.md`: V4 Agentic 系统提示（工具调用 + 叙述生成）
- `flash_analysis.md`: Flash 意图分析（输出严格 JSON）
- `flash_cpu_system.md`: Flash CPU 系统提示
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
