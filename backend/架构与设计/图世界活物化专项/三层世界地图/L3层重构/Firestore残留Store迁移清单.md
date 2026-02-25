# Firestore 残留 Store 迁移清单

> 创建时间：2026-02-23
> 背景：P0-1 审计（42 处同步 Firestore I/O）发现，除 WorldGraph 快照和世界加载外，其余 Firestore Store 均为遗留/遗漏，应迁移至本地持久化（随 SessionRuntime 快照落盘）或直接删除。

> **2026-02-23 执行**：7 个文件已全部删除（含 `companion_state.py` 模型）。22 处断裂引用待后续重建时清理。
>
> **2026-02-24 Phase C 执行**：FlashCPU 溶解 + admin_coordinator / world_runtime / narrative_service Store 脱离。Tier 1（T1-T5）+ Tier 3（T9-T10）已完成。剩余断裂靠 `.pyc` 缓存兜底，新环境部署会崩。
>
> **2026-02-24 Codex 审计**：确认 Tier 2（T6-T8）+ Tier 4（T11-T13）+ area_runtime 断裂仍存在。详见末尾"Phase C 后审计发现"章节。
>
> **2026-02-24 P0 AreaRuntime 清理完成**：全部 15 处引用已删除 + .pyc 缓存已删除。纯清理策略（AreaRuntime 在所有活路径中已被 bypass）。测试 709→766 passed（+57），零回归。

---

## 断裂引用清单（删除后需重建或清理）

| 调用方文件 | 引用的已删模块 | 重建时处理方式 |
|-----------|--------------|--------------|
| `session_runtime.py` :53,340,893,954,1512 | `area_runtime.AreaRuntime` | ✅ 已删除（纯清理，不需重建） |
| `session_runtime.py` :382 | `companion_instance.CompanionInstance` | 删除：移除 `_restore_companions()` |
| `runtime/__init__.py` :15 | `area_runtime.AreaRuntime` | ✅ 调查确认已无 re-export |
| `party_service.py` :21 | `party_store.PartyStore` | 重建：改为内存操作 + 快照持久化 |
| `admin_coordinator.py` :38,44,47 | `GameSessionStore` / `PartyStore` / `CharacterStore` | ~~重建：改接 SessionRuntime/WorldGraph~~ **✅ Phase C Step 1 已完成**：Store import 全部删除/lazy 化 |
| `flash_cpu_service.py` :22 | `GameSessionStore` | ~~重建：改接 StateManager~~ **✅ Phase C Step 6c**：整个文件已删除（503 行） |
| `world_runtime.py` :15 | `GameSessionStore` | ~~重建：改接 SessionRuntime~~ **✅ Phase C Step 1c 已完成** |
| `narrative_service.py` :27 | `GameSessionStore` | ~~重建：改接 SessionRuntime~~ **✅ Phase C Step 1c 已完成** |
| `character_service.py` :16 | `CharacterStore` | ✅ 已清理（store=None + .pyc 删除） |
| `ability_check_service.py` :6 | `CharacterStore` | ✅ 已清理（store=None + .pyc 删除） |
| `services/__init__.py` :7 | `GameSessionStore` | ~~删除：移除导出~~ **✅ Phase C 已完成** |
| `combat_mcp_server.py` :20,25 | `CombatDataRepository` / `GameSessionStore` | 重建：改接 WorldInstance（**A 修已完成**：死 import 删除 + NotImplementedError 哨兵） |
| `enemy_registry.py` :8 | `CombatDataRepository` | 重建：改接 WorldInstance（**A 修已完成**：repository=None 时抛 NotImplementedError） |
| `mcp/tools/character_tools.py` :4 | `CharacterStore` | ✅ 已 stub 化（返回"已废弃"错误） |
| `mcp/tools/inventory_tools.py` :6 | `CharacterStore` | ✅ 已 stub 化（lookup_item/search_items 保留） |
| `mcp/tools/party_tools.py` :5 | `PartyStore` | ✅ 已 stub 化（返回"已废弃"错误） |

---

## 总览

| 文件 | 同步 I/O 数 | 分类 | 迁移目标 |
|------|-----------|------|---------|
| `companion_instance.py` | 7 | 遗留 — 直接删除 | N/A |
| `party_store.py` | 7 | 遗漏 — 应随快照本地持久化 | SessionRuntime 快照 |
| `game_session_store.py` | 6 | 遗漏 — 应随快照本地持久化 | SessionRuntime 快照 |
| `character_store.py` | 3 | 过渡态 — 应完成迁移 | WorldGraph PlayerNode |
| `area_runtime.py` | 6 | 过渡态 — 应完成迁移 | WorldGraph + SessionRuntime |
| `data_repository.py` | 2 | 遗漏 — 应迁移至 WorldInstance | WorldInstance 启动加载 |
| **保留** | | | |
| `world_instance.py` | 8 | 保留 — 世界静态数据启动加载 | 不动 |
| `session_runtime.py` | 3 | 保留 — WorldGraph 快照读写 | 不动（后续可改 AsyncClient） |

