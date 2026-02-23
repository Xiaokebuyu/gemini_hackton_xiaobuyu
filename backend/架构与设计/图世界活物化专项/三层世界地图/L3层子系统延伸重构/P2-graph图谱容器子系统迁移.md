# P2 执行记录：graph/ 图谱容器子系统迁移

> **执行日期**: 2026-02-23
> **状态**: 已完成
> **验证**: 416 passed, 0 P2 引入的失败（60 个 SessionRuntime 失败均为 P0 残留）

---

## 目标

将 5 个图谱相关 L3 文件迁入 `app/world/graph/`，建立核心数据层子系统。graph/ 是所有后续子系统（events/memory/player/narrative/...）的底座。

---

## 用户决策

| 问题 | 决策 |
|------|------|
| models.py（444 行，60 处引用）归属 | 移入 `graph/`，不留根级 |
| 后续会再移动的文件是否改 import | 全改（一刀切） |
| 兼容桥 | 不留 |

---

## 文件变动

### git mv（5 个文件）

| 原路径 | 新路径 | 行数 | 备注 |
|--------|--------|------|------|
| `app/world/world_graph.py` | `app/world/graph/world_graph.py` | 998 | |
| `app/world/models.py` | `app/world/graph/models.py` | 444 | |
| `app/world/graph_builder.py` | `app/world/graph/builder.py` | 1254 | 改名 |
| `app/world/snapshot.py` | `app/world/graph/snapshot.py` | 410 | |
| `app/services/graph_schema.py` | `app/world/graph/schema.py` | 81 | 跨目录+改名 |

### 新建

| 文件 | 内容 |
|------|------|
| `app/world/graph/__init__.py` | 导出 WorldGraph, EdgeChange, GraphBuilder, 全部 models 类型, 快照函数, Schema 验证函数 |

### 更新

| 文件 | 动作 |
|------|------|
| `app/world/__init__.py` | 4 处 import 路径更新指向 graph/ 子包 |

### import 变更（~100 处，5 轮批量替换）

#### 轮 1: `from app.world.models import` → `from app.world.graph.models import`（~60 处）

| 文件 | 处数 |
|------|------|
| `app/world/graph/world_graph.py`（内部） | 1 |
| `app/world/graph/builder.py`（内部） | 1 |
| `app/world/graph/snapshot.py`（内部） | 1 |
| `app/world/__init__.py` | 1 |
| `app/runtime/session_runtime.py` | 16 |
| `app/world/behavior_engine.py` | 1 |
| `app/world/event_propagation.py` | 1 |
| `app/world/player_node.py` | 1 |
| `app/world/intent_executor.py` | 3 |
| `app/world/intent_resolver.py` | 4 |
| `app/world/spreading_activation.py` | 1 |
| `app/world/gm_extra_tools.py` | 1 |
| `app/services/admin/pipeline_orchestrator.py` | 1 |
| `app/services/npc_reactor.py` | 1 |
| `app/services/memory_graphizer.py` | 1 |
| 16 个测试文件 | 26 |

#### 轮 2: `from app.world.world_graph import` → `from app.world.graph.world_graph import`（~20 处）

| 文件 | 处数 |
|------|------|
| `app/world/graph/builder.py`（内部） | 1 |
| `app/world/graph/snapshot.py`（内部） | 1 |
| `app/world/__init__.py` | 1 |
| `app/world/behavior_engine.py` | 1 |
| `app/world/event_propagation.py` | 1 |
| `app/world/player_node.py` | 1 |
| `app/world/spreading_activation.py` | 1 |
| 14 个测试文件 | 14 |

#### 轮 3: `from app.world.graph_builder import` → `from app.world.graph.builder import`（7 处）

| 文件 |
|------|
| `app/world/__init__.py` |
| `app/runtime/session_runtime.py` |
| `tests/test_snapshot.py` |
| `tests/test_c8_migration.py` |
| `tests/test_graph_builder.py` |
| `tests/test_p7_side_effects.py` |
| `tests/test_c7_integration.py` |
| `tests/test_c9_stages.py` |

#### 轮 4: `from app.world.snapshot import` → `from app.world.graph.snapshot import`（7 处）

| 文件 |
|------|
| `app/world/__init__.py` |
| `app/runtime/session_runtime.py`（2 处） |
| `tests/test_snapshot.py` |
| `tests/test_world_graph_memory.py` |
| `tests/test_knowledge_loading.py`（2 处） |
| `tests/test_c7_integration.py` |

#### 轮 5: `from app.services.graph_schema import` → `from app.world.graph.schema import`（3 处）

| 文件 |
|------|
| `app/tools/graph_review.py` |
| `app/tools/graph_importer.py` |
| `app/services/admin/event_service.py` |

---

## 最终目录结构

```
app/world/graph/
├── __init__.py          ← 公开 API（67 行）
├── world_graph.py       ← 图容器 + 索引 + dirty（998 行）
├── models.py            ← 世界类型系统（444 行）
├── builder.py           ← 7 步构建工厂（1254 行）
├── snapshot.py          ← 快照序列化（410 行）
└── schema.py            ← 图谱 Schema 定义（81 行）
```

**总计 3,187 行 + 67 行 init = 3,254 行**

---

## 验证结果

| 检查项 | 结果 |
|--------|------|
| `from app.world.models import` 残留 | 0 |
| `from app.world.world_graph import` 残留 | 0 |
| `from app.world.graph_builder import` 残留 | 0 |
| `from app.world.snapshot import` 残留 | 0 |
| `from app.services.graph_schema import` 残留 | 0 |
| graph 相关测试（不含 SessionRuntime） | **416 passed** |
| SessionRuntime 相关测试 | 60 failed（P0 残留：`area_runtime` 已删模块，非 P2 引起） |

---

## 发现的 P0 阻塞

测试过程中暴露了大量 P0 残留（均为已知问题，非 P2 引入）：

| 阻塞源 | 影响测试数 | 根因 |
|--------|-----------|------|
| `app.runtime.area_runtime`（已删除） | 60 个 | SessionRuntime.__init__ 硬依赖 |
| `app.services.game_session_store`（已删除） | 16 个 | admin_coordinator 链路 |
| `mcp` 包（系统 Python 未安装） | 若干 | mcp_client_pool 链路 |
