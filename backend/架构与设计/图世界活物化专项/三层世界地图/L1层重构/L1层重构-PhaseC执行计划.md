# L1 层重构 Phase C — FlashCPU 溶解 + Store 断裂修复

> **创建时间**: 2026-02-24
> **状态**: 待执行
> **前置**: Phase A ✅ / Phase B ✅
> **测试基线**: 694 passed / 131 failed / 12 errors（零回归目标）
> **参考文档**: `L1层重构-执行计划.md`（母文档）、`Firestore残留Store迁移清单.md`（Store 断裂详情）

---

## 范围与决策记录

### FlashCPU（503 行）溶解决策

| 职责 | 行数 | 决策 | 理由 |
|------|------|------|------|
| 1. LLM 持有 + prompt 加载 | 68-74 | **提取** → 直注 + 模块级函数 | 简单机械替换 |
| 2. execute_request 分发器（7 种操作） | 76-336 | **删除** | 遗留，全部 7 种操作已废弃 |
| 3. call_combat_tool MCP 代理 | 488-503 | **内联到 admin_coordinator** | 战斗 MCP 未完成，仅 2 行搬运 |
| 4+5. state delta + combat sync | 338-411 | **内联到 admin_coordinator** | 战斗未完成，不改逻辑，仅搬运 |
| 6. NPC 对话 | 413-486 | **删除** | 遗留，/interact/stream 已替代 |

### gm_extra_tools 决策

**全部 8 个工具删除**，`gm_extra_tools.py` 文件删除。理由：
- `npc_dialogue`：遗留（/interact/stream 替代）
- 3 个战斗工具：战斗 MCP 未完成，不可用
- `add/remove/disband_teammate`：遗留（immersive_tools + PartyService 已覆盖）
- `ability_check`：遗留（immersive_tools 已覆盖）

连带删除：`_make_combat_action_tool` + `_is_combat_active`（teammate 中的战斗工具，同样因 MCP 未完成不可用）

### Store 断裂修复决策

| Store | 决策 | 方向（来自 Firestore 迁移清单） |
|-------|------|------|
| GameSessionStore | **不恢复，替换** | Session CRUD → SessionRuntime 新增工厂方法 |
| PartyStore | **不恢复，消除** | PartyService 已支持 `party_store=None` 纯内存模式 |
| CharacterStore | **不恢复，替换** | Character 读取 → `session.player` / WorldGraph PlayerNode |

---

## 执行步骤

### Step 0：instance_manager.py 遗漏修复（1 行）

Phase A 遗漏。

**操作**：

```python
# app/world/npc/instance_manager.py:403
# 旧:
from app.services.memory_graphizer import MemoryGraphizer
# 新:
from app.agentic.memory_graphizer import MemoryGraphizer
```

**涉及文件**：`app/world/npc/instance_manager.py`

---

### Step 1：Store 断裂修复 — admin_coordinator 脱离 3 个已删 Store

**目标**：消除 `admin_coordinator.py` 对 `GameSessionStore` / `PartyStore` / `CharacterStore` 的 import 和使用，解除 L2-1 链式导入阻断。

> **详细断裂分析见** `Firestore残留Store迁移清单.md` 的 Tier 1（T1-T5）。

#### 1a：SessionRuntime 扩展 — 新增 Session CRUD 类方法

`app/runtime/session_runtime.py` 新增 3 个类方法（不改现有实例方法）：

| 新方法 | 替代 | 说明 |
|--------|------|------|
| `@classmethod async create(world_id, participants)` | `GameSessionStore.create_session()` | 创建会话文档 + 返回 session_id |
| `@classmethod async list_sessions(world_id, user_id, limit)` | `GameSessionStore.list_sessions()` | 查询会话列表 |
| `@classmethod async get_session_meta(world_id, session_id)` | `GameSessionStore.get_session()` | 轻量读取会话元数据（不完整 restore） |

