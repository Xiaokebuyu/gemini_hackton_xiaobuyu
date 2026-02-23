# WorldGraph 统一记忆系统方案

> 创建时间：2026-02-22
> 状态：✅ **L3+L2+L1 全部完成**（2026-02-22）
> 前置：`L3-存储层审视.md`、三层世界架构（阶段 1-4 完成）
> 核心思路：**废弃 MemoryGraph，让 WorldGraph 成为唯一的图容器**

---

## 0. 实施快照（2026-02-22）

- 执行者：Codex（GPT-5 coding agent，负责本轮实现收口与验收）
- 本轮定位：落实“先底层、后上层”的执行顺序，优先打通 L3 读写主链路

### 0.1 已落地内容（与本方案对齐）

1. L3 统一入口已建立：`SessionRuntime.recall()`、`SessionRuntime.record_memory()`、`SessionRuntime.graphize_messages()`
2. 记忆工具调用已收口到 Session 门面：`immersive_tools` 不再依赖 `recall_orchestrator` / `graph_store`
3. `IntentExecutor` 的 recall 已走 `session.recall(...)`，不再接受旧 orchestrator 注入
4. `MemoryGraphizer` 已改为 WorldGraph-only（GraphStore 合并路径删除）
5. `InstanceManager` 实时图谱化改为显式 `world_graph` 透传，调用链已在 Pipeline/FlashCPU/Teammate 补齐
6. 旧记忆 CLI 入口已退役，避免引用已删除模块导致运行时错误

### 0.2 与三层架构的对应

1. L3：容器/算法/写入目标已收敛到 WorldGraph ✅
2. L2：编排策略与权限语义已解耦，SessionRuntime 记忆门面已落地 ✅
3. L1：遗留清理 + AdminEventService 简化 + FlashService 全删 ✅（2026-02-22 完成，详见 `L1-上层改造执行方案.md` §十五）

### 0.3 验收结论（Codex 实测）

1. 记忆链路专项与相关回归通过（核心用例通过）
2. 全量失败项集中在环境依赖测试（world_id 约束与 MCP 服务可达性），非本方案主链路回归

---

## 一、目标

把记忆系统收编进三层世界架构，消除最后一个游离在架构外的子系统。

**做什么**：
1. WorldGraph 吸收 MemoryGraph 的有用功能（索引、查询、激活算法兼容）
2. 记忆节点直接住在 WorldGraph 里，和结构节点共享生命周期
3. 所有记忆读写改为操作 `session.world_graph`，不再碰 GraphStore
4. 废弃 MemoryGraph、GraphScope、GraphStore 的记忆相关路径

**不做什么**：
1. 不改 WorldGraph 的核心机制（seal/dirty/snapshot）
2. 不改 GraphBuilder 的前 7 步构建流程
3. 不改 SpreadingActivation 的核心传播机制（迭代、收敛、横向抑制）；衰减参数层面新增 `cross_owner_decay`（与现有 `cross_chapter_decay`、`perspective_cross_decay` 同级，属于参数扩展）
4. 不动 MemoryGraphizer 的 LLM 提取逻辑（只改写入目标）
5. 不在本阶段做 GraphScope 6→3 收缩

---

## 二、设计哲学对齐

| 三层架构原则 | 当前记忆系统 | 统一后 |
|-------------|------------|--------|
| D1: 运行时全内存 | 每次 recall 读 Firestore，每次 graphize 写 Firestore | recall 在本地 WorldGraph 上跑激活，graphize 写本地 WorldGraph |
| D4: WorldGraph 是真理源 | MemoryGraph 是独立平行容器 | WorldGraph 是唯一图容器 |
| 底层纯机械零 AI | MemoryGraph 容器本身无 AI（符合） | WorldGraph 容器无 AI（不变） |
| persist 时才碰存储 | 逐条 upsert Firestore | snapshot 统一落盘 |

---

## 三、记忆节点在 WorldGraph 中的位置

### 3.1 新增节点类型

在 `WorldNodeType` 枚举中新增：

```python
# 记忆节点类型（新增）
EVENT_GROUP = "event_group"     # 对话/事件组（MemoryGraphizer 产出的顶层容器）
MEMORY_EVENT = "memory_event"   # 子事件（event_group 下的原子事件）
IMPRESSION = "impression"       # NPC 对其他角色的印象
KNOWLEDGE = "knowledge"         # NPC 获得的知识/技能
RUMOR = "rumor"                 # 听说的传闻
MEMORY = "memory"               # GM 创建的通用记忆节点
```

### 3.2 新增边类型

在 `WorldEdgeType` 枚举中新增：

```python
# 记忆边类型（新增）
HAS_MEMORY = "has_memory"       # NPC/地点 → 记忆节点（归属关系）
OCCURRED_AT = "occurred_at"     # 记忆节点 → 地点（发生地）
PARTICIPATED = "participated"   # 记忆节点 → NPC（参与者）
CAUSED = "caused"               # 记忆 → 记忆（因果链）
FOLLOWED_BY = "followed_by"    # 记忆 → 记忆（时间序列）
MENTIONS = "mentions"           # 记忆 → 任意节点（提及）
KNOWS = "knows"                 # NPC → NPC（认识）
TRUSTS = "trusts"               # NPC → NPC（信任）
```

### 3.3 层级挂载

```
world_root
  ├── chapter_1
  │     └── area_tavern
  │           ├── location_bar
  │           │     ├── npc_elena ──(HAS_MEMORY)──→ event_group_day3_talk
  │           │     │                                   ├── (CONTAINS) → memory_event_1
  │           │     │                                   ├── (OCCURRED_AT) → location_bar
  │           │     │                                   └── (PARTICIPATED) → npc_elena, player
  │           │     └── rumor_bar_gossip ←──(HAS_MEMORY)── location_bar
  │           └── knowledge_area_legend ←──(HAS_MEMORY)── area_tavern
  ├── camp
  │     └── memory_camp_plan ←──(HAS_MEMORY)── camp
  └── npc_elena（也可直接挂在顶层）
        ├── impression_player_kind ←──(HAS_MEMORY)── npc_elena
        └── knowledge_healing_herb ←──(HAS_MEMORY)── npc_elena
```

**scope 隔离 → 图距离隔离**：
- NPC 的个人记忆挂在 NPC 节点下（1 跳可达，高激活）
- 其他 NPC 的记忆需要绕多跳（自然衰减到阈值以下）
- 角色 scope 过滤可保留为激活算法的 owner 衰减参数

---

## 四、WorldGraph 需要新增的能力

### 4.1 新增索引（5 个）

```python
# 在 WorldGraph.__init__ 中新增
self._name_index: Dict[str, Set[str]] = defaultdict(set)          # name.lower() → {node_ids}
self._day_index: Dict[int, Set[str]] = defaultdict(set)            # game_day → {node_ids}
self._participant_index: Dict[str, Set[str]] = defaultdict(set)    # participant_id → {node_ids}
self._owner_index: Dict[str, Set[str]] = defaultdict(set)          # owner_node_id → {node_ids}
self._edge_id_index: Dict[str, Tuple[str, str, str]] = {}         # edge_id → (src, tgt, key)
```

