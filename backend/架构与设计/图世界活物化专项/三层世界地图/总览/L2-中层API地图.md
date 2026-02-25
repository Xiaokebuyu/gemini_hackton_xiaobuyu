# L2 中层 API 地图 — 代码对齐版

> 更新时间：2026-02-24
> 口径：以当前仓库 `app/` 代码为准（排除 `app/tools/`）
> 基线：`全量文件清单与层级归类.md`（2026-02-24 代码对齐版）
> 定义：L2 = 接口暴露 + 编排调度 + 边界适配（不承载世界机械真理）

---

## 1. L2 快照

| 指标 | 数值 |
|------|------|
| L2 文件数（不含 `__init__.py`） | **19** |
| L2 代码总行数 | **7,937** |
| `except Exception` 总数 | **70** |
| 最大文件 | `pipeline_orchestrator.py`（1545） |
| 最大函数 | `PipelineOrchestrator.process()`（621） |

> 对齐结果：与全量清单中 L2 口径（19 文件 / 7937 行）一致。

---

## 2. 当前 L2 模块清单

### 2.1 REST 接入层（1）

| 文件 | 行数 | 说明 |
|------|------|------|
| `app/routers/game_v2.py` | 1048 | 统一对外路由（37 个端点） |

### 2.2 MCP 接口层（10）

| 文件 | 行数 | 说明 |
|------|------|------|
| `app/mcp/game_tools_server.py` | 70 | Game MCP 入口 |
| `app/mcp/tools/character_tools.py` | 40 | 角色工具 |
| `app/mcp/tools/inventory_tools.py` | 101 | 背包工具 |
| `app/mcp/tools/narrative_tools.py` | 19 | 叙事工具 |
| `app/mcp/tools/navigation_tools.py` | 36 | 导航工具 |
| `app/mcp/tools/npc_tools.py` | 147 | NPC 工具 |
| `app/mcp/tools/party_tools.py` | 54 | 队伍工具 |
| `app/mcp/tools/passerby_tools.py` | 54 | 路人工具 |
| `app/mcp/tools/time_tools.py` | 20 | 时间工具 |
| `app/combat/combat_mcp_server.py` | 798 | 战斗 MCP 服务器 |

### 2.3 管线编排层（4）

| 文件 | 行数 | 归类 | 说明 |
|------|------|------|------|
| `app/services/admin/pipeline_orchestrator.py` | 1545 | L2/L3 桥 | 三阶段总编排 |
| `app/services/admin/admin_coordinator.py` | 1265 | L2/L3 混合 | 入口协调器 |
| `app/services/admin/event_service.py` | 299 | L2 | 事件摄入与发布 |
| `app/services/admin/world_runtime.py` | 245 | L2 | 世界运行时薄壳 |

### 2.4 服务门面层（4）

| 文件 | 行数 | 归类 | 说明 |
|------|------|------|------|
| `app/services/area_navigator.py` | 726 | L2 | 导航服务 |
| `app/services/passerby_service.py` | 507 | L2 | 路人生命周期编排 |
| `app/services/mcp_client_pool.py` | 886 | L2 | MCP 连接池 |
| `app/services/world_api.py` | 77 | L2 | L1→L3 统一动作门面（第一版） |

---

## 3. L2 结构性屎点（当前）

### P0（高风险）

1. 仍有已删除 Store 的导入残留（新环境直接崩）：
   - `app/mcp/tools/character_tools.py:4`
   - `app/mcp/tools/inventory_tools.py:6`
   - `app/mcp/tools/party_tools.py:5`
   - `app/combat/combat_mcp_server.py:26`
2. 路由与编排链仍承接大量历史兼容路径，收口不彻底。

### P1（中高风险）

1. God Function：`PipelineOrchestrator.process()` 621 行（单函数过大）。
2. 胖路由：`game_v2.py` 承载 37 个端点，边界聚合过度。
3. 宽异常密度高：L2 层 `except Exception` 70 处。
   - `game_v2.py`：35
   - `pipeline_orchestrator.py`：10
   - `admin_coordinator.py`：12
   - `mcp_client_pool.py`：7
4. 编排与引擎交叉：`pipeline_orchestrator/admin_coordinator` 仍直接操作 runtime 细节。

### P2（中风险）

1. MCP 工具与主流程仍有实例边界不清，容易产生缓存/状态分叉。
2. `WorldAPI` 目前是薄委托，权限矩阵、审计日志、参数验证未完全落地。

---

## 4. 复杂度观测

| 维度 | 现状 |
|------|------|
| L2 Top1 文件占比 | `pipeline_orchestrator.py` 占 L2 19.5% |
| L2 Top3 文件占比 | `pipeline_orchestrator/admin_coordinator/game_v2` 合计约 48.5% |
| L2 异常密度 | 8.8 / 千行（70 / 7937） |
| 关键耦合输出度（import 依赖） | `admin_coordinator` 最高（12） |

---

## 5. 建议治理顺序（L2）

1. 先修断裂导入（Store 残留），保证边界稳定。
2. 拆 `PipelineOrchestrator.process()` 为阶段化 `Stage` 对象（A/B/C）。
3. 将 `game_v2.py` 按上下文拆分路由模块（session/combat/party/narrative）。
4. 将宽异常替换为分级异常（Validation/Dependency/External）。
5. 将 `WorldAPI` 从薄委托升级为“单写入口”守门层（权限 + 审计 + 约束）。

---

## 6. 与三层哲学对齐度

| 项目 | 结论 |
|------|------|
| 目录层面 | 基本对齐（L2 文件位置清晰） |
| 依赖方向 | 部分对齐（仍有混合耦合） |
| 单一入口 | 部分对齐（WorldAPI 已出现，但未强制） |
| 可维护性 | 中等偏低（大型编排文件集中） |

> 结论：L2 当前不是“错层”，而是“**编排过重、边界不硬**”。
