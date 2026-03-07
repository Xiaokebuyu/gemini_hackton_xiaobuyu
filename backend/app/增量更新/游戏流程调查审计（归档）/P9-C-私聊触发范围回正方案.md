# P9-C 私聊触发范围回正方案

## 问题定义

`PrivateChatTriggerHook`（P75a）当前全量扫描 `relations.npc_dispositions`，不做任何场景过滤。
设计文档（编排层 §3.2 L480）明确要求"遍历**场景内** NPC"。

**直接后果**：不在当前区域的 NPC（如远在 cow_girl_farm 的 cow_girl）会发出 `npc_wants_to_chat`，
玩家体感"关系系统没有隔离"。

---

## 与设计文档的完整差距清单

| # | 设计规范要求 | 来源 | 现状 | 差距 |
|---|------------|------|------|------|
| 1 | 遍历**场景内** NPC | 编排层 §3.2 L480 | 全量扫 `npc_dispositions` | **严重** |
| 2 | NPC 可达（同区域/子地点，非战斗/忙碌） | NPC规范 §7.1 L412 | 无位置检查 | **严重** |
| 3 | 关系阶段 >= 相识 | NPC规范 §7.1 L411 | 未检查 stage | 中等 |
| 4 | "小概率"触发 | NPC规范 §7.1 L416 | 确定性（过阈值必触发） | 中等 |
| 5 | 仅"营火/休息时"主动邀请 | NPC规范 §7.1 L416 | 每次 settlement 无条件扫描 | 中等 |
| 6 | trust > 60 | NPC规范 §7.1 L416 | `TRUST_THRESHOLD = 50` | 轻微（注：编排层 §3.2 写 50，两份文档矛盾） |
| 7 | 不在私密子地点内时才可发起 | NPC规范 §7.1 L414 | 未检查 | 轻微 |

---

## 分步方案

### Step 1：场景过滤（解决 #1 #2 — 核心问题）

**改动文件**：`app/game_core/orchestration/hooks/private_chat_trigger.py`

**改动位置**：`PrivateChatTriggerHook.execute()` 方法（L117-176）

**逻辑**：在遍历 `npc_dispositions` 之前，构建"当前场景可达 NPC"集合，然后只评估该集合内的 NPC。

```python
# --- 新增：构建场景内 NPC 集合 ---
reachable_npc_ids: set[str] = set()

if context.state.has_slice("player") and context.state.has_slice("areas"):
    player_area = context.state.player.current_area
    if player_area:
        # AreaSlice.npc_locations 记录了每个 area 内 NPC 的位置
        area_snap = context.state.areas.snapshot()
        area_data = area_snap.get("areas", {}).get(player_area, {})
        npc_locs: dict = area_data.get("npc_locations", {})
        reachable_npc_ids = set(npc_locs.keys())

        # 队伍成员也算"可达"（他们跟随玩家，不一定注册在 npc_locations）
        if context.state.has_slice("party"):
            reachable_npc_ids |= set(context.state.party.get_members().keys())

if not reachable_npc_ids:
    return HookResult(metadata={"skipped": "no_reachable_npcs"})

# --- 原循环改为：只遍历可达 NPC ---
for npc_id, dispositions in dispositions_map.items():
    if npc_id not in reachable_npc_ids:   # <--- 新增守卫
        continue
    # ... 后续逻辑不变
```

**数据通路确认**：
- `state.player.current_area`：PlayerSlice L96，`str` 类型
- `state.areas.snapshot()["areas"][area_id]["npc_locations"]`：AreaState L21，`dict[str, str | None]`
- `state.party.get_members()`：PartySlice L66，`dict[str, dict]`
- P60 NpcScheduleHook（priority 60）在本 Hook（priority 75）之前运行，确保 `npc_locations` 已被日程系统更新

**不改动**：
- `PrivateChatTriggerEvaluator` Protocol 签名不变
- `BasicPrivateChatTriggerEvaluator` 的判定逻辑不变
- SSE 事件格式不变

---

### Step 2：关系阶段前置（解决 #3）

**改动文件**：同上，`execute()` 方法

**改动位置**：在 `should_initiate()` 调用之前增加 stage 守卫

```python
# --- 新增：陌生人不可发起 ---
_NON_CHAT_STAGES: frozenset[str] = frozenset({"stranger"})

# 在循环体内，cooldown 检查之后、evaluator 调用之前：
stage = stages_map.get(npc_id, "stranger")
if stage in _NON_CHAT_STAGES:
    continue
```

**依据**：NPC规范 §7.1 L411 "关系阶段 >= 相识"。`_POSITIVE_STAGE_ORDER` 中 stranger 排第一，
acquaintance 排第二，所以排除 stranger 即等价于 ">= 相识"。
负面阶段（cold/hostile/nemesis/enemy）也排除——这些 NPC 不会主动示好。