维护时机：`add_node()` / `remove_node()` 中根据 `node.properties` 更新索引。

### 4.2 新增查询方法（7 个）

```python
def find_nodes_by_name(self, name: str) -> List[WorldNode]
def find_nodes_by_day(self, day: int) -> List[WorldNode]
def find_nodes_by_participant(self, participant_id: str) -> List[WorldNode]
def find_memories_of(self, owner_id: str) -> List[WorldNode]     # _owner_index
def in_neighbors(self, node_id: str) -> List[Tuple[str, Dict]]   # 入边邻居
def degree(self, node_id: str) -> int                              # 节点度数
def subgraph(self, node_ids: Iterable[str]) -> "WorldGraph"       # 提取子图
```

### 4.3 索引维护扩展（含去索引闭环）

索引维护必须在 **写入、替换、删除** 三个路径上同时覆盖，否则产生脏索引。

#### `add_node()` — 写入 + 替换

```python
# 替换分支（node.id 已存在）：先去旧索引再加新索引
if existed_before:
    old_node = self.get_node(node.id)
    if old_node:
        self._type_index.get(old_node.type, set()).discard(node.id)  # 现有
        # ---- 新增索引去除 ----
        if old_node.name:
            self._name_index.get(old_node.name.lower(), set()).discard(node.id)
        old_day = old_node.properties.get("day") or old_node.properties.get("game_day")
        if old_day is not None:
            self._day_index.get(int(old_day), set()).discard(node.id)
        for pid in old_node.properties.get("participants", []):
            self._participant_index.get(pid, set()).discard(node.id)
        old_owner = old_node.properties.get("owner")
        if old_owner:
            self._owner_index.get(old_owner, set()).discard(node.id)

# 新增索引写入（新建和替换都走）：
if node.name:
    self._name_index[node.name.lower()].add(node.id)
if "day" in node.properties or "game_day" in node.properties:
    day = node.properties.get("day") or node.properties.get("game_day")
    if day is not None:
        self._day_index[int(day)].add(node.id)
if "participants" in node.properties:
    for pid in node.properties["participants"]:
        self._participant_index[pid].add(node.id)
if "owner" in node.properties:
    self._owner_index[node.properties["owner"]].add(node.id)
```

#### `_deindex_node(nid)` — 删除

现有 `_deindex_node()` 只处理 `_type_index` + `_children` + `_parents` + `_entities_at`。需新增：

```python
old_node = self.get_node(nid)
if old_node:
    if old_node.name:
        self._name_index.get(old_node.name.lower(), set()).discard(nid)
    day = old_node.properties.get("day") or old_node.properties.get("game_day")
    if day is not None:
        self._day_index.get(int(day), set()).discard(nid)
    for pid in old_node.properties.get("participants", []):
        self._participant_index.get(pid, set()).discard(nid)
    owner = old_node.properties.get("owner")
    if owner:
        self._owner_index.get(owner, set()).discard(nid)
```

#### `remove_edge()` / 边删除 — `_edge_id_index` 清理

```python
# 边删除时：
if edge_key in self._edge_id_index:
    del self._edge_id_index[edge_key]
```

`add_edge()` 增加 `_edge_id_index` 维护：

```python
if key:
    self._edge_id_index[key] = (source, target, key)
```

### 4.4 _ENTITY_RELATIONS 扩展

```python
# 原有
_ENTITY_RELATIONS = frozenset({"hosts", "has_event", "has_item"})

# 扩展（让 _entities_at 索引也覆盖记忆节点）
_MEMORY_OWNER_RELATIONS = frozenset({"has_memory"})
```

`HAS_MEMORY` 走 `_owner_index` 而非 `_entities_at`，避免污染现有实体查询。

---

## 五、SpreadingActivation 适配

> **文件迁移**：`app/services/spreading_activation.py` → `app/world/spreading_activation.py`
>
> SA 本质上是纯图算法（和 `event_propagation.py` 同级），不应放在中层 services 里。
> 两者的区别：EventPropagator 是事件沿物理空间传播（BFS，服务于 BehaviorEngine），SA 是记忆语义激活（多轮迭代扩散，服务于 Recall）。算法不同、用途不同，不合并，但都放底层。

### 5.1 算法需要的接口

SpreadingActivation 当前访问 MemoryGraph 的 6 个方法：

| 方法 | WorldGraph 现有 | 需要新增 |
|------|----------------|---------|
| `graph.graph` (NetworkX) | ✅ 已有 | — |
| `graph.degree(node_id)` | ❌ | ✅ 新增 |
| `graph.neighbors(node_id)` → `[(id, edge)]` | ❌ 签名不同 | ✅ 新增适配版 |
| `graph.in_neighbors(node_id)` → `[(id, edge)]` | ❌ | ✅ 新增 |
| `graph.has_node(node_id)` | ✅ 已有 | — |
| `graph.get_node(node_id)` | ✅ 已有 | — |

### 5.2 spreading_activation.py 修改（完整清单）

**import 层**：
- `from app.services.memory_graph import MemoryGraph` → `from app.world.world_graph import WorldGraph`
- `from app.models.graph import MemoryEdge, MemoryNode` → `from app.world.models import WorldNode`（MemoryEdge 不再使用）

**签名层**：
- `spread_activation(graph: MemoryGraph, ...)` → `spread_activation(graph: WorldGraph, ...)`
- `extract_subgraph(graph: MemoryGraph, ...)` → `extract_subgraph(graph: WorldGraph, ...)`
- `find_paths(graph: MemoryGraph, ...)` → `find_paths(graph: WorldGraph, ...)`

**节点属性访问**（关键差异）：
- MemoryGraph 在 NetworkX 节点数据中存 `{"properties": {...}}`（model_dump 打散）
- WorldGraph 在 NetworkX 节点数据中存 `{"_node": WorldNode}` 对象引用
- `_get_node_props()` 需重写：
```python
# 旧：
data = graph.graph.nodes[node_id]
return data.get("properties") or {}
# 新：
node = graph.get_node(node_id)
return node.properties if node else {}
```

**边对象访问**：
- WorldGraph 的 `graph.edges[src, tgt, key]` 返回 `Dict`（`{relation, weight, ...}`），不是 MemoryEdge 对象
- `edge.weight` → `edge.get("weight", 1.0)`
- `edge.relation` → `edge.get("relation", "")`

**`extract_subgraph()` 返回值**：
- 不再构造 `MemoryGraph` + `MemoryNode`
- 改为返回 `Dict[str, float]`（node_id → activation score）+ 调用方通过 `graph.get_node()` 获取节点内容
- 或返回 `List[Tuple[WorldNode, float]]`（节点 + 分数列表）

**placeholder 判断**：
- 旧：`props.get("placeholder", False)`
- 新：`node.properties.get("placeholder", False)` 或 `node.state.get("placeholder", False)`（取决于 placeholder 存储位置）

### 5.3 跨 owner 衰减（新增）

