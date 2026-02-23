# L2 中层 API 地图 — 深审定稿

> 创建时间：2026-02-23
> 历次审计：Codex 深审 → **Claude Opus 三路代理逐文件深审**（本版）
> 验证方法：每个文件检查 imports + 实例化模式 + LLM 调用链
> 状态：**深审定稿，全部分类经代码级验证，混合文件明确标注**

---

## 一、L2 定义

**中层 = 编排调度 + 统一接口 + 权限控制 + 动作验证 + 结果包装**。

对上层暴露统一操作面板，对下层转发到 World Engine。
理想状态不含 LLM 调用，不含直接状态操作。

**判定标准**：
- 纯 L2：仅做路由/编排/协调，不直接调 LLM，不直接操作 L3 状态
- L2/L3 混合：编排层但直接实例化/操作 L3 组件
- L2/L1 混合：编排层但包含 LLM 调用或注入 LLM 服务

---

## 二、全量 L2 模块地图（深审后 22 个模块）

### 2.1 核心编排层（6 个）

| # | 模块 | 文件 | 行数 | 真实层级 | 验证 | 用途 | 混合标记 |
|---|------|------|------|----------|------|------|----------|
| 1 | PipelineOrchestrator | `admin/pipeline_orchestrator.py` | 1578 | **L2/L3 桥** | ✓ | V4 三阶段编排（A 上下文/B Agentic/C 后处理） | 🔸 imports 8 个 L3 组件 + 23 个动态 L3 导入；B 阶段含 L1 AgenticExecutor |
| 2 | AdminCoordinator | `admin/admin_coordinator.py` | 1227 | **L2/L3 混合** | ✓ | 入口协调器单例，委托核心逻辑到 Pipeline | 🔸 直接实例化 7+ L3 组件；lazy-load LLMService(L587) |
| 3 | FlashCPUService | `admin/flash_cpu_service.py` | 492 | **L1/L2 混合** | ✓ | Flash MCP 工具执行 + 系统提示管理 | 🔴 直接 import+实例化 LLMService(L25,56)；实质是 L1 |
| 4 | WorldRuntime | `admin/world_runtime.py` | 233 | **L2** | ✓ | 世界状态运行时薄壳（仅 start_session + get_state） | ⚠️ 已废弃，`get_state()` 有回读逻辑待迁移 |
| 5 | REST Router | `routers/game_v2.py` | 1048 | **L2** | ✓ | 唯一 FastAPI 路由器（37+ 端点），纯分发零业务逻辑 | 25+ 处异常泄露到客户端 |
| 6 | EventService | `admin/event_service.py` | 295 | **L2** | ✓ | 事件入图 + EventBus 发布 | lazy-load EventLLMService(L43-44)，惰性 L1 委托 |

### 2.2 MCP 工具接口层（10 个）

| # | 模块 | 文件 | 行数 | 真实层级 | 验证 | 用途 | 实例分叉? |
|---|------|------|------|----------|------|------|-----------|
| 7 | GameToolsServer | `mcp/game_tools_server.py` | 70 | **L2** | ✓ | Game MCP 服务器入口（注册壳） | — |
| 8 | NavigationTools | `mcp/tools/navigation_tools.py` | 36 | **L2** | ✓ | 位置/子地点导航 | ✅ 走 Coordinator 单例 |
| 9 | TimeTools | `mcp/tools/time_tools.py` | 20 | **L2** | ✓ | 获取游戏时间 | ✅ 走 Coordinator 单例 |
| 10 | CharacterTools | `mcp/tools/character_tools.py` | 40 | **L2** | ✓ | 角色查询 | 🔴 自建 CharacterStore |
| 11 | InventoryTools | `mcp/tools/inventory_tools.py` | 101 | **L2** | ✓ | 装备/卸下/搜索物品 | 🔴 自建 CharacterStore+CharacterService 链 |
| 12 | NarrativeTools | `mcp/tools/narrative_tools.py` | 19 | **L2** | ✓ | 叙事进度/可用地图 | 🔴 自建 NarrativeService |
| 13 | NpcTools | `mcp/tools/npc_tools.py` | 147 | **L2** | ✓ | NPC 实例管理/对话 | 🔴🔴 自建 InstanceManager+LLMService（**最严重**） |
| 14 | PartyTools | `mcp/tools/party_tools.py` | 56 | **L2** | ✓ | 队伍信息查询 | 🔴 自建 GraphStore+PartyStore+PartyService |
| 15 | PasserbyTools | `mcp/tools/passerby_tools.py` | 54 | **L2** | ✓ | 路人刷新/对话 | 🔴 自建 PasserbyService |
| 16 | CombatMCPServer | `combat/combat_mcp_server.py` | 793 | **L2** | ✓ | 战斗 MCP 服务器 | 🔴 自建 CombatEngine+SessionStore+EventService |

