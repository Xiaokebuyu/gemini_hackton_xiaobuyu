# CRPG 图谱架构设计 v2（最终版）

> 日期：2026-02-06
> 状态：已确认，进入实现阶段

## 设计决策

| 项目 | 决策 |
|------|------|
| Firestore 路径 | 混合：章节/区域物理隔离，世界级知识扁平 |
| 叙述事件 | 独立 event MemoryNode，参与扩散激活 |
| 扩散策略 | 角色 + 叙述 + 营地图谱合并后统一扩散，跨视角 x0.5 |
| 好感度存储 | `dispositions/` 子集合存标量+历史，`approves` 边参与扩散 |
| 营地路径 | 顶级 `camp/graph/`，跨章节始终可访问 |
| 选择存储 | `choices/` 集合存完整记录，角色图谱存 choice 节点用于扩散 |
| 因果链 | `caused` 边保底 60% 信号，确保后果不丢失 |
| 兼容方案 | 扩展 MemoryNode/MemoryEdge properties，不替换 |

---

## 1. Firestore 路径结构

```
worlds/{world_id}/
│
├── graphs/world/                              # 世界级本体知识（扁平）
│   ├── nodes/{node_id}                        # faction/deity/race/monster/concept/knowledge/item
│   └── edges/{edge_id}
│
├── chapters/{chapter_id}/                     # 章节（物理隔离）
│   ├── meta                                   # {name, order, status, started_at}
│   ├── areas/{area_id}/                       # 区域
│   │   ├── meta                               # {name, danger_level, atmosphere}
│   │   ├── graph/                             # 区域级叙述图谱
│   │   │   ├── nodes/{node_id}
│   │   │   └── edges/{edge_id}
│   │   └── locations/{location_id}/           # 小地点
│   │       ├── meta                           # {name, description, resident_npcs}
│   │       └── graph/                         # 地点级叙述图谱
│   │           ├── nodes/{node_id}
│   │           └── edges/{edge_id}
│   └── graph/                                 # 章节级叙述图谱（跨区域大事件）
│       ├── nodes/{node_id}
│       └── edges/{edge_id}
│
├── camp/                                      # 营地（跨章节特殊区域）
│   ├── meta                                   # {name, description, unlocked_features}
│   └── graph/                                 # 营地叙述图谱
│       ├── nodes/{node_id}
│       └── edges/{edge_id}
│
├── characters/{character_id}/                 # 角色（跨章节共享，记忆累积）
│   ├── profile                                # 角色资料
│   ├── state                                  # 当前状态
│   ├── nodes/{node_id}                        # 个人视角图谱
│   ├── edges/{edge_id}
│   └── dispositions/                          # 好感度子集合
│       └── {target_id}                        # {approval, trust, fear, romance, history[]}
│
├── choices/                                   # 选择后果追踪
│   └── {choice_id}                            # {description, chapter_id, consequences[], resolved}
│
├── maps/{map_id}/                             # 地图配置（不变）
│   └── ...
│
└── meta/info                                  # 世界元数据（不变）
```

## 2. GraphScope 统一寻址

```python
@dataclass
class GraphScope:
    scope_type: str   # "world" | "chapter" | "area" | "location" | "character" | "camp"
    chapter_id: Optional[str] = None
    area_id: Optional[str] = None
    location_id: Optional[str] = None
    character_id: Optional[str] = None
```

路径映射：

| scope_type | Firestore 路径 |
|-----------|---------------|
| `world` | `worlds/{wid}/graphs/world/` |
| `chapter` | `worlds/{wid}/chapters/{cid}/graph/` |
| `area` | `worlds/{wid}/chapters/{cid}/areas/{aid}/graph/` |
| `location` | `worlds/{wid}/chapters/{cid}/areas/{aid}/locations/{lid}/graph/` |
| `character` | `worlds/{wid}/characters/{char_id}/` |
| `camp` | `worlds/{wid}/camp/graph/` |

## 3. 节点类型

### 结构性节点

