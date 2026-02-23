# L1 Agentic 层审视

> 状态：✅ 审视完毕，执行已完成（见 `L1-上层改造执行方案.md` §十五）
> 设计哲学：**运行时全在本地，存档时才存 Firestore**

---

## 一、现有工具清单

### base 工具（GM/NPC/队友共享）

| 工具 | 写入路径 | 状态 | 备注 |
|------|---------|------|------|
| `react_to_interaction` | session.update_disposition → persist | OK | FEELING_MAP 翻译层 |
| `recall_experience` | 只读 | **需修复** | 只返回 node_id+score，不返回内容 |
| `form_impression` | GraphStore 直写 | **违反设计哲学** | 应走本地运行时 |
| `share_thought` | SceneBus 发布 | OK | |
| `notice_something` | SceneBus 发布 | OK | |

### gm 专属工具

| 工具 | 写入路径 | 状态 | 备注 |
|------|---------|------|------|
| `create_memory` | GraphStore 直写 | **遗留 + 违反设计哲学** | |
| `update_disposition` | session → persist | OK | |
| `heal_player` | session → mark_dirty → persist | OK | |
| `damage_player` | session → mark_dirty → persist | OK | |
| `add_xp` | session → mark_dirty → persist | OK | |
| `add_item` | session → mark_dirty → persist | OK | |
| `remove_item` | session → mark_dirty → persist | OK | |
| 事件工具 ×6 | session → persist | OK | |
| `generate_scene_image` | 外部服务 | OK | |
| `report_flash_evaluation` | session.flash_results | OK | |

### trait 工具（NPC 特化）

| 工具 | 状态 | 备注 |
|------|------|------|
| `evaluate_offer` (merchant) | stub | 对应设施未就位 |
| `propose_deal` (merchant) | stub | 同上 |
| `adjust_my_prices` (merchant) | stub | 同上 |
| `grant_passage` (guard) | stub | 同上 |
| `offer_quest` (quest_giver) | stub | 同上 |
| `offer_healing` (healer) | stub | 同上 |

### teammate 专属

| 工具 | 状态 | 备注 |
|------|------|------|
| `express_need` | stub | 对应设施未就位 |
| `choose_battle_action` | stub | 同上 |
| `assess_situation` | stub | 同上 |

---

## 二、问题诊断

### P1: `recall_experience` 返回值无用

**位置**: `immersive_tools.py:200-223`

当前返回：
```json
{"memories": [{"concept": "event_group_20250221_xxx", "relevance": 0.85}]}
```

LLM 只拿到 node_id + 分数，不知道记忆内容是什么。应返回 name + summary + content。

### P2: `create_memory` / `form_impression` 违反设计哲学

两个工具直接调用 `GraphStore.upsert_node_v2()` 即时写 Firestore，违反"运行时全在本地，存档时才存 Firestore"。

- `create_memory`: GM 独占，写 area/character scope
- `form_impression`: 全角色可用，写 character(agent_id) scope

应改为写入本地运行时，persist 时统一写。

### P3: `create_memory` 是 GM 遗留工具

GM 不应该手动"创建记忆"——记忆是 NPC 自己的事。应该由 NPC/队友的图谱化系统自动处理。这个工具是旧架构中 GM 全能控制的遗留。

### P4: MCP graph_tools 与沉浸式工具重叠

MCP `graph_tools.py` 是底层 CRUD 接口（旧架构），沉浸式工具是角色化封装（新架构）。两者并存造成：
- `recall_memory` (MCP) vs `recall_experience` (沉浸式) — 前者更强但 LLM 调不到
- `upsert_scoped_node` (MCP) vs `create_memory` (沉浸式) — 功能重叠
- `get_disposition` (MCP) — 沉浸式层没有好感度查询工具

---

## 三、下层数据收集（留作后用）

### GraphStore 直写问题

GraphStore (`app/services/graph_store.py`) 是旧架构的 Firestore 直连层，每次操作即时写。在新设计哲学下应有本地运行时缓冲层。

**影响范围**：
- `create_memory` → `GraphStore.upsert_node_v2()`
- `form_impression` → `GraphStore.upsert_node_v2()`
- `MemoryGraphizer.graphize()` → 大量 `upsert_node_v2/edge_v2`
- `_merge_scene_bus_messages()` → 大量 `upsert_node_v2/edge_v2`

### 阈值触发图谱化 (MemoryGraphizer session_history 模式)

**触发条件**: ContextWindow 80% (160K/200K) token 占用
**流程**: 加载 profile + 50 重要节点 → LLM 提取 → 写入 character scope
**质量评价**:
- 优：NPC 第一人称视角、完整 transcript 保留、层级结构、世界知识桥接
- 劣：50 节点上限不够、LLM 降级粗糙、110K 一次性喂入、无重要事件主动触发、无效果审计
- **全程直写 Firestore**（3+ 次读 + N 次写），违反设计哲学

### SceneBus 自动图谱化

**位置**: `pipeline_orchestrator.py` C 阶段
**模式**: 纯规则化（utterance/topic/actor 节点，BY/ABOUT/RESPONDS_TO 边）
**评价**: 粗糙，无语义提取。用户认为可删除。
**注意**: 删除需在下层（Pipeline 编排层）操作

---

## 四、本层可执行项

（待讨论确认）