---

## 一、遗留 — companion_instance.py（直接删除）

**判定**：同伴三层记忆是遗留设计，应删除。

### 当前实现

**Firestore 路径**：
```
worlds/{world_id}/sessions/{session_id}/companions/{character_id}/
  data/state                    ← 情感状态（mood/trust/loyalty）
  shared_events/                ← CompactEvent 文档（最多 100 条）
  area_summaries/               ← CompanionAreaSummary 文档（最多 50 条）
```

**数据模型**：
- `CompanionEmotionalState`：mood / trust_level / loyalty / recent_interactions / relationship_tags
- `CompactEvent`：event_id / event_name / summary / area_id / game_day / importance / player_role
- `CompanionAreaSummary`：area_id / area_name / visit_day / key_events / interactions_with_player / mood_during_visit / summary

**公开方法**：
| 方法 | 用途 |
|------|------|
| `load()` | 从 Firestore 加载情感状态 + 事件 + 区域摘要 |
| `save()` | batch 写入情感状态 + 事件 + 摘要 |
| `add_event(event)` | 追加事件（内存，save 时落盘） |
| `add_area_summary(summary)` | 追加区域摘要（内存） |
| `get_memory_context()` | 返回 LLM 用的记忆字典（最近 20 事件 + 10 摘要 + 情感） |

**调用方**：仅 `session_runtime.py:_restore_companions()`

**迁移方案**：直接删除文件 + 删除 SessionRuntime 中的 `_restore_companions()` 调用。

---

## 二、遗漏 — party_store.py（应随快照本地持久化）

**判定**：队伍数据应随 SessionRuntime 快照一起本地持久化，当前走 Firestore 是遗漏。

### 当前实现

**Firestore 路径**：
```
worlds/{world_id}/parties/{session_id}/          ← 队伍主文档
  members/{character_id}/                        ← 成员子集合
```

**数据模型**：

队伍文档：
```python
{
    "party_id": str,
    "world_id": str,
    "session_id": str,
    "leader_id": str,
    "formed_at": datetime,
    "max_size": int,          # 默认 4
    "auto_follow": bool,      # 默认 True
    "share_events": bool,     # 默认 True
    "current_location": Optional[str],
    "current_sub_location": Optional[str],
    "updated_at": datetime,
}
```

成员文档（`character_id` 为文档 ID）：
```python
{
    "name": str,
    "role": str,              # TeammateRole 枚举值
    "personality": str,
    "response_tendency": str,
    "joined_at": datetime,
    "is_active": bool,
    "current_mood": str,
    "graph_ref": str,
    "model_config_override": Optional[dict],  # TeammateModelConfig
}
```

**公开方法**：
| 方法 | 用途 |
|------|------|
| `create_party()` | 创建队伍文档 |
| `get_party()` | 加载队伍 + 遍历成员子集合 |
| `add_member()` | 添加成员文档 |
| `remove_member()` | 删除成员文档 |
| `update_member_status()` | 更新成员活跃状态 |
| `update_party_location()` | 更新队伍位置 |
| `delete_party()` | 删除队伍 + 遍历删除成员 |

**调用方**：`PartyService`（包装层）→ `AdminCoordinator` / `MCP party_tools`

**迁移方案**：Party 数据纳入 SessionRuntime 快照，随 `persist()` 一起序列化到本地。`PartyService` 仅操作内存，快照负责落盘。

---

## 三、遗漏 — game_session_store.py（应随快照本地持久化）

**判定**：会话元数据应随 SessionRuntime 快照一起本地持久化。

### 当前实现

**Firestore 路径**：
```
worlds/{world_id}/sessions/{session_id}/         ← 会话文档
```

**数据模型**（`GameSessionState`）：
```python
{
    "session_id": str,
    "world_id": str,
    "participants": List[str],
    "status": str,                    # "scene" / "combat"
    "current_scene": SceneState,      # 嵌套文档
    "active_combat_id": Optional[str],
    "combat_context": CombatContext,   # 嵌套文档
    "updated_at": datetime,
}
```

**公开方法**：
| 方法 | 用途 |
|------|------|
| `create_session()` | 创建会话（自动生成 session_id，8 次重试） |
| `get_session()` | 读取会话 |
| `list_sessions()` | 列举会话（支持 user_id 过滤，limit） |
| `update_session()` | 部分更新（merge，支持 dot path） |
| `set_scene()` | 便捷：设场景 + status="scene" |
| `set_combat()` | 便捷：设战斗 + status="combat" |
| `clear_combat()` | 便捷：清战斗 + status="scene" |

**调用方**：`AdminCoordinator` / `FlashCPUService` / `WorldRuntime` / `NarrativeService` / `CombatMCPServer`

**迁移方案**：会话元数据纳入 SessionRuntime 快照。`list_sessions()` 需保留一个轻量索引（本地文件或内存），其余字段随快照落盘。注意 `create_session()` 的去重逻辑需改为本地实现。

---

## 四、过渡态 — character_store.py（完成迁移至 WorldGraph）

