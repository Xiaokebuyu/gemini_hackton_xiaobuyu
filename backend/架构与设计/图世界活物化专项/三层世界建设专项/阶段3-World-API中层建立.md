# 阶段 3：World API 中层建立

> 状态：✅ 完成
> 目标：SessionRuntime 升级为统一中层 API，机械操作收编，MCP 直写废弃。
> 前置：阶段 2 ✅ 完成

---

## 架构决策（已确认）

### D1. 存储策略：状态数据零运行时写，消息日志允许异步写

运行时期间**状态数据**只存在于内存（SessionRuntime + WorldGraph）。Firestore 只在两个时刻被访问：

- `restore()` — 会话开始时从 Firestore 加载到内存
- `persist()` — 回合结束时从内存写入 Firestore

**状态数据零运行时 Firestore 读写。** 消息日志（SessionHistory）允许 fire-and-forget 异步写入 Firestore，因为消息日志不影响游戏状态，且丢失可接受。MCP Tools 现有的 CharacterStore/GraphStore 直写路径已标记 deprecated。

### D2. 中层形态：复用 SessionRuntime 就地升级

不新建 `WorldAPI` class。SessionRuntime 已经是"内存唯一真理源"，直接在其上增加机械操作方法（从 V4AgenticTools 下沉）。IntentExecutor 保持不变，作为"意图→操作"的分派器。

### D3. 两套接口

中层暴露两种接口，底层操作相同，外衣不同：

- **机械直调接口**（玩家/系统）：直接操作，如 `navigate()`、`buy()`、`equip()`
- **沉浸式接口**（LLM/Agent）：角色视角工具，如 `react_to_interaction()`、`recall_experience()` → 阶段 4 实现

### D4. 数据模型方向 B：WorldGraph + SessionRuntime 侧子系统

- WorldGraph 存关系和状态标记（轻量标量）
- SessionRuntime 持有结构化游戏子系统（InventoryManager、ShopManager 等）
- persist() 时两者一起快照
- 子系统（商店、装备等）尚未实现，可跳过，预留接口位即可

---

## 预期架构拓扑（阶段 3 完成后）

```
上层 (Agents / Players)  ← 阶段 3 不动，阶段 4 改造
│
├── PipelineOrchestrator (回合编排 A/B/C)
├── FlashCPU (GM Agent, LLM + 工具调用)
├── V4AgenticToolRegistry (瘦身: 仅 LLM 壳 + SSE 推送)
├── TeammateResponseService / NPCReactor
└── REST API (game_v2.py)
         │
─────────┼────────────────────────────────────────────
         │
中层 (World API)  ← 阶段 3 核心
│
├── 机械直调接口 (玩家/系统操作)
│   navigate() / buy() / equip() / use_item() / rest() / ...
│
├── 沉浸式接口 [阶段 4 实现，阶段 3 预留位]
│   react_to_interaction() / recall_experience() / ...
│
├── SessionRuntime (核心状态容器 + 机械操作)
│   ├── 状态: player / party / narrative / world_graph / time / ...
│   ├── 生命周期: restore() / persist()
│   ├── 机械操作 (从 V4AgenticTools 下沉):
│   │   heal() / damage() / add_xp() / add_gold()
│   │   add_item() / remove_item()
│   │   activate_event() / complete_event() / fail_event()
│   │   advance_chapter() / complete_objective()
│   │   update_disposition()
│   └── SceneBus (回合消息总线)
│
├── IntentExecutor (意图→机械操作)
│   move / sublocation / talk_pending / rest / examine / use_item
│
├── stats_manager (纯函数: xp/hp/gold)
│
└── [未来子系统, 可跳过]
    ShopManager / EquipManager / InventoryManager
         │
─────────┼────────────────────────────────────────────
         │
底层 (World Engine)  ← 阶段 1+2 已完成
│
├── WorldGraph / BehaviorEngine / EventPropagation
├── GraphBuilder / Snapshot / models / constants
├── IntentResolver / PlayerNodeView / TimeManager
└── 纯机械，零 AI，零 Firestore 运行时读写
         │
─────────┼────────────────────────────────────────────
         │
存储层 (Firestore)  ← 仅存档
    restore() 读 / persist() 写
    运行时零读写，MCP 直写路径已废弃
```

---

## 3.1 V4AgenticTools 机械操作下沉

**目标**：将 V4AgenticToolRegistry 中的 14 个纯机械操作抽出，作为 SessionRuntime 的方法。V4AgenticToolRegistry 瘦身为纯上层（LLM 工具注册 + SSE 推送 + 错误处理 + `_record()`），调用 SessionRuntime 的方法。

**下沉操作清单**：