类似现有 `cross_chapter_decay`，新增 `cross_owner_decay`：

```python
# SpreadingActivationConfig 新增
cross_owner_decay: float = 0.3    # 跨不同 owner 的记忆衰减

# _compute_cross_decay 中新增
source_owner = source_props.get("owner")
target_owner = target_props.get("owner")
if source_owner and target_owner and source_owner != target_owner:
    mult *= config.cross_owner_decay
```

---

## 六、写路径迁移

### 6.1 MemoryGraphizer（主要写入方）

**当前**：LLM 提取 → 逐条 `graph_store.upsert_node_v2()` / `upsert_edge_v2()`

**改造后**：LLM 提取 → 直接 `session.world_graph.add_node()` / `add_edge()`

```python
# _merge_to_graph() 核心改动
# 旧：
await self.graph_store.upsert_node_v2(world_id, scope, node, merge=True)
# 新：
wg.add_node(WorldNode(
    id=node.id, type=node.type, name=node.name,
    importance=node.importance,
    properties={**node.properties, "owner": npc_id},
    state={}, behaviors=[]
))
wg.add_edge(npc_id, node.id, "has_memory")
```

**关键变化**：
- MemoryGraphizer 需要接收 `world_graph` 引用（替代 `graph_store`）
- `target_scope` 参数废弃，改为 `owner_node_id`（挂到哪个节点下）
- transcript 存储策略见 §八

### 6.2 immersive_tools.form_impression()

```python
# 旧：
await graph_store.upsert_node_v2(world_id, GraphScope.character(agent_id), node)
# 新：
ctx.session.world_graph.add_node(WorldNode(...))
ctx.session.world_graph.add_edge(agent_id, node.id, "has_memory")
```

### 6.3 immersive_tools.create_memory()

```python
# 旧：
await graph_store.upsert_node_v2(world_id, scope, node)
# 新：
ctx.session.world_graph.add_node(WorldNode(...))
ctx.session.world_graph.add_edge(owner_id, node.id, "has_memory")
```

### 6.4 AdminEventService.ingest_event()

- 事件节点直接加入 WorldGraph
- 参与者边直接加入 WorldGraph
- 不再经过 FlashService 分发到各 character scope

### 6.5 FlashService 记忆写入

- `ingest_event()` / `ingest_nodes()` / `ingest_edges()` 改为操作 WorldGraph
- 如果在会话外调用（CLI/离线），保留 GraphStore 直写作为兜底

---

## 七、读路径迁移

### 7.1 RecallOrchestrator

**当前**：
```
加载 6 个 Firestore scope → MemoryGraph.from_multi_scope() 合图 → 激活
```

**改造后**：
```
直接在 session.world_graph 上跑 spread_activation()
```

**核心变化**：
- `recall()` 方法签名改为接收 `world_graph: WorldGraph`
- 去掉所有 `graph_store.load_graph_v2()` 调用
- 去掉 `MemoryGraph.from_multi_scope()` 合图步骤
- 去掉 `_inject_disposition_edges()` —— disposition 数据保持在 `NPC node.state.dispositions`（现有存储位置不变），激活算法改为在种子初始化阶段读取 `state.dispositions` 调整种子分数（好感度高的 NPC 相关记忆获得更高初始激活）
- `recall_for_role()` 的 scope 过滤改为**激活种子选择**（NPC 从自己出发，GM 从区域出发）

### 7.2 immersive_tools.recall_experience()

- 调 RecallOrchestrator（改造后），传入 `session.world_graph`
- 返回值不变（activated_nodes + subgraph）

### 7.3 IntentExecutor.enter_sublocation

- 调 RecallOrchestrator（改造后）
- 不再单独加载 scope

---

## 八、快照容量策略

### 8.1 问题

Firestore 单文档上限 **1MB**。当前 WorldGraph 快照约 100KB（~500 结构节点）。

记忆节点的 `properties` 可能包含 transcript（完整对话记录），单个 event_group 的 transcript 可达 **10-50KB**。积累 30 次对话就可能超限。

### 8.2 方案：transcript 外置（按 event_group 分片）

```
world_snapshot/current                          ← 主快照（结构 + 记忆摘要，<500KB）
world_snapshot/transcripts/{event_group_id}     ← 每个 event_group 独立存储
```

单文档累计会再次触碰 1MB 限制，因此按 `event_group_id` 分片存储。每个 transcript 文档通常 10-50KB，远在限制以内。

记忆节点的 `properties` 只存：
- `summary`（摘要，<500 字符）
- `day`、`participants`、`owner`、`emotion` 等元数据
- `transcript_ref`（指向 `transcripts/{event_group_id}` 的 key）

完整 transcript 存在独立分片文档中，仅在需要时按需加载（比如 LLM 需要完整上下文时）。

### 8.3 节点数量预估

| 类型 | 单位增长 | 100 轮后预估 |
|------|---------|-------------|
| 结构节点（不变） | 0 | ~500 |
| event_group | 每次图谱化 1 个 | ~30（不是每轮都触发） |
| memory_event | 每次图谱化 2-5 个 | ~100 |
| impression | 每次交互 0-1 个 | ~30 |
| knowledge/rumor | 每次图谱化 0-3 个 | ~50 |
| **总计** | — | **~710 节点** |

每节点平均 500 字节（不含 transcript）→ 快照增量约 **100KB**，加上原有 100KB ≈ **200KB**，远在 1MB 以内。

### 8.4 长期策略

- 监控 snapshot 大小（persist 时 log）
- 超过 500KB 时告警
- 如果真的不够，迁移到自建数据库或 Firestore 子集合

---

## 九、首次会话初始化

### 9.1 问题：GraphBuilder 是同步的

当前 Firestore 里有 `graph_prefill_loader` 预填充的知识数据（world/chapter/area/character scope）。首次会话需要把它们加载进 WorldGraph。

但 `GraphBuilder.build()` 是同步 `@staticmethod`（`graph_builder.py:543`），且在同步流程 `_build_world_graph()` 中被调用（`session_runtime.py:441`）。Firestore 加载是 `async`，**不能放进 GraphBuilder**。

### 9.2 方案：知识加载走 SessionRuntime 异步 restore 通道

**GraphBuilder 不动**（保持同步 Step 1-7），知识图谱加载放在 `_restore_world_graph_snapshot()` 的"无快照"分支中：

```python
# session_runtime.py
async def _restore_world_graph_snapshot(self) -> None:
    """从 Firestore 加载快照并恢复到 world_graph。"""
    if not self.world_graph:
        return
    snapshot_doc = await self._load_snapshot_doc()
    if snapshot_doc:
        # 有快照 → 现有 apply delta 逻辑（不变）
        self.world_graph.restore_snapshot(snapshot_doc)
    else:
        # 无快照（首次会话）→ 异步加载知识图谱
        await self._load_knowledge_into_world_graph()
```