实现参考已删除的 `GameSessionStore`（Firestore路径：`worlds/{world_id}/sessions/{session_id}/`）。

#### 1b：admin_coordinator.py 替换 Store 引用

| 行号 | 旧代码 | 新代码 |
|------|--------|-------|
| 38 | `from app.services.game_session_store import GameSessionStore` | 删除 |
| 44 | `from app.services.party_store import PartyStore` | 删除 |
| 47 | `from app.services.character_store import CharacterStore` | 删除 |
| 80 | `self._session_store = session_store or GameSessionStore()` | 删除（不再需要） |
| 103 | `self.party_store = PartyStore()` | 删除（PartyService 已纯内存运行） |
| 105 | `self.character_store = CharacterStore()` | 删除 |
| 207 | `await self._session_store.create_session(...)` | `await SessionRuntime.create(...)` |
| 211 | `await self._session_store.get_session(...)` | `await SessionRuntime.get_session_meta(...)` |
| 226 | `await self._session_store.list_sessions(...)` | `await SessionRuntime.list_sessions(...)` |
| 246 | `await self.party_store.get_party(...)` | `session.party is not None`（通过已有 SessionRuntime） |
| 258,564,689 | `await self.character_store.get_character(...)` | `session.player is not None`（通过已有 SessionRuntime） |
| 284 | `await self._session_store.set_scene(...)` | 内联 Firestore 更新或走 SessionRuntime.persist() |

#### 1c：清理其他 Store 引用

| 文件 | 操作 |
|------|------|
| `app/services/admin/world_runtime.py:15,30` | 删除 GameSessionStore import + 实例化 |
| `app/services/__init__.py:7` | 删除 GameSessionStore 导出 |
| `app/world/party/party_service.py:21` | 删除 PartyStore import；`__init__` 中 `party_store=None` 时纯内存 |
| `app/world/narrative/narrative_service.py:27` | 删除 GameSessionStore stub；直接实例化 Firestore client |

**验证**：运行测试，确认 L2-1 链式导入问题消失。

---

### Step 2：LLM / ImageService 直注 + prompt_loader 提取

**目标**：Pipeline 和 Coordinator 不再从 `flash_cpu` 取 LLM 服务。

#### 2a：新建 `app/agentic/prompt_loader.py`（~15 行）

从 `flash_cpu_service.py:68-74` 提取 `_load_agentic_prompt()` 为模块级函数。

#### 2b：PipelineOrchestrator 改造

新增 `llm_service` / `image_service` 构造参数，替换引用：

| 行号 | 旧 | 新 |
|------|----|----|
| 300 | `getattr(self.flash_cpu, "image_service", None)` | `self.image_service` |
| 326, 798, 862, 1073 | `AgenticExecutor(self.flash_cpu.llm_service)` | `AgenticExecutor(self.llm_service)` |
| 329 | `self.flash_cpu._load_agentic_prompt()` | `load_agentic_prompt()` |
| 1228 | `self.flash_cpu.llm_service.generate_simple(...)` | `self.llm_service.generate_simple(...)` |
| 1232 | `self.flash_cpu.llm_service._strip_code_block` | `self.llm_service._strip_code_block` |

#### 2c：AdminCoordinator 改造

新增 `llm_service` 构造参数，替换引用：

| 行号 | 旧 | 新 |
|------|----|----|
| 626 | `self.flash_cpu.llm_service` | `self.llm_service` |
| 718 | `await self.flash_cpu.llm_service.generate_simple(...)` | `await self.llm_service.generate_simple(...)` |
| 761 | `await self.flash_cpu.llm_service.generate_simple(...)` | `await self.llm_service.generate_simple(...)` |

**涉及文件**：

| 文件 | 操作 |
|------|------|
| `app/agentic/prompt_loader.py` | **新建** |
| `app/services/admin/pipeline_orchestrator.py` | 新增参数 + 替换 7 处 |
| `app/services/admin/admin_coordinator.py` | 新增参数 + 替换 3 处 + 传入 Pipeline |

