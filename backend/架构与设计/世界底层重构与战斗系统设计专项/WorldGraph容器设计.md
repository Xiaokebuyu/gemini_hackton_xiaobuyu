# WorldGraph 容器设计

> 日期: 2026-02-15
> 分支: buyu的异世界冒险
> 父文档: `世界活图详细设计.md`
> 前置: `世界图数据模型设计（已实施）.md` (Step C1)
> 定位: Step C2 (`world_graph.py`) 的完整规格
> 状态: 设计完成，待实施

---

## 一、设计决策总表

| # | 决策 | 结论 | 讨论记录 |
|---|------|------|---------|
| G1 | 图类型 | **`nx.MultiDiGraph()`** — 支持两节点间多条边（如 NPC 多维关系），与 MemoryGraph 一致 | DiGraph 在 RELATES_TO 多关系场景受限 |
| G2 | 多父索引 | **`_parents: Dict[str, Set[str]]`** — 多父时选 active chapter 分支 | 设计文档决策 A3/A6，region 可被多 chapter 共享 |
| G3 | 节点存储 | **存 WorldNode 对象引用** — NetworkX 节点属性仅存 `_node` 引用 | 复用 C1 的 7 个方法，避免重复实现 |
| G4 | CONNECTS 双向 | **默认自动加反向边** — `one_way=True` 时只加单向 | 简化查询，get_neighbors 只需查 successors |
| G5 | get_node 返回值 | **`Optional[WorldNode]`** — 而非 Dict | 保留类型信息和方法调用能力 |
| G6 | 快照范围 | **C2 只做基础 state 快照** — spawned/removed/modified_edges 留给 C4/C6 | 增量实施，避免过早复杂化 |
| G7 | 查询策略 | **自建索引查询** — 不依赖 NetworkX 原生属性过滤 | O(1) 查表 vs O(n) 全扫描 |
| G8 | 底层图访问 | **禁止外部直接操作 `self.graph`** — 提供 `query_raw()` 逃生通道 | 保证索引一致性 |
| G9 | 错误策略 | **读操作返回 None，写操作 raise KeyError** | fail-fast，不静默吞错 |

---

## 二、与 MemoryGraph 的对比

WorldGraph 和 MemoryGraph 是**完全不同的图**，服务于不同目的：

| | MemoryGraph | WorldGraph |
|---|---|---|
| **用途** | 主观记忆网络（NPC 个人视角） | 客观世界结构（全局真理源） |
| **底层** | `nx.MultiDiGraph()` | `nx.MultiDiGraph()` |
| **节点类型** | MemoryNode（model_dump 打散存储） | WorldNode 对象引用（`_node` 属性） |
| **边** | MemoryEdge（edge.id 作为 key） | Dict 属性（relation 类型作为 key） |
| **索引** | 9 个（type/name/chapter/area/location/day/participant/edge/perspective） | 4 个（type/children/parents/entities_at） |
| **查询依赖** | 混合（索引 + NetworkX 原生） | 纯自建索引（不依赖 NetworkX 属性过滤） |
| **持久化** | GraphStore (Firestore) | WorldSnapshot (Firestore，C6 实现) |
| **生命周期** | 按需加载/卸载（AreaRuntime 管理） | 全量内存加载（会话级） |
| **可变性** | 主要追加（记忆只增不减） | 频繁状态变更（state 每回合可能变） |

**关键区别：节点存储方式**

```python
# MemoryGraph: 打散为 dict 属性 → 取出时需重建 MemoryNode 对象
self.graph.add_node(node.id, **node.model_dump())
node = MemoryNode(**dict(self.graph.nodes[node_id]))  # 每次 get 都重建

# WorldGraph: 存对象引用 → 取出即用，C1 方法直接可调用
self.graph.add_node(node.id, _node=node)
node = self.graph.nodes[node_id]["_node"]  # 零开销，原始对象
node.get_active_behaviors(TriggerType.ON_TICK)  # ✅ 直接可用
node.tick_cooldowns()                            # ✅ 直接可用
```