`_load_knowledge_into_world_graph()`：
- 从 Firestore 并行加载各 scope（world/chapter/area/character）的知识图谱数据
- 转换为 WorldNode 添加到 WorldGraph（`add_node` + `add_edge`）
- 用 HAS_MEMORY 边连接到对应的层级节点
- 这些节点在 seal 之后添加，属于 **spawned_nodes**
- 首次 `persist()` 时会作为增量写入快照

**关键区别**：知识节点是 spawned（不是基础节点），因为它们在 seal 后添加。这意味着首次快照会比后续快照大（包含完整知识数据），但后续 restore 从快照恢复，不再触发 Firestore 加载。

### 9.3 后续会话

- `_build_world_graph()` 重建基础图（Step 1-7，同步，不含知识图谱）
- `_restore_world_graph_snapshot()` 从快照恢复增量（知识节点 + 运行时记忆节点 + state 变化）
- 效果：基础结构 + 知识 + 运行时记忆都在一张图里

---

## 十、执行阶段

### Phase 1：WorldGraph 增强（打地基）

**改动文件**：`app/world/world_graph.py`、`app/world/models.py`

| 任务 | 内容 |
|------|------|
| 1.1 | 新增 6 个记忆节点类型到 `WorldNodeType` |
| 1.2 | 新增 8 个记忆边类型到 `WorldEdgeType` |
| 1.3 | 新增 5 个索引 + 维护代码（add_node/remove_node/_deindex_node） |
| 1.4 | 新增 7 个查询方法 |
| 1.5 | 单测：新索引、新查询方法 |

**验收**：新增记忆类型节点可以 add/query/remove，索引正确维护，现有测试不回归。

### Phase 2：SpreadingActivation 适配（含文件迁移）

**改动文件**：`app/services/spreading_activation.py` → **迁移至** `app/world/spreading_activation.py`、`app/models/activation.py`

| 任务 | 内容 |
|------|------|
| 2.1 | import + 签名全部从 `MemoryGraph/MemoryEdge/MemoryNode` 改为 `WorldGraph/WorldNode` |
| 2.2 | `_get_node_props()` 重写：从 `graph.graph.nodes[id]["properties"]` 改为 `graph.get_node(id).properties` |
| 2.3 | 边对象从 MemoryEdge 改为 Dict 访问（`edge.weight` → `edge.get("weight", 1.0)` 等） |
| 2.4 | 新增 `cross_owner_decay` 配置（`SpreadingActivationConfig` + `_compute_cross_decay`） |
| 2.5 | `extract_subgraph()` 返回值适配：不再构造 MemoryGraph，改为返回 `Dict[str, float]` 或 `List[Tuple[WorldNode, float]]` |
| 2.6 | 确保在 WorldGraph 上运行正确（单测） |

**验收**：spreading_activation 可以在 WorldGraph 实例上运行，`MemoryGraph`/`MemoryEdge` 零引用，现有激活测试适配后通过。

### Phase 3：写路径迁移

**改动文件**：`app/services/memory_graphizer.py`、`app/world/immersive_tools.py`、`app/services/admin/event_service.py`

| 任务 | 内容 |
|------|------|
| 3.1 | MemoryGraphizer._merge_to_graph() 改写为 WorldGraph 操作 |
| 3.2 | immersive_tools.form_impression / create_memory 改写 |
| 3.3 | AdminEventService.ingest_event 改写 |
| 3.4 | FlashService 写入路径适配（会话内走 WorldGraph，会话外保留兜底） |
| 3.5 | transcript 外置策略实现 |

**验收**：图谱化产出的节点/边出现在 WorldGraph 中，persist 后 snapshot 包含这些数据，restore 后数据恢复。

### Phase 4：读路径迁移

**改动文件**：`app/services/admin/recall_orchestrator.py`、`app/world/immersive_tools.py`、`app/world/intent_executor.py`

| 任务 | 内容 |
|------|------|
| 4.1 | RecallOrchestrator.recall() 改为在 WorldGraph 上运行激活 |
| 4.2 | RecallOrchestrator.recall_for_role() 改为种子选择策略 |
| 4.3 | 去掉多 scope 加载和合图步骤 |
| 4.4 | 去掉 disposition 边注入，改为种子初始化阶段读 `node.state.dispositions` 调整种子分数 |
| 4.5 | immersive_tools.recall_experience 适配 |
| 4.6 | IntentExecutor.enter_sublocation 适配 |

**验收**：recall 不再触发 Firestore 读取，激活结果与旧路径等价。

### Phase 5：首次会话 + 知识图谱初始化

**改动文件**：`app/runtime/session_runtime.py`

| 任务 | 内容 |
|------|------|
| 5.1 | SessionRuntime 新增 `_load_knowledge_into_world_graph()`：首次会话从 Firestore 异步加载知识图谱到 WorldGraph（走 restore 通道，不动 GraphBuilder） |
| 5.2 | 验证 disposition 数据通过 `node.state.dispositions` 正常参与激活种子分数调整 |
| 5.3 | 快照大小监控（persist 时 log 大小） |
| 5.4 | 端到端测试：新会话 → 记忆 → persist → restore → recall |

**验收**：首次会话能加载预填充知识，后续会话从快照恢复完整状态。

### Phase 6：清理

**删除/废弃**：

| 文件 | 操作 | 状态 |
|------|------|------|
| `app/services/memory_graph.py` | 删除 | ✅ 已删除（M2 完成时） |
| `app/services/graph_store.py` | **删除**（运行时完全移除；离线工具改为直连 Firestore Client） | ✅ 已删除（2026-02-23） |
| `app/models/graph_scope.py` | 保留（离线工具 + EventService 的 world scope 仍用到），标记为初始化/离线专用 | ✅ 保留 |
| `app/services/flash_service.py` | 移除图相关方法（ingest_event/recall） | ✅ 已完成（M2） |
| `app/services/instance_manager.py` | 移除 `_memory_graph` 字段 | ✅ 已完成（M2） |
| `app/runtime/area_runtime.py` | 移除 `area_graph` 字段 | ✅ 已完成（M2） |
| 所有 `from app.services.memory_graph import` | 清理 | ✅ 零引用 |
| `app/dependencies.py` | 删除 `get_graph_store()` | ✅ 已删除 |
| `app/services/__init__.py` | 移除 GraphStore 导出 | ✅ 已清理 |
| `tests/test_graph_store_v2.py` | 删除 | ✅ 已删除 |
| 相关测试 | 适配或重写 | ✅ 40/40 通过 |
| 6 个离线工具 | 改为直连 `firestore.Client`，不再依赖 GraphStore | ✅ 已完成 |

**验收**：运行时代码 `grep "from app.services.graph_store"` → 零结果，`pytest` 40 直接相关测试全通过。

---

## 十一、影响范围汇总（按三层架构分层标注）

### 底层改动（World Engine — `app/world/`）

