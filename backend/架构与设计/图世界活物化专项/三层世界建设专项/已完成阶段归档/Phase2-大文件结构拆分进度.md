# Phase 2: 大文件结构拆分进度

> 基线：790 passed / 24 failed（全为环境依赖，与 Phase 1 完全一致）
> **Phase 2 已全部完成 ✅**

## 已完成

### 1. `pipeline_orchestrator.py` — 提取 process() 为 3 阶段方法

**状态：已完成 ✅**

- 新增 `_PipelineState` dataclass（输入参数 + A/B/C 阶段间共享状态）
- `process()` 退化为 ~10 行编排骨架：创建 state → 调用三阶段 → return
- 提取方法：
  - `_stage_a_assemble_context(s)` — RuntimeSetup + ContextAssemble + EnginePreExec + NPCReactor
  - `_stage_b_agentic_session(s)` — AgenticExecutor + RoleRegistry 沉浸式工具
  - `_stage_c_post_process(s)` → `CoordinatorResponse` — 队友响应 + 历史 + 持久化 + 构建响应
- 各阶段方法头部从 state 解包局部变量，尾部写回 state，代码体零改动
- 回归测试：790 passed / 24 failed ✅

### 2. `game_v2.py` — 按域拆分路由

**状态：已完成 ✅**

新建 5 个文件：

| 文件 | 内容 | 端点数 |
|------|------|--------|
| `app/routers/_common.py` | `map_exception_to_http` 共享工具 | — |
| `app/routers/game_combat.py` | 战斗 4 端点 (trigger/action/start/resolve) | 4 |
| `app/routers/game_party.py` | 队伍 5 端点 + 3 个 Pydantic 模型 | 5 |
| `app/routers/game_narrative.py` | 叙事 4 端点 + 事件 2 端点 | 6 |
| `app/routers/game_npc.py` | interact + passerby 3 + private-chat + 2 模型 | 5 |

- `game_v2.py` 保留：世界/会话/角色/上下文/输入/位置/时间/掷骰/历史 (~490行，原 1048 行)
- 子路由通过 `router.include_router()` 挂载，URL 路径零变化
- 向后兼容再导出：`CreatePartyRequest`, `AddTeammateRequest`, `add_teammate`, `create_party` 等（测试依赖）
- 回归测试：790 passed / 24 failed ✅

### 3. `admin_coordinator.py` — 提取域服务

**状态：已完成 ✅**

新建 2 个文件：

| 文件 | 内容 | 方法数 |
|------|------|--------|
| `app/services/admin/combat_coordinator.py` | CombatCoordinator 类 | 8 |
| `app/services/admin/narrative_coordinator.py` | NarrativeCoordinator 类 | 6 |

- **CombatCoordinator** (218行)：`start_combat`, `resolve_combat`, `trigger_combat`, `execute_combat_action`, `_call_combat_tool`, `_build_state_delta`, `_apply_delta`, `_sync_combat_result_to_character`
- **NarrativeCoordinator** (426行)：`resume_session`, `_generate_resume_narration`, `generate_opening_narration`, `_generate_chapter_transition`, `_get_world_background`, `_get_character_roster`
- AdminCoordinator 从 1264 行 → 694 行（-570 行），保留薄委托层
- `_detect_output_anomalies` 保留在 AdminCoordinator（测试引用）
- 清理：死缓存字段、未使用 import（`Path`, `CombatStartRequest`）
- 测试 `test_admin_story_flow.py` 更新为直接测试 NarrativeCoordinator
- 回归测试：790 passed / 24 failed ✅

### 4. `teammate/response_service.py` — 提取 context_builder

**状态：已完成 ✅**

新建 1 个文件：

| 文件 | 内容 | 方法数 |
|------|------|--------|
| `app/agentic/teammate/context_builder.py` | TeammateContextBuilder 类 | 12 |

- **TeammateContextBuilder** (241行)：`load_prompt`, `resolve_round_basics`, `get_location_context`, `build_filtered_context`, `get_effective_player_input`, `format_previous_responses`, `format_location_values`, `chunk_text`, `members_for_decision`, `get_agentic_system_prompt`, `build_response_prompt`, `format_context_package`
- response_service.py 从 1136 行 → 953 行（-183 行），所有上下文组装调用通过 `self._ctx.*` 委托
- 测试 `test_teammate_migration.py` mock 路径已更新
- 回归测试：790 passed / 24 failed ✅

## 汇总

| 文件 | 原行数 | 拆分后行数 | 提取目标 |
|------|--------|-----------|----------|
| `pipeline_orchestrator.py` | 1545 | 1545（内部拆分） | 同文件 3 阶段方法 |
| `game_v2.py` | 1048 | ~490 | 5 子路由文件 |
| `admin_coordinator.py` | 1264 | 694 | combat_coordinator + narrative_coordinator |
| `teammate/response_service.py` | 1136 | 953 | context_builder |

## 验证命令

```bash
GEMINI_API_KEY=dummy GOOGLE_APPLICATION_CREDENTIALS=/dev/null venv/bin/python3 -m pytest tests/ -q \
  --ignore=tests/test_combat_mcp_session_validation.py \
  --ignore=tests/test_fastapi_to_mcp.py
```
预期：790 passed / 24 failed