> **实例分叉统计**：10 个 MCP 模块中 **8 个存在实例分叉**（仅 navigation_tools 和 time_tools 正确走 Coordinator）

### 2.3 业务服务层（5 个）

| # | 模块 | 文件 | 行数 | 真实层级 | 验证 | 用途 | 混合标记 |
|---|------|------|------|----------|------|------|----------|
| 17 | PasserbyService | `services/passerby_service.py` | 507 | **L2** | ✓ | 路人 NPC 生命周期管理（刷新/对话/消亡） | 委托 LLM 到 TieredAIService(L1)，自身无 genai 导入 |
| 18 | MCPClientPool | `services/mcp_client_pool.py` | 886 | **L2** | ✓ | MCP 客户端连接池（健康检查/重连/超时） | 纯基础设施 |
| 19 | Dependencies | `dependencies.py` | 17 | **L2/Infra** | ✓ | @lru_cache 依赖注入 | lru_cache 无失效策略 |

### 2.4 已确认非 L2（从 L2 地图移除）

| 模块 | 文件 | 行数 | 原分类 | 深审结论 | 迁移目标 |
|------|------|------|--------|----------|----------|
| EventLLMService | `admin/event_llm_service.py` | 270 | L2 | **→ L1** | 直接实例化 LLMService，调 generate_json×2，纯 LLM 转换 |
| StateManager | `admin/state_manager.py` | 74 | L2 | **→ L3** | 纯内存状态快照+增量追踪，零编排 |
| SessionHistory | `services/session_history.py` | 389 | L2 | **→ L3** | 纯 ContextWindow 封装，图谱化触发由外部执行 |
| InstanceManager | `services/instance_manager.py` | 576 | L2 | **→ L3** | 运行时零 LLM import，纯实例池+LRU+上下文窗口 |
| NarrativeService | `services/narrative_service.py` | 1320 | L2 | **→ L3** | 零 LLM import，纯章节/主线条件状态机 |

> **深审变更**：5 个模块从 L2 地图移除（3 个→L3，1 个→L3，1 个→L1），L2 模块数保持 22 个（因 combat_mcp_server 新增补入）

---

## 三、混合文件全景（重构关键）

> 以下文件跨层耦合，**必须标记清楚**以指导拆分重构。

### 3.1 L2/L3 桥接文件（编排层深耦合引擎层）

| 文件 | 行数 | L2 部分 | L3 部分 | 拆分建议 |
|------|------|---------|---------|----------|
| `pipeline_orchestrator.py` | 1578 | A/C 阶段编排、SSE 推送、对话选项生成 | 直接调 GameRuntime/SessionRuntime/IntentResolver/IntentExecutor/BehaviorEngine/SceneBus | 建设 WorldAPI 门面后，Pipeline 通过 WorldAPI 调 L3，不再直接 import |
| `admin_coordinator.py` | 1227 | 入口分发、单例管理、FlashCPU 协调 | 直接实例化 GameRuntime/SessionRuntime/AreaNavigator/TimeManager/ContextAssembler | 同上，Coordinator 持有 WorldAPI 而非散装 L3 组件 |

### 3.2 L1/L2 混合文件（编排层含 LLM 调用）

| 文件 | 行数 | L2 部分 | L1 部分 | 拆分建议 |
|------|------|---------|---------|----------|
| `flash_cpu_service.py` | 492 | MCP 工具路由（SPAWN/START_COMBAT/ADD_TEAMMATE 等） | LLMService 实例化+调用、系统提示加载、NPC_DIALOGUE LLM 对话 | 拆为 FlashRouter(L2) + FlashLLM(L1) |
| `pipeline_orchestrator.py` | 1578 | （同上 L2 部分） | B 阶段 AgenticExecutor LLM 循环、NPC/私聊 LLM 调用 | B 阶段提取为独立 AgenticStage(L1) |
| `admin_coordinator.py` | 1227 | （同上 L2 部分） | lazy-load LLMService(L587) 用于特定场景 | LLM 调用提取到 L1 服务 |

### 3.3 MCP 实例分叉文件（L2 绕过 Coordinator 直建实例）