**修正**：将 `_NON_CHAT_STAGES` 定义为模块级常量：

```python
_NON_CHAT_STAGES: frozenset[str] = frozenset({
    "stranger",   # 未建立关系
    "cold",       # 负面
    "hostile",    # 负面
    "nemesis",    # 负面
    "enemy",      # 负面
})
```

---

### Step 3：休息/营火时机限定（解决 #5）

**改动文件**：同上，`execute()` 方法

**逻辑**：检查 SceneBus 中是否有 REST 或 LONG_REST 标签。只在休息结算时才评估 NPC 主动私聊。

```python
# --- 新增：时机检查 ---
def _is_rest_tick(scene_bus: SceneBus) -> bool:
    """只在休息/营火 tick 才评估 NPC 主动私聊。"""
    bus_snap = scene_bus.snapshot()
    for entry in bus_snap.get("entries", []):
        if isinstance(entry, dict) and entry.get("source") == "ENGINE":
            tags = entry.get("tags", [])
            if "REST" in tags or "LONG_REST" in tags:
                return True
    return False
```

在 `execute()` 开头加守卫：

```python
if not _is_rest_tick(context.scene_bus):
    return HookResult(metadata={"skipped": "not_rest_tick"})
```

**依据**：NPC规范 §7.1 L416 "在营火/休息时主动邀请私聊"。
`_SEMANTIC_TAGS`（tick_coordinator.py L26-28）已将 `rest_long` 映射为 `["REST", "LONG_REST"]`、
`rest_short` 映射为 `["REST", "SHORT_REST"]`，标签可靠。

**影响**：触发频率大幅降低（从"每次 settlement"变为"仅休息时"），配合 cooldown 更接近设计意图。

---

### Step 4：概率化（解决 #4）

**改动文件**：同上

**逻辑**：evaluator 返回 True 后，再做一次概率 roll。不同 reason 用不同概率。

```python
import random

_TRIGGER_CHANCE: dict[str, float] = {
    "romance":  0.40,   # romance 驱动：较高概率
    "trust":    0.30,   # trust 驱动：中等概率
    "intimate": 0.60,   # 灵魂伴侣：更高概率
}

# 在 evaluator 返回 True 之后：
if not should_trigger:
    continue
chance = _TRIGGER_CHANCE.get(reason, 0.30)
if random.random() > chance:
    continue  # 概率未中，跳过
```

**依据**：NPC规范 §7.1 L416 "小概率"。具体数值为经验初始值，后续可调。
灵魂伴侣给更高概率是因为 §7.1 的设计意图：intimate 阶段 NPC 有强烈的私密互动欲望。

**可测试性**：evaluator 仍可注入 `NullPrivateChatTriggerEvaluator` 跳过全部触发。
对概率本身的测试，用 `random.seed()` 固定种子或 mock `random.random`。

---

### Step 5：私聊中防重入（解决 #7）

**改动文件**：同上，`execute()` 方法

**逻辑**：检查玩家当前是否已在私聊临时子地点中。

```python
# --- 新增：私聊中不再触发新邀请 ---
if context.state.has_slice("player"):
    current_loc = context.state.player.current_location
    if current_loc and current_loc.startswith("_private_"):
        return HookResult(metadata={"skipped": "already_in_private_chat"})
```

**依据**：
- NPC规范 §7.1 L414 "不在私密子地点内"
- PrivateChatCoordinator 创建的子地点 id 格式为 `_private_{npc_id}_{tick}`（private_chat.py L343）
- 用前缀匹配是安全的——没有其他系统生成 `_private_` 前缀的 location

---

### Step 6：trust 阈值文档对齐（解决 #6）

**两份文档矛盾**：
- NPC规范 §7.1 L416："trust > 60"
- 编排层 §3.2 L480："trust >= 50"

**决策**：采用编排层的 `trust >= 50`，即**保持当前值不改**。理由：
1. 编排层是 Hook 的直接设计文档，NPC规范 §7.1 L416 描述的是"NPC 主动发起"的场景级行为，两者可能描述的是不同粒度
2. Step 3（休息限定）+ Step 4（概率化）已大幅收紧触发频率，阈值不需要再提高
3. 如果后续需要调整，改一个常量即可

**行动**：`TRUST_THRESHOLD = 50` 不改。在代码注释中标注文档差异：

```python
# 编排层 §3.2 写 trust>=50；NPC规范 §7.1 L416 写 trust>60。
# 采用编排层值（50）配合概率化 + 休息限定来控制频率。
TRUST_THRESHOLD: int = 50
```

---

## 执行顺序与依赖

```
Step 5（防重入）  ─┐
Step 3（休息限定）─┤  execute() 开头的三个早返回守卫，互相独立
Step 1（场景过滤）─┘
        ↓
Step 2（阶段前置）── 循环体内，cooldown 之后 evaluator 之前
        ↓
Step 4（概率化）── 循环体内，evaluator 之后
```