**判定**：运行时已走 `PlayerNodeView`（WorldGraph 适配器），CharacterStore 仅用于初始引导 + 兜底。应完成迁移。

### 当前实现

**Firestore 路径**：
```
worlds/{world_id}/sessions/{session_id}  → 字段 "player_character"   ← 角色数据嵌套在会话文档中
worlds/{world_id}/character_creation/config                          ← 角色创建配置
```

**数据模型**：
- `PlayerCharacter`：Pydantic 模型，`model_dump(mode="json")` 序列化
- 角色创建配置：Dict，三级查找（内存缓存 → Firestore → 本地 JSON）

**公开方法**：
| 方法 | 用途 |
|------|------|
| `save_character()` | 写入角色到会话元数据（merge） |
| `get_character()` | 读取角色，失败返回 None |
| `get_creation_config()` | 获取角色创建配置（缓存 + 兜底） |

**调用方**：`AdminCoordinator`（创建/恢复检查）/ `FlashCPUService`（战斗自动填充）/ `CharacterService`（CRUD 包装）/ `SessionRuntime`（初始种子加载）

**当前双路径**：
1. `SessionRuntime._restore_player()` 从 CharacterStore 加载初始种子
2. WorldGraph 构建后，`session.player` 返回 `PlayerNodeView`（图为唯一真理源）
3. 图不可用时降级返回 `_player_character`

**迁移方案**：
- `save_character` / `get_character`：角色数据完全走 WorldGraph PlayerNode，删除 Firestore 路径
- `get_creation_config`：角色创建配置属于世界静态数据，迁移到 `WorldInstance` 启动加载
- 删除兜底路径，WorldGraph 作为唯一来源

---

## 五、过渡态 — area_runtime.py（完成迁移至 WorldGraph）

**判定**：事件逻辑已迁移到 BehaviorEngine/WorldGraph，但区域状态 / 访问历史 / NPC 快照仍走 Firestore。应完成迁移。

### 当前实现

**Firestore 路径**：
```
worlds/{world_id}/areas/{area_id}/
  state/current/           ← AreaState（visit_count / discovered_sub_locations / cleared_encounters / custom_state）
  visits/                  ← VisitSummary 集合（每次 unload 写一条）
  npc_contexts/{npc_id}/   ← NPC 上下文快照
```

**数据模型**：

AreaState：
```python
{
    "area_id": str,
    "visit_count": int,
    "last_visit_day": int,
    "discovered_sub_locations": List[str],
    "cleared_encounters": List[str],
    "custom_state": Dict[str, Any],
    "updated_at": datetime,
}
```

VisitSummary：
```python
{
    "visit_id": str,
    "area_id": str,
    "session_id": str,
    "entered_at": datetime,
    "left_at": datetime,
    "actions_taken": List[str],
    "events_triggered": List[str],
    "npcs_interacted": List[str],
    "game_day": int,
    "summary_text": str,
}
```

**公开方法**：
| 方法 | 用途 |
|------|------|
| `load()` | 并行加载 state + visits + npc_contexts |
| `unload()` | 生成 VisitSummary + 持久化 state + 清理临时数据 |
| `persist_state()` | 增量保存 state + npc_contexts |
| `get_area_context()` | 构建 L2 区域上下文（给 LLM） |
| `get_location_context()` | 构建 L3 子地点上下文 |
| `record_action()` | 记录当前访问日志 |

**调用方**：仅 `SessionRuntime`（restore / navigate / persist）

**迁移方案**：
- `AreaState`（visit_count / discovered 等）→ 存入 WorldGraph 的 area 节点 state
- `VisitSummary` → 存入 WorldGraph 边或事件节点
- `npc_contexts` → 随 InstanceManager 或 WorldGraph 角色节点管理
- `get_area_context()` / `get_location_context()` 保留，但数据来源改为 WorldGraph

---

## 六、遗漏 — data_repository.py（应迁移至 WorldInstance）

> **2026-02-23 A 修完成**：`combat_mcp_server.py` 死 import 已删除，`enemy_registry.py` 的 `repository=None` 路径改为显式 `NotImplementedError`。"导入即炸"和"静默 NameError"问题已消除。B 修（重建接口接入 WorldInstance）待后续执行。

**判定**：战斗实体模板是世界静态数据，应由 WorldInstance 启动时加载，当前走 Firestore 是遗漏。

### 当前实现

**Firestore 路径**（两种格式，自动探测）：
```
# 格式 1：紧凑文档
worlds/{world_id}/combat_entities/{entity_type}    ← 单文档包含所有同类实体

# 格式 2：集合
worlds/{world_id}/{entity_type}/{entity_id}        ← 每个实体一个文档
```

**兜底**：本地 JSON `data/{world_id}/structured_new/` 或 `data/{world_id}/structured/`

**数据模型**：
```python
{
    "id": str,          # 从 id/enemy_id/skill_id/item_id/name 推导
    "name": str,
    "source": "world_data",
    "type": str,        # 可选
    "stats": dict,      # 可选
    "effect": str,      # 可选
    "description": str, # 可选
    "properties": dict, # 可选
}
```

