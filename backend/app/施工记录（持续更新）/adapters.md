# 适配器层施工记录

**设计文档**：表现层设计-视觉小说风格.md（部分）
**代码路径**：`app/game_core/adapters/`
**Phase**：5D

## 模块状态

| 组件 | 状态 | 说明 |
|------|------|------|
| `InputPort` Protocol | [完成] | process_text / process_action |
| `NullInputPort` | [完成] | stub 返回 |
| `OutputPort` Protocol | [完成] | emit(payload) |
| `NullOutputPort` | [完成] | 记录 last_payload |
| `PersistencePort` Protocol | [完成] | load(key) / save(key, payload) |
| `NullPersistencePort` | [完成] | 内存 dict 存储 |
| `PresentationPort` Protocol | [完成] | publish(event) |
| `NullPresentationPort` | [完成] | 记录 events 列表 |
| `SSEPresentationPort` | [完成] | collect-drain SSE 格式化，duck-typed 接受 SSEEvent/InteractionOutputEvent |
| `FastAPIInputPort` | [完成] | 纯输入归一化桥接（text aliases + interaction normalize） |
| `LocalFilePersistencePort` | [完成] | 本地文件持久化 + SessionCatalogPort |
| `FirestorePersistencePort` | [完成] | Firestore 文档持久化 + SessionCatalogPort（D-A03） |
| `SaveStore` | [完成] | 会话级 save/load 编排 |

## 决策记录

### [D-A01] 四端口模型

六层架构的外部边界通过 4 个 Protocol 端口定义：
- **Inbound**：玩家输入（文本 / 结构化动作）
- **Outbound**：响应输出
- **Persistence**：状态持久化（Firestore 适配器）
- **Presentation**：SSE 事件流推送

Null 实现用于测试和骨架验证，无外部依赖。

### [D-A02] SSEPresentationPort — collect-drain 模式

**日期**：2026-02-28

**问题**：PresentationPort Protocol 已定义但未接入架构。3 个 streaming 路由各自重复 SSE 格式化逻辑（`_format_sse_event()` × 5-7 次/路由）。

**方案**：collect-drain 模式，应用层后处理。

```
game_core 产出 SSEEvent (纯数据)
    ↓
应用层 (main.py) 创建 SSEPresentationPort
    ↓
publish_raw() / publish() / publish_many() → 缓冲 SSE text
    ↓
drain() → list[str] → StreamingResponse
```

**关键设计**：
- **Duck-typed publish()**：通过 `getattr(event, "event_type/payload")` 同时接受 `SSEEvent` 和 `InteractionOutputEvent`
- **publish_raw()**：构建信封事件（action_result, stream_end, input_parsed 等）
- **publish_many()**：批量发布 PipelineResult.sse_events
- **格式对齐**：`ensure_ascii=False`, `separators=(",",":")` — 与原 `_format_sse_event()` 完全一致
- **应用层使用**：game_core 不持有端口引用（隔离红线），调用方在 main.py

**重构效果**：
- 删除 `main.py._format_sse_event()` 和 `_disabled_stream_payload()`
- 3 个 streaming 路由统一使用 `SSEPresentationPort`
- 测试基线 290 → 299（+9 新增 SSE port 测试）

### [D-A03] FirestorePersistencePort

**日期**：2026-02-28

**实现**：`app/game_core/adapters/firestore_persistence.py`（~40 行）

**设计**：
- 整个 session payload 存为单个 Firestore 文档（collection/{session_id}）
- 实现 `PersistencePort` + `SessionCatalogPort` 全 4 个方法
- `AsyncClient()` 延迟导入，自动使用 ADC 认证
- `list_keys` 用 `select([])` 字段投影，只拉 document ID
- `GameRuntime._default_persistence_port()` 通过 `PERSISTENCE_BACKEND` 环境变量切换（默认 `local`，设为 `firestore` 切换）

**测试**：6 个 mock 测试 in `tests/test_firestore_persistence.py`

## 填充 TODO

- [x] `FirestorePersistencePort`：接入 Firestore — D-A03 完成
- [x] `SSEPresentationPort`：collect-drain SSE 格式化（D-A02）
- [x] `FastAPIOutputPort`：评估结论——不需要实现（FastAPI 原生 response_model 已足够）

### [D-A04] main.py God Module 拆分

**日期**：2026-02-28

**问题**：`app/main.py` 877 行混合 6 种职责（世界种子数据、验证工具、响应构建器、会话加载、动作执行、16 个路由），阅读和定位成本高。

**方案**：按领域拆分为 FastAPI Router 模块。

```
app/
  main.py          ← 瘦入口（~10 行）：re-export app + include 4 routers
  deps.py          ← 共享依赖叶子模块：app 实例、getters、validators、session 加载、_execute_structured_action
  world_seed.py    ← WORLD_CATALOG + _shell_world_seed()（纯数据）
  routers/
    sessions.py    ← 6 路由：health, worlds, session CRUD
    character.py   ← 3 路由：creation options, create, get panel
    panels.py      ← 3 路由：inventory, map, quests
    gameplay.py    ← 4 路由：navigate + 3 streaming
```

**关键决策**：
- `deps.py` 是叶子模块（不 import routers），避免循环依赖
- `main.py` re-export `app` 保持 `import app.main as api_main` 测试兼容
- 删除 dead code：`_world_content_unavailable()`、`_disabled_stream()`（从未被调用）
- 响应构建器随路由迁移（`_map_response` → panels.py, `_action_execution_response` → gameplay.py 等）

