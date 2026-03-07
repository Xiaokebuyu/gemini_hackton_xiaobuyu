# P9-A 任务入口全面对齐执行计划

> 对应 P9 问题一：任务入口模型错位
> 目标：将公告板交互从错误的 `/interact/stream` 管线迁移到设计文档要求的 `/action/stream` 主管线，bulletin 存储从 NarrativePlanSlice 迁移到 AreaSlice，planner 投递目标对齐真实世界数据。

---

## 一、设计决策汇总

| # | 决策点 | 结论 | 理由 |
|---|--------|------|------|
| D1 | browse 是否消耗时间 | 消耗 1/6 格 | 设计文档 interact_object 统一 time_cost=1/6 |
| D2 | browse 走哪条管线 | /action/stream 主管线 | 需要 GM B 阶段可观察 |
| D3 | 命令语义 | 新建 BoardHandler 处理 4 种 command | board 有独立校验需求 |
| D4 | board 级校验位置 | 下沉到 handler | 对齐"命令合法性由 RulesEngine 验证" |
| D5 | board_id 语义 | = interactable ID（如 `quest_board`） | 当前最简，直接对应 maps.json |
| D6 | bulletin 存储位置 | 迁移到 AreaSlice | 全面对齐设计文档 |
| D7 | 旧路径处理 | 完全移除 | 不留兼容层债务 |

---

## 二、架构变更概览

### 变更前

```
前端点击委托板
  → POST /interact/stream {target_kind:"board", target_id:"quest_board", intent:"browse"}
  → FastAPIInputPort._normalize_board_interaction()
  → InteractionService.execute()
    → validate_presence(): current_location == board_id  ← 永远失败
    → _execute_snapshot() → board_snapshot SSE
  → 数据源: NarrativePlanSlice.active_bulletins（board_id 硬编码 "board"）
```

### 变更后

```
前端点击委托板
  → POST /action/stream {action_type:"browse_board", params:{board_id:"quest_board"}}
  → ActionDispatcher.dispatch() → Command(type="browse_board")
  → BoardHandler.validate()  ← 检查玩家所在 sub_location 含该 interactable
  → BoardHandler.compute()   ← 从 AreaSlice.board_bulletins 读取任务列表
  → PipelineOrchestrator B 阶段（GM 可观察 SceneBus）
  → SSE action_result + board_snapshot
  → 数据源: AreaSlice.board_bulletins[board_id]（planner 动态解析）
```

---

## 三、数据结构设计

### 3.1 BulletinEntry（新增 dataclass）

位置：`app/game_core/state/slices/area.py`（与 AreaState 同文件）

```python
@dataclass(slots=True)
class BulletinEntry:
    """公告板上的一条任务公告。"""
    board_id: str                          # interactable ID（如 "quest_board"）
    quest_id: str = ""                     # 关联的动态任务 ID
    title: str = ""                        # 公告标题
    content: str = ""                      # 公告内容描述
    published_at_tick: int = 0             # 发布时的 tick 数
    source: str = "narrative_planner"      # 来源标识
```

### 3.2 AreaState 新增字段

```python
@dataclass(slots=True)
class AreaState:
    # ... 现有字段 ...
    board_bulletins: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # key = board_id (interactable ID)
    # value = 该 board 上的公告列表（BulletinEntry 序列化后的 dict）
```

说明：value 存为 `list[dict]` 而非 `list[BulletinEntry]`，与 `container_states`、`interactable_states` 等现有字段保持一致的 schemaless 存储模式。`BulletinEntry` dataclass 用于写入端的类型约束和文档化，存储时 `dataclasses.asdict()` 序列化。

### 3.3 BoardHandler command 类型与参数

| command_type | 必需参数 | 可选参数 | 返回 |
|-------------|---------|---------|------|
| `browse_board` | `board_id: str` | — | ExecuteResult: metadata 含 entries 列表 |
| `board_accept_quest` | `board_id: str`, `quest_id: str` | — | ExecuteResult: StateChange 修改 dynamic_quest status |
| `board_complete_quest` | `board_id: str`, `quest_id: str` | — | 同上 |
| `board_retire_quest` | `board_id: str`, `quest_id: str` | — | 同上 |

