# P1 执行记录：scene/ + time/ 子系统迁移

> **执行日期**: 2026-02-23
> **状态**: 已完成
> **验证**: 101 passed, 0 failed（P2 之前的测试基线）

---

## 目标

将 scene_bus 和 time_manager 作为首批子系统迁入 `app/world/` 子文件夹，验证迁移模式。

---

## 文件变动

### git mv

| 原路径 | 新路径 |
|--------|--------|
| `app/world/scene_bus.py` | `app/world/scene/scene_bus.py` |
| `app/services/time_manager.py` | `app/world/time/time_manager.py` |

### 新建

| 文件 | 内容 |
|------|------|
| `app/world/scene/__init__.py` | 导出 `SceneBus`, `BusEntry`, `BusEntryType` |
| `app/world/time/__init__.py` | 导出 `TimeManager`, `GameTime`, `TimePeriod`, `TimeEvent` |

### import 变更（20 处）

**scene_bus（18 处）**:
`from app.world.scene_bus import` → `from app.world.scene import`

| 文件 | 处数 |
|------|------|
| `app/services/admin/pipeline_orchestrator.py` | 9 |
| `app/runtime/session_runtime.py` | 2 |
| `app/world/intent_executor.py` | 1 |
| `app/services/npc_reactor.py` | 1 |
| `app/world/immersive_tools.py` | 2 |
| `tests/test_npc_reactor.py` | 1 |
| `tests/test_intent_executor.py` | 1 |
| `tests/test_scene_bus.py` | 1 |
| `tests/test_phase4a.py` | 1（同时修复 P0-7 残留断言） |

**time_manager（2 处）**:
`from app.services.time_manager import` → `from app.world.time import`

| 文件 |
|------|
| `app/runtime/session_runtime.py` |
| `app/services/admin/world_runtime.py` |

---

## 额外修复

- `tests/test_phase4a.py`: 移除 `assert "choose_battle_action" in names`（P0-7 删除 stub 后的残留断言）
