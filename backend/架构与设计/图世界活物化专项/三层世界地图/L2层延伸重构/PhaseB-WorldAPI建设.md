# Phase B 执行记录：WorldAPI 建设

> **执行日期**: 2026-02-23
> **状态**: 已完成
> **验证**: 655 passed / 126 failed / 15 errors（与基线完全一致，零回归）

---

## 目标

在 Phase A 层级净化基础上，建立 L2 WorldAPI 边界：L1 Agent 工具不再直接 `ctx.session.xxx()` 穿透到 L3 SessionRuntime，而是通过 `ctx.api.xxx()` 走 L2 统一接口。

---

## Step B1: 创建 `app/services/world_api.py`

### 新建

| 文件 | 行数 | 说明 |
|------|------|------|
| `app/services/world_api.py` | ~80 | L2 薄委托门面，16 个方法全部委托到 SessionRuntime |

### WorldAPI 方法清单

| 方法 | 委托目标 | 分组 |
|------|----------|------|
| `heal(amount)` | `session.heal()` | Stat Ops |
| `damage(amount)` | `session.damage()` | Stat Ops |
| `add_xp(amount)` | `session.add_xp()` | Stat Ops |
| `add_item(item_id, item_name, quantity)` | `session.add_item()` | Stat Ops |
| `remove_item(item_id, quantity)` | `session.remove_item()` | Stat Ops |
| `activate_event(event_id)` | `session.activate_event()` | Event Ops |
| `complete_event(event_id, outcome_key)` | `session.complete_event()` | Event Ops |
| `fail_event(event_id, reason)` | `session.fail_event()` | Event Ops |
| `advance_stage(event_id, stage_id)` | `session.advance_stage()` | Event Ops |
| `complete_event_objective(event_id, objective_id)` | `session.complete_event_objective()` | Event Ops |
| `advance_chapter(target_chapter_id, transition_type)` | `session.advance_chapter()` | Narrative Ops |
| `complete_objective(objective_id)` | `session.complete_objective()` | Narrative Ops |
| `update_disposition(npc_id, deltas, reason)` | `session.update_disposition()` | Narrative Ops |
| `async recall(role, actor_id, seeds, **kw)` | `await session.recall()` | Memory Ops |
| `record_memory(owner_id, memory_type, **kw)` | `session.record_memory()` | Memory Ops |
| `set_flash_result(prompt, result)` | `session.flash_results[prompt] = bool(result)` | Flash Results |

---

## Step B2: 修改 `app/agentic/immersive_tools.py`

### B2a: AgenticContext 新增 `api` 字段

| 位置 | 变更 |
|------|------|
| `AgenticContext` dataclass 末尾 | 新增 `api: Any = None  # L2 WorldAPI — 所有游戏操作走此接口` |

`session` 字段保留（`gm_extra_tools` 闭包 + Pipeline 传递仍需要）。

### B2b: 24 处调用改写

**null check（7 处）**：`if not ctx.session:` → `if not ctx.api:`

| 函数 | 旧 | 新 |
|------|----|----|
| `recall_experience` | `if not ctx.session:` | `if not ctx.api:` |
| `form_impression` | `if not ctx.session:` | `if not ctx.api:` |
| `complete_event` | `if not ctx.session:` | `if not ctx.api:` |
| `advance_chapter` | `if not ctx.session:` | `if not ctx.api:` |
| `fail_event` | `if not ctx.session:` | `if not ctx.api:` |
| `report_flash_evaluation` | `if not hasattr(ctx.session, "flash_results"):` | `if not ctx.api:` |
| `create_memory` | `if not ctx.session:` | `if not ctx.api:` |

**方法调用（18 处）**：`ctx.session.xxx()` → `ctx.api.xxx()`