建议一次性改完 `execute()` 方法体，步骤间无真正的阶段依赖。

---

## 改动文件清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `app/game_core/orchestration/hooks/private_chat_trigger.py` | 编辑 | 主要改动：execute() + 新增模块级常量/辅助函数 |
| `tests/test_private_chat_trigger.py` | 编辑 | 更新现有测试 + 新增场景过滤/阶段前置/休息限定/概率化/防重入测试 |
| `app/施工记录（持续更新）/narrative.md` | 编辑 | D-N20 追加 Phase C 回正记录 |

**不改动的文件**：
- `PrivateChatTriggerEvaluator` Protocol 签名 — 向后兼容
- `PrivateChatCoordinator` — 它是执行端，不是触发端
- `AreaSlice` / `PlayerSlice` / `PartySlice` — 已有 API 足够，无需扩展
- `defaults.py` — Hook 注册不变
- `SettlementContext` — 已暴露 state + scene_bus，不缺字段

---

## 测试计划

### 需更新的现有测试

现有 19 个测试（`tests/test_private_chat_trigger.py`）中，以下测试需要更新 `_make_context` helper 来注入 AreaSlice + PlayerSlice + PartySlice + SceneBus REST 标签，否则会被新守卫拦截：

| 测试 | 需更新原因 |
|------|-----------|
| `test_npc_romance_above_threshold_emits_event` | 需 NPC 在场 + REST 标签 |
| `test_npc_trust_above_threshold_emits_event` | 同上 |
| `test_cooldown_expired_allows_retrigger` | 同上 |
| `test_cooldown_set_after_trigger` | 同上 |
| `test_multiple_npcs_multiple_events` | 同上 |
| `test_npc_name_populated_from_registry` | 同上 |
| `test_npc_name_falls_back_to_id_when_no_registry` | 同上 |
| `test_metadata_contains_triggered_count` | 同上 |
| `test_no_flags_slice_still_triggers` | 同上 |

**更新方式**：修改 `_make_context` 新增参数：
- `player_area: str = "town"`
- `npc_in_area: dict[str, str | None] | None = None`（默认将 dispositions 的 key 全放入同区域）
- `party_members: dict[str, dict] | None = None`
- `inject_rest_tag: bool = True`（测试默认注入 REST 标签）

不触发的测试（`test_npc_below_threshold_no_event`、`test_cooldown_active_suppresses_event` 等）也需更新上下文，否则它们会在更早的守卫处返回而非在预期的检查点返回。

### 新增测试

| # | 测试名 | 验证点 |
|---|--------|-------|
| 1 | `test_npc_not_in_player_area_skipped` | NPC 在 relations 中但不在当前 area 的 npc_locations → 不触发 |
| 2 | `test_party_member_always_reachable` | NPC 不在 npc_locations 但在 party.members → 可触发 |
| 3 | `test_stranger_stage_blocked` | stage=stranger + 高 trust → 不触发 |
| 4 | `test_cold_stage_blocked` | stage=cold + 高 romance → 不触发 |
| 5 | `test_acquaintance_stage_allowed` | stage=acquaintance + 高 trust → 可触发（概率mock） |
| 6 | `test_not_rest_tick_skipped` | SceneBus 无 REST 标签 → 全部跳过 |
| 7 | `test_rest_tick_triggers` | SceneBus 有 LONG_REST 标签 → 正常评估 |
| 8 | `test_short_rest_also_triggers` | SceneBus 有 REST+SHORT_REST → 正常评估 |
| 9 | `test_probability_roll_can_suppress` | mock random.random 返回 0.99 → 不触发 |
| 10 | `test_probability_roll_can_pass` | mock random.random 返回 0.01 → 触发 |
| 11 | `test_already_in_private_chat_skipped` | current_location="_private_npc_1_42" → 全部跳过 |
| 12 | `test_no_player_slice_skipped` | 无 PlayerSlice → skipped（无法确定区域） |
| 13 | `test_no_area_slice_skipped` | 无 AreaSlice → skipped |

---

## execute() 方法最终结构（伪代码）

