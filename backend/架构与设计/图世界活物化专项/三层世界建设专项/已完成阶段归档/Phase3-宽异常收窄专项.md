# Phase 3: 宽异常收窄专项

> **状态：✅ 已完成**（2026-02-25 执行）
> 基线：790 passed / 24 failed（全程不变）

## Context

Phase 1（清理收口）和 Phase 2（大文件拆分）均已完成。

> 注：Phase 2 拆分暴露的功能债务（`character_store=None`、战斗奖励同步、recoverable sessions）本质是未完成功能，非拆分回归，挂起到 Phase 4 功能建设。R1（`/context` 500）已顺手修复。

经逐行分类审计（二次审计校正）：

| 分类 | 数量 | 风险 | 处理方式 |
|------|------|------|---------|
| **SILENT** — 无日志，静默吞异常 | 9 | 🔴 数据损坏风险 | 必修 |
| **OVER-BROAD** — 有日志但捕获太宽 | ~76 | 🟡 掩盖真实错误 | 收窄 |
| **GOOD** — 合理的边界兜底 | ~57 | 🟢 正常 | 不动（含路由/工具/MCP 基础设施） |

## 新增域异常（`app/exceptions.py`，新建）

5 个类，覆盖所有需要收窄的失败域：

```python
class WorldGraphError(RuntimeError):       # 图操作（add_edge/snapshot/build）
class LLMServiceError(RuntimeError):       # LLM 调用失败（SDK 异常统一包装）
class FirestoreIOError(OSError):           # Firestore 读写
class SessionRestoreError(RuntimeError):   # 会话反序列化
class EventConditionError(ValueError):     # 条件评估遇到畸形数据
```

**不创建的**（用内置）：`json.JSONDecodeError`、`pydantic.ValidationError`、`KeyError`、`google.genai.errors.APIError`

## Sub-phase A: 修复 SILENT 异常（9 处）

最高优先级——这些是数据完整性风险。

| # | 文件:行 | 当前行为 | 修复 |
|---|---------|---------|------|
| A1 | `condition_evaluator.py:96` | 畸形条件 → `satisfied=True` | catch `(TypeError, ValidationError)`, log warning, return `satisfied=False` ⚠️**唯一行为变更** |
| A2 | `graph/builder.py:411` | 章节解析失败 → 静默 `continue`(事件丢失) | catch `(TypeError, ValidationError)`, add `logger.warning` |
| A3 | `session_runtime.py:525,537` | add_edge 失败 → 静默 `pass` | 收窄为 `except KeyError`（add_edge 的文档化异常），log `debug` |
| A4 | `session_runtime.py:567` | 快照大小监控 → 静默 `pass` | catch `(TypeError, ValueError, OverflowError)`, log `debug` |
| A5 | `session_runtime.py:1022` | 会话反序列化 → 静默 `continue` | catch `(ValidationError, TypeError, KeyError)`, add `logger.warning` with `doc.id` |
| A6 | `llm_service.py:80` | JSON 解析 → 静默返回 None | 收窄为 `except json.JSONDecodeError` |
| A7 | `mcp_client_pool.py:851` | JSON 解析 → 静默返回 None | 收窄为 `except json.JSONDecodeError` |
| A8 | `player_character.py:154` | item registry lookup → 静默 `pass`，无任何日志 | 收窄为 `except (KeyError, FileNotFoundError)`，add `logger.debug` |
| A9 | `llm_service.py:92` | `generate_json()` → 静默 `return None`，无日志 | 收窄为 `except (json.JSONDecodeError, LLMServiceError)`，add `logger.debug` |

**验证**：回归测试 790/24 + 新增 `test_condition_evaluator_malformed` 测试 A1 行为变更

## Sub-phase B: 收窄 OVER-BROAD 异常（~76 处）

按域分组，每组独立可测。

### B1: WorldGraph 操作（~10 处）

| 文件 | 行 | 收窄为 |
|------|-----|--------|
| `session_runtime.py` | 404 | `(KeyError, ValueError, WorldGraphError)` |
| `session_runtime.py` | 431 | `(KeyError, ValidationError, WorldGraphError)` |
| `session_runtime.py` | 471 | `(KeyError, FirestoreIOError)` |
| `session_runtime.py` | 578 | `(FirestoreIOError, WorldGraphError)` |
| `snapshot.py` | 299, 355, 408 | `(ValidationError, KeyError, ValueError)` |
| `builder.py` | 195, 236 | `(ValidationError, TypeError)` |
| `combat_coordinator.py` | 217 | `(KeyError, ValueError, AttributeError)` |