### 3.4 browse_board 返回结构

```python
ExecuteResult(
    success=True,
    time_cost=1/6,
    metadata={
        "board_id": "quest_board",
        "entries": [
            {
                "quest_id": "goblin_cave_01",
                "title": "哥布林洞穴讨伐",
                "content": "近日有哥布林出没...",
                "quest_status": "available",    # 从 QuestSlice 富化
                "published_at_tick": 3,
            },
            ...
        ],
    },
    changes=[],   # browse 不改变状态
)
```

### 3.5 board_accept_quest 返回结构

```python
ExecuteResult(
    success=True,
    time_cost=1/6,
    metadata={
        "board_id": "quest_board",
        "quest_id": "goblin_cave_01",
        "action": "accept",
    },
    changes=[
        StateChange(
            slice="quests",
            operation="modify",
            path=f"dynamic_quests.goblin_cave_01",
            value={...existing_quest_data, "status": "active"},
        ),
    ],
)
```

---

## 四、分阶段执行计划

### Phase 1：BoardHandler + 主管线注册

> 目标：新建 BoardHandler，注册到主管线，让公告板交互走 /action/stream。
> 本阶段 handler 暂时从 NarrativePlanSlice.active_bulletins 读取数据（Phase 2 迁移存储）。

#### 1.1 新建 `app/game_core/rules/handlers/board.py`

**BoardHandler 职责**：

```python
class BoardHandler(CommandHandler):
    COMMAND_TYPES = ("browse_board", "board_accept_quest", "board_complete_quest", "board_retire_quest")
```

**validate() 方法** — 空间可达性校验：

```
1. 检查 player slice 和 maps registry 存在
2. 提取 board_id 参数（非空字符串校验）
3. 获取玩家当前 area_id + location_id（current_location）
4. 从 MapRegistry 获取该 sub_location 的 InteractableTemplate 列表
5. 检查 board_id 对应的 interactable 是否存在于该列表中
6. 对 accept/complete/retire：额外检查 quest_id 参数非空
```

校验失败返回 `ValidationResult(ok=False, reason="...")`。

**compute() 方法** — 按 command_type 分派：

- `browse_board`：
  1. 从 NarrativePlanSlice.active_bulletins 过滤 board_id 匹配的条目（Phase 1 临时方案）
  2. 从 QuestSlice 富化 quest_status
  3. 返回 handler_success，metadata 含 entries 列表，time_cost=1/6

- `board_accept_quest`：
  1. 验证 quest_id 在该 board 的 bulletin 中存在
  2. 获取 dynamic_quest 当前数据，检查 status 允许接取（如 status 不是 "active"/"completed"）
  3. 创建 StateChange：`dynamic_quests.{quest_id}` → status="active"
  4. 返回 handler_success，time_cost=1/6

- `board_complete_quest`：
  1. 验证 quest_id 在该 board 上
  2. 验证 quest status == "active"
  3. StateChange → status="completed"

- `board_retire_quest`：
  1. 验证 quest_id 在该 board 上
  2. StateChange → status="retired"

**辅助方法**：

```python
def _find_sub_location_interactable(
    self, world: WorldInstance, area_id: str, location_id: str, interactable_id: str
) -> InteractableTemplate | None:
    """在指定子地点查找 interactable。"""

def _get_board_bulletins(
    self, state: StateContainer, board_id: str
) -> list[dict[str, Any]]:
    """获取指定 board 的公告列表。Phase 1 从 NarrativePlanSlice 读取。"""

def _enrich_with_quest_status(
    self, entries: list[dict], state: StateContainer
) -> list[dict]:
    """从 QuestSlice 富化 quest_status 字段。"""
```

#### 1.2 修改 `app/game_core/orchestration/defaults.py`

在 `DEFAULT_ACTION_COMMAND_TYPES` 中注册：

```python
("browse_board", "browse_board"),
("board_accept_quest", "board_accept_quest"),
("board_complete_quest", "board_complete_quest"),
("board_retire_quest", "board_retire_quest"),
```

#### 1.3 新建测试 `tests/test_board_handler.py`

覆盖场景：