**为什么不统一？** MemoryGraph 是已有系统，改动成本高且无收益。WorldGraph 是新建系统，直接选择更优方案。两个图独立运作，无互操作需求。

---

## 三、索引设计

### 3.1 四套索引

```python
# 按类型查找 — "找出所有 NPC 节点"
_type_index: Dict[str, Set[str]] = defaultdict(set)
# {"npc": {"npc_guild_girl", "npc_barkeeper"}, "area": {"frontier_town"}, ...}

# 子节点 — "这个 area 下有哪些 location？"
_children: Dict[str, Set[str]] = defaultdict(set)
# {"frontier_town": {"guild_hall", "tavern"}, "chapter_1": {"region_border"}, ...}

# 父节点 — "这个 location 属于哪个 area？"（支持多父）
_parents: Dict[str, Set[str]] = defaultdict(set)
# {"guild_hall": {"frontier_town"}, "region_border": {"chapter_1", "chapter_2"}}

# 地点实体 — "guild_hall 里有哪些 NPC/物品/事件？"
_entities_at: Dict[str, Set[str]] = defaultdict(set)
# {"guild_hall": {"npc_guild_girl", "npc_goblin_slayer"}}
```

### 3.2 索引维护规则

**所有图操作必须通过 WorldGraph 的公开方法**，禁止直接操作 `self.graph`。

```python
# ✅ 通过公开方法 — 索引自动同步
world_graph.add_node(node)
world_graph.remove_node(node_id)
world_graph.add_edge(source, target, relation, **attrs)

# ❌ 禁止 — 会导致索引与实际数据不一致
world_graph.graph.add_node(...)       # 跳过了索引更新
world_graph.graph.remove_node(...)    # 跳过了级联删除
```

### 3.3 索引由哪些边驱动

| 边类型 | 驱动的索引 |
|--------|-----------|
| CONTAINS | `_children` + `_parents` |
| HOSTS | `_entities_at` |
| HAS_EVENT | `_entities_at` |
| HAS_ITEM | `_entities_at` |
| CONNECTS / RELATES_TO / GATE / MEMBER_OF | 不驱动索引（通过图遍历查询） |

### 3.4 查询策略说明

WorldGraph 不使用 NetworkX 原生的属性过滤查询（如 `[n for n, d in graph.nodes(data=True) if d["type"] == "npc"]`）。原因：

1. **性能**: 原生查询每次都全扫描所有节点 O(n)，自建索引查表 O(1)
2. **一致性**: 节点数据存为对象引用 (`_node`)，NetworkX 看不到内部字段
3. **实际场景**: 游戏运行中增删节点**少**（偶尔 SPAWN/REMOVE），查询**频繁**（每回合 tick），索引的维护成本远低于查询收益

提供 `query_raw()` 作为逃生通道，用于索引未覆盖的特殊查询：

```python
def query_raw(self, predicate: Callable[[WorldNode], bool]) -> List[WorldNode]:
    """原生全扫描查询（慎用）。

    当自建索引无法满足查询需求时使用。
    注意: 这是 O(n) 操作，n 为图中节点总数。
    """
    return [
        data["_node"] for _, data in self.graph.nodes(data=True)
        if predicate(data["_node"])
    ]
```

---

## 四、WorldGraph 完整 API

### 4.1 类定义与初始化