---

### Step 3：FlashCPU 遗留代码删除

**目标**：删除 execute_request 分发器（职责2）和 NPC 对话（职责6）。

#### 3a：删除 execute_request 相关

`flash_cpu_service.py` 中删除：
- `execute_request()` 方法（:76-336）
- `_extract_party_members()` / `_build_party_state_changes()` helper
- `_npc_dialogue_with_instance()` / `_npc_dialogue_direct_flash()` 方法（:413-486）
- `FlashRequest` / `FlashResponse` / `FlashOperation` 等模型（如果仅被 execute_request 使用）

#### 3b：废弃 pipeline NPC 对话 fallback

`pipeline_orchestrator.py:1419` 的 `_pipeline_npc_dialogue` 调用 `flash_cpu.execute_request(NPC_DIALOGUE)` → 删除此方法或替换为 `logger.warning + return None`。

---

### Step 4：gm_extra_tools 删除 + Pipeline 更新

**目标**：删除 `gm_extra_tools.py` 整个文件；Pipeline 不再构建 extra_tools。

#### 操作

1. 删除 `app/agentic/gm_extra_tools.py`（409 行）
2. `pipeline_orchestrator.py` 中：
   - 删除 `from app.agentic.gm_extra_tools import build_gm_extra_tools` import
   - 删除 `build_gm_extra_tools(...)` 调用（:305-311）
   - `AgenticExecutor.run()` 的 `extra_tools` 参数改为 `None`
   - 删除 `ENGINE_TOOL_EXCLUSIONS` 引用（如有）
3. 删除 `app/agentic/gm_extra_tools.py` 中定义的 `ENGINE_TOOL_EXCLUSIONS` 映射（Pipeline 若引用需同步清理）

**涉及文件**：

| 文件 | 操作 |
|------|------|
| `app/agentic/gm_extra_tools.py` | **删除** |
| `app/services/admin/pipeline_orchestrator.py` | 删除 import + 调用 + extra_tools=None |

---

### Step 5：Teammate flash_cpu 解除 + 战斗工具删除

**目标**：`teammate/response_service.py` 不再依赖 flash_cpu；删除不可用的战斗工具。

#### 操作

1. 删除 `_make_combat_action_tool()` 函数（:44-76）— 战斗 MCP 未完成，此工具不可用
2. 删除 `_is_combat_active()` 函数（:33-42）— 仅被上述工具使用
3. `__init__` 中删除 `flash_cpu` 参数（:85）和 `self.flash_cpu`（:93）
4. `_run_agentic_generation_payload` 中删除战斗 extra_tools 注入逻辑（:1024-1026）— 改为 `extra_tools=None`
5. `admin_coordinator.py` 更新 TeammateResponseService 构造（删除 `flash_cpu=`）

**涉及文件**：

| 文件 | 操作 |
|------|------|
| `app/agentic/teammate/response_service.py` | 删除 flash_cpu 参数 + 删除 _make_combat_action_tool + _is_combat_active |
| `app/services/admin/admin_coordinator.py` | 更新 TeammateResponseService 构造 |

---

### Step 6：FlashCPU 残骸清理与删除

**前提**：Step 1-5 全部通过测试。

#### 6a：战斗相关代码搬运（仅搬运，不改逻辑）

| 来源（flash_cpu_service.py） | 目标 |
|------|------|
| `call_combat_tool()` (:488-503) | 内联到 admin_coordinator（2 行：`pool = await MCPClientPool.get_instance(); return await pool.call_tool("combat", ...)`) |
| `_apply_delta()` (:379-385) | 内联到 admin_coordinator 战斗方法中（2 处） |
| `_build_state_delta()` (:338-344) | 同上 |
| `sync_combat_result_to_character()` (:387-411) | 内联到 admin_coordinator（1 处） |

