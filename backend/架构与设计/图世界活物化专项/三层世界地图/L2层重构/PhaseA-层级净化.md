# Phase A 执行记录：层级净化

> **执行日期**: 2026-02-23
> **状态**: 已完成
> **验证**: 655 passed / 126 failed / 15 errors（与基线完全一致，零回归）

---

## 目标

净化三层文件归属：将错放在 L3（`app/world/`）的 4 个 L1 文件搬到 `app/agentic/`，搬迁 `event_llm_service`，拆分 `memory_graphizer` 的 L3 代码，修复 `flash_cpu_service` 的静态 L1 依赖。

---

## Step A1: 创建 `app/agentic/` + 搬迁 4 个 L1 文件

### git mv（4 个文件）

| 原路径 | 新路径 | 说明 |
|--------|--------|------|
| `app/world/immersive_tools.py` | `app/agentic/immersive_tools.py` | 30 个沉浸式工具 + AgenticContext |
| `app/world/agentic_executor.py` | `app/agentic/agentic_executor.py` | 统一 Agent 执行器 |
| `app/world/gm_extra_tools.py` | `app/agentic/gm_extra_tools.py` | 8 个 GM MCP 依赖工具 |
| `app/world/role_registry.py` | `app/agentic/role_registry.py` | 按 (role, traits) 映射工具集 |

### 新建

| 文件 | 内容 |
|------|------|
| `app/agentic/__init__.py` | L1 Agentic infrastructure 包声明 |

---

## Step A2: 更新所有 import 路径

### 搬迁文件内部互引（3 处）

| 文件 | 变更 |
|------|------|
| `app/agentic/agentic_executor.py` | `from app.world.role_registry` → `from app.agentic.role_registry` |
| `app/agentic/role_registry.py`（2 处） | `from app.world.immersive_tools` → `from app.agentic.immersive_tools` |
| `app/agentic/gm_extra_tools.py` | docstring 中 `from app.world.gm_extra_tools` → `from app.agentic.gm_extra_tools` |

### 生产代码（2 个文件，~10 处）

| 文件 | 处数 | 变更 |
|------|------|------|
| `app/services/admin/pipeline_orchestrator.py` | 7 | 3×AgenticExecutor + 3×AgenticContext + 1×gm_extra_tools |
| `app/services/teammate_response_service.py` | 2 | 1×AgenticExecutor + 1×AgenticContext |

### 测试文件（8 个文件，~35 处）

| 文件 | 处数 | 变更类型 |
|------|------|---------|
| `tests/test_phase4b.py` | ~20 | import + lazy import（immersive_tools + agentic_executor） |
| `tests/test_phase4c_immersive.py` | 2 | import（immersive_tools + role_registry） |
| `tests/test_phase4c_extra_tools.py` | 1 | import（gm_extra_tools） |
| `tests/test_phase4c_integration.py` | 3 | lazy import（gm_extra_tools） |
| `tests/test_immersive_tools_wg.py` | 1 | import（immersive_tools） |
| `tests/test_phase4a.py` | 3 | lazy import（immersive_tools + role_registry） |
| `tests/test_teammate_migration.py` | 6 | @patch 字符串 + lazy import（agentic_executor + immersive_tools） |
| `tests/test_interact_endpoint.py` | 5 | @patch 字符串（agentic_executor）— **计划遗漏，执行中发现** |

> **注意**: `test_interact_endpoint.py` 的 5 处 `@patch("app.world.agentic_executor.AgenticExecutor")` 未在原执行计划中列出，执行时通过全局 grep 发现并一并修复。

---

## Step A3: 搬迁 `event_llm_service.py`

### git mv（1 个文件）

| 原路径 | 新路径 |
|--------|--------|
| `app/services/admin/event_llm_service.py` | `app/services/event_llm_service.py` |

### import 变更（1 处）

| 文件 | 变更 |
|------|------|
| `app/services/admin/event_service.py:44` | `from app.services.admin.event_llm_service` → `from app.services.event_llm_service` |

---

## Step A4: 拆分 `memory_graphizer.py` 的 L3 代码

### 新建

| 文件 | 行数 | 说明 |
|------|------|------|
| `app/world/memory/graph_writer.py` | ~190 | 从 memory_graphizer.py 提取的 L3 图谱写入操作 |

### 提取函数

| 原方法（memory_graphizer.py） | 新函数（graph_writer.py） | 行数 |
|------------------------------|--------------------------|------|
| `_extract_metadata()` L485-507 | `_extract_metadata(extraction)` | 23 |
| `_merge_to_world_graph()` L509-638 | `merge_extraction(world_graph, npc_id, extraction)` | 130 |
| `_get_important_nodes_from_wg()` L640-699 | `get_context_nodes(world_graph, npc_id, ...)` | 60 |

### memory_graphizer.py 改造

`graphize()` 方法中的 L3 操作改为调用 graph_writer：

```python
# 旧（L1+L3 混合）:
existing_nodes = self._get_important_nodes_from_wg(wg, ...)    # L3 读
extraction = self._extract_graph_elements(...)                  # L1 LLM
merge_result = self._merge_to_world_graph(wg, ...)             # L3 写

# 新（L1 + 委托 L3）:
from app.world.memory.graph_writer import get_context_nodes, merge_extraction
existing_nodes = get_context_nodes(wg, ...)                     # L3 读（graph_writer）
extraction = self._extract_graph_elements(...)                  # L1 LLM（留在自身）
merge_result = merge_extraction(wg, ...)                        # L3 写（graph_writer）
```

原方法体删除，留下注释指向 graph_writer。`graphize()` 签名不变，5 个调用方（memory_ops、pipeline、session_history、instance_manager、测试）无感。

### 测试文件更新