```python
class WorldGraph:
    """三维世界图容器。

    职责:
      - 全量内存加载的世界图结构
      - 节点/边 CRUD + 自动索引维护
      - Z 轴层级查询（祖先链、后代、作用域链）
      - X/Y 平面查询（邻居、连接区域、地点实体）
      - 状态修改 + 脏标记（dirty tracking）
      - 事件日志 + 基础快照

    不负责:
      - 构建（C3 GraphBuilder）
      - 行为评估（C4 BehaviorEngine）
      - 事件传播（C5 EventPropagator）
      - 完整快照持久化（C6 Snapshot）

    ⚠️ 重要: 禁止在本类之外直接操作 self.graph。
    所有图操作必须通过本类的公开方法，以保证索引一致性。
    """

    def __init__(self):
        self.graph: nx.MultiDiGraph = nx.MultiDiGraph()

        # ===== 索引 =====
        self._type_index: Dict[str, Set[str]] = defaultdict(set)
        self._children: Dict[str, Set[str]] = defaultdict(set)
        self._parents: Dict[str, Set[str]] = defaultdict(set)
        self._entities_at: Dict[str, Set[str]] = defaultdict(set)

        # ===== 脏标记 =====
        self._dirty_nodes: Set[str] = set()
        self._dirty_edges: Set[Tuple[str, str]] = set()

        # ===== 事件日志 =====
        self._event_log: List[WorldEvent] = []
```

### 4.2 节点 CRUD

```python
def add_node(self, node: WorldNode) -> None:
    """添加节点到图中，更新类型索引。

    如果节点已存在，替换数据并重建索引。
    不处理边关系 — 边需要单独通过 add_edge() 添加。
    """

def remove_node(self, node_id: str) -> None:
    """移除节点及其所有后代子节点和关联边。

    级联删除: 通过 _children 索引递归删除所有 CONTAINS 子节点。
    同时清理相关索引项。
    Raises KeyError if node_id not found.
    """

def get_node(self, node_id: str) -> Optional[WorldNode]:
    """获取节点对象。不存在返回 None。"""

def has_node(self, node_id: str) -> bool:
    """检查节点是否存在。"""

def get_node_state(self, node_id: str) -> Dict[str, Any]:
    """只获取节点的可变 state dict。

    Raises KeyError if node_id not found.
    """

def node_count(self) -> int:
    """返回图中节点总数。"""
```

### 4.3 状态修改（自动标记 dirty）

```python
def set_state(self, node_id: str, key: str, value: Any) -> None:
    """修改节点的单个 state 字段。

    委托给 WorldNode.set_state()（自动更新 updated_at）。
    标记节点为 dirty。
    Raises KeyError if node_id not found.
    """

def merge_state(self, node_id: str, updates: Dict[str, Any]) -> None:
    """批量合并 state 字段。

    委托给 WorldNode.merge_state()。
    标记节点为 dirty。
    """
```

### 4.4 查询 — Z 轴（层级）

```python
def get_children(self, node_id: str, type_filter: str = None) -> List[str]:
    """获取 CONTAINS 子节点 ID 列表，可按 type 过滤。

    使用 _children 索引，O(1) + 过滤。
    """

def get_parent(self, node_id: str) -> Optional[str]:
    """获取父节点 ID。

    大多数节点只有一个父，直接返回。
    多父时（region 被多 chapter 共享），选 state.status=="active" 的 chapter 路径。
    """

def get_all_parents(self, node_id: str) -> Set[str]:
    """获取所有 CONTAINS 父节点 ID（多父场景）。"""

def get_ancestors(self, node_id: str) -> List[str]:
    """获取从当前节点到 world_root 的完整祖先链。

    多父分支时选 active chapter 路径（决策 A6）。
    返回: [parent, grandparent, ..., world_root]
    """

def get_descendants(self, node_id: str, type_filter: str = None) -> List[str]:
    """获取所有 CONTAINS 后代节点（递归 BFS）。

    可按 type 过滤。
    用途: BehaviorEngine.tick() 确定活跃范围。
    """

def get_scope_chain(self, location_id: str) -> List[str]:
    """获取玩家当前位置的完整作用域链。

    返回: [location_id, area_id, region_id, chapter_id, world_root]
    等价于 [location_id] + get_ancestors(location_id)
    """
```

### 4.5 查询 — X/Y 平面（水平关系）