| 测试 | 验证 |
|------|------|
| browse 成功 | 返回正确的 entries 列表 |
| browse 空 board | entries 为空列表 |
| browse 不存在的 board | validate 失败 |
| browse 玩家不在对应 sub_location | validate 失败 |
| accept 成功 | quest status → active |
| accept 不存在的 quest | compute 返回 success=False |
| accept 已激活的 quest | compute 返回 success=False |
| complete 成功 | quest status → completed |
| retire 成功 | quest status → retired |
| 时间消耗 | 所有操作 time_cost == 1/6 |

预计 10-12 个测试。

#### 1.4 Phase 1 验收标准

- [ ] `browse_board` 通过 BoardHandler 返回公告列表
- [ ] `board_accept_quest` 正确推进任务状态
- [ ] validate() 正确拒绝不可达的 board
- [ ] 所有操作 time_cost=1/6
- [ ] 新测试全部通过
- [ ] 既有测试基线不受影响

---

### Phase 2：Bulletin 存储迁移到 AreaSlice

> 目标：将 bulletin 数据从 NarrativePlanSlice 迁移到 AreaSlice，实现按区域隔离。

#### 2.1 修改 `app/game_core/state/slices/area.py`

**2.1.1 新增 BulletinEntry dataclass**（文件顶部，AreaState 之前）

```python
@dataclass(slots=True)
class BulletinEntry:
    board_id: str
    quest_id: str = ""
    title: str = ""
    content: str = ""
    published_at_tick: int = 0
    source: str = "narrative_planner"
```

**2.1.2 AreaState 新增字段**

```python
board_bulletins: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
```

**2.1.3 AreaState.snapshot() 新增**

```python
"board_bulletins": {
    bid: [dict(entry) for entry in entries]
    for bid, entries in self.board_bulletins.items()
},
```

**2.1.4 _coerce_area_state() 新增恢复逻辑**

```python
board_bulletins_raw = raw.get("board_bulletins", {})
if isinstance(board_bulletins_raw, Mapping):
    for bid, entries in board_bulletins_raw.items():
        if isinstance(entries, list):
            area.board_bulletins[str(bid)] = [
                dict(e) for e in entries if isinstance(e, Mapping)
            ]
```

**2.1.5 validate() 新增校验**

```python
if not isinstance(area.board_bulletins, dict):
    issues.append(f"area '{area_id}' board_bulletins must be a dict")
```

**2.1.6 apply_state_change() 新增路径**

```python
if change.path.startswith("board_bulletins."):
    # 路径格式: "board_bulletins.{board_id}"
    # operation: "append" → 追加一条 bulletin
    # value: dict（BulletinEntry 序列化）
    if change.operation != "append":
        raise ValueError("board_bulletins only supports append operation")
    if not isinstance(change.value, Mapping):
        raise ValueError("bulletin entry must be a mapping")
    _, board_id = change.path.split(".", 1)
    area_id = str(change.value.get("area_id", "")).strip()
    if not area_id:
        raise ValueError("bulletin must include area_id")
    area_state = self._ensure_area(area_id)
    bulletin_list = area_state.board_bulletins.setdefault(board_id, [])
    bulletin_entry = dict(change.value)
    bulletin_entry.pop("area_id", None)  # area_id 是路由字段，不存储
    bulletin_list.append(bulletin_entry)
    return
```

**2.1.7 新增便捷方法**

```python
def add_board_bulletin(self, area_id: str, board_id: str, entry: dict[str, Any]) -> None:
    """向指定区域的公告板追加一条公告。"""
    area_state = self._ensure_area(area_id)
    bulletin_list = area_state.board_bulletins.setdefault(board_id, [])
    bulletin_list.append(dict(entry))
    self._dirty = True

def get_board_bulletins(self, area_id: str, board_id: str) -> list[dict[str, Any]]:
    """获取指定区域指定公告板的公告列表。返回防御性拷贝。"""
    area_state = self.areas.get(area_id)
    if area_state is None:
        return []
    return [dict(e) for e in area_state.board_bulletins.get(board_id, [])]

def remove_board_bulletin(self, area_id: str, board_id: str, quest_id: str) -> bool:
    """移除指定公告板上某个任务的公告。返回是否找到并移除。"""
    area_state = self.areas.get(area_id)
    if area_state is None:
        return False
    entries = area_state.board_bulletins.get(board_id, [])
    for i, entry in enumerate(entries):
        if str(entry.get("quest_id", "")).strip() == quest_id:
            entries.pop(i)
            self._dirty = True
            return True
    return False
```