| 文件 | 改动类型 | 预估行数 | 说明 |
|------|---------|---------|------|
| `app/world/world_graph.py` | **扩展** | +150 | 新索引 + 查询方法 + in_neighbors/degree + 去索引闭环 |
| `app/world/models.py` | **扩展** | +20 | 记忆节点/边类型枚举 |
| `app/world/spreading_activation.py` | **迁移+适配** | ~50 改 | 从 `app/services/` 迁入；接口从 MemoryGraph 适配为 WorldGraph（与 `event_propagation.py` 同级，纯图算法归底层） |
| `app/world/graph_builder.py` | **不变** | 0 | 保持同步 Step 1-7，不新增 Step 8 |
| `app/world/snapshot.py` | **微调** | +10 | transcript 外置支持（可选） |

底层改动集中在 Phase 1+2，是纯机械扩展，零 AI 依赖。

### 中层改动（World API — SessionRuntime 周边）

| 文件 | 改动类型 | 预估行数 | 说明 |
|------|---------|---------|------|
| `app/runtime/session_runtime.py` | **扩展** | +70 | 首次会话知识图谱异步加载（`_load_knowledge_into_world_graph`）+ 快照大小监控 |
| `app/runtime/area_runtime.py` | **瘦身** | ~-15 | 移除 `area_graph: MemoryGraph` 字段 |

中层主要新增在 SessionRuntime 的 restore 通道——首次会话的知识图谱加载。

### 上层改动（Agent 层 — LLM/编排/工具）⚠️ 重点

| 文件 | 改动类型 | 预估行数 | 说明 |
|------|---------|---------|------|
| `app/services/memory_graphizer.py` | **改写写入目标** | ~80 改 | `_merge_to_graph()` 从 GraphStore 改为 WorldGraph；需接收 `world_graph` 引用替代 `graph_store`；`_get_important_nodes()` 改为从 WorldGraph 查询 |
| `app/services/admin/recall_orchestrator.py` | **大幅简化** | ~-100 | 去掉多 scope 加载 + 合图 + disposition 注入；`recall()` 直接在 WorldGraph 上激活；`recall_for_role()` 改为种子选择策略 |
| `app/world/immersive_tools.py` | **改写 3 个工具** | ~30 改 | `recall_experience`：调改造后的 RecallOrchestrator；`form_impression`：从 GraphStore 改为 `ctx.session.world_graph.add_node()`；`create_memory`：同上 |
| ~~`app/services/spreading_activation.py`~~ | **已迁至底层** | — | 见底层改动表（`app/world/spreading_activation.py`） |
| `app/services/admin/event_service.py` | **改写写入目标** | ~30 改 | `ingest_event()` 从 GraphStore 改为 WorldGraph |
| `app/services/flash_service.py` | **瘦身** | ~-80 | 移除 `recall()`/`ingest_event()`/`ingest_nodes()`/`ingest_edges()` 等图操作方法；保留 LLM 相关方法 |
| `app/services/instance_manager.py` | **瘦身** | ~-20 | 移除 `NPCInstance._memory_graph` 懒加载属性；`maybe_graphize_instance()` 改为传 `world_graph` |
| `app/services/session_history.py` | **微调** | ~5 改 | `maybe_graphize()` 调用链适配（MemoryGraphizer 接口变更） |
| `app/services/admin/admin_coordinator.py` | **微调** | ~10 改 | 服务初始化适配：RecallOrchestrator 不再需要 graph_store 运行时依赖 |
| `app/services/admin/pipeline_orchestrator.py` | **微调** | ~15 改 | 传递 `session.world_graph` 给 MemoryGraphizer/RecallOrchestrator；`maybe_graphize_instance()` 调用适配 |
| `app/services/teammate_response_service.py` | **无直接改动** | 0 | 通过 AgenticContext 间接使用 recall，上游适配后自动生效 |
| `app/world/agentic_executor.py` | **无直接改动** | 0 | 工具绑定机制不变，工具实现变了但绑定接口不变 |
| `app/world/gm_extra_tools.py` | **无直接改动** | 0 | 不直接操作记忆系统 |

### 上层改动详细说明

#### MemoryGraphizer（写路径核心）

**当前接口**：
```python
class MemoryGraphizer:
    def __init__(self, graph_store: GraphStore): ...
    async def graphize(self, request, flash_service, npc_profile,
                       existing_nodes, target_scope, mode) -> GraphizeResult: ...
```

**改造后接口**：
```python
class MemoryGraphizer:
    def __init__(self): ...  # 不再需要 graph_store
    async def graphize(self, request, flash_service, npc_profile,
                       world_graph: WorldGraph,   # 新：直接操作 WorldGraph
                       owner_node_id: str,         # 新：替代 target_scope
                       mode) -> GraphizeResult: ...
```

**影响链**：
- `AdminCoordinator.__init__()` → 不再传 graph_store 给 MemoryGraphizer
- `InstanceManager.maybe_graphize_instance()` → 传 world_graph 替代 graph_store
- `PipelineOrchestrator` → 在图谱化触发时传 `session.world_graph`

#### RecallOrchestrator（读路径核心）

**当前接口**：
```python
class RecallOrchestrator:
    def __init__(self, graph_store, get_character_id_set, get_area_chapter_map): ...
    async def recall(self, world_id, character_id, seed_nodes,
                     intent_type, chapter_id, area_id, location_id) -> RecallResponse: ...
    async def recall_for_role(self, role, world_id, character_id,
                               seed_nodes, ...) -> RecallResponse: ...
```

**改造后接口**：
```python
class RecallOrchestrator:
    def __init__(self): ...  # 不再需要 graph_store 和回调
    def recall(self, world_graph: WorldGraph, character_id: str,
               seed_nodes: List[str], intent_type: str = None) -> RecallResponse: ...
    def recall_for_role(self, world_graph: WorldGraph, role: str,
                         character_id: str, seed_nodes: List[str],
                         intent_type: str = None) -> RecallResponse: ...
```

**关键变化**：
- 变为**同步方法**（不再有 Firestore 异步 I/O）
- 不再需要 world_id、chapter_id、area_id、location_id（图距离替代 scope 选择）
- 不再需要 `get_character_id_set` / `get_area_chapter_map` 回调

**影响链**：
- `immersive_tools.recall_experience()` → 传 `ctx.session.world_graph`
- `IntentExecutor.enter_sublocation` → 传 `session.world_graph`
- `AgenticContext` → 可能需要持有 world_graph 引用

#### ImmersiveTools（Agent 工具层）

3 个工具改动：

```python
# recall_experience：改调用方式
# 旧：await recall_orchestrator.recall_for_role(role, world_id, char_id, seeds, ...)
# 新：recall_orchestrator.recall_for_role(ctx.session.world_graph, role, char_id, seeds)

# form_impression：改写入目标
# 旧：await graph_store.upsert_node_v2(world_id, GraphScope.character(agent_id), node)
# 新：ctx.session.world_graph.add_node(node); ctx.session.world_graph.add_edge(...)

# create_memory：改写入目标
# 旧：await graph_store.upsert_node_v2(world_id, scope, node)
# 新：ctx.session.world_graph.add_node(node); ctx.session.world_graph.add_edge(...)
```

#### NPC 双层认知影响

**InstanceManager.NPCInstance** 当前有 `_memory_graph: Optional[MemoryGraph]` 懒加载属性：