ID 通过 `_safe_slug()` 归一化（小写 + 下划线）。

**公开方法**：
| 方法 | 用途 |
|------|------|
| `list_monsters()` | 加载所有怪物（缓存） |
| `list_skills()` | 加载所有技能（缓存） |
| `list_items()` | 加载所有物品（缓存） |
| `get_monster(id)` | 按 slug 查找单个怪物 |
| `get_skill(id)` | 按 slug 查找单个技能 |

**调用方**：`EnemyRegistry` → `CombatMCPServer`

**迁移方案**：
- `WorldInstance` 启动时已有 `_load_combat_entities()`，直接复用
- `CombatDataRepository` 改为从 `WorldInstance` 读取缓存，不再直连 Firestore
- 本地 JSON 兜底保留（离线开发用）

---

## 迁移优先级

| 优先级 | 项目 | 工作量 | 依赖 |
|--------|------|--------|------|
| 1 | companion_instance.py 删除 | 0.5 天 | 无 |
| 2 | data_repository.py → WorldInstance | 0.5 天 | 无 |
| 3 | character_store.py 完成迁移 | 1 天 | WorldGraph PlayerNode 稳定 |
| 4 | area_runtime.py 完成迁移 | 2 天 | WorldGraph area 节点 |
| 5 | party_store.py → 快照 | 1 天 | SessionRuntime 快照机制 |
| 6 | game_session_store.py → 快照 | 1-2 天 | SessionRuntime 快照机制 |

---

## 2026-02-23 深度审计：引用文件逐个分析

> P0-5 摸底。以下列出每个仍引用已删 Store 的文件，及其具体调用点和用途。

### SessionRuntime 已覆盖（零工作量）

| 能力 | 现有机制 |
|------|---------|
| Player 内存管理 | `session.player` + `mark_player_dirty()` |
| Party 内存管理 | `session.party` + `_dirty_party` |
| GameState 管理 | `session.game_state` + `_dirty_game_state` |
| 时间/叙事/区域 | `advance_time()` / `narrative` / `area` |
| WorldGraph 持久化 | `_persist_world_graph_snapshot()` + `_restore_world_graph_snapshot()` |
| Player 持久化 | `persist()` 已有 WorldGraph 快照路径 |

### 类别 A：V4 管线核心（crash 级别）

#### A1. `admin_coordinator.py` — 全局入口协调器

所有 game_v2 路由经 `get_coordinator()` 进入。引用全部三个 Store。

| Store | 调用方法 | 行号 | 用途 |
|-------|---------|------|------|
| GameSessionStore | `create_session()` | 207 | 创建新会话 |
| GameSessionStore | `get_session()` | 211, 285 | 加载会话状态 |
| GameSessionStore | `list_sessions()` | 226 | 列出可恢复会话 |
| GameSessionStore | `set_scene()` | 284 | 更新场景 |
| PartyStore | `get_party()` | 246 | 检查队伍是否存在（恢复阶段检测） |
| CharacterStore | `get_character()` | 258, 564, 689 | 检查角色是否存在（阶段检测） |

**迁移**: Session CRUD → `SessionRuntime.create()` / `.list()` / `.from_session_id()`；角色/队伍检查 → `session.player is not None` / `session.party is not None`

#### A2. `world_runtime.py` — 导航/时间/状态初始化

被 admin_coordinator 的 start_session / resume_session 调用。

| Store | 调用方法 | 行号 | 用途 |
|-------|---------|------|------|
| GameSessionStore | `create_session()` | 138 | 初始化会话 |
| GameSessionStore | `get_session()` | 57 | 恢复状态 |
| GameSessionStore | `update_session()` | 68, 187, 202 | 持久化状态变更 |

**迁移**: 全部改走 `SessionRuntime.persist()`

### 类别 B：活跃业务服务

#### B1. `character_service.py` — 角色创建/读写全生命周期

被 AdminCoordinator (L106) 和 FlashCPUService (L61) 创建。

- L25: `self.store = store or CharacterStore()`
- 全文使用角色 CRUD：创建、加载、更新、保存

**迁移**: 创建角色 → 写入 `session.player` + `mark_dirty`；读取 → `session.player`

#### B2. `party_service.py` — 队伍增删/位置同步

被 coordinator、pipeline_orchestrator、game_v2 路由广泛调用。

- L31: `self.party_store = party_store`
- 全文使用：`create_party()` / `get_party()` / `delete_party()` / `add_member()` 等

**迁移**: Party CRUD → 操作 `session.party` + `mark_dirty`

#### B3. `ability_check_service.py` — d20 能力检定

被 FlashCPUService ABILITY_CHECK 操作调用。

- L71: `self.store = store or CharacterStore()`
- L130: `store.get_character()` 获取角色属性计算修正值

**迁移**: 直接取 `session.player` 属性

### 类别 C：已 Stub（不 crash 但有残留）

#### C1. `flash_cpu_service.py`