| type | 说明 | importance |
|------|------|------------|
| `chapter` | 章节弧 | 0.95 |
| `area` | 地图区域 | 0.8 |
| `location` | 小地点 | 0.6 |
| `character` | 角色 | main=0.95, secondary=0.7, passerby=0.3 |

### 内容性节点

| type | 说明 | importance |
|------|------|------------|
| `event` | 事件记忆（双视角） | combat=0.7, quest=0.8, social=0.4, choice=0.9 |
| `choice` | 关键抉择 | 0.9 |
| `quest` | 个人任务线/忠诚任务 | active=0.8, locked=0.4 |
| `faction` | 组织/势力 | 0.6 |
| `deity` | 神祇 | 0.7 |
| `race` | 种族 | 0.4 |
| `monster` | 怪物类型 | 0.4 |
| `item` | 物品 | 0.3 |
| `concept` | 抽象概念 | 0.3 |
| `knowledge` | 知识/传言 | 0.4 |

### 节点 properties 中的 scope 标记

```python
{
    "scope_type": "location",
    "chapter_id": "chapter_1",
    "area_id": "frontier_region",
    "location_id": "tavern",
    "perspective": "narrative",   # "narrative" | "personal"
    "character_id": null,         # personal 视角时填角色ID
}
```

## 4. 边类型与权重

### 结构性边（世界书预填，不衰减）

| relation | 权重 |
|----------|------|
| `opens_area` | 1.0 |
| `has_location` | 1.0 |
| `connects_to` | 0.8 |
| `hosts_npc` | 0.7 |
| `default_area` | 0.6 |

### 世界观边（世界书预填，不衰减）

| relation | 权重 |
|----------|------|
| `companion_of` | 0.9 |
| `enemy_of` | 0.8 |
| `member_of` | 0.7 |
| `worships` | 0.6 |
| `ally_of` | 0.6 |
| `rules` | 0.7 |
| `native_to` | 0.5 |
| `located_at` | 0.6 |

### 社交关系边（运行时动态）

| relation | 权重规则 |
|----------|----------|
| `approves` | (approval+100)/200 → 0.0-1.0 |
| `trusts` | 0.1-1.0 动态 |
| `respects` | 0.3-0.9 |
| `fears` | 0.5-0.9 |
| `rivals` | 0.6-0.8 |
| `romantic` | 0.0-1.0 |

### 战斗结果边

| relation | 权重 |
|----------|------|
| `fought_in` | 0.8 |
| `defeated` | 0.7 |
| `defeated_by` | 0.6 |
| `protected` | 0.8 |
| `healed` | 0.7 |

### 因果/任务边

| relation | 权重 | 特殊规则 |
|----------|------|----------|
| `caused` / `led_to` | 0.9 | 保底 60% 信号 |
| `resulted_from` | 0.9 | |
| `advances` | 0.8 | |
| `perspective_of` | 1.0 | 链接双视角 event |

### 边 properties 规范

```python
{
    "description": "关系描述",
    "evidence_text": "原文依据",
    "source_entry_uid": 42,
    "confidence": 0.9,
    "created_by": "worldbook",   # worldbook / game_event / player
    "game_day": 3,
    "perspective": "personal",   # personal / narrative
}
```

## 5. 好感度系统（BG3 风格）

### 存储：`characters/{id}/dispositions/{target_id}`

```python
{
    "approval": 0,       # -100 ~ 100
    "trust": 0,          # -100 ~ 100
    "fear": 0,           # 0 ~ 100
    "romance": 0,        # 0 ~ 100
    "last_updated": datetime,
    "history": [
        {"delta": +5, "reason": "protected_villagers", "day": 3}
    ]
}
```

### Approval 阶段

| 阶段 | 范围 | 效果 |
|------|------|------|
| hostile | <= -40 | 可能离队，拒绝配合 |
| disapproval | -39 ~ -20 | 冷淡，拒绝深谈 |
| neutral | -19 ~ +19 | 正常交互 |
| warm | +20 ~ +39 | 解锁个人任务 |
| high | +40 ~ +59 | 特殊对话 |
| exceptional | +60 ~ +79 | 忠诚任务高级阶段 |
| devoted | >= +80 | 特殊结局选项 |