```python
@property
def memory_graph(self) -> MemoryGraph:
    if self._memory_graph is None:
        self._memory_graph = MemoryGraph()  # 懒初始化
    return self._memory_graph
```

**改造后**：删除此属性。NPC 的长期记忆直接在 WorldGraph 中（挂在 NPC 节点下），不需要独立的 MemoryGraph 实例。

**图谱化触发链**：
```
当前：ContextWindow 90% → InstanceManager.maybe_graphize_instance()
      → MemoryGraphizer.graphize(graph_store=..., target_scope=character(npc_id))
      → graph_store.upsert_node_v2() × N

改后：ContextWindow 90% → InstanceManager.maybe_graphize_instance()
      → MemoryGraphizer.graphize(world_graph=session.world_graph, owner_node_id=npc_id)
      → world_graph.add_node() × N + world_graph.add_edge() × M
      → persist() 时 snapshot 自动落盘
```

### 不受影响的上层模块

| 文件 | 原因 |
|------|------|
| `app/world/agentic_executor.py` | 只做工具绑定 + LLM 循环，不直接操作图 |
| `app/world/gm_extra_tools.py` | 操作战斗/队伍，不操作记忆 |
| `app/world/role_registry.py` | 工具注册表，不涉及图操作 |
| `app/services/tiered_ai_service.py` | 纯 LLM 分发，无图依赖 |
| `app/runtime/context_assembler.py` | 只读 WorldGraph（`get_node("world_root")`），已兼容 |
| `app/services/context_window.py` | 纯 token 管理，不碰图 |
| `app/prompts/*` | 纯文本模板 |

### 离线工具 / CLI 影响

| 文件 | 影响 | 处理 |
|------|------|------|
| `app/tools/graph_importer.py` | 使用 MemoryGraph + GraphStore | 保留 GraphStore 路径（离线工具不走 WorldGraph 运行时） |
| `app/tools/flash_natural_cli.py` | 使用 MemoryGraph | 适配或保留旧路径 |
| `app/tools/world_initializer/graph_prefill_loader.py` | 使用 GraphScope + GraphStore | 不变（首次会话知识加载从这些数据读取） |
| `app/mcp/tools/party_tools.py` | 使用 GraphScope | 不变（MCP 已标记 deprecated） |

### 生产代码改动汇总

| 层级 | 文件数 | 净行数变化 |
|------|--------|-----------|
| 底层（World Engine） | 4（含 SA 迁入） | +230 |
| 中层（SessionRuntime） | 2 | +55 |
| 上层（Agent 层） | 10 | ~-290 |
| 删除 | 1（memory_graph.py） | -460 |
| **合计** | **17** | **约 -465** |

### 测试改动

| 文件 | 操作 |
|------|------|
| `tests/test_spreading_activation.py` | 适配 WorldGraph |
| `tests/test_crpg_activation.py` | 适配 WorldGraph |
| `tests/test_recall_orchestrator.py` | 重写为 WorldGraph 模式 |
| `tests/test_graph_store_v2.py` | 保留（首次会话知识加载仍用 GraphStore） |
| `tests/test_graph_scope.py` | 保留 |
| 新增 | WorldGraph 记忆功能测试 |

---

## 十二、风险与缓解

| 风险 | 级别 | 缓解 |
|------|------|------|
| 快照膨胀超 1MB | 中 | transcript 外置 + 大小监控 + 必要时迁移存储 |
| 激活算法在大图上变慢 | 低 | 当前 30-150ms（1500 节点），预估 710 节点内无影响 |
| 首次会话加载知识图谱慢 | 低 | 异步并行加载，后续从快照恢复 |
| Phase 迁移期间双写不一致 | 中 | 逐 Phase 推进，每 Phase 完成后可独立运行 |
| 现有 CLI 工具（graph_importer 等）依赖旧路径 | 低 | 保留 GraphStore 用于离线工具，不影响会话内路径 |

---

## 十三、执行顺序与里程碑

```
Phase 1（WorldGraph 增强）
  └── Phase 2（SpreadingActivation 适配）
        ├── Phase 3（写路径迁移）  ← 可与 Phase 4 交替
        └── Phase 4（读路径迁移）  ← 可与 Phase 3 交替
              └── Phase 5（首次会话 + 知识图谱）
                    └── Phase 6（清理）
```

**M1（Phase 1+2 完成）**：WorldGraph 具备记忆能力，激活算法可在其上运行 ✅ 已完成（2026-02-22）
**M2（Phase 3+4 完成）**：会话内记忆读写脱离 MemoryGraph / GraphStore ✅ 已完成（2026-02-22）
**M3（Phase 5+6 完成）**：GraphStore 删除，WorldGraph 是唯一图容器 ✅ 已完成（2026-02-23）

---

## 十四、和旧 L3 方案的区别

| 维度 | 旧 L3 方案 | 本方案 |
|------|-----------|--------|
| 容器数量 | 2 个（WorldGraph + SessionRuntime.memory_graph） | 1 个（WorldGraph） |
| 生命周期 | 两套独立的 restore/persist | 一套 |
| scope 处理 | 保留 6 scope，later 收缩到 3 | scope 变成图层级位置，无需显式收缩 |
| 合图步骤 | 保留 from_multi_scope | 消除 |
| 快照 | 两份独立快照 | 一份 |
| 代码量 | 净增 | 净减 ~300 行 |
| 架构一致性 | 部分（新建平行容器） | 完全（D1 + D4 对齐） |

---

## 十五、修订记录

### Rev 2（2026-02-22）— 阻塞项修复

根据代码核验反馈，修复以下 6 项：

| # | 类型 | 修复内容 | 影响段落 |
|---|------|---------|---------|
| 1 | **阻塞** | Step 8 知识加载从 GraphBuilder（同步）移到 SessionRuntime._restore 异步通道 | §九 重写、Phase 5 更新、底层/中层改动表更新 |
| 2 | **阻塞** | 删除 APPROVES 边类型，disposition 保持在 `node.state.dispositions`，激活种子初始化阶段读 state 调整分数 | §三.2 删 APPROVES、§七.1 重写 disposition 处理、Phase 4.4/5.2 更新 |
| 3 | **阻塞** | SpreadingActivation 适配补全：节点属性访问方式差异（`graph.graph.nodes[id]["properties"]` vs `graph.get_node(id).properties`）、import/签名全面改写 | §五.2 重写为完整清单、Phase 2 任务 6 项 |
| 4 | **高风险** | 索引去索引闭环：`add_node()` 替换分支 + `_deindex_node()` + 边删除全面覆盖 5 个新索引 | §四.3 重写 |
| 5 | **中风险** | Transcript 外置从单文档改为按 `event_group_id` 分片（`transcripts/{event_group_id}`） | §八.2 更新 |
| 6 | **低风险** | 统一"不做什么"措辞：明确"不改核心传播机制"，`cross_owner_decay` 属于参数扩展 | §一 更新 |

### Rev 3（2026-02-22）— 三层方向定调

