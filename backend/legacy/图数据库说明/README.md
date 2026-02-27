# Phase 1：图谱基础设施说明

本目录用于说明当前“图谱数据库”基础设施的设计与落地范围。这里的“图数据库”并非专用图数据库产品，而是 **NetworkX 内存图结构 + Firestore 持久化** 的组合。

## 已完成内容（Phase 1 / 1.5）

### 1) 内存图结构（MemoryGraph）
- 节点/边 CRUD
- 邻居、度数、子图
- 简易索引：按 `type` / `name` 查询
- 局部扩展：`expand_nodes(seeds, depth, direction)`

实现文件：
- `app/services/memory_graph.py`

### 2) 激活扩散算法（Spreading Activation）
- 按配置参数传播激活
- 返回激活节点 + 子图抽取
- 路径查找 `find_paths`
- 可选侧向抑制（`lateral_inhibition` + `inhibition_factor`）

实现文件：
- `app/services/spreading_activation.py`

### 3) Firestore 持久化（GraphStore）
- 整图加载/保存
- 单节点/单边读写
- 局部子图“快速模式”加载（不全量读图）
- 索引写入/查询/重建

实现文件：
- `app/services/graph_store.py`

### 4) 基础 Schema 校验
- 基础节点/关系集合
- 可选严格模式（unknown type/relation 直接报错）
- 可选事件字段校验（event 必须有 day/summary）

实现文件：
- `app/services/graph_schema.py`

### 5) API 层（图谱基础设施接口）
- 读/写整图、节点、边
- 激活扩散
- 子图获取（支持 fast 模式 + ref 解析）
- 索引查询（type/name/day）

实现文件：
- `app/routers/graphs.py`

### 6) Demo / 工具 / 测试
- Demo: `app/tools/graph_demo.py`
- 索引重建工具: `app/tools/graph_indexer.py`
- 导入工具: `app/tools/graph_importer.py`
- 切分工具: `app/tools/ontology_batch_prep.py`
- 评审工具: `app/tools/graph_review.py`
- 合并工具: `app/tools/graph_merge.py`
- 测试: `tests/test_spreading_activation.py`

## Firestore 数据结构（当前实现）

**GM / Ontology 图**
```
worlds/{world_id}/graphs/{graph_type}/nodes/{node_id}
worlds/{world_id}/graphs/{graph_type}/edges/{edge_id}
```

**角色图**
```
worlds/{world_id}/characters/{char_id}/nodes/{node_id}
worlds/{world_id}/characters/{char_id}/edges/{edge_id}
```

**索引结构（可选写入）**
```
.../type_index/{type}/nodes/{node_id}
.../name_index/{lower_name}/nodes/{node_id}
.../timeline/{day}/events/{node_id}
```

> 说明：索引是“冗余结构”，用于加速检索。写入时可选开启。

## API 速览（/api 前缀）

- 整图：
  - `GET  /graphs/{world_id}/{graph_type}`
  - `POST /graphs/{world_id}/{graph_type}`
    - Query: `build_indexes`, `validate`, `strict`

- 节点/边：
  - `POST /graphs/{world_id}/{graph_type}/nodes`
  - `GET  /graphs/{world_id}/{graph_type}/nodes/{node_id}`
  - `POST /graphs/{world_id}/{graph_type}/edges`
  - `GET  /graphs/{world_id}/{graph_type}/edges/{edge_id}`

- 子图：
  - `GET /graphs/{world_id}/{graph_type}/subgraph`
    - Query: `seed_nodes`, `depth`, `direction`, `fast`, `resolve_refs`

- 索引：
  - `GET /graphs/{world_id}/{graph_type}/index/type/{node_type}`
  - `GET /graphs/{world_id}/{graph_type}/index/name/{name}`
  - `GET /graphs/{world_id}/{graph_type}/index/day/{day}`

## 使用示例

### 示例文件
示例 JSON 已整理到 `examples/`：
- `examples/phase3/`：Flash 摄入/召回
- `examples/phase4/`：Pro 档案/上下文
- `examples/phase5/`：GM 事件分发
- `examples/phase6/`：Game Loop 会话/战斗

### 运行 Demo
```
python -m app.tools.graph_demo
```

### 保存 Demo 图到 Firestore
```
python -m app.tools.graph_demo --save --world demo_world --graph gm
```

### 重建索引
```
python -m app.tools.graph_indexer --world demo_world --graph gm
```

### 运行测试
```
PYTHONPATH=. pytest tests/ -v
```

### 生成世界观批量抽取输入
```
python -m app.tools.ontology_batch_prep --input data/world.txt --output data/ontology_chunks.jsonl
```

## 当前限制
- 默认子图接口仍可选择“全量加载”模式（fast=false）。
- 索引写入需要显式开启（`build_indexes` / `index`）。
- Schema 校验只提供基础规则，后续可扩展更严格的类型约束。

## 接下来（Phase 2）
- 定义正式 Schema（实体类型、关系类型、字段约束）
- 批量导入世界观资料
- 建立可查询的世界观图谱

---

如需调整 Firestore 路径或 Schema 约束，请在 Phase 2 开始前提出。
## 引用机制（ref）
角色图谱中允许 `_ref` 节点类型，属性内可包含：`target_graph`、`target_id`。
当 API 请求携带 `resolve_refs=true` 时，会在返回数据的节点 `properties.resolved`
中附带被引用节点的完整内容。