#### 2.2 修改 `app/game_core/orchestration/hooks/narrative_planner.py`

**publish_bulletin 指令处理改为写入 AreaSlice**：

```python
if kind == "publish_bulletin":
    board_id = self._coerce_non_empty_string(payload.get("board_id"))
    if board_id is None:
        return False
    # 确定目标区域：优先 payload 指定，fallback 到玩家当前区域
    area_id = self._coerce_non_empty_string(payload.get("area_id"))
    if area_id is None:
        area_id = self._get_player_current_area(context.state)
    if not area_id:
        return False
    quest_id = ""
    raw_metadata = _normalize_mapping(payload.get("metadata"))
    if isinstance(raw_metadata, dict):
        quest_id = str(raw_metadata.get("quest_id", "")).strip()
    context.state.area.add_board_bulletin(area_id, board_id, {
        "board_id": board_id,
        "quest_id": quest_id,
        "title": self._string_or_empty(payload.get("title")),
        "content": self._string_or_empty(payload.get("content")),
        "published_at_tick": current_tick,
        "source": "narrative_planner",
    })
    return True
```

#### 2.3 修改 `app/game_core/rules/handlers/board.py`

**_get_board_bulletins() 切换数据源**：

```python
def _get_board_bulletins(self, state: StateContainer, board_id: str) -> list[dict[str, Any]]:
    """从 AreaSlice 读取指定 board 的公告。"""
    area_id = state.player.current_area or ""
    if not area_id:
        return []
    return state.area.get_board_bulletins(area_id, board_id)
```

#### 2.4 修改 `app/opening_views.py`

行 116-117 当前从 `narrative_plan.active_bulletins` 读取第一条告示。
改为从 AreaSlice 读取（需要知道玩家所在区域的 board）：

```python
# 替换: bulletin = state.narrative_plan.active_bulletins[0] if ...
# 改为: 从 AreaSlice 获取当前区域任意 board 的第一条 bulletin
area_id = state.player.current_area or ""
all_bulletins = []
if area_id and hasattr(state, "area"):
    area_state = state.area.areas.get(area_id)
    if area_state:
        for entries in area_state.board_bulletins.values():
            all_bulletins.extend(entries)
bulletin = all_bulletins[0] if all_bulletins else None
```

#### 2.5 测试更新

| 测试 | 内容 |
|------|------|
| AreaSlice snapshot/restore 含 board_bulletins | 序列化往返一致 |
| AreaSlice validate 检查 board_bulletins 类型 | dict 类型校验 |
| add_board_bulletin / get_board_bulletins | CRUD 正确性 |
| remove_board_bulletin | 移除 + 未找到返回 False |
| NarrativePlannerHook publish_bulletin 写入 AreaSlice | 集成验证 |
| BoardHandler 从 AreaSlice 读取 | 数据源切换后正确 |

预计 8-10 个测试。

#### 2.6 Phase 2 验收标准

- [ ] bulletin 数据存储在 AreaSlice.board_bulletins 中
- [ ] NarrativePlannerHook.publish_bulletin 写入 AreaSlice
- [ ] BoardHandler 从 AreaSlice 读取
- [ ] AreaSlice snapshot/restore 正确处理 board_bulletins
- [ ] opening_views.py 从 AreaSlice 读取
- [ ] 既有测试基线通过

---

### Phase 3：Planner board_id 对齐 + 旧路径清理

> 目标：去掉硬编码 board_id="board"，从世界数据动态解析；移除 InteractionService 中所有 board 路径。

#### 3.1 修改 `app/game_core/planning/planner.py`

**去掉硬编码，动态解析 board_id**：