#### 6b：AgenticContext.flash_cpu 字段删除

| 文件 | 操作 |
|------|------|
| `app/agentic/immersive_tools.py:41` | 删除 `flash_cpu: Any = None` 字段 |
| `app/services/admin/pipeline_orchestrator.py` | 删除所有 AgenticContext 构造中的 `flash_cpu=...` |

#### 6c：FlashCPU 彻底删除

1. `admin_coordinator.py` 中删除 FlashCPU 实例化（:111-120 + 后期注入 :121-137）
2. `pipeline_orchestrator.py` 构造函数去掉 `flash_cpu` 参数
3. 删除 `app/services/admin/flash_cpu_service.py`
4. 更新 `app/services/admin/__init__.py`：移除 `FlashCPUService` 导出
5. 更新 `app/services/__init__.py`：移除 `FlashCPUService` 导出

#### 6d：测试文件清理

| 测试文件 | 操作 |
|---------|------|
| `tests/test_phase4c_integration.py` | 移除 flash_cpu mock |
| `tests/test_phase4c_extra_tools.py` | 更新工具调用签名（flash_cpu → api） |
| `tests/test_interact_endpoint.py` | 移除 flash_cpu.llm_service mock → 直接 mock llm_service |
| `tests/test_private_chat_pipeline.py` | 同上 |
| `tests/test_pipeline_teammate_stage.py` | 移除 `flash_cpu=object()` |
| `tests/test_admin_story_flow.py` | 移除 `coordinator.flash_cpu = ...` mock |
| `tests/test_teammate_migration.py` | 移除 Store stub（Step 1 已消除根因）；更新 combat tool 测试 |
| `tests/test_flash_cpu_npc_defaults.py` | **整个文件作废**（NPC_DIALOGUE 已删除） |
| `tests/test_flash_mcp_integration.py` | 迁移为直接 MCPClientPool 测试 |
| `tests/test_flash_combat_error_semantics.py` | 迁移为 MCPClientPool 错误语义测试 |

---

## 验证

每个 Step 完成后运行：

```bash
python3 -m pytest tests/ -q --tb=short --continue-on-collection-errors
```

**目标始终：≥ 694 passed / ≤ 131 failed / ≤ 12 errors**

---

## 关键文件清单（按修改量排序）

| 文件 | 修改量 | 步骤 |
|------|--------|------|
| `app/services/admin/admin_coordinator.py` | 大（~25处） | 1b + 2c + 5 + 6 |
| `app/services/admin/pipeline_orchestrator.py` | 大（~15处） | 2b + 4 + 6 |
| `app/runtime/session_runtime.py` | 中（+3 类方法） | 1a |
| `app/agentic/teammate/response_service.py` | 中（删 flash_cpu + 删战斗工具） | 5 |
| `app/agentic/immersive_tools.py` | 小（删 1 字段） | 6b |
| `app/world/npc/instance_manager.py` | 极小（1 行） | 0 |
| `app/agentic/prompt_loader.py` | **新建**（~15 行） | 2a |
| `app/agentic/gm_extra_tools.py` | **删除** | 4 |
| `app/services/admin/flash_cpu_service.py` | **删除** | 6c |
| 测试文件（~10 个） | 更新 mock 模式 | 6d |

---

## 弃用部件

Phase C 完成后以下部件被弃用/删除：