### 与扩散激活耦合

`approves` 边权重 = `(approval+100)/200`
高 approval → 更强传播信号 → 更频繁关联 → 更活跃参与

## 6. 扩散激活适配

### 合并扩散流程

```
1. 加载角色个人图谱
2. 加载当前区域叙述图谱
3. 加载营地图谱（始终）
4. 可选：加载章节级图谱
5. 合并到统一 MemoryGraph
6. 注入好感度动态边权重
7. 运行 spread_activation
```

### 新增衰减参数

```python
class SpreadingActivationConfig(BaseModel):
    # 现有参数...
    perspective_cross_decay: float = 0.5    # 跨视角衰减
    cross_chapter_decay: float = 0.4        # 跨章节衰减
    causal_min_signal: float = 0.6          # 因果链保底信号
    current_chapter_id: Optional[str] = None
```

### 特殊规则

- 跨视角传播 x0.5
- 跨章节传播 x0.4
- 营地节点不受跨章节衰减
- `caused` 边保底 60% 源激活值

## 7. MCP 工具接口

| 工具 | 状态 | 说明 |
|------|------|------|
| `recall_memory` | 扩展 | 新增 chapter_id, area_id, include_narrative, include_camp |
| `query_scoped_graph` | 新增 | 替代 query_graph，支持 scope_type |
| `upsert_scoped_node` | 新增 | 替代 upsert_node，明确层级 |
| `upsert_scoped_edge` | 新增 | 新增边写入 |
| `query_local_subgraph` | 新增 | 局部子图查询 |
| `get_disposition` | 新增 | 查询好感度 |
| `update_disposition` | 新增 | 更新好感度 |
| `record_choice` | 新增 | 记录选择 |
| `get_unresolved_consequences` | 新增 | 查询未解决后果 |
| `query_graph` | deprecated | 转发到 query_scoped_graph |
| `upsert_node` | deprecated | 转发到 upsert_scoped_node |

## 8. 实现阶段

### Phase 1：基础设施
- GraphScope 数据类
- GraphStore 新增 `_get_base_ref_v2()` + 好感度接口 + 选择接口
- MemoryGraph 新增 chapter/area/location/day/participant 索引
- SpreadingActivationConfig 新增 CRPG 参数
- 旧接口保留

### Phase 2：MCP 工具
- 新增 scoped 工具 + disposition 工具 + choice 工具
- 旧工具标记 deprecated

### Phase 3：Admin 层适配
- AdminCoordinator._recall_memory() 改用 v2 接口
- 战斗结果传播 + 事件双视角写入 + 好感度变更

### Phase 4：图谱化管线
- 世界书预填映射规则
- chapters.json 生成
- 营地自动生成
- 角色初始好感度

### Phase 5：数据迁移与清理
- 现有 characters/ 和 graphs/world/ 保持不变
- LocationGraphStore 废弃
- 旧 MCP 工具移除

## 9. 世界书预填映射规则

| 源数据 | 目标节点 type | 边 |
|--------|--------------|-----|
| maps.json 地图 | `area` | `connects_to` |
| maps.json sub_locations | `location` | `has_location` |
| characters.json main/secondary | `character` | `default_area` + `hosts_npc` + 关系映射 |
| characters.json passerby | 不入图谱 | 走 passerby_pool |
| mainlines.json 章节 | `chapter` | `opens_area` |
| world_graph.json faction/deity/race/monster/item/concept | 对应 type | 世界观边 |

### 关系预填映射

```
同伴/队友/战友 → companion_of (0.9)
敌人/仇敌     → enemy_of (0.8)
好友/朋友     → knows (0.7) + trusts (0.6)
崇敬/师傅     → knows (0.8) + trusts (0.8)
其他          → knows (0.5)
```

每条边 `properties.evidence_text` = 原始关系描述，`properties.created_by` = "worldbook"