- L22-23: `GameSessionStore = None  # type: ignore`
- `__init__` 参数 `session_store` 保留；`_apply_delta()` (L376) 是 V4 不走的遗留路径

**迁移**: 删除 stub + 死代码

#### C2. `narrative_service.py`

- L28-29: `GameSessionStore = None  # type: ignore`
- 仅用于获取 Firestore 客户端引用（L54-56）

**迁移**: 删除 stub，直接实例化 Firestore client（如仍需要）

### 类别 D：MCP 工具（独立子进程）

| 文件 | 引用的 Store | 模块级实例 | 工具数 |
|------|-------------|-----------|--------|
| `character_tools.py` | CharacterStore (L4) | `_store = CharacterStore()` (L6) | 2 |
| `inventory_tools.py` | CharacterStore (L6) | `_store = CharacterStore()` (L9) + `_service = CharacterService()` (L10) | 5 |
| `party_tools.py` | PartyStore (L5) | `_party_service = PartyService(PartyStore())` (L7-9) | 1 |
| `combat_mcp_server.py` | GameSessionStore (L25) | `session_store = GameSessionStore()` (L54) | N/A |

> **架构决策（待定）**：MCP 工具跑在独立子进程，无法访问主进程 SessionRuntime 内存。
>
> - **方案 A**: 废弃这些 MCP 工具（V4 已有 immersive_tools + gm_extra_tools 替代大部分功能）
> - **方案 B**: 改为 RPC/IPC 从主进程获取数据

### 附加：MCP 工具实例分叉（非 Store 相关）

以下 MCP 工具不引用已删 Store，但仍存在独立实例问题：

| 文件 | 独立实例 | 应共享的来源 |
|------|---------|-------------|
| `passerby_tools.py` | `_passerby_service = PasserbyService()` (L7) | AdminCoordinator.passerby_service |
| `narrative_tools.py` | `_narrative_service = NarrativeService()` (L6) | AdminCoordinator.narrative_service |

正确模式参考：`navigation_tools.py` / `time_tools.py` 使用 `AdminCoordinator.get_instance()` 懒加载。

---

## 执行计划（按 Tier）

### Tier 1 — admin_coordinator / world_runtime 脱离 Store

| # | 任务 | 替代方案 | 涉及文件 | 状态 |
|---|------|---------|---------|------|
| T1 | Session 创建 | `SessionRuntime.create()` 工厂方法 | session_runtime, admin_coordinator | ✅ Phase C Step 1a/1b |
| T2 | Session 列表/恢复 | `SessionRuntime.list()` / `from_session_id()` | session_runtime, admin_coordinator | ✅ Phase C Step 1a/1b |
| T3 | Session 状态更新 | 已有 `persist()` — world_runtime 改走 SessionRuntime | world_runtime | ✅ Phase C Step 1c |
| T4 | 角色存在检查 | `session.player is not None` | admin_coordinator | ✅ Phase C Step 1b |
| T5 | 队伍存在检查 | `session.party is not None` | admin_coordinator | ✅ Phase C Step 1b |

### Tier 2 — 业务服务脱离 Store

| # | 任务 | 替代方案 | 涉及文件 | 状态 |
|---|------|---------|---------|------|
| T6 | CharacterService 改用 SessionRuntime | 创建角色 → `session.player` + `mark_dirty` | character_service | ✅ 纯清理：store=None + .pyc 删除 |
| T7 | PartyService 改用 SessionRuntime | Party CRUD → `session.party` + `mark_dirty` | party_service | ✅ 纯清理：party_tools stub + .pyc 删除（PartyService 已有 None 守卫） |
| T8 | AbilityCheckService 改用 session.player | 直接取 `session.player` 属性 | ability_check_service | ✅ 纯清理：store=None + .pyc 删除 |

### Tier 3 — Stub 清理

| # | 任务 | 涉及文件 | 状态 |
|---|------|---------|------|
| T9 | 删除 `GameSessionStore = None` stub + `_apply_delta` 死代码 | flash_cpu_service | ✅ Phase C Step 6c：整文件已删除 |
| T10 | 删除 `GameSessionStore = None` stub | narrative_service | ✅ Phase C Step 1c |

### Tier 4 — MCP 工具（需架构决策）

| # | 任务 | 涉及文件 | 状态 |
|---|------|---------|------|
| T11 | character_tools / inventory_tools / party_tools 迁移或废弃 | 3 个 MCP tool 文件 | ✅ 全部 stub 化（返回"已废弃"错误） |
| T12 | combat_mcp_server GameSessionStore 引用 | combat_mcp_server | ✅ import 删除 + session_store=None stub |
| T13 | passerby_tools / narrative_tools 实例分叉修复 | 2 个 MCP tool 文件 | 待定（非 Store 相关，低优先） |

---

## L2 层延伸重构残留（2026-02-24 Codex 审计后整理）

> 来源：L2 层延伸重构 Phase A + Phase B 完成后，GPT-5 Codex 代码审计发现的残留项 + 执行计划中显式标注的延后项。
> 执行记录：`L2层延伸重构/PhaseA-层级净化.md`、`L2层延伸重构/PhaseB-WorldAPI建设.md`