| 部件 | 文件 | 状态 |
|------|------|------|
| `FlashCPUService` 整个类 | `flash_cpu_service.py` | 删除（503 行） |
| `gm_extra_tools.py` 整个文件 | `gm_extra_tools.py` | 删除（409 行，全部 8 个工具） |
| `FlashRequest` / `FlashResponse` / `FlashOperation` | models/ | 如无其他引用则删除 |
| `AgenticContext.flash_cpu` 字段 | `immersive_tools.py` | 删除 |
| `_make_combat_action_tool()` + `_is_combat_active()` | `teammate/response_service.py` | 删除 |
| `_pipeline_npc_dialogue()` | `pipeline_orchestrator.py` | 删除 |
| `ENGINE_TOOL_EXCLUSIONS` | `gm_extra_tools.py` → Pipeline | 随文件删除 |
| `GameSessionStore` import | `admin_coordinator.py` + 其他 | 删除（文件已不存在） |
| `PartyStore` import | `admin_coordinator.py` + `party_service.py` | 删除（文件已不存在） |
| `CharacterStore` import | `admin_coordinator.py` + 其他 | 删除（文件已不存在） |
| `test_flash_cpu_npc_defaults.py` | 测试文件 | 整个作废 |

---

## 遗留代码定位索引

> 执行时在对应行加 `# LEGACY(Phase-C): <说明>` 标记，方便 grep 定位。

### FlashCPU 引用（删除目标）

**admin_coordinator.py**：
| 行号 | 代码 | 说明 |
|------|------|------|
| :21 | `from app.services.admin.flash_cpu_service import FlashCPUService` | import |
| :75 | `flash_cpu: Optional[FlashCPUService] = None` | 构造参数 |
| :111-120 | `self.flash_cpu = flash_cpu or FlashCPUService(...)` | 实例化整块 |
| :123-124 | `assert self._state_manager is self.flash_cpu.state_manager` | 随 FlashCPU 删除 |
| :129 | `flash_cpu=self.flash_cpu` | TeammateResponseService 构造 |
| :141 | `flash_cpu=self.flash_cpu` | PipelineOrchestrator 构造 |
| :626 | `llm_service = self.flash_cpu.llm_service` | → `self.llm_service` |
| :718 | `await self.flash_cpu.llm_service.generate_simple(...)` | → `self.llm_service` |
| :761 | `await self.flash_cpu.llm_service.generate_simple(...)` | → `self.llm_service` |
| :296, :326 | `await self.flash_cpu.call_combat_tool(...)` | 战斗：内联 MCPClientPool |
| :924, :940, :961, :989 | `await self.flash_cpu.call_combat_tool(...)` | 战斗：内联 MCPClientPool |
| :939 | `self.flash_cpu._apply_delta(...)` + `_build_state_delta(...)` | 战斗：内联 |
| :978 | `self.flash_cpu.sync_combat_result_to_character(...)` | 战斗：内联 |
| :981 | `self.flash_cpu._apply_delta(...)` + `_build_state_delta(...)` | 战斗：内联 |

**pipeline_orchestrator.py**：
| 行号 | 代码 | 说明 |
|------|------|------|
| :41 | `flash_cpu: Any` | 构造参数 |
| :52 | `self.flash_cpu = flash_cpu` | 赋值 |
| :285 | `from app.agentic.gm_extra_tools import build_gm_extra_tools, ENGINE_TOOL_EXCLUSIONS` | import（整文件删除） |
| :300 | `image_service=getattr(self.flash_cpu, "image_service", None)` | → `self.image_service` |
| :301 | `flash_cpu=self.flash_cpu` | AgenticContext 构造 |
| :305-307 | `extra_tools = build_gm_extra_tools(flash_cpu=self.flash_cpu, ...)` | → `extra_tools=None` |
| :317 | `ENGINE_TOOL_EXCLUSIONS.get(...)` | 随 gm_extra_tools 删除 |
| :326 | `AgenticExecutor(self.flash_cpu.llm_service)` | → `AgenticExecutor(self.llm_service)` |
| :329 | `self.flash_cpu._load_agentic_prompt()` | → `load_agentic_prompt()` |
| :798 | `AgenticExecutor(self.flash_cpu.llm_service)` | → `AgenticExecutor(self.llm_service)` |
| :862 | `gm_executor = AgenticExecutor(self.flash_cpu.llm_service)` | → `self.llm_service` |
| :1073 | `executor = AgenticExecutor(self.flash_cpu.llm_service)` | → `self.llm_service` |
| :1228-1232 | `self.flash_cpu.llm_service.generate_simple(...)` | → `self.llm_service` |
| :1415-1425 | `self.flash_cpu.execute_request(NPC_DIALOGUE, ...)` | _pipeline_npc_dialogue 废弃 |