```python
def get_neighbors(self, node_id: str, relation: str = None) -> List[Tuple[str, Dict]]:
    """获取邻居节点 ID + 边数据。

    可按 relation 过滤。
    由于 CONNECTS 已自动加反向边，只查 successors 即可。
    返回: [(neighbor_id, edge_data), ...]
    """

def get_connected_areas(self, area_id: str) -> List[str]:
    """获取与指定区域相连的所有区域 ID。

    便捷方法 = get_neighbors(area_id, relation="connects") 的 ID 列表。
    """

def get_entities_at(self, location_id: str) -> List[str]:
    """获取某地点的所有实体 ID（NPC、物品、事件）。

    使用 _entities_at 索引，O(1)。
    """
```

### 4.6 查询 — 按类型

```python
def get_by_type(self, node_type: str) -> List[str]:
    """按类型获取所有节点 ID。

    使用 _type_index 索引，O(1)。
    """

def find_events_in_scope(self, scope_node_id: str) -> List[str]:
    """获取指定节点及其后代中的所有 event_def 节点。

    用途: BehaviorEngine 确定当前作用域下的活跃事件。
    实现: get_descendants(scope_node_id, type_filter="event_def")
    """
```

### 4.7 查询 — 原生逃生通道

```python
def query_raw(self, predicate: Callable[[WorldNode], bool]) -> List[WorldNode]:
    """原生全扫描查询 — 当自建索引无法满足时使用。

    ⚠️ O(n) 操作，慎用。仅在索引不覆盖的特殊查询时使用。
    """
```

### 4.8 边操作

```python
def add_edge(self, source: str, target: str, relation: str,
             key: str = None, **attrs) -> None:
    """添加边。

    - CONNECTS 类型默认自动加反向边（除非 one_way=True）
    - CONTAINS 类型自动更新 _children + _parents 索引
    - HOSTS/HAS_EVENT/HAS_ITEM 类型自动更新 _entities_at 索引
    - key 参数用于 MultiDiGraph 的边标识（不指定则自动生成）
    - 标记边为 dirty

    Raises KeyError if source or target node not found.
    """

def get_edge(self, source: str, target: str, key: str = None) -> Optional[Dict]:
    """获取边数据。

    MultiDiGraph: 如不指定 key，返回第一条匹配的边。
    """

def get_edges_between(self, source: str, target: str) -> List[Dict]:
    """获取两节点间所有边（MultiDiGraph 可能有多条）。"""

def update_edge(self, source: str, target: str, key: str,
                updates: Dict) -> None:
    """更新边属性。标记边为 dirty。"""

def remove_edge(self, source: str, target: str, key: str = None) -> None:
    """移除边。同步清理相关索引。"""
```

### 4.9 事件日志

```python
def log_event(self, event: WorldEvent) -> None:
    """记录事件到本回合日志。"""

def flush_event_log(self) -> List[WorldEvent]:
    """取出并清空本回合事件日志。"""
```

### 4.10 快照（C2 基础版）

```python
def snapshot(self) -> Dict[str, Dict[str, Any]]:
    """生成增量快照 — 仅 dirty 节点的 state。

    返回: {node_id: state_dict}

    C2 基础版仅包含 node_states。
    后续 C4/C6 扩展: spawned_nodes, removed_node_ids, modified_edges,
    behavior_states (见 WorldSnapshot 模型)。
    """

def snapshot_full(self) -> Dict[str, Dict[str, Any]]:
    """生成完整快照 — 所有节点的 state。

    用途: 首次保存、调试。
    """

def restore_snapshot(self, node_states: Dict[str, Dict[str, Any]]) -> None:
    """从快照恢复节点 state。

    遍历 node_states，将 state 覆盖到对应节点。
    不存在的 node_id 静默跳过（可能是被删除的节点）。
    """

def clear_dirty(self) -> None:
    """清除脏标记（快照保存后调用）。"""
```

### 4.11 统计与调试