| 操作 | 当前位置 | 目标位置 | 底层调用 |
|------|---------|---------|---------|
| heal_player | V4AgenticTools | SessionRuntime | stats_manager.add_hp() |
| damage_player | V4AgenticTools | SessionRuntime | stats_manager.remove_hp() |
| add_xp | V4AgenticTools | SessionRuntime | stats_manager.add_xp() |
| add_item | V4AgenticTools | SessionRuntime | player.add_item() |
| remove_item | V4AgenticTools | SessionRuntime | player.remove_item() |
| update_time | V4AgenticTools | SessionRuntime | advance_time() (已有) |
| activate_event | V4AgenticTools | SessionRuntime | wg.merge_state() + tick |
| complete_event | V4AgenticTools | SessionRuntime | wg.merge_state() + 奖励 |
| fail_event | V4AgenticTools | SessionRuntime | wg.merge_state() |
| advance_stage | V4AgenticTools | SessionRuntime | wg.merge_state() + tick |
| complete_event_objective | V4AgenticTools | SessionRuntime | wg.merge_state() |
| advance_chapter | V4AgenticTools | SessionRuntime | narrative 直改 |
| complete_objective | V4AgenticTools | SessionRuntime | narrative 直改 |
| update_disposition | V4AgenticTools | SessionRuntime | wg.merge_state() |

**V4AgenticToolRegistry 瘦身后保留**：
- LLM 工具壳（调用 SessionRuntime 方法 + `_record()` + SSE 推送）
- MCP 代理工具（npc_dialogue / combat / ability_check）
- 上层服务工具（recall_memory / create_memory / generate_image）
- 引擎排除逻辑（`_ENGINE_TOOL_EXCLUSIONS`）

---

## 3.2 MCP 直写路径废弃

**目标**：MCP Tools 现有的 Firestore 直写路径废弃，统一走 SessionRuntime。

**当前 MCP 直写路径**（需废弃或改造）：

| MCP 工具 | 当前路径 | 问题 |
|---------|---------|------|
| add_item | CharacterStore → Firestore | 绕过 WorldGraph |
| remove_item | CharacterStore → Firestore | 绕过 WorldGraph |
| heal_player | CharacterService → CharacterStore | 绕过 stats_manager |
| damage_player | CharacterService → CharacterStore | 绕过 stats_manager |
| add_player_xp | CharacterService → CharacterStore | 绕过 stats_manager |
| update_disposition_v2 | GraphStore → Firestore | 运行时直写 |

**处理方式**：
- 主游戏循环不经过这些 MCP 工具（已走 V4 路径）
- 这些 MCP 工具主要供外部工具集成使用
- 短期：标记为 deprecated
- 中期：改为调用 SessionRuntime（需要 MCP server 持有 session 引用，或改为 HTTP 调用主管线）
- 如确认无外部消费者，可直接删除

---

## 3.3 机械直调接口定义

**目标**：定义中层暴露给玩家/系统的直调操作集。

**部分功能（商店、装备管理等）尚未实现，可跳过，预留接口位即可。**

```
导航
  navigate(area_id)              → IntentExecutor.execute_move()
  enter_sublocation(sub_id)      → IntentExecutor.execute_sublocation_enter()
  leave_sublocation()            → IntentExecutor.execute_leave()

玩家属性
  heal(amount)                   → SessionRuntime.heal()
  damage(amount)                 → SessionRuntime.damage()
  add_xp(amount)                 → SessionRuntime.add_xp()
  add_gold(amount)               → SessionRuntime.add_gold()

背包与装备 [部分未实现]
  add_item(item_id, qty)         → SessionRuntime.add_item()
  remove_item(item_id, qty)      → SessionRuntime.remove_item()
  equip(slot, item_id)           → [未实现]
  unequip(slot)                  → [未实现]
  use_item(item_id)              → IntentExecutor.execute_use_item()

商店 [未实现]
  browse_shop(shop_id)           → [未实现]
  buy(shop_id, item_id, qty)     → [未实现]
  sell(shop_id, item_id, qty)    → [未实现]

事件
  activate_event(event_id)       → SessionRuntime.activate_event()
  complete_event(event_id, outcome) → SessionRuntime.complete_event()
  fail_event(event_id)           → SessionRuntime.fail_event()

叙事
  advance_chapter(chapter_id)    → SessionRuntime.advance_chapter()
  complete_objective(obj_id)     → SessionRuntime.complete_objective()

社交
  update_disposition(npc_id, deltas) → SessionRuntime.update_disposition()
  talk_to(npc_id, message)       → IntentExecutor.execute_talk()

时间
  advance_time(minutes)          → SessionRuntime.advance_time()
  rest(duration)                 → IntentExecutor.execute_rest()

战斗
  start_combat(enemies)          → [现走 MCP，暂不动]
  combat_action(action_id)       → [现走 MCP，暂不动]

查询
  get_inventory()                → SessionRuntime.player.inventory
  get_stats()                    → SessionRuntime.player (HP/XP/金币等)
  get_location()                 → SessionRuntime.player_location
  get_event_summaries()          → SessionRuntime.get_event_summaries_from_graph()
```

---

## 3.4 AdminCoordinator 瘦身

**目标**：AdminCoordinator 从"大管家"瘦身为"路由分发器"。