**teammate/response_service.py**：
| 行号 | 代码 | 说明 |
|------|------|------|
| :38-41 | `def _is_combat_active(...)` | 删除整个函数 |
| :44-75 | `def _make_combat_action_tool(flash_cpu, ...)` | 删除整个函数 |
| :85 | `flash_cpu: Optional[Any] = None` | 构造参数删除 |
| :93 | `self.flash_cpu = flash_cpu` | 赋值删除 |
| :1024-1026 | `if self.flash_cpu and _is_combat_active(session):` | → `extra_tools=None` |

**immersive_tools.py**：
| 行号 | 代码 | 说明 |
|------|------|------|
| :41 | `flash_cpu: Any = None` | AgenticContext 字段删除 |

### Store 断裂引用（替换目标）

**admin_coordinator.py**：
| 行号 | 代码 | 说明 |
|------|------|------|
| :38 | `from app.services.game_session_store import GameSessionStore` | → 删除，改用 SessionRuntime |
| :44 | `from app.services.party_store import PartyStore` | → 删除 |
| :47 | `from app.services.character_store import CharacterStore` | → 删除 |
| :69 | `session_store: Optional[GameSessionStore] = None` | 构造参数删除 |
| :80 | `self._session_store = session_store or GameSessionStore()` | → 删除 |
| :103 | `self.party_store = PartyStore()` | → 删除 |
| :105 | `self.character_store = CharacterStore()` | → 删除 |

**其他文件**：
| 文件 | 行号 | 说明 |
|------|------|------|
| `world_runtime.py` | :15 | `from app.services.game_session_store import GameSessionStore` |
| `world_runtime.py` | :26, :30 | 构造参数 + `GameSessionStore()` 实例化 |
| `party_service.py` | :21 | `from app.services.party_store import PartyStore` |
| `party_service.py` | :29 | 构造参数 `party_store: Optional[PartyStore]` |
| `services/__init__.py` | :11 | `FlashCPUService` 导出 |
| `services/__init__.py` | :24 | `"FlashCPUService"` in `__all__` |
| `services/admin/__init__.py` | :5 | `from .flash_cpu_service import FlashCPUService` |
| `services/admin/__init__.py` | :12 | `"FlashCPUService"` in `__all__` |

### 其他遗留

| 文件 | 行号 | 说明 |
|------|------|------|
| `instance_manager.py` | :403 | `from app.services.memory_graphizer import MemoryGraphizer` → `app.agentic.memory_graphizer` |
| `gm_extra_tools.py` | 全文 | 整个文件删除（409 行） |
| `flash_cpu_service.py` | 全文 | 整个文件删除（503 行） |

---

## Phase C 后 agentic/ 结构

```
app/agentic/
├── __init__.py
├── agentic_executor.py          # Agent 执行循环
├── immersive_tools.py           # AgenticContext（flash_cpu 字段已删除）
├── role_registry.py             # 角色→工具集映射
├── memory_graphizer.py          # 对话→图谱 LLM 转换
├── event_llm_service.py         # 事件 NLP 3 步管线
├── passerby_llm.py              # 路人 LLM 响应
├── prompt_loader.py             # prompt 加载（从 FlashCPU 提取）
└── teammate/
    ├── __init__.py
    └── response_service.py      # 队友决策 + Agentic 响应（flash_cpu + 战斗工具已删除）
```

> **已删除**：`gm_extra_tools.py`（全部 8 个工具 + ENGINE_TOOL_EXCLUSIONS）。
> GM Agent 仅保留 immersive_tools（30 个工具），extra_tools 归零。