### L2-1（严重）：`services/__init__.py` 链式导入阻断 WorldAPI 独立导入

**现象**：`from app.services.world_api import WorldAPI` 在独立环境（测试、脚本）中触发 `ModuleNotFoundError: app.services.game_session_store`。

**导入链**：
```
app.services.__init__:6  → AdminEventService
  → app.services.admin.__init__:3  → AdminCoordinator
    → admin_coordinator.py:38  → GameSessionStore  ← 已删除
```

**运行时影响**：Pipeline / TeammateService 用 lazy import（方法内 `from app.services.world_api import WorldAPI`），FastAPI 启动后走到该行时 `app.services` 已加载完毕，所以**生产不炸**。但独立导入（测试、CLI 工具）会炸。

**根因**：`app/services/__init__.py` 和 `app/services/admin/__init__.py` 急切导入了过多模块（包括引用已删 Store 的 AdminCoordinator），形成了导入深坑。

**修复方向**：
- **方案 A（推荐）**：清空 `services/__init__.py` 和 `admin/__init__.py` 的急切导入，改为空包或仅导出轻量类。各调用方按需 import 具体模块。
- **方案 B**：先修 T1-T10（让 AdminCoordinator 不再引用已删 Store），链自然通畅。

**与本清单关系**：与 Tier 1 的 `admin_coordinator.py` 脱离 Store（T1-T5）是同一根因。修完 T1-T5 后此项自动解决。

---

### L2-2（严重）：`area_runtime` 已删但仍被硬引用

**现象**：`ModuleNotFoundError: No module named 'app.runtime.area_runtime'`，导致大量测试失败。

**引用点**：
| 文件 | 行号 | 用途 |
|------|------|------|
| `session_runtime.py` | 54, 340, 893, 954, 1512 | `from app.runtime.area_runtime import AreaRuntime` |
| `event_machine.py` | 265, 346 | `from app.runtime.area_runtime import AreaRuntime` |
| `runtime/__init__.py` | 15 | re-export |

**与本清单关系**：已在断裂引用清单第一行记录。属于"五、过渡态 — area_runtime.py"迁移范围。

---

### L2-3（中 / 已修）：graph_writer 边数统计漏记

**现象**：`merge_extraction()` 中 `npc → event_group` 的 `has_memory` 边和 `event_group → sub_event` 的 `contains` 边被创建，但未累加 `result.new_edges`。上层 `memory_graphizer.py:144` 直接返回该计数，导致统计偏低。

**状态**：✅ 2026-02-24 已修复（`graph_writer.py` 两处补 `result.new_edges += 1`）。

---

### L2-4（~~中 / 延后~~ → ✅ 已解决）：gm_extra_tools 仍直接操作 session（L3 穿透）

> **2026-02-24 Phase C Step 4**：`gm_extra_tools.py` 整文件已删除（409 行，全部 8 个工具 + `ENGINE_TOOL_EXCLUSIONS`）。GM Agent 仅保留 immersive_tools（30 个工具），extra_tools 归零。此项自动解决。

~~**现象**：按骨架"L1 统一经 WorldAPI"目标，`gm_extra_tools.py` 中 8 个闭包仍直接捕获 `session` 并调用 L3 方法，未走 `ctx.api`。~~

---

### L2-5（低 / 延后）：teammate_response_service L1+L2+L3 三层混合

**现象**：`teammate_response_service.py`（1182 行）混合了 L1 LLM 调用（决策/响应）、L2 编排（round 处理）和 L3 状态操作（session 直接访问）。

**已完成**：Phase B 已注入 WorldAPI（`api=tm_api`），immersive tools 路径不再穿透。

**残留**：service 自身仍在多处直接操作 `session`（非通过 immersive tools 的路径），如模型选择、提示词构建、日志等。

**延后原因**：体量大，拆分收益需配合 L1 扩展。WorldAPI 注入后核心穿透已减少。

---

### L2-6（低 / 延后）：AgenticContext.session 字段废弃标记

**现象**：`AgenticContext.session` 字段仍存在且被 Pipeline 内部传递使用。按终态应标记为内部字段，L1 工具不应再直接使用。

**当前状态**：immersive_tools 已全部改为 `ctx.api`；`flash_cpu` 字段已在 Phase C Step 6b 删除；`gm_extra_tools` 已在 Phase C Step 4 整文件删除。`session` 字段仅被 Pipeline 编排内部使用。

**修复方向**：将 `session` 改名为 `_session`（或加 `# internal` 注释），明确其为非公开字段。

---

### L2 残留优先级