```python
def stats(self) -> Dict[str, Any]:
    """返回图的统计信息。

    返回: {node_count, edge_count, dirty_count, type_distribution, event_log_size}
    """
```

---

## 五、多父解析算法

### 5.1 问题

```
chapter_1 (status=completed) ─[CONTAINS]→ region_border
chapter_2 (status=active)    ─[CONTAINS]→ region_border
```

`region_border` 有两个父节点。`get_parent()` / `get_ancestors()` 需要选择哪条路径。

### 5.2 解析规则

```python
def _resolve_active_parent(self, node_id: str, parents: Set[str]) -> Optional[str]:
    """多父时选择 active chapter 路径上的父节点。

    策略:
    1. 检查每个 parent 是否为 chapter 类型
    2. 如果是 chapter: 选 state.status == "active" 的
    3. 如果都不是 chapter（即多个同级别节点指向同一子节点）:
       递归向上查找，直到找到 active chapter 分支
    4. 都找不到: 返回第一个（fallback）
    """
```

### 5.3 get_ancestors 多父处理

```python
def get_ancestors(self, node_id: str) -> List[str]:
    """
    示例: guild_hall → frontier_town → region_border → ???

    region_border 有两个父: chapter_1(completed), chapter_2(active)
    选 chapter_2 → world_root

    返回: [frontier_town, region_border, chapter_2, world_root]
    """
    result = []
    current = node_id
    visited = {node_id}  # 防环
    while True:
        parent = self.get_parent(current)
        if parent is None or parent in visited:
            break
        result.append(parent)
        visited.add(parent)
        current = parent
    return result
```

---

## 六、CONNECTS 双向边处理

### 6.1 自动反向规则

```python
def add_edge(self, source, target, relation, key=None, **attrs):
    # ... 添加正向边 ...

    # CONNECTS 默认双向
    if relation == WorldEdgeType.CONNECTS and not attrs.get("one_way"):
        # 添加反向边（共享相同属性）
        reverse_key = f"{key}_reverse" if key else None
        self.graph.add_edge(target, source, key=reverse_key,
                           relation=relation, **attrs)
```

### 6.2 one_way 使用场景

```python
# 地牢单向通道（跳下悬崖，不可返回）
world_graph.add_edge("cliff_top", "cliff_bottom",
                     relation="connects", one_way=True,
                     description="可以跳下去，但爬不回来")

# 普通双向通道（默认行为）
world_graph.add_edge("guild_hall", "tavern", relation="connects")
# 自动生成: tavern → guild_hall 反向边
```

### 6.3 边 Key 命名规范

MultiDiGraph 要求每条边有 key。规范：

| 边类型 | Key 规则 | 示例 |
|--------|---------|------|
| CONTAINS | `contains` | `(world_root, chapter_1, "contains")` |
| CONNECTS | `connects` / `connects_reverse` | `(town, cave, "connects")` |
| HOSTS | `hosts_{role}` | `(guild_hall, npc_girl, "hosts_resident")` |
| HAS_EVENT | `has_event` | `(town, evt_quest, "has_event")` |
| RELATES_TO | `{relationship_type}` | `(npc_a, npc_b, "mentor")` |
| GATE | `gate` | `(ch1, ch2, "gate")` |
| MEMBER_OF | `member_of` | `(npc, camp, "member_of")` |

RELATES_TO 是唯一可能在同一对节点间有**多条边**的类型，用 `relationship_type` 作为 key 来区分（如 `"mentor"`, `"rival"`, `"romantic"`）。

---

## 七、remove_node 级联删除

```python
def remove_node(self, node_id: str) -> None:
    """
    删除节点时级联删除所有 CONTAINS 后代：

    remove_node("frontier_town") 会同时删除:
      ├── guild_hall
      │   ├── npc_guild_girl
      │   └── npc_goblin_slayer
      ├── tavern
      │   └── npc_barkeeper
      └── evt_goblin_quest

    步骤:
    1. BFS 收集所有后代 ID（通过 _children 索引）
    2. 反序删除（先叶子后父节点）
    3. 每个节点: 清理所有索引项 + 删除关联边
    4. 最后删除目标节点自身
    """
```