```python
# 替换硬编码 board_id="board"
# 改为：从 MapRegistry 查找当前区域中带 quest_source tag 的 interactable

def _resolve_quest_board(self, state, world) -> tuple[str, str] | None:
    """找到当前区域中第一个 quest_source interactable。

    返回 (area_id, board_id) 或 None。
    """
    area_id = state.player.current_area or ""
    if not area_id or not world.has_registry("maps"):
        return None
    area_template = world.maps.get(area_id)
    if area_template is None:
        return None
    for sub_loc in area_template.sub_locations.values():
        for iact in sub_loc.interactables:
            if "quest_source" in iact.tags:
                return (area_id, iact.id)
    return None
```

在 quest seeding 中使用：

```python
board_info = self._resolve_quest_board(state, world)
if board_info is None:
    return []  # 无可用公告板，跳过 bulletin 投递
area_id, board_id = board_info

# publish_bulletin 指令
{
    "kind": "publish_bulletin",
    "payload": {
        "board_id": board_id,       # 动态解析，如 "quest_board"
        "area_id": area_id,         # 如 "frontier_town"
        "title": f"New Lead: {milestone_label}",
        "content": f"A fresh lead is available: {milestone_label}.",
        "metadata": {
            "quest_id": quest_id,
            "source_milestone": milestone_id,
        },
    },
}
```

#### 3.2 修改 `app/interaction_service.py` — 移除 board 路径

删除以下内容：

- `_snapshot_builders` 中的 `"board"` 条目
- `_build_board_snapshot` 方法引用
- `_execute_pipeline_action` 中 board 相关的 `post_snapshot` 处理
- execute() 中 `target_kind == "board"` 的所有分支

保留 NPC / 商店 / 物品检视等非 board 交互路径。

#### 3.3 修改 `app/game_core/adapters/inbound.py` — 移除 board 归一化

删除 `FastAPIInputPort._normalize_board_interaction()` 方法及其调用点。

#### 3.4 修改 `app/game_core/orchestration/interaction.py` — 移除 board 验证

删除以下内容：

- `_validate_board_presence()` 函数
- `validate_presence()` 中 `target_kind == "board"` 的分支
- `board_has_quest()` 函数
- `validate_preconditions()` 中所有 board 相关的前置条件检查
- `InteractionPolicyContext` 中仅为 board 服务的字段（如有）
- `build_interaction_policy_context()` 中仅为 board 服务的构建逻辑（如有）

#### 3.5 修改 `app/interaction_views.py` — 清理 board 视图函数

删除以下函数（功能已被 BoardHandler 取代）：

- `build_board_snapshot_payload()`
- `build_board_entries()`
- `find_linked_bulletin()`（如果只被 board 路径使用）

如果 `find_linked_bulletin` 被其他地方使用（如 NPC talk 中引用关联任务），则保留并调整数据源。

#### 3.6 清理 `app/game_core/state/slices/narrative_plan.py`

- `active_bulletins` 字段保留（避免旧存档 restore 报错），但不再有写入端
- `add_bulletin()` 方法保留但标注废弃注释
- 或者：如果 `restore()` 能容忍字段缺失（当前应该可以，因为默认空列表），可以直接移除

#### 3.7 测试更新

- 更新 `tests/test_api_shell.py` 中所有 `add_bulletin` 调用 → 改为 `area.add_board_bulletin`
- 更新 `tests/test_interaction_service.py` 中 board 相关测试 → 改为通过 /action/stream 测试
- 删除不再适用的 board snapshot 测试
- 新增 planner board_id 动态解析测试

#### 3.8 Phase 3 验收标准

- [ ] planner 不再硬编码 board_id="board"
- [ ] planner 从 MapRegistry 动态解析 quest_source interactable
- [ ] InteractionService 中无任何 board 相关代码
- [ ] FastAPIInputPort 中无 board 归一化逻辑
- [ ] interaction.py 中无 board 验证逻辑
- [ ] 既有测试基线全部通过

---

### Phase 4：测试收尾 + 施工记录

#### 4.1 端到端验证

新增集成测试覆盖完整链路：

| 测试 | 链路 |
|------|------|
| 完整 browse 链路 | planner 投递 → AreaSlice 存储 → browse_board → 返回 entries |
| 完整 accept 链路 | browse → accept → quest status 变为 active |
| 空 board | 进入有 board 的 sub_location，board 无公告 → entries 为空 |
| 不在 guild | 玩家在其他 sub_location → browse_board validate 失败 |