| 函数 | 变更 |
|------|------|
| `react_to_interaction` | `ctx.session.update_disposition(...)` → `ctx.api.update_disposition(...)` |
| `recall_experience` | `ctx.session.recall(...)` → `ctx.api.recall(...)` |
| `form_impression` | `ctx.session.record_memory(...)` → `ctx.api.record_memory(...)` |
| `complete_event` | `ctx.session.complete_event(...)` → `ctx.api.complete_event(...)` |
| `advance_chapter` | `ctx.session.advance_chapter(...)` → `ctx.api.advance_chapter(...)` |
| `fail_event` | `ctx.session.fail_event(...)` → `ctx.api.fail_event(...)` |
| `report_flash_evaluation` | `ctx.session.flash_results[prompt] = bool(result)` → `ctx.api.set_flash_result(prompt, bool(result))` |
| `heal_player` | `ctx.session.heal(...)` → `ctx.api.heal(...)` |
| `damage_player` | `ctx.session.damage(...)` → `ctx.api.damage(...)` |
| `add_xp` | `ctx.session.add_xp(...)` → `ctx.api.add_xp(...)` |
| `add_item` | `ctx.session.add_item(...)` → `ctx.api.add_item(...)` |
| `remove_item` | `ctx.session.remove_item(...)` → `ctx.api.remove_item(...)` |
| `activate_event` | `ctx.session.activate_event(...)` → `ctx.api.activate_event(...)` |
| `complete_objective` | `ctx.session.complete_objective(...)` → `ctx.api.complete_objective(...)` |
| `advance_stage` | `ctx.session.advance_stage(...)` → `ctx.api.advance_stage(...)` |
| `complete_event_objective` | `ctx.session.complete_event_objective(...)` → `ctx.api.complete_event_objective(...)` |
| `update_disposition` (GM) | `ctx.session.update_disposition(...)` → `ctx.api.update_disposition(...)` |
| `create_memory` | `ctx.session.record_memory(...)` → `ctx.api.record_memory(...)` |

---

## Step B3: 修改 `pipeline_orchestrator.py` — 4 处 WorldAPI 注入

| Line | 方法 | 角色 | 注入 |
|------|------|------|------|
| ~290 | `process()` B 阶段 | GM | `gm_api = WorldAPI(session, role="gm", agent_id="gm")` → `api=gm_api` |
| ~776 | `process_interact_stream()` B1 NPC | NPC | `npc_api = WorldAPI(session, role="npc", agent_id=npc_id)` → `api=npc_api` |
| ~840 | `process_interact_stream()` B2 GM Observer | GM | `gm_obs_api = WorldAPI(session, role="gm", agent_id="gm")` → `api=gm_obs_api` |
| ~1044 | `process_private_chat_stream()` NPC | NPC | `npc_api = WorldAPI(session, role="npc", agent_id=npc_id)` → `api=npc_api` |

每处均使用 lazy import（`from app.services.world_api import WorldAPI`），`session=session` 保留。

---

## Step B4: 修改 `teammate_response_service.py` — 1 处

| Line | 变更 |
|------|------|
| ~1002 | `from app.services.world_api import WorldAPI` + `tm_api = WorldAPI(session, role="teammate", agent_id=member.character_id)` → `api=tm_api` |

---

## Step B5: 更新测试文件（3 个）

### `tests/test_phase4c_immersive.py`

- `_make_ctx()`：新增 `api = overrides.pop("api", None) or MagicMock()`，传入 `api=api`
- `replace_all` 将所有 `ctx.session.xxx` → `ctx.api.xxx`（~20 处）
- `replace_all` 将所有 `ctx.session = None` → `ctx.api = None`（4 处）
- `report_flash_evaluation` 测试特殊处理：
  - `test_stores_result`：`ctx.api.flash_results["Is it raining?"] is True` → `ctx.api.set_flash_result.assert_called_once_with("Is it raining?", True)`
  - `test_no_flash_results_attr` → 改名为 `test_no_api_returns_error`，改为 `ctx.api = None`
- `TestCreateMemory` 3 个测试：从 `session=MagicMock()` + `session.record_memory` 改为 `ctx.api.record_memory`（直接在 ctx 上设置 mock）

### `tests/test_phase4b.py`