**测试影响**：
- `test_api_shell.py`：无需改动（通过 re-export 访问 app）
- `test_interaction_service.py`：`_shell_world_seed` import 更新为 `from app.world_seed import`

### [D-A05] 伪流式 → 真流式改造

**日期**：2026-02-28

**问题**：3 个 streaming 路由使用 `SSEPresentationPort` collect-drain 模式——pipeline 完整执行后所有 SSE 事件一次性吐出，客户端在执行期间看不到任何事件。

**方案**：`asyncio.Queue` + `create_task` 桥接 pipeline 执行和 SSE 流。

```
Route
  ↓ asyncio.Queue
  ↓ create_task(_execute)              async_generator:
  ↓ return StreamingResponse             await queue.get()
                                         yield format_sse(event)
  _execute task:
    TickCoordinator.process(
      payload,
      event_sink=queue.put  ← 事件实时推送
    )
```

**改动范围**：
- `presentation.py`：提取 `format_sse_event()` 模块级函数
- `tick_coordinator.py`：`process()` + `_tick_settlement()` 新增 `event_sink` 参数
  - event_sink 非 None 时，每个 hook SSE 事件立即推送到 sink
  - 同时仍收集到 collected_events list（InteractionService 等非流式消费者向下兼容）
- `deps.py`：`_execute_structured_action()` 透传 event_sink；移除 `_stream_response()`
- `gameplay.py`：
  - `action_stream` / `input_stream`：asyncio.Queue + create_task（真流式）
  - `interact_stream`：简化 async generator（InteractionService 暂不支持 event_sink）
  - navigate 不变（返回 JSON）
  - 路由级错误处理：HTTPException 转为 `stream_error` SSE 事件（SSE 标准模式）

**行为变更**：streaming 路由始终返回 HTTP 200 + SSE stream，错误通过 `stream_error` 事件传达。

**测试**：3 新 event_sink 测试

### [D-A06] 应用层类型约束收窄

**日期**：2026-02-28

**问题**：应用层大量使用 `getattr(obj, "field", default)` 访问已知类型的 dataclass 属性，参数声明为 `Any`/`object` 而实际类型明确。类型检查器失效，可读性差。

**关键发现**：`getattr(state, "relations", None)` 提供虚假保护——`StateContainer.relations` 是 property（不触发 `AttributeError`），底层 `get_slice()` 抛 `KeyError`（getattr 不捕获），行为与直接访问完全一致。

**改动范围**：
- `deps.py`：`_execute_structured_action` 返回 `Any` → `PipelineResult`
- `gameplay.py`：`_action_execution_response(result: Any)` → `result: PipelineResult`，4 getattr 替换
- `sessions.py`：`_session_summary_response(item: object)` → `item: SavedSessionInfo`，11 getattr 替换
- `interaction_service.py`（最大改动）：
  - `build_interaction_context(session: Any)` → `session: ManagedSession`，顶层 getattr 链 + 状态切片访问全部替换
  - InteractionService 5 个方法参数类型收窄（`session: Any` → `ManagedSession`，`result: Any` → `PipelineResult`）
  - `__init__` 回调类型从 `Callable[[Any, Any], Awaitable[Any]]` 收窄为具体签名
  - `player.inventory` 从 schemaless `isinstance(raw_item, Mapping)` 改为直接访问 `ItemStack` 属性
  - `milestone_states` 从 3-branch isinstance 简化为直接 `milestone.state`

**不动部分**：ContentRegistry schemaless 数据的 isinstance 检查、`api_models.py` 的灵活序列化类型、FastAPI State 动态类型

**测试**：449 passed（不变，纯类型收窄不影响运行时行为）

### [D-A07] 代码评审问题修复（Batch 1-3）

**日期**：2026-02-28

**来源**：外部代码评审 7 条问题，核实后分批修复。

**Batch 1 — 卫生清理**：
- `pyproject.toml` 加 `pythonpath = ["."]`，裸跑 `pytest` 不再需要手动 `PYTHONPATH=.`
- 删除根目录 pip 残留文件 `=0.24.0`、`=1.2.0`

**Batch 2 — hook_error 可观测性增强**：
- `tick_coordinator.py`：`hook_error` SSE payload 从 `{"hook": name}` 扩展为 `{"hook", "error_type", "message"}`
- 钩子命令执行失败新增 `command_error` SSE 事件（原来只有 logger，无客户端通知）
- 不改变"继续执行后续钩子"的容错行为

**Batch 3 — README 重写**：
- 旧 README（457 行）描述已不存在的 Agentic v3 架构（MCP、services/、combat/、AdminCoordinator）
- 重写为当前实际结构：game_core 五层六边形 + routers 薄 HTTP 壳（~130 行）

**延后项**：
- GameRuntime 单例生命周期 — 当前单进程部署，测试已隔离，不紧急
- `dict[str, Any]` 收窄 — 架构选择（schemaless 数据），非治理范畴
- SpellHandler / AreaSlice 拆分 — 大重构，需单独规划

**测试**：450 passed（+1 command_error 测试）

## 补充说明

- `FastAPIInputPort` 已经完成，但当前定位已收敛为：
  - 路由层 → `game_core` 的输入协议归一化桥接
  - 负责 text alias / interaction payload normalize
  - **不**直接承担 runtime-aware presence / precheck / execution 编排