| 优先级 | 编号 | 严重级 | 依赖 | 预估 |
|--------|------|--------|------|------|
| 1 | L2-1 | 严重 | 与 T1-T5 同根因，修 T1-T5 即解 | ✅ Phase C Step 1 已完成（admin_coordinator 路径已通）；残留见 Tier 2/4 |
| 2 | L2-2 | 严重 | 与"五、area_runtime 迁移"同根因 | ✅ 已完成（纯清理 + .pyc 删除） |
| 3 | L2-3 | 中 | 无 | ✅ 已修 |
| 4 | L2-4 | 中 | ~~战斗系统重构~~ | ✅ Phase C Step 4：gm_extra_tools.py 整文件已删除 |
| 5 | L2-5 | 低 | L1 扩展规划 | 2-3 天 |
| 6 | L2-6 | 低 | ~~L2-4 完成后~~ 可随时执行 | 0.5 天 |

---

## Phase C 后 Codex 审计发现（2026-02-24）

> 来源：Phase C FlashCPU 溶解完成后，Codex 对代码库进行全量审计。
> 结论：Phase C 本身无回归；以下为 **c3d25ef（三层架构重构）遗留** 的技术债，Phase C 未扩大也未缩小其范围（除 Tier 1 + Tier 3 已修项外）。

### 审计-1（高）：已删 Store 仍被运行时关键路径 module-level import

**现象**：`character_store.py` / `game_session_store.py` / `party_store.py` 源文件已在 c3d25ef 删除，但 `.pyc` 缓存仍存在于 `app/services/__pycache__/`，Python 运行时通过 bytecode 缓存加载。**清缓存或新环境部署 = 立即 ModuleNotFoundError**。

**Phase C 已修路径**（不再是问题）：
- `admin_coordinator.py` — Store import 已删除/lazy 化（Step 1）
- `world_runtime.py` — GameSessionStore import 已删除（Step 1c）
- `narrative_service.py` — GameSessionStore stub 已删除（Step 1c）
- `flash_cpu_service.py` — 整文件已删除（Step 6c）
- `services/__init__.py` — FlashCPU/Store 导出已清理

**仍存在的 module-level 断裂**（全部靠 .pyc 兜底）：

| 文件 | 行号 | import 的已删模块 | Tier |
|------|------|-----------------|------|
| `app/world/player/character.py` | :16 | `CharacterStore` | T6 |
| `app/world/player/ability_check.py` | :6 | `CharacterStore` | T8 |
| `app/mcp/tools/character_tools.py` | :4 | `CharacterStore` | T11 |
| `app/mcp/tools/inventory_tools.py` | :6 | `CharacterStore` | T11 |
| `app/mcp/tools/party_tools.py` | :5 | `PartyStore` | T11 |
| `app/combat/combat_mcp_server.py` | :26 | `GameSessionStore` | T12 |

**影响评估**：
- 当前开发环境：**低**（.pyc 兜底正常运行）
- 新环境 / CI / 部署：**高**（直接崩溃）
- 修复路径：完成 Tier 2（T6-T8）+ Tier 4（T11-T12）

### 审计-2（~~高~~ ✅ 已解决）：area_runtime.py 已删但 SessionRuntime/EventMachine 强依赖

> **2026-02-24 已完成**：全部引用已删除 + .pyc 已删除。纯清理策略。

~~**现象**~~：`area_runtime.py`（547 行）在 c3d25ef 被删除，`.pyc` 缓存仍存在于 `app/runtime/__pycache__/`。

**引用点**（全部 lazy import，但 session_runtime:54 在 `__init__` 内无条件执行）：

| 文件 | 行号 | 触发条件 | 影响 |
|------|------|---------|------|
| `session_runtime.py` | :54 | `__init__` 无条件执行 | 每次创建 SessionRuntime 实例 |
| `session_runtime.py` | :353 | `_restore_area()` | 恢复有位置的会话 |
| `session_runtime.py` | :647 | `enter_area()` | 玩家导航 |
| `event_machine.py` | :265 | `build_narrative_update()` | 活跃事件有完成条件时 |
| `event_machine.py` | :346 | `_try_activate_locked_event()` | 锁定事件激活时 |

**影响评估**：与审计-1 相同 — 当前靠 .pyc 兜底，新环境直接崩。
**修复路径**：完成本文档"五、过渡态 — area_runtime.py"迁移方案。

### 审计-3（中）：测试资产仍绑定已删模块

| 测试文件 | 问题 | 来源 |
|---------|------|------|
| `tests/test_game_session_store.py` | import 已删的 `GameSessionStore` → collection error | c3d25ef 预存 |
| `tests/test_character_store_serialization.py` | import 已删的 `CharacterStore` → collection error | c3d25ef 预存 |
| `tests/test_admin_story_flow.py` | 2 个 async 测试缺 `pytest-asyncio` 标记 → failure | 框架配置问题 |

**处理建议**：前两个测试文件应随 Tier 2/4 迁移后重写或删除；第三个需补 pytest-asyncio 依赖或改为 `asyncio.run()` 风格。

### ~~审计-4（中）：.pyc 缓存依赖风险总览~~ ✅ 全部已清除

> **2026-02-24 全部清除**：4 个 .pyc 文件已删除，对应 import 已全部 stub 化或删除。`app/` 下零 Store 残留 import。

~~**当前 `.pyc` 兜底清单**~~：