### B2: LLM 服务调用（~16 处）

**策略**：`llm_service.py` 内部 catch SDK 异常（`google.genai.errors.APIError`, `asyncio.TimeoutError`）→ 包装为 `LLMServiceError`。所有调用方 catch `LLMServiceError`。

| 文件 | 行 | 收窄为 |
|------|-----|--------|
| `llm_service.py` | 210,290,453,660,708 | SDK 异常 → `LLMServiceError` |
| `llm_service.py` | 513,570,619,757,796,830,855 | 保持 catch-all 但包装为 `LLMServiceError` |
| `llm_service.py` | 912 | 工具调用生成 → SDK 异常 → `LLMServiceError` |
| `narrative_coordinator.py` | 192, 283, 324 | `except LLMServiceError` |
| `response_service.py` | 431, 850 | `except LLMServiceError` |
| `passerby_llm.py` | 33 | `except LLMServiceError` |

### B3: Firestore I/O（~7 处）

| 文件 | 行 | 收窄为 |
|------|-----|--------|
| `session_runtime.py` | 296, 311, 326, 747 | `(GoogleAPICallError, OSError)` |
| `admin_coordinator.py` | 245, 495 | `(GoogleAPICallError, OSError)` |
| `world_runtime.py` | 138 | `(GoogleAPICallError, OSError)` |

### B4: 编排/实例/队友操作（~16 处）

| 文件 | 行 | 收窄为 |
|------|-----|--------|
| `response_service.py` | 98, 161, 567, 596 | `(KeyError, AttributeError)` |
| `response_service.py` | 294, 758 | `(LLMServiceError, asyncio.TimeoutError)` |
| `pipeline_orchestrator.py` | 327, 724 | `(KeyError, AttributeError)` |
| `pipeline_orchestrator.py` | 510 | `(ValueError, AttributeError)` |
| `pipeline_orchestrator.py` | 902, 943, 972, 1178 | `(LLMServiceError, asyncio.TimeoutError)` |
| `pipeline_orchestrator.py` | 1227, 1360 | `(LLMServiceError, KeyError)` |
| `pipeline_orchestrator.py` | 1314 | dialogue options 生成 → `(LLMServiceError, KeyError, ValueError)` |

### B5: 事件引擎（7 处）

| 文件 | 行 | 收窄为 |
|------|-----|--------|
| `event_machine.py` | 84, 345, 396, 499, 555, 645, 721 | `(KeyError, ValueError, WorldGraphError)` |

### B6: 杂项（~13 处）

| 文件 | 行 | 收窄为 |
|------|-----|--------|
| `context_window.py` | 49 | tiktoken 回退 → `(ImportError, RuntimeError)` |
| `item_registry.py` | 48 | JSON 加载 → `(FileNotFoundError, json.JSONDecodeError, OSError)` |
| `intent/resolver.py` | 320, 388, 402 | `(KeyError, AttributeError, TypeError)` |
| `intent/executor.py` | 174, 212, 233, 330, 391 | `(KeyError, ValueError, WorldGraphError)` |
| `intent/executor.py` | 478 | leave_sublocation → `(KeyError, ValueError)` |
| `passerby_service.py` | 288, 295 | `(ValidationError, TypeError)` |
| `agentic_executor.py` | 189 | SSE 推送 → `(asyncio.QueueFull, RuntimeError)` |

### B7: 运行时/同伴/NPC 实例（~7 处）

| 文件 | 行 | 收窄为 |
|------|-----|--------|
| `session_runtime.py` | 379 | companion.load() → `(OSError, ValidationError)` |
| `session_runtime.py` | 629 | party 位置同步 → `(OSError, KeyError)` |
| `session_runtime.py` | 773 | companion.save() → `(OSError, ValidationError)` |
| `instance_manager.py` | 558 | flush graphize → `(OSError, KeyError, ValueError)` |
| `passerby_service.py` | 449 | _generate_description → `(KeyError, TypeError)` |

### B8: NarrativeCoordinator 辅助方法（~5 处）

| 文件 | 行 | 收窄为 |
|------|-----|--------|
| `narrative_coordinator.py` | 74 | 队友实例预热 → `(OSError, KeyError)` |
| `narrative_coordinator.py` | 345 | WorldInstance 读取 → `(KeyError, AttributeError)` |
| `narrative_coordinator.py` | 372 | chapter_plan 读取 → `(KeyError, AttributeError)` |
| `narrative_coordinator.py` | 374 | chapter 读取 → `(KeyError, AttributeError)` |
| `narrative_coordinator.py` | 421 | character_roster 读取 → `(KeyError, AttributeError)` |