- `_make_ctx()`：新增 `api` 参数，默认创建 `api=MagicMock(recall=AsyncMock(return_value=[]), record_memory=MagicMock(return_value="mem_test"))`
- `TestBoundToolCallsWithCtx.test_bound_tool_calls_with_ctx`：`mock_session.update_disposition` → `mock_api.update_disposition`，传入 `api=mock_api`
- `TestRecallExperience`：`mock_session.recall` → `mock_api.recall`，null check 改为 `ctx.api = None`
- `TestFormImpression`：`mock_session.record_memory` → `mock_api.record_memory`，null check 改为 `ctx.api = None`
- `TestCompleteEvent`：`mock_session.complete_event` → `mock_api.complete_event`，null check 改为 `ctx.api = None`
- `TestStubToolsStillWork`：`mock_session.advance_chapter/fail_event` → `mock_api.xxx`

### `tests/test_immersive_tools_wg.py`

- `_make_ctx()`：传入 `api=session`（`_FakeSession` 实现了与 WorldAPI 相同的方法接口，避免触发 `app.services.__init__` 链式导入）
- `test_form_impression_stub_without_session` → 改名 `test_form_impression_stub_without_api`，改为 `ctx.api = None`
- `test_recall_experience_stub_without_session` → 改名 `test_recall_experience_stub_without_api`，改为 `ctx.api = None`

> **注意**: `test_immersive_tools_wg.py` 中未使用 `WorldAPI` 类直接导入，而是将 `_FakeSession` 实例直接作为 `api` 传入。原因：`from app.services.world_api import WorldAPI` 会触发 `app/services/__init__.py`，进而链式导入已删除的 `app.services.game_session_store`（预存 P0 环境问题）。`_FakeSession` 实现了完整的方法签名，功能等价，测试目标不变（测 immersive tools 行为，非测 WorldAPI 本身）。

---

## 验证结果

| 检查项 | 结果 |
|--------|------|
| `ctx.session.` 残留（immersive_tools.py 函数体） | 0 |
| `ctx.api.` 总数（immersive_tools.py） | 18 处调用 + 7 处 null check = 25 |
| Phase B 相关测试（4 文件） | **90 passed** |
| 全量测试基线 | **655 passed** / 126 failed / 15 errors（零回归） |

---

## 执行中发现

### test_immersive_tools_wg.py 的导入链问题

`from app.services.world_api import WorldAPI` 在 wg 测试中触发导入链：`app.services` → `admin_coordinator` → `flash_cpu_service` → `mcp_client_pool` → `mcp`（未安装），最终导致 8 个测试 collection error。

**解法**: 用 `_FakeSession` 实例直接作为 `api` 字段，绕过导入。WorldAPI 是纯委托，`_FakeSession` 实现了相同接口，语义等价。

**教训**: `world_api.py` 文件本身无问题（语法验证通过），但通过 `app.services` 包路径导入会触发 `__init__.py` 的全量链式导入。当需要在测试中隔离导入时，可用协议鸭子类型替代真实 WorldAPI。

---

## 最终目录结构

### 新增 `app/services/world_api.py`

```
app/services/
├── world_api.py             ← L2 WorldAPI 门面（B1 新建，~80 行）
├── memory_graphizer.py      ← 已改造（委托 graph_writer）
├── event_llm_service.py     ← 已搬迁（A3）
└── admin/
    └── pipeline_orchestrator.py  ← 已注入 WorldAPI（B3，4 处）
```

---

## 涉及部件

### 新增部件

| 文件 | 说明 |
|------|------|
| `app/services/world_api.py` | L2 WorldAPI 门面（薄委托，16 方法） |

### 改动部件

| 文件 | 改动性质 |
|------|------------|
| `app/agentic/immersive_tools.py` | AgenticContext 新增 `api` 字段；25 处 `ctx.session` → `ctx.api` 改写 |
| `app/services/admin/pipeline_orchestrator.py` | 4 处 AgenticContext 构造注入 WorldAPI |
| `app/services/teammate_response_service.py` | 1 处 AgenticContext 构造注入 WorldAPI |
| `tests/test_phase4c_immersive.py` | `_make_ctx()` + 断言方向（session→api） |
| `tests/test_phase4b.py` | `_make_ctx()` + 断言方向（session→api） |
| `tests/test_immersive_tools_wg.py` | `_make_ctx()` WorldAPI 等价注入 |

### 弃用部件

| 部件 | 说明 |
|------|------|
| `ctx.session.heal/damage/add_xp/...` 直接调用模式 | 已改为 `ctx.api.xxx()`；session 字段保留但不再用于游戏操作 |
