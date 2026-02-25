# P3 执行记录：memory/ + player/ 子系统迁移

> **执行日期**: 2026-02-23
> **状态**: 已完成
> **验证**: 415 passed, 0 P3 引入的失败（52 个 SessionRuntime 失败均为 P0 残留）

---

## 用户决策

| 问题 | 决策 |
|------|------|
| character_service.py / ability_check_service.py（依赖已删 CharacterStore）如何处理 | 一起迁移，CharacterStore import 保持断裂 |
| 兼容桥 | 不留（一刀切） |

---

## Part A: memory/ 子系统

### 文件变动

#### git mv（2 个文件）

| 原路径 | 新路径 | 行数 | 备注 |
|--------|--------|------|------|
| `app/world/spreading_activation.py` | `app/world/memory/activation.py` | 271 | 改名 |
| `app/world/recall.py` | `app/world/memory/recall.py` | 165 | |

#### 新建

| 文件 | 内容 |
|------|------|
| `app/world/memory/__init__.py` | 导出 spread_activation, extract_subgraph, find_paths, WorldGraphRecallOrchestrator, RECALL_CONFIGS, check_memory_write_permission, record_memory |
| `app/world/memory/recorder.py` | 从 session_runtime 提取的 L3 记忆写入 + 权限检查（72 行） |

#### session_runtime 改动（3 处）

| 方法 | 改动 |
|------|------|
| `recall()` L1998 | lazy import 路径更新：`from app.world.recall` → `from app.world.memory.recall` |
| `_check_memory_write_permission()` | 整体替换为委托：`from app.world.memory.recorder import check_memory_write_permission` |
| `record_memory()` | 整体替换为委托：`from app.world.memory.recorder import record_memory as _record` |

### import 变更（3 处外部）

| 文件 | 旧 | 新 |
|------|-----|-----|
| `tests/test_world_spreading_activation.py` | `from app.world.spreading_activation import` | `from app.world.memory.activation import` |
| `tests/test_world_graph_recall.py` | `from app.world.recall import` | `from app.world.memory.recall import` |
| `memory/recall.py`（内部） | `from app.world.spreading_activation import` | `from app.world.memory.activation import` |

### 最终目录结构

```
app/world/memory/
├── __init__.py          ← 公开 API
├── activation.py        ← 扩散激活算法（271 行）
├── recall.py            ← 召回编排（165 行）
└── recorder.py          ← 记忆写入 + 权限检查（72 行，新提取）
```

**总计 508 行 + 72 行 recorder = 580 行**

---

## Part B: player/ 子系统

### 文件变动

#### git mv（6 个文件）

| 原路径 | 新路径 | 行数 | 备注 |
|--------|--------|------|------|
| `app/world/stats_manager.py` | `app/world/player/stats.py` | 193 | 改名 |
| `app/world/player_node.py` | `app/world/player/node_view.py` | 484 | 改名 |
| `app/world/constants.py` | `app/world/player/constants.py` | 346 | |
| `app/services/character_service.py` | `app/world/player/character.py` | 425 | 跨目录+改名 |
| `app/services/ability_check_service.py` | `app/world/player/ability_check.py` | 193 | 跨目录+改名 |
| `app/services/item_registry.py` | `app/world/player/item_registry.py` | 80 | 跨目录 |

#### 新建

| 文件 | 内容 |
|------|------|
| `app/world/player/__init__.py` | 导出 stats 函数、PlayerNodeView、constants、item_registry（CharacterService/AbilityCheckService 因 Store 断裂暂不 re-export） |

#### 更新

| 文件 | 动作 |
|------|------|
| `app/world/__init__.py` | constants re-export 路径更新：`from app.world.constants` → `from app.world.player.constants` |

### import 变更（~25 处，7 轮批量替换）

#### 轮 1: `from app.world import stats_manager` → `from app.world.player import stats as stats_manager`（10 处）