```
app/services/__pycache__/
  character_store.cpython-313.pyc    ← ✅ 已删除（2026-02-24）
  game_session_store.cpython-313.pyc ← ✅ 已删除（2026-02-24）
  party_store.cpython-313.pyc        ← ✅ 已删除（2026-02-24）

app/runtime/__pycache__/
  area_runtime.cpython-313.pyc       ← ✅ 已删除（2026-02-24）
```

---

## 剩余工作优先级总览（Phase C 后）

| 优先级 | 范围 | 内容 | 预估 | 阻断级 |
|--------|------|------|------|--------|
| **P0** | area_runtime 迁移 | ~~5 处引用改为 WorldGraph area 节点~~ | ~~2 天~~ | ✅ **已完成**（纯清理，+57 passed） |
| **P0b** | .pyc 炸弹全清 | CharacterStore/PartyStore/GameSessionStore .pyc 删除 + import stub 化 | — | ✅ **已完成**（`app/` 零 Store import 残留） |
| **P1** | Tier 2（T6-T8） | ~~CharacterService / PartyService / AbilityCheck 脱离 Store~~ | ~~2 天~~ | ✅ **已完成**（纯清理：store=None + .pyc 删除） |
| **P2** | Tier 4（T11-T13） | ~~MCP 工具迁移或废弃~~ | ~~1-2 天~~ | ✅ **已完成**（character/inventory/party tools stub 化 + combat_mcp stub 化） |
| **P3** | 测试清理 | 删除 2 个废弃测试文件 + 修复 async 标记 | 0.5 天 | 低 |
| **P4** | 文档同步 | L1 执行计划 / Phase C 计划状态更新 + L1 地图 + 全量文件清单 | 0.5 天 | 低 |

---

## 结构性屎山治理建议（不以功能断裂为前提）

> 2026-02-24 代码体检快照（仅看结构复杂度，不看是否可运行）
>
> - 运行时代码：`139` 个 `.py`，`35,296` 行
> - 代码集中度：Top 10 文件占 `35.3%`；Top 20 占 `55.7%`
> - 超大文件：`>=1000` 行文件 `10` 个
> - 超长函数：`>=80` 行函数 `55` 个，`>=120` 行函数 `16` 个
> - 宽异常：`except Exception` `152` 处（不含 `app/tools`）
> - 路由胖化：`game_v2.py` 单文件 `37` 个端点
> - 循环依赖：`session_runtime ↔ world.graph.builder`（1 个实际循环）

### 为什么“已经按功能拆分”仍然屎山

1. **是物理拆分，不是依赖收口**：目录分开了，但执行主链仍高度集中在少数 God File。
2. **写入口未强制单通道**：状态修改仍可多路径进入（路由/编排/工具/运行时门面）。
3. **编排层承载了过多职责**：上下文、引擎前置、Agent 执行、后处理、持久化都在一个主流程内。
4. **异常治理偏“兜底吞掉”**：大量宽异常让复杂度被隐藏，排障成本高。

### 建议任务包（与 Store 迁移并行推进）

| 包 | 目标 | 主要动作 | 预估 | 收益 |
|----|------|---------|------|------|
| G1 | 拆 God Function | 将 `pipeline_orchestrator.process()` 按 A/B/C 阶段拆为 3-5 个 `Stage` 类；单方法上限 120 行 | 2 天 | 降低单点认知负担，回归可控 |
| G2 | 路由解胖 | 将 `game_v2.py` 按 bounded context 拆为 `session/combat/party/narrative` 路由模块 | 1 天 | API 变更影响面下降 |
| G3 | 依赖单向化 | 打断 `session_runtime ↔ graph.builder` 循环：`builder` 不再 import `SessionRuntime`，改 `Protocol/DTO` 输入 | 1 天 | 减少跨层牵连，避免初始化副作用 |
| G4 | 状态单写口 | 规定 L1/L2 仅能通过 `WorldAPI` 写状态；新增 lint/脚本扫描 `ctx.session.*` 写入 | 1 天 | 从机制上防止“越层直写”回潮 |
| G5 | 异常分级治理 | 在高频路径（router/coordinator/pipeline/llm_service）替换宽异常为领域异常（Validation/Dependency/External/Invariant） | 1-2 天 | 定位精度提升，日志噪声下降 |
| G6 | 复杂度预算闸门 | CI 增加阈值：单文件 >1200 行/单函数 >120 行/新增 `except Exception` 触发告警 | 0.5 天 | 防止屎山二次增长 |

### 推荐执行顺序（结构治理）

1. **先做 G3 + G4**（边界与写入口先收口，不然拆文件很快回潮）
2. **再做 G1 + G2**（拆编排、拆路由，形成稳定模块面）
3. **最后做 G5 + G6**（把质量门槛固化到工程流程）

### 与本文档现有 Tier 的关系

- Tier 2/4 解决的是 **“删除 Store 后的生存问题”**（不崩）
- G1-G6 解决的是 **“可维护性问题”**（不继续长成屎山）
- 两者需要并行推进，否则会出现“能跑但难改”长期状态