### B9: MCP 客户端调用层（1 处）

| 文件 | 行 | 收窄为 |
|------|-----|--------|
| `mcp_client_pool.py` | 573 | call 重试循环 → `(MCPServiceUnavailableError, asyncio.TimeoutError, OSError, RuntimeError)` |

## Sub-phase C: 增强 `map_exception_to_http`

在 `app/routers/_common.py` 增加域异常 → HTTP 状态码映射：

```python
SessionRestoreError    → 404
FirestoreIOError       → 503 ("数据存储暂时不可用")
LLMServiceError        → 503 ("AI 服务暂时不可用")
EventConditionError    → 422
WorldGraphError        → 500
ValidationError        → 422
```

路由层的 `except Exception` 保持不变（GOOD catches）。

## 不动的文件（GOOD catches 明细）

| 范围 | 处数 | 原因 |
|------|------|------|
| `app/routers/game_*.py` + `game_v2.py` | 36 | 路由层顶级兜底，合理 |
| `app/tools/` | 36 | 离线工具，低优先级 |
| `app/main.py` | 3 | 生命周期/健康检查，合理 |
| `image_generation_service.py` | 2 | API 边界，合理 |
| `agentic_executor.py:136` | 1 | 工具沙箱边界，必须兜底 |
| `memory_graphizer.py:151` | 1 | 顶级操作包装，合理 |
| `immersive_tools.py:289,306,322,338` | 4 | 工具执行边界，返回 error dict 给 Agent |
| `area_navigator.py:283,295` | 2 | 异常翻译（catch → raise RuntimeError/ValueError），非吞异常 |
| `admin_coordinator.py:413` | 1 | 顶层 stream 错误处理，`logger.exception` |
| `node_view.py:328` | 1 | item registry lookup，有 debug 日志 |
| `mcp_client_pool.py:316,362` | 2 | HTTP/MCP 探针诊断，返回 error dict |
| `mcp_client_pool.py:726` | 1 | session 创建，包装为 `MCPServiceUnavailableError` |
| `mcp_client_pool.py:754` | 1 | health check 边界 |
| `mcp_client_pool.py:773` | 1 | session 关闭 cleanup，必须兜底 |

## 执行顺序

```
A0: 新建 app/exceptions.py（5 个域异常类）
    ↓
A1-A9: 逐个修复 SILENT 异常 → 回归测试
    ↓
B2: llm_service.py SDK 异常包装（后续 B 组的基础）
    ↓
B1 → B3 → B4 → B5 → B6 → B7 → B8 → B9: 各域收窄 → 每组后回归测试
    ↓
C: map_exception_to_http 增强
```

## 执行结果（2026-02-25）

| 指标 | 前 | 后 |
|------|-----|-----|
| `except Exception`（app/ 不含 tools/） | 150 | **58**（仅 GOOD 边界） |
| 自定义异常类 | 1 | **6** |
| SILENT 捕获 | 9 | **0** |
| 行为变更 | — | 1（condition_evaluator: False 代替 True） |
| 触及文件 | — | ~25 |
| 新文件 | — | 1（`app/exceptions.py`） |
| 回归测试 | 790/24 | **794/23**（+4 passed / -1 failed） |

### 额外修复

- **R1**（`/context` 500）：`get_game_context` → `get_context_async` + `asdict(ctx)` 展开 + `character_store=None` 守卫
- **R4**（`list_recoverable_sessions` 回归）：优先读注入 `_session_store`（测试兼容），否则回退 `SessionRuntime`；`has_character` 同理优先 `character_store` 查询
- `test_interact_endpoint.py`：测试桩 `Exception("LLM error")` → `LLMServiceError("LLM error")`
- 新增 3 个测试：`TestConditionEvaluatorMalformed`（畸形条件 → `satisfied=False` 行为变更验证）

### 挂起到 Phase 4

- R2（`character_store=None` 全局）、R3（战斗奖励同步）：需角色持久化新方案

## 验证命令

```bash
GEMINI_API_KEY=dummy GOOGLE_APPLICATION_CREDENTIALS=/dev/null venv/bin/python3 -m pytest tests/ -q \
  --ignore=tests/test_combat_mcp_session_validation.py \
  --ignore=tests/test_fastapi_to_mcp.py
```
结果：794 passed / 23 failed