- 服务注册工厂职责保留
- 核心逻辑已在 PipelineOrchestrator + SessionRuntime
- 独立端点（战斗/队伍/叙事查询）改为调用 SessionRuntime 直调接口

---

## 执行顺序

| 次序 | 任务 | 预估改动 |
|------|------|---------|
| 1 | SessionRuntime 新增机械操作方法（3.1） | ~200 行 |
| 2 | V4AgenticToolRegistry 瘦身（调用 SessionRuntime） | ~-300 行 |
| 3 | 机械直调接口定义 + REST 端点对接（3.3） | ~100 行 |
| 4 | MCP 直写路径标记 deprecated（3.2） | ~30 行 |
| 5 | AdminCoordinator 瘦身（3.4） | ~-50 行 |
| 6 | 测试 + 回归验证 | ~150 行 |

---

## 补充决策（阶段 3 执行期间确认）

### Q1. REST 旁路收口

- `advance_time` / `enter_sub_location` / `leave_sub_location`：已在 AdminCoordinator 和 WorldRuntime 添加废弃标记，主流走 SessionRuntime + IntentExecutor
- `trigger_event`（旧事件路径）：推迟到阶段 4（D12），3 条活跃旁路均已标记 deprecated：
  - REST `POST .../narrative/trigger-event` → `narrative_service.trigger_event()`
  - MCP `trigger_event` → `narrative_service.trigger_event()`
  - FlashCPU `TRIGGER_NARRATIVE_EVENT` → `narrative_service.trigger_event()`

### Q3. 统一返回格式

SessionRuntime 机械操作方法统一返回 `{"success": bool, "error"?: str, ...data}`，V4AgenticTools 薄壳透传此格式。

### Q4. 私聊流推迟

`private_chat_stream` 推迟到阶段 4（D11）。私聊会影响 NPC 全局状态（好感度等），需要统一接入 Pipeline。当前 2 处旁路：
- `app/routers/game_v2.py` 私聊 SSE 端点
- `app/services/admin/admin_coordinator.py` `process_private_chat_stream()`

---

## 已知遗留（推阶段 4 处理）

### D1 额外旁路路径

以下路径仍存在运行时 Firestore 直写，违反 D1 约束，但因调用方仍活跃暂不可删除：

| 路径 | 位置 | 说明 |
|------|------|------|
| 队友好感度直写 | `teammate_agentic_tools.py:186` | `graph_store.update_disposition()` 直写 Firestore，已标 TODO(Phase 4) |
| MCP 好感度直写 | `graph_tools.py:201` | 同上，已标 deprecated |
| MCP 时间推进 | `time_tools.py:24` | 经 AdminCoordinator → WorldRuntime → Firestore，已标 deprecated |
| MCP 角色属性 | `character_tools.py` | heal/damage/add_xp/set_hp 直写 CharacterStore，已标 deprecated |
| MCP 背包操作 | `inventory_tools.py` | add_item/remove_item 直写 CharacterStore，已标 deprecated |

**处置原则**：所有旧路径仍有活跃调用方（REST/MCP/FlashCPU），阶段 4 迁移调用方后再删除。

### IntentExecutor 子地点校验

阶段 3 审计后补充了 IntentExecutor 的子地点校验：
- 子地点存在性校验（通过 `WorldInstance.area_registry`）
- SHOP 类子地点营业时间校验（08:00-20:00）
- 对齐了 WorldRuntime 原有的完整校验逻辑

---

## 验证标准

- [x] V4AgenticTools 的 14 个纯机械操作已下沉到 SessionRuntime
- [x] V4AgenticToolRegistry 瘦身后只包含 LLM 壳 + MCP 代理 + 上层服务
- [x] MCP 直写路径标记 deprecated（character/inventory/graph/time/narrative 共 10 个工具）
- [x] REST 旁路方法标记 deprecated（AdminCoordinator + WorldRuntime + trigger-event 端点）
- [x] 状态数据零运行时 Firestore 读写（消息日志允许异步写，已知遗留见上表）
- [x] 返回格式统一为 `{"success": bool, "error"?: str, ...data}`
- [x] IntentExecutor 子地点校验补全（存在性 + 营业时间）
- [x] `pytest tests/ -v` 基线不恶化（62 failed / 570 passed / 11 errors）

---

## 变更日志

| 日期 | 操作 |
|------|------|
| 2026-02-20 | 创建骨架 |
| 2026-02-20 | 重写：加入架构决策（D1-D4）、预期拓扑、详细任务分解、操作下沉清单 |
| 2026-02-21 | **阶段 3 完成**：14 个机械操作下沉到 SessionRuntime，V4AgenticTools 瘦身为薄壳，MCP/REST 旁路标记 deprecated，D1 放宽（消息日志允许异步写），补充 Q1/Q3/Q4 决策 |
| 2026-02-21 | **审计修补**：修复 activate_event 返回格式（message→error），补全 IntentExecutor 子地点校验，trigger_event 3 条旁路加废弃标记，D1 额外旁路记录（teammate/graph/time MCP），总览文档状态同步 |