```python
async def execute(self, context: SettlementContext) -> HookResult:
    # Guard 0: 基础 slice 检查
    if not context.state.has_slice("relations"):
        return HookResult(metadata={"skipped": "no_relations"})

    # Guard 1: 私聊中防重入
    if context.state.has_slice("player"):
        current_loc = context.state.player.current_location
        if current_loc and current_loc.startswith("_private_"):
            return HookResult(metadata={"skipped": "already_in_private_chat"})

    # Guard 2: 仅休息/营火 tick
    if not _is_rest_tick(context.scene_bus):
        return HookResult(metadata={"skipped": "not_rest_tick"})

    # Guard 3: 构建可达 NPC 集合
    reachable_npc_ids = _collect_reachable_npcs(context)
    if not reachable_npc_ids:
        return HookResult(metadata={"skipped": "no_reachable_npcs"})

    # 准备数据
    current_tick = (context.state.time.absolute_tick()
                    if context.state.has_slice("time") else 0)
    dispositions_map = context.state.relations.npc_dispositions
    stages_map = context.state.relations.relationship_stages
    has_flags = context.state.has_slice("flags")
    sse_events: list[SSEEvent] = []

    for npc_id, dispositions in dispositions_map.items():
        # Filter: 场景内
        if npc_id not in reachable_npc_ids:
            continue

        # Filter: 关系阶段
        stage = stages_map.get(npc_id, "stranger")
        if stage in _NON_CHAT_STAGES:
            continue

        # Filter: cooldown
        cooldown_key = f"private_chat_cooldown_{npc_id}"
        if has_flags:
            cooldown_until = context.state.flags.get(cooldown_key, 0)
            if isinstance(cooldown_until, int) and current_tick < cooldown_until:
                continue
            if context.state.flags.has(cooldown_key):
                context.state.flags.remove(cooldown_key)

        # Evaluate: disposition/stage 阈值
        should_trigger, reason = self._evaluator.should_initiate(
            npc_id,
            dict(dispositions) if not isinstance(dispositions, dict) else dispositions,
            stage,
        )
        if not should_trigger:
            continue

        # Filter: 概率 roll
        chance = _TRIGGER_CHANCE.get(reason, 0.30)
        if random.random() > chance:
            continue

        # 触发！
        if has_flags:
            context.state.flags.set(cooldown_key, current_tick + COOLDOWN_TICKS)

        npc_name = _get_npc_name(context.world, npc_id)
        sse_events.append(SSEEvent(
            event_type="npc_wants_to_chat",
            payload={"npc_id": npc_id, "npc_name": npc_name, "reason": reason},
        ))

    return HookResult(
        sse_events=sse_events,
        metadata={"triggered": len(sse_events)},
    )
```

辅助函数：

```python
def _collect_reachable_npcs(context: SettlementContext) -> set[str]:
    """返回当前区域内可达的 NPC id 集合。"""
    result: set[str] = set()
    if not (context.state.has_slice("player") and context.state.has_slice("areas")):
        return result
    player_area = context.state.player.current_area
    if not player_area:
        return result
    area_snap = context.state.areas.snapshot()
    area_data = area_snap.get("areas", {}).get(player_area, {})
    result = set(area_data.get("npc_locations", {}).keys())
    if context.state.has_slice("party"):
        result |= set(context.state.party.get_members().keys())
    return result


def _is_rest_tick(scene_bus: SceneBus) -> bool:
    """检查当前 tick 是否包含休息事件。"""
    bus_snap = scene_bus.snapshot()
    for entry in bus_snap.get("entries", []):
        if isinstance(entry, dict) and entry.get("source") == "ENGINE":
            tags = entry.get("tags", [])
            if "REST" in tags or "LONG_REST" in tags:
                return True
    return False
```

---

## 验收标准

- [ ] 不在当前区域的 NPC 不会发出 `npc_wants_to_chat`
- [ ] stage=stranger/cold/hostile/nemesis/enemy 的 NPC 不会触发
- [ ] 非休息 tick 不触发任何 NPC 主动私聊
- [ ] 触发带概率（不是过阈值必触发）
- [ ] 已在私聊中（`_private_` 前缀 location）不会收到新邀请
- [ ] 队伍成员无论 npc_locations 如何，都算可达
- [ ] 现有 19 个测试更新后全部通过
- [ ] 新增 ~13 个测试全部通过
- [ ] 施工记录更新

## 风险与注意事项

1. **npc_locations 未注册的 NPC**：如果某个 NPC 有高关系但从未被 NpcScheduleHook 注册到 npc_locations（例如动态生成的 NPC），它不会触发私聊。这是**正确行为**——不在场就不该触发。但如果它是 party member 则不受影响。

2. **REST 标签依赖**：如果玩家从不休息，NPC 永远不会主动邀请私聊。这符合设计意图（"营火/休息时"），但如果后续需要扩展其他触发时机（如"回到安全区域"），可以扩展 `_is_rest_tick` 的判定条件。

3. **概率值需要 playtesting 调优**：初始值（romance 40%、trust 30%、intimate 60%）是经验估值，需要实际游戏测试后调整。可以考虑后续提取为 evaluator 参数。

4. **SceneBus 快照格式**：`scene_bus.snapshot()` 返回的结构依赖 SceneBus 实现。当前通过 CampfireHook 的相同模式（campfire.py L135-136）验证过此路径是可靠的。