| 文件 | 变更 |
|------|------|
| `tests/test_memory_graphizer_wg.py` | 新增 `from app.world.memory.graph_writer import merge_extraction, get_context_nodes, _extract_metadata`；所有 `graphizer._merge_to_world_graph(...)` → `merge_extraction(...)`；所有 `graphizer._get_important_nodes_from_wg(...)` → `get_context_nodes(...)`；所有 `graphizer._extract_metadata(...)` → `_extract_metadata(...)` |

---

## Step A5: 修复 `flash_cpu_service.py` 静态 L1 依赖

### 变更

| 位置 | 旧 | 新 |
|------|----|----|
| 顶层 import (line 24) | `from app.services.llm_service import LLMService` | 删除 |
| 新增 TYPE_CHECKING | — | `if TYPE_CHECKING: from app.services.llm_service import LLMService` |
| `__init__` 参数类型 | `Optional[LLMService]` | `Optional["LLMService"]` |
| `__init__` 默认值 | `self.llm_service = llm_service or LLMService()` | lazy import: `if llm_service is None: from ... import LLMService; llm_service = LLMService()` |

---

## 验证结果

| 检查项 | 结果 |
|--------|------|
| `from app.world.immersive_tools import` 残留 | 0 |
| `from app.world.agentic_executor import` 残留 | 0 |
| `from app.world.gm_extra_tools import` 残留 | 0 |
| `from app.world.role_registry import` 残留 | 0 |
| `app.world.(immersive_tools\|agentic_executor\|gm_extra_tools\|role_registry)` 残留 | 0（仅计划文档 6 处） |
| `from app.services.admin.event_llm_service import` 残留 | 0 |
| `from app.services.llm_service import LLMService`（flash_cpu 顶层） | 0（仅 TYPE_CHECKING） |
| Phase A 相关测试 | **113 passed**（不含 MCP 缺包的 5 个） |
| 全量测试基线 | **655 passed** / 126 failed / 15 errors（零回归） |

---

## 执行中发现

### test_interact_endpoint.py 遗漏

原执行计划 Step A2 的导入方分析遗漏了 `tests/test_interact_endpoint.py` 中 5 处 `@patch("app.world.agentic_executor.AgenticExecutor")` 装饰器。执行时通过全局 `grep "app.world.(immersive_tools|agentic_executor|gm_extra_tools|role_registry)"` 发现并修复。

**教训**: `@patch()` 的字符串参数不会被 IDE 的 import 分析捕获，需要全局文本搜索覆盖。

---

## 最终目录结构

### 新增 `app/agentic/`

```
app/agentic/
├── __init__.py              ← L1 Agentic infrastructure 包
├── immersive_tools.py       ← 30 个沉浸式工具 + AgenticContext + FEELING_MAP
├── agentic_executor.py      ← 统一 Agent 执行器（GM/NPC/队友共用）
├── gm_extra_tools.py        ← 8 个 GM MCP 依赖工具 + ENGINE_TOOL_EXCLUSIONS
└── role_registry.py         ← 按 (role, traits) 映射工具集
```

### 新增 `app/world/memory/graph_writer.py`

```
app/world/memory/
├── __init__.py
├── activation.py            ← 扩散激活（P3 迁入）
├── recall.py                ← 召回编排（P3 迁入）
├── recorder.py              ← 记忆写入（P3 提取）
└── graph_writer.py          ← 图谱写入（A4 从 memory_graphizer 提取，~190 行）
```

---

## 涉及部件

### 新增部件

| 文件 | 说明 |
|------|------|
| `app/agentic/__init__.py` | L1 Agent 基础设施包 |
| `app/agentic/immersive_tools.py` | ← `world/`（搬迁） |
| `app/agentic/agentic_executor.py` | ← `world/`（搬迁） |
| `app/agentic/gm_extra_tools.py` | ← `world/`（搬迁） |
| `app/agentic/role_registry.py` | ← `world/`（搬迁） |
| `app/world/memory/graph_writer.py` | 图谱写入（从 memory_graphizer 提取） |
| `app/services/event_llm_service.py` | ← `admin/`（搬迁） |

### 改动部件

| 文件 | 改动性质 |
|------|---------|
| `app/services/memory_graphizer.py` | 3 个 L3 方法体提取到 graph_writer，`graphize()` 改为委托调用 |
| `app/services/admin/flash_cpu_service.py` | LLMService 顶层 import → TYPE_CHECKING + lazy import |
| `app/services/admin/event_service.py` | event_llm_service import 路径 |
| `app/services/admin/pipeline_orchestrator.py` | 7 处 import 路径 |
| `app/services/teammate_response_service.py` | 2 处 import 路径 |
| `tests/test_memory_graphizer_wg.py` | 直接调用 graph_writer 函数替代旧私有方法 |
| 8 个测试文件 | import 路径 + @patch 字符串 |

### 弃用部件

| 部件 | 说明 |
|------|------|
| `app/world/immersive_tools.py` | 搬至 `agentic/`，旧路径已删除 |
| `app/world/agentic_executor.py` | 搬至 `agentic/`，旧路径已删除 |
| `app/world/gm_extra_tools.py` | 搬至 `agentic/`，旧路径已删除 |
| `app/world/role_registry.py` | 搬至 `agentic/`，旧路径已删除 |
| `app/services/admin/event_llm_service.py` | 搬至 `services/`，旧路径已删除 |
| `MemoryGraphizer._merge_to_world_graph()` | 提取到 `graph_writer.merge_extraction()` |
| `MemoryGraphizer._get_important_nodes_from_wg()` | 提取到 `graph_writer.get_context_nodes()` |
| `MemoryGraphizer._extract_metadata()` | 提取到 `graph_writer._extract_metadata()` |