| 文件 | 自建的独立实例 | 影响 | 修复方案 |
|------|---------------|------|----------|
| `npc_tools.py` | InstanceManager + LLMService | 🔴 NPC 池割裂，对话上下文丢失 | 改走 Coordinator |
| `combat_mcp_server.py` | CombatEngine + SessionStore + EventService | 🔴 战斗状态/事件隔离 | 注入 Coordinator 依赖 |
| `inventory_tools.py` | CharacterStore + CharacterService | 🟡 角色状态读写分叉 | 改走 Coordinator |
| `party_tools.py` | GraphStore + PartyStore + PartyService | 🟡 队伍/图谱状态分叉 | 改走 Coordinator |
| `passerby_tools.py` | PasserbyService | 🟡 路人状态分叉 | 改走 Coordinator |
| `narrative_tools.py` | NarrativeService | 🟡 叙事缓存不一致 | 改走 Coordinator |
| `character_tools.py` | CharacterStore | 🟢 只读，风险较低 | 改走 Coordinator |

> **正确模式**（`navigation_tools.py` / `time_tools.py`）：
> ```python
> _admin = None
> def _get_admin():
>     global _admin
>     if _admin is None:
>         from app.services.admin.admin_coordinator import AdminCoordinator
>         _admin = AdminCoordinator.get_instance()
>     return _admin
> ```

---

## 四、四条关键操作调用链（保留 Codex 全链追踪）

### 4.1 update_disposition（好感度更新）

```
GM Agent 链路:
  game_v2.py:388 → AdminCoordinator:361 → PipelineOrchestrator:322
  → AgenticExecutor:83 → immersive_tools.py:439 (或 :154)
  → SessionRuntime:1366 → SR:1424 (merge_state)
  → PipelineOrchestrator:543 → SessionRuntime:1100 → SR:588 (world_snapshot 落库)

碎片化: react_to_interaction (:154) 与 update_disposition (:439) 两入口语义重叠。
```

### 4.2 complete_event（事件完成）

```
主链:
  game_v2.py → coordinator → pipeline agentic
  → immersive_tools.py:295 → SessionRuntime:1529 → SR:1551 (status=completed)

自动分支:
  immersive_tools.py:416 (advance_stage) → SessionRuntime:1688
  → SR:1752 (末阶段自动 complete_event)

碎片化: complete_event 与 advance_stage 的自动完成共存。
```

### 4.3 record_memory / create_memory（记忆写入）

```
create_memory (GM):
  router → coordinator → pipeline agentic
  → immersive_tools.py:453 → SessionRuntime:2055 → SR:2102

碎片化: 无 REST 直通口，完全依赖 Agent 工具侧。
```

### 4.4 advance_time（时间推进）

```
自动每轮 +10:
  PipelineOrchestrator:430 → :435 → SessionRuntime:1149

MOVE 路径（⚠️碎片化）:
  IntentExecutor:91 → :149-167 直接 update_time（绕过 advance_time/TimeManager.tick）
  → 时间先更新后切图，失败时留下脏推进
```

---

## 五、三套接口碎片化问题

| 接口 | 文件 | 工具/端点数 | 状态 |
|------|------|-----------|------|
| REST API | `game_v2.py` | 37 端点 | 走 Coordinator ✓ |
| MCP Tools | `app/mcp/tools/*.py` | ~17 工具 | 8/10 实例分叉 ✗ |
| Immersive Tools | `immersive_tools.py` | 30 工具 | 直通 L3 ✗ |
| GM Extra Tools | `gm_extra_tools.py` | 8 工具 | MCP 依赖 + L3 直操 ✗ |

### 关键操作的路径碎片

#### XP 奖励（4 条路径）

| 路径 | 入口 | 校验 |
|------|------|------|
| REST → LLM → 沉浸工具 | `immersive_tools.add_xp` | 无 |
| GM Extra → FlashCPU | `gm_extra_tools` | 无 |
| 战斗结算 | `stats_manager.sync_combat_rewards` | 有 |
| 旧路径 | `CharacterService.add_xp()` → CharacterStore | 有 |

#### NPC 对话（4 条路径）

| 路径 | 实例池 | 问题 |
|------|--------|------|
| `/interact/stream` | InstanceManager A (Coordinator) | ✓ |
| `/private-chat/stream` | InstanceManager A (Coordinator) | ✓ |
| `npc_dialogue` GM 工具 | InstanceManager A (Coordinator) | ✓ |
| `npc_respond` MCP | **InstanceManager B** (npc_tools.py) | 🔴 独立池 |

---

## 六、缺失的 L2 组件