| # | 类型 | 补充内容 | 影响段落 |
|---|------|---------|---------|
| 1 | **架构对齐** | 三层覆盖度矩阵，确认底层可执行 + 中上层方向定调 | §十六 |
| 2 | **中上层方向** | 中层记忆门面 + 上层工具收口 + 观测指标的目标与边界 | §十七.1-17.4 |
| 3 | **执行策略** | 三层严格顺序推进（底→中→上），子文档体系 | §十七.5-17.6 |

### Rev 7（2026-02-22）— M2 Phase 3+4 完成

| # | 类型 | 内容 | 影响段落 |
|---|------|------|---------|
| 1 | **执行** | Phase 3 写路径迁移：MemoryGraphizer 新增 `_merge_to_world_graph()` + `_get_important_nodes_from_wg()`；immersive_tools form_impression/create_memory 优先 WorldGraph | §六 |
| 2 | **执行** | Phase 4 读路径迁移：新建 `app/world/recall.py` WorldGraphRecallOrchestrator；SA 加 initial_scores 参数；recall_experience 修复返回值加 name+summary | §七 |
| 3 | **架构** | AgenticContext 新增 world_graph 字段；PipelineOrchestrator/TeammateResponseService 条件替换 RecallOrchestrator | §十一 上层改动 |
| 4 | **里程碑** | M2 标记 ✅ 已完成 | §十三 |

### Rev 5（2026-02-22）— 三文档对齐

| # | 类型 | 补充内容 | 影响段落 |
|---|------|---------|---------|
| 1 | **门面闭环** | §17.2 增加 `record_memory()` 写门面确认落地状态，标注权限校验原则 | §17.2 |
| 2 | **事件模型** | §17.3 增加 per-character 记忆模型 + SceneBus 广播事件分发说明（D-L1.2/D-L1.3） | §17.3 |
| 3 | **验收加强** | §17.2/17.3 验收标准新增"工具不直触 world_graph" | §17.2, §17.3 |

### Rev 4（2026-02-22）— SA 归属调整 + 中层遗留盘点

| # | 类型 | 补充内容 | 影响段落 |
|---|------|---------|---------|
| 1 | **架构归属** | SpreadingActivation 从 `app/services/`（中层）迁移到 `app/world/`（底层），与 EventPropagator 同级；两者都是纯图算法，用途不同（事件空间传播 vs 记忆语义激活），不合并 | §五 迁移说明、Phase 2 文件路径、§十一 底层/上层表调整 |
| 2 | **中层核验** | 中层遗留代码盘点：死代码（InstanceManager 孤儿属性、AreaRuntime 死链路）+ 双轨路径（Player 持久化顺序反、GameState 三层回退）+ GraphStore v1 残留 | §十六b 新增 |

---

## 十六、三层覆盖度判断（回答当前问题）

当前这份《WorldGraph 统一记忆系统方案》已经包含三层信息，但**执行重心仍是底层**。

| 层级 | 当前覆盖度 | 现状判断 |
|------|------------|----------|
| 底层（World Engine） | 高 | 已有完整目标模型、接口改造点、阶段拆分与清理路径（Phase 1/2/5/6） |
| 中层（World API / SessionRuntime） | 中 | 已涉及 `SessionRuntime` 和 `IntentExecutor`，但缺少统一“记忆能力门面”定义 |
| 上层（Agents / Pipeline / Tools） | 中偏低 | 已列出受影响文件，但尚未形成独立上层改造目标、契约和验收指标 |

结论：
1. 本文档覆盖三层方向定调，但**可执行重心在底层**（Phase 1-6）。
2. 中层和上层的执行方案以子文档形式，在底层完成后基于代码现状编写。
3. 三层严格**顺序推进**：底层 → 中层 → 上层，不并行。

---

## 十六b、中层遗留代码盘点（2026-02-22 代码核验）

> 虽然中层近期做过重构（三层世界架构阶段 1-4），仍残留了以下遗留代码和双轨路径。

### 死代码（可直接删）

| 位置 | 内容 | 证据 |
|------|------|------|
| `instance_manager.py:48-90` | `NPCInstance._flash_service`、`_memory_graph` 懒加载属性 | 声明了但全代码库无调用方 |
| `instance_manager.py` ContextWindowSnapshot | `to_snapshot()` 定义了但从未被调用 | 孤儿基础设施 |
| `area_runtime.py:92,99-100` | `area_graph` + `_graph_store` 字段 | Pipeline 不消费 area_graph |
| `area_runtime.py:196-213` | `_load_area_graph()` 方法 | 加载了区域级 MemoryGraph 但无人读取 |
| `area_runtime.py:245-250` | `persist_state()` 中 area_graph 持久化块 | 同一条死链路 |

### 双轨 / 过时路径

| 位置 | 内容 | 现状 |
|------|------|------|
| `session_runtime.py:50,65` | `_graph_store` 注入参数 | 自身从未使用，只透传给 AreaRuntime（AreaRuntime 的 area_graph 也是死的） |
| `session_runtime.py:1031-1052` | Player 双轨持久化 | 先 CharacterStore 兜底 → 再 WorldGraph 快照（顺序反了） |
| `session_runtime.py:503-526` | `_fallback_persist_player()` | CharacterStore 直写，快照可靠后可删 |
| `session_runtime.py:259-291` | GameState 三层回退 | StateManager → SessionStore → 空（SessionStore 回退冗余） |
| `session_runtime.py:336-343` | Narrative 从 GameState 回退加载 | NarrativeService 可用时不需要 |

### GraphStore v1 残留

| 位置 | 调用方 |
|------|--------|
| `graph_store.py:241-256` `get_node()` v1 方法 | `reference_resolver.py:33`、`party_service.py:141` |
| `graph_store.py:190-230` disposition 子集合直接访问 | `pipeline_orchestrator.py` 读取，与 WorldGraph node.state.dispositions 双轨 |

### 处理策略

死代码在 Phase 6（清理阶段）统一删除。双轨路径在中层改造时根据具体情况收口或保留作为降级兜底。

---

## 十七、上层改造专项计划（新增）

> 目标：在不破坏现有底层迁移节奏的前提下，让记忆系统在三层中“职责清晰、调用收口、可观测”。

### 17.1 目标与边界

**上层目标（L1）**：
1. Agent 不直接依赖存储细节（GraphStore/GraphScope）。
2. Agent 只通过“世界接口记忆能力”完成 recall / write / summarize。
3. Prompt 中的记忆输入结构稳定（避免每条链路各自拼接）。

**中层目标（L2）**：
1. `SessionRuntime` 对外提供统一记忆能力门面。
2. 统一权限和可见性（gm / npc / teammate / player）。
3. 统一观测：每轮记忆读写次数、激活节点数、耗时、上下文占用。

**边界**：
1. 不新增第二个图容器（仍坚持 WorldGraph 单容器）。
2. 不改变底层激活核心机制（只做接口和流程收口）。

### 17.2 中层（World API）改造清单