---

## 八、快照 C2 基础版 vs C6 完整版

### 8.1 C2 基础版（本步骤实施）

```python
# snapshot() 返回格式:
{
    "node_id_1": {"mood": "angry", "hp": 15, ...},    # 只有 dirty 节点
    "node_id_2": {"status": "completed", ...},
}

# snapshot_full() 返回格式:
{
    "node_id_1": {...},
    "node_id_2": {...},
    ...                # 所有节点的 state
}

# restore_snapshot() 输入格式: 同上
```

### 8.2 C6 完整版（未来扩展）

```python
class WorldSnapshot(BaseModel):
    world_id: str
    session_id: str
    created_at: datetime
    game_day: int
    game_hour: int

    node_states: Dict[str, Dict[str, Any]]              # ← C2 已覆盖
    behavior_states: Dict[str, Dict[str, Dict]]          # ← C4 扩展
    spawned_nodes: List[Dict[str, Any]]                  # ← C4 扩展
    removed_node_ids: List[str]                          # ← C4 扩展
    modified_edges: List[Dict[str, Any]]                 # ← C4 扩展
```

C2 的 `snapshot()` / `restore_snapshot()` 接口设计兼容 C6 扩展，后续只需增加字段，不需要改接口签名。

---

## 九、实施清单

### 9.1 文件

```
app/world/world_graph.py    # 新建，约 350-450 行
app/world/__init__.py       # 更新，添加 WorldGraph 导出
```

### 9.2 实施顺序

```
1. 类定义 + __init__（索引 + 脏标记 + 事件日志）
2. add_node / remove_node（含级联删除 + 索引维护）
3. add_edge（含 CONNECTS 自动反向 + 索引驱动）
4. get_node / has_node / get_node_state / node_count
5. set_state / merge_state（含 dirty 标记）
6. Z 轴查询: get_children / get_parent / get_all_parents / get_ancestors / get_descendants / get_scope_chain
7. X/Y 查询: get_neighbors / get_connected_areas / get_entities_at
8. 类型查询: get_by_type / find_events_in_scope
9. query_raw（逃生通道）
10. 边辅助: get_edge / get_edges_between / update_edge / remove_edge
11. 事件日志: log_event / flush_event_log
12. 快照: snapshot / snapshot_full / restore_snapshot / clear_dirty
13. stats（调试用）
14. 单元测试验证
```

### 9.3 依赖

```python
# 标准库
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# 第三方
import networkx as nx

# 项目内
from app.world.models import (
    WorldNode, WorldNodeType, WorldEdgeType,
    WorldEvent, TriggerType
)
```

无新增第三方依赖（NetworkX 已在 requirements.txt 中）。

---

## 十、与父文档第四章的差异

本文档相对于 `世界活图详细设计.md` 第四章的变更：

| 父文档原设计 | 本文档更新 |
|-------------|-----------|
| `nx.DiGraph()` | → `nx.MultiDiGraph()`（支持多边关系） |
| `_parent: Dict[str, str]` | → `_parents: Dict[str, Set[str]]`（支持多父） |
| `get_node() -> Dict` | → `get_node() -> Optional[WorldNode]`（保留类型信息） |
| 节点 `model_dump()` 打散存储 | → 存 WorldNode 对象引用（复用 C1 方法） |
| 无双向边处理 | → CONNECTS 自动加反向边 |
| 无查询策略说明 | → 明确自建索引 + `query_raw()` 逃生通道 |
| `self.engine: BehaviorEngine` | → C2 不包含 engine（C4 挂载） |
| 快照返回 `Dict[str, Any]` | → 明确 C2 基础版 vs C6 完整版 |

---

> **下一步**: 根据本文档实施 `app/world/world_graph.py`，然后进入 C3 (GraphBuilder)。