预计 4-6 个测试。

#### 4.2 更新施工记录

在 `app/施工记录（持续更新）/` 下新增或更新对应的 D-Rxx 记录：

- 记录 BoardHandler 4 个 command_type
- 记录 AreaSlice.board_bulletins 数据结构
- 记录 bulletin 存储迁移决策
- 记录 planner board_id 动态解析逻辑
- 记录删除的旧代码路径

#### 4.3 Phase 4 验收标准

- [ ] 全部测试通过（含新增测试）
- [ ] 施工记录已更新
- [ ] DEFAULT_ACTION_COMMAND_TYPES 注册表同步

---

## 五、完整文件变更清单

### 新建文件（1 个）

| 文件 | 用途 |
|------|------|
| `app/game_core/rules/handlers/board.py` | BoardHandler：4 种 board command 的验证与计算 |

### 修改文件（9 个）

| 文件 | Phase | 改动性质 |
|------|-------|---------|
| `app/game_core/orchestration/defaults.py` | 1 | +4 行注册 |
| `app/game_core/state/slices/area.py` | 2 | +BulletinEntry dataclass, +board_bulletins 字段, +snapshot/restore/validate/apply_state_change/便捷方法 |
| `app/game_core/orchestration/hooks/narrative_planner.py` | 2 | publish_bulletin 写入目标从 NarrativePlanSlice → AreaSlice |
| `app/opening_views.py` | 2 | bulletin 读取源切换 |
| `app/game_core/planning/planner.py` | 3 | board_id 动态解析，去掉硬编码 |
| `app/interaction_service.py` | 3 | 移除 board snapshot/pipeline_action 路径 |
| `app/game_core/adapters/inbound.py` | 3 | 移除 _normalize_board_interaction() |
| `app/game_core/orchestration/interaction.py` | 3 | 移除 _validate_board_presence, board_has_quest 等 |
| `app/interaction_views.py` | 3 | 移除 board 视图函数 |

### 测试文件（2-3 个）

| 文件 | Phase | 内容 |
|------|-------|------|
| `tests/test_board_handler.py`（新建） | 1 | BoardHandler 单元测试 ~12 个 |
| `tests/test_area_slice_bulletins.py`（新建或合入现有） | 2 | AreaSlice bulletin CRUD + snapshot/restore ~8 个 |
| `tests/test_api_shell.py` + `tests/test_interaction_service.py` | 3 | 更新现有 board 相关测试 |

---

## 六、预估行数与工作量

| Phase | 新增行数 | 删除行数 | 净变化 | 文件数 |
|-------|---------|---------|--------|--------|
| 1: BoardHandler | ~150 | 0 | +150 | 2+测试 |
| 2: 存储迁移 | ~120 | ~10 | +110 | 4+测试 |
| 3: 对齐+清理 | ~30 | ~180 | -150 | 6+测试 |
| 4: 测试收尾 | ~40 | 0 | +40 | 1-2 |
| **合计** | **~340** | **~190** | **+150** | **12-14** |

---

## 七、风险评估

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| 旧存档 restore 丢失 active_bulletins 数据 | 低 | NarrativePlanSlice.active_bulletins 字段保留，restore 不报错。旧 bulletin 数据丢失可接受（动态生成，planner 会重新投递） |
| 前端未同步改动 | 中 | Phase 1 完成后后端即可用，前端改动范围明确（/interact → /action，payload 结构类似） |
| opening_views.py bulletin 读取 | 低 | 简单改数据源，逻辑不变 |
| test_api_shell 大量 add_bulletin 调用 | 中 | Phase 3 统一修改，但该测试文件本身在 --ignore 列表中 |

---

## 八、执行依赖关系

```
Phase 1 ──→ Phase 2 ──→ Phase 3 ──→ Phase 4
   │              │            │
   │              │            └─ 依赖 Phase 2 存储就位
   │              └─ 依赖 Phase 1 handler 就位
   └─ 无前置依赖
```

每个 Phase 都是闭合的可验证单元。Phase 1 完成后 board 交互即可工作（只是数据源暂时在旧位置）。Phase 2 完成后存储对齐。Phase 3 清理旧代码。Phase 4 收尾。