> **已落地状态**（2026-02-22 三文档对齐后）：
> - `recall()` → L1-2 落地（SessionRuntime 门面，含角色权限过滤）
> - `record_memory()` → L1-2 落地（SessionRuntime 门面，含角色写入权限校验）
> - `graphize_messages()` → 通过 InstanceManager / SessionHistory 间接调用，未提取独立门面（当前不需要）
> - `build_memory_context()` → 当前由 ContextWindow.build_context_with_injection() 处理，未提取独立门面（当前不需要）

在 `SessionRuntime` 增加统一记忆门面：

```python
# 已确认落地（L1-2）
recall(role, actor_id, seeds, intent_type, limit=10) -> List[Dict]        # 读门面
record_memory(owner_id, memory_type, name, summary, importance, role, **props) -> str  # 写门面（含权限校验）

# 待定（当前由现有组件间接满足，未提取独立门面）
# graphize_messages(owner_id, messages, context) -> GraphizeResult
# build_memory_context(viewer_role, viewer_id, token_budget) -> Dict
```

**权限校验原则**（对齐三层”中层负责权限”）：
- `recall()`: 通过 `recall_for_role()` 按角色过滤可见记忆节点
- `record_memory()`: NPC 只能写 impression，teammate 只能写 impression/memory_event，GM 无限制
- 工具调用资格（哪个 role 能调用哪些 tool）仍由上层 RoleRegistry 控制

建议改动文件：
- `app/runtime/session_runtime.py`
- `app/world/intent_executor.py`
- `app/services/admin/pipeline_orchestrator.py`

验收标准：
1. 上层调用不再出现 `GraphScope`、`graph_store.load_graph_v2()`。
2. 私聊和主流程都走同一套 `SessionRuntime` 记忆入口。
3. 记忆可见性策略由中层统一执行，不在上层分散实现。
4. `form_impression` / `create_memory` 通过 `session.record_memory()` 写入，不直触 `world_graph`。

### 17.3 上层（Agent 层）改造清单

**工具层收口**：
- `app/world/immersive_tools.py`
  - `recall_experience` → 改调 `ctx.session.recall()`
  - `form_impression` / `create_memory` → 改调 `ctx.session.record_memory()`（权限校验在中层完成）
- `app/world/role_registry.py`
  - 工具调用资格不变（RoleRegistry 按 role+traits 映射工具集）
  - 记忆写入权限由 `session.record_memory()` 中层校验，不在上层重复

**编排层收口**：
- `app/services/admin/pipeline_orchestrator.py`
  - AgenticContext 删除 `recall_orchestrator` 字段（L1-3）
  - 统一记录记忆调用 trace（供 SSE 和排障）

**多 Agent 链路收口**：
- `app/services/teammate_response_service.py`
- `app/world/npc_reactor.py`
  - 禁止自行拼 Firestore 记忆查询，统一走 recall 能力。

**事件分发模型**（D-L1.2 + D-L1.3）：
- per-character 记忆：每个角色通过 ContextWindow → MemoryGraphizer 生成自己视角的记忆节点
- SceneBus 成员模型决定”谁知道什么”：在场 → 感知 → 图谱化；不在场 → 无记忆
- AdminEventService 系统事件走 SceneBus 广播（L1-5），不再做 perspective transform

验收标准：
1. `app/world/` 与 `app/services/*agent*` 主链路不再直接访问 GraphStore 记忆读写。
2. 所有 Agent（GM/NPC/队友）记忆输入结构一致（字段名和裁剪规则一致）。
3. 工具调用 trace 能区分”记忆读””记忆写””记忆摘要注入”。
4. `grep -rn “world_graph\.add_node\|world_graph\.add_edge” app/world/immersive_tools.py` → 零结果。

### 17.4 观测与治理（新增硬指标）

每轮采集：
1. recall 调用次数与平均耗时
2. 激活节点数（top-k）与最终注入 token 数
3. 写入记忆节点/边数量
4. 快照大小增长量（world_snapshot 增量）

门禁建议：
1. 若记忆上下文超过预算，优先裁剪低激活节点再降级到摘要。
2. 若 recall 超时，允许 fail-open，但必须上报 `memory_degraded=true`。

### 17.5 顺序推进策略

三层改造**严格顺序执行**，不并行。每层完成后产出子文档指导下一层。

```
阶段 1: 底层（Phase 1-6）
  → 交付：WorldGraph 统一容器 + MemoryGraph 删除
  → 产出子文档：L2-中层改造执行方案.md

阶段 2: 中层（17.2）
  → 交付：SessionRuntime 记忆门面 + 调用收口
  → 产出子文档：L1-上层改造执行方案.md

阶段 3: 上层（17.3 + 17.4）
  → 交付：Agent 工具收口 + 观测指标 + 三层闭环
```

**理由**：底层接口未稳定前设计中层门面会返工；中层门面未定型前上层工具改动无法收口。

### 17.6 子文档体系

| 文档 | 产出时机 | 内容 |
|------|---------|------|
| 本文档 | 当前 | 总方案 + 方向定调 |
| `L3-底层执行方案.md` | Phase 1 开始前 | 底层 Phase 1-6 的可执行细节（即本文档 §四-§十的细化） |
| `L2-中层改造执行方案.md` | Phase 6 完成后 | SessionRuntime 门面接口设计 + 迁移步骤 |
| `L1-上层改造执行方案.md` | L2 完成后 | Agent 工具收口 + 编排层改造 + 观测接入 |

每份子文档在上一阶段完成时基于代码现状重新核验后编写，避免基于过时假设推进。

### Rev 8（2026-02-23）— M3 Phase 5+6 完成，GraphStore 完全删除

| # | 类型 | 内容 |
|---|------|------|
| 1 | **执行** | `app/services/graph_store.py` 完全删除（335 行） |
| 2 | **执行** | AdminCoordinator：`list_worlds()` 直连 Firestore；`_get_world_background()` / `_get_character_roster()` 改读 WorldInstance 内存缓存 |
| 3 | **执行** | PartyService：`_ensure_character_graph()` 改为 no-op（WorldGraph 已由 GraphBuilder 负责节点创建） |
| 4 | **执行** | EventService：内联 Firestore 直写（`_get_scope_refs()` 辅助），移除 GraphStore 依赖 |
| 5 | **执行** | SessionRuntime：`_load_knowledge_into_world_graph()` 内联 Firestore 读取逻辑，删除 `_graph_store` 字段 |
| 6 | **执行** | 6 个离线工具（graph_importer / graph_indexer / gm_natural_cli / graph_prefill_loader / character_loader / party_tools）改为直连 `firestore.Client` |
| 7 | **里程碑** | M3 标记 ✅ 已完成（2026-02-23） |

### Rev 6（2026-02-22）— M1 执行完成

M1（Phase 1+2）已落地，里程碑标记 ✅：

- **Phase 1（WorldGraph 增强）**：WorldNodeType +6、WorldEdgeType +8、5 新索引（含闭环维护）、7 新查询方法、修复 day=0 falsy bug
- **Phase 2（SA 适配）**：新建 `app/world/spreading_activation.py`（WorldGraph 版），旧文件不动；cross_owner_decay 已加入 SpreadingActivationConfig
- 测试：79 新 + 841 回归全通过