| 文件 | 处数 | 备注 |
|------|------|------|
| `app/runtime/session_runtime.py` | 7 | 含 2 处别名变体 `as _sm` / `as _sm2` |
| `app/services/admin/flash_cpu_service.py` | 1 | 顶层 |
| `app/world/intent_executor.py` | 1 | 顶层 |
| `app/world/gm_extra_tools.py` | 1 | lazy |

#### 轮 2: `from app.world.player_node import` → `from app.world.player.node_view import`（4 处）

| 文件 |
|------|
| `app/runtime/session_runtime.py` |
| `app/world/graph/builder.py` |
| `tests/test_c7_integration.py` |
| `tests/test_player_node_view.py` |

#### 轮 3: `from app.world.constants import` → `from app.world.player.constants import`（3 处外部）

| 文件 |
|------|
| `app/world/graph/builder.py` |
| `tests/test_stats_manager.py` |
| `tests/test_player_node_view.py` |

#### 轮 4: `from app.services.character_service import` → `from app.world.player.character import`（2 处）

| 文件 |
|------|
| `app/services/admin/admin_coordinator.py` |
| `app/mcp/tools/inventory_tools.py` |

#### 轮 5: `from app.services.ability_check_service import` → `from app.world.player.ability_check import`（3 处）

| 文件 |
|------|
| `app/routers/game_v2.py` |
| `app/services/admin/flash_cpu_service.py` |
| `app/services/admin/pipeline_orchestrator.py` |

#### 轮 6: `from app.services.item_registry import` → `from app.world.player.item_registry import`（1 处外部）

| 文件 |
|------|
| `app/models/player_character.py` |

#### 轮 7: `from app.world.stats_manager import` → `from app.world.player.stats import`（1 处）

| 文件 |
|------|
| `tests/test_stats_manager.py` |

### player/ 内部互引更新（4 处）

| 文件 | 改动 |
|------|------|
| `player/stats.py` | `from app.world.constants` → `from app.world.player.constants` |
| `player/node_view.py` | `from app.world.constants` → `from app.world.player.constants`；`from app.services.item_registry` → `from app.world.player.item_registry` |
| `player/character.py` | `from app.services.item_registry` → `from app.world.player.item_registry`；CharacterStore import 保持不动 |
| `player/ability_check.py` | CharacterStore import 保持不动 |

### 最终目录结构

```
app/world/player/
├── __init__.py          ← 公开 API（50 行）
├── stats.py             ← 数值变更（193 行）
├── node_view.py         ← Player 图谱适配器（484 行）
├── constants.py         ← D&D 规则表（346 行）
├── character.py         ← 角色创建/点买（425 行，CharacterStore 断裂待修）
├── ability_check.py     ← d20 判定（193 行，CharacterStore 断裂待修）
└── item_registry.py     ← 物品注册表（80 行）
```

**总计 1,721 行 + 50 行 init = 1,771 行**

---

## 执行中发现

### player/__init__.py eager import 传染

`player/__init__.py` 最初 re-export 了 `CharacterService` 和 `AbilityCheckService`。由于它们顶层 import 已删除的 `CharacterStore`，任何 `from app.world.player.xxx import` 都会触发 `__init__.py` → 触发 CharacterStore 断裂 → 传染到所有使用 player/ 的测试。

**解决**: 从 `__init__.py` 移除 CharacterService 和 AbilityCheckService 的 eager import，改为注释说明调用方直接 import。

---

## 验证结果

| 检查项 | 结果 |
|--------|------|
| `from app.world.spreading_activation import` 残留 | 0 |
| `from app.world.recall import` 残留 | 0 |
| `from app.world import stats_manager` 残留 | 0 |
| `from app.world.stats_manager import` 残留 | 0 |
| `from app.world.player_node import` 残留 | 0 |
| `from app.world.constants import` 残留 | 0 |
| `from app.services.character_service import` 残留 | 0 |
| `from app.services.ability_check_service import` 残留 | 0 |
| `from app.services.item_registry import` 残留 | 0 |
| P3 相关测试（不含 SessionRuntime） | **415 passed** |
| SessionRuntime 相关测试 | 52 failed（P0 残留：`area_runtime` 已删模块，非 P3 引起） |