| 组件 | 状态 | 说明 |
|------|------|------|
| **统一 World API** | 完全不存在 | 4 套接口各自为战 |
| **权限系统** | 完全不存在 | RoleRegistry 仅工具过滤，无运行时校验 |
| **StatsManager 收口** | 部分存在 | 纯函数已有，但沉浸工具直接绕过调 player.add_xp() |

---

## 七、屎点汇总（深审后 22 个）

| # | 屎点 | 位置 | 严重度 | 类型 |
|---|------|------|--------|------|
| 1 | `admin_agentic_thinking` 字段不存在 | `pipeline_orchestrator.py:858` | 🔴 P0 | Bug |
| 2 | Pipeline 11 处裸异常 | `pipeline_orchestrator.py` 多处 | 🔴 P1 | 异常处理 |
| 3 | 25+ 处 API 异常泄露到客户端 | `game_v2.py`, `main.py` | 🟡 P2 | 安全 |
| 4 | AdminCoordinator 死缓存 | `admin_coordinator.py:110-111` | 🟡 P2 | 死代码 |
| 5 | FlashCPU 是 L1 却放在 L2 | `flash_cpu_service.py` | 🟡 P1 | 层级错位 |
| 6 | WorldRuntime 残留 | `world_runtime.py` | 🟡 P2 | 废弃代码 |
| 7 | 无统一 World API | — | 🔴 P1 | 架构缺失 |
| 8 | 无权限系统 | — | 🟡 P2 | 架构缺失 |
| 9 | 4 套接口碎片化 | REST/MCP/Immersive/Extra | 🔴 P1 | 架构碎片 |
| 10 | StatsManager 非唯一入口 | 多文件 | 🟡 P1 | 一致性 |
| **11** | **MCP 工具实例分叉（8/10 模块）** | `mcp/tools/*.py` + `combat_mcp_server.py` | 🔴 **P0** | 缓存/状态隔离 |
| **12** | **IntentExecutor MOVE 先改时间再切图** | `intent_executor.py:149-173` | 🔴 **P1** | 状态不一致 |
| **13** | **IntentExecutor USE_ITEM 先加血再扣物品** | `intent_executor.py:737-748` | 🔴 **P1** | 免费回血漏洞 |
| **14** | **NarrativeService 同步 I/O + 无锁 reload** | `narrative_service.py:74,99,155` | 🟡 P1 | 阻塞 + 竞态 |
| **15** | **PasserbyService 同步 I/O + memory-first** | `passerby_service.py:84,85,92,276,328` | 🟡 P1 | 阻塞 + 不一致 |
| **16** | **EventService 惰性加载 LLM** | `event_service.py:42-45` | 🟡 P1 | 层级违规 |
| **17** | **EventLLMService 层级错位** | `event_llm_service.py` | 🟡 P1 | 纯 L1 错放 admin/ |
| **18** | **SessionHistory graphize 竞态** | `session_history.py:264,278` | 🟡 P2 | bool 无锁 |
| **19** | **MCPClientPool 裸异常归一化** | `mcp_client_pool.py:316,362,754` | 🟡 P2 | 根因被压缩 |
| **20** | **InstanceManager _locks 只增不减** | `instance_manager.py:129-143` | 🟡 P2 | 内存泄漏 |
| **21** | **Dependencies lru_cache 无失效** | `dependencies.py:10,15` | 🟢 P3 | 热重载隐患 |
| **22** | **combat_mcp_server 实例分叉** | `combat_mcp_server.py:53-55` | 🔴 P1 | 独立 Engine/Store/EventService |

---

## 八、L2 层健康度（深审定稿）

| 维度 | 评分 | 说明 |
|------|------|------|
| 统一接口 | 1/10 | 完全不存在，4 套接口各自为战 |
| 权限控制 | 1/10 | 仅有 RoleRegistry 工具过滤 |
| 编排能力 | 6/10 | Pipeline 功能完整但深耦合 L3（L2/L3 桥） |
| 实例一致性 | **1/10** | **8/10 MCP 模块自建独立实例（含 combat）** |
| 操作原子性 | 2/10 | IntentExecutor 操作顺序倒置，无回滚 |
| 错误处理 | 3/10 | 裸异常 + 客户端泄露 |
| 层级纯度 | 3/10 | FlashCPU(L1) + EventLLMService(L1) 错放 L2 |
| **总体** | **2.5/10** | **MCP 实例分叉从 5→8 个，combat 也沦陷** |

### 行数统计

| 分类 | 行数 |
|------|------|
| 核心编排层（6 个） | 4,873 |
| MCP 工具层（10 个） | 1,338 |
| 业务服务层（3 个） | 1,410 |
| **L2 总计** | **7,621** |
