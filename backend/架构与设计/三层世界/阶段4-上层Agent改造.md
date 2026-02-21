# 阶段 4：上层 Agent 改造

> 状态：⏸️ 待启动
> 目标：Agent 通过沉浸式接口与世界交互，搭建回合系统和新功能。
> 前置：阶段 3 完成

---

## 架构决策（已确认）

### D5. 沉浸式接口：LLM 以角色视角行动

所有 Agent（GM / NPC / 队友）通过同一套沉浸式接口与世界交互。工具描述使用角色视角语言，AI 不知道底层是数值操作。

示例：NPC 不调用 `update_disposition(approval: +10)`，而是调用 `react_to_interaction(feeling="grateful", intensity="moderate", reason="玩家帮我找回了戒指")`。中层翻译为好感度变更。

### D6. 角色工具注册：同一套接口，按 role + traits 过滤

GM / NPC / 队友都用沉浸式接口，但看到的工具集不同：

- **GM**：事件工具（activate_event / complete_event / advance_chapter）+ 全局观察
- **普通 NPC**：情感反应 + 记忆 + 观察
- **商人 NPC**：情感反应 + 记忆 + 交易工具（evaluate_offer / adjust_prices）
- **队友**：情感反应 + 记忆 + 战斗行动 + 需求表达

### D7. 情感量化：预定义档位

情感→数值映射使用预定义档位，LLM 选择更准确：

```
dimension × level → 数值
  approval × slight   → +5
  approval × moderate → +10
  approval × strong   → +20
  trust    × slight   → +5
  trust    × moderate → +10
  trust    × strong   → +20
  fear     × slight   → +5
  fear     × moderate → +10
  fear     × strong   → +20
  （负向同理，取负值）
```

### D8. SceneBus 成员模型：接触制

- **常驻成员**：玩家 + 队伍成员（始终在总线上）
- **临时成员**：NPC 被玩家接触后加入总线，可感知后续事件并反应
- **场景切换**：清空临时成员，保留常驻成员
- **未接触 NPC**：存在于场景中（GM 描述可提及），但不在总线上、不反应

### D9. 回合时间系统

- 每轮行动 ≈ 10 分钟游戏时间（可配置）
- 区域间旅行 +旅行时间（边属性定义）
- 场景切换（子地点）+5 分钟
- 休息 +自定义时长

### D10. 商店 + 好感度联动

- 直接购买 = 纯机械操作（基础价格）
- 商人 NPC 好感度影响价格：通过沉浸式接口 `adjust_my_prices(discount, duration, reason)`
- 商人"角色扮演"调价，不是玩家直接砍价
- 底层：商店节点 `state.price_modifiers` 存折扣信息

### D11. 私聊接入 Pipeline

- `process_private_chat_stream()` 接入 PipelineOrchestrator
- 补全 SessionHistory、好感度持久化、事件推进
- 设计隐秘性模型（公开 vs 私密对话）
- **注意**：私聊会影响 NPC 全局状态（好感度变化等），必须通过 SessionRuntime 统一处理
- 阶段 3 确认推迟到此阶段

### D12. trigger_event 旧路径融合

- 旧的 `trigger_event` REST 端点（`narrative_service.trigger_event()`）绕过 WorldGraph/BehaviorEngine
- 需要与阶段 1+2 建立的新事件系统（event_def 节点 + BehaviorEngine tick）融合
- 阶段 3 确认推迟到此阶段

---

## 预期游戏体验

```
玩家进入公会大厅 ──→ 第一轮（+10 分钟）
  │  GM: "大厅里人头攒动，公告板前围了几个冒险者..."
  │  （NPC 们在场但未加入总线）
  ▼
玩家走向酒保说话 ──→ 第二轮（+10 分钟）
  │  酒保加入 SceneBus
  │  酒保回应玩家
  │  队友（常驻总线）可能插嘴
  ▼
玩家继续和酒保聊 ──→ 第三轮（+10 分钟）
  │  酒保感知上一轮对话，继续响应
  │  旁边的铁匠（未接触）沉默
  ▼
玩家离开，前往森林 ──→ 旅行（+30 分钟）
  │  SceneBus 清空临时成员
  │  队友留在总线
  │  进入新场景，新的第一轮
```

---

## 4.1 沉浸式接口实现

### LLM 工具清单

```
情感与社交                                → 底层映射
  react_to_interaction(                   → disposition change
    dimension,                              "approval"/"trust"/"fear"/"romance"
    level,                                  "slight"/"moderate"/"strong"
    is_positive,                            true/false
    reason                                  角色视角自由文本
  )
  share_thought(                          → scene_bus narrative hint
    thought,
    visibility                              "spoken"/"whispered"/"internal"
  )

记忆
  recall_experience(topic, context)       → spreading activation recall
  form_impression(about, impression,      → memory graph node create
    significance)

交易（商人专属）
  evaluate_offer(item, feeling)           → shop price check
  propose_deal(offer, ask, reasoning)     → shop transaction
  adjust_my_prices(items, discount,       → shop node state modifier
    duration, reason)

战斗中
  choose_battle_action(intention, target) → combat action
  assess_situation(concern)               → combat state query

观察
  notice_something(observation, reaction) → scene_bus observation
  express_need(need, urgency)             → narrative hint
```

### 翻译层

```python
FEELING_MAP = {
    ("approval", "slight", True):   {"approval": +5},
    ("approval", "moderate", True): {"approval": +10},
    ("approval", "strong", True):   {"approval": +20},
    ("approval", "slight", False):  {"approval": -5},
    # ...
    ("trust", "strong", True):      {"trust": +20},
    ("fear", "moderate", True):     {"fear": +10},
    # ...
}
```

### RoleRegistry

```python
def get_tools_for_role(role: str, traits: list = []) -> list:
    base = [react_to_interaction, recall_experience,
            share_thought, notice_something]

    if role == "gm":
        base += [activate_event, complete_event, advance_chapter, ...]
    if role == "teammate":
        base += [choose_battle_action, assess_situation, express_need]
    if "merchant" in traits:
        base += [evaluate_offer, propose_deal, adjust_my_prices]

    return base
```

---

## 4.2 SceneBus 升级

当前 SceneBus 是简单的 `List[BusEntry]`，需要升级：

```python
class SceneBus:
    area_id: str
    permanent_members: Set[str]    # 玩家 + 队友 ID
    active_members: Set[str]       # 接触过的 NPC ID
    entries: List[BusEntry]

    def contact(self, npc_id: str):
        """玩家接触 NPC → 加入总线"""
        self.active_members.add(npc_id)

    def reset_scene(self, new_area_id: str):
        """场景切换 → 清临时成员，保留常驻"""
        self.area_id = new_area_id
        self.active_members.clear()
        self.entries.clear()

    def get_visible_entries(self, member_id: str) -> List[BusEntry]:
        """按成员过滤可见事件"""
        ...

    def is_member(self, entity_id: str) -> bool:
        return entity_id in self.permanent_members or entity_id in self.active_members
```

---

## 4.3 私聊接入 Pipeline

- `process_private_chat_stream()` 改为调用 `PipelineOrchestrator.process(is_private=True)`
- ContextAssembler 添加 `is_private` 支持
- V4AgenticToolRegistry 工具 gate（私聊模式禁用高权限工具）
- SessionHistory 支持 `visibility="private"` 标记
- TeammateResponseService 支持"隐秘对话"选项

---

## 4.4 新功能搭建（部分未实现，可跳过）

- [ ] 回合时间系统（每轮 +10 分钟）
- [ ] 商店系统（ShopManager + 好感度联动定价）
- [ ] 装备系统（EquipManager）
- [ ] 背包结构化（InventoryManager）
- [ ] 升级系统完善

---

## 4.5 废弃旧路径清理（阶段 3 遗留）

阶段 3 标记了 deprecated 但保留了代码（因调用方仍活跃）。阶段 4 迁移调用方后删除：

### 旧路径删除清单

| 旧路径 | 位置 | 替代方案 | 阻塞项 |
|--------|------|---------|--------|
| AdminCoordinator.advance_time | admin_coordinator.py | SessionRuntime.advance_time() | REST 端点迁移 |
| AdminCoordinator.enter/leave_sub_location | admin_coordinator.py | IntentExecutor + SessionRuntime | REST 端点迁移 |
| WorldRuntime.advance_time | world_runtime.py | SessionRuntime.advance_time() | AdminCoordinator 解耦 |
| WorldRuntime.enter/leave_sub_location | world_runtime.py | IntentExecutor + SessionRuntime | AdminCoordinator 解耦 |
| MCP heal/damage/add_xp/set_hp | character_tools.py | SessionRuntime.heal/damage/add_xp() | MCP Server 持有 Session 引用 |
| MCP add/remove_item | inventory_tools.py | SessionRuntime.add/remove_item() | 同上 |
| MCP update_disposition | graph_tools.py | SessionRuntime.update_disposition() | 同上 |
| MCP advance_time | time_tools.py | SessionRuntime.advance_time() | 同上 |
| MCP trigger_event | narrative_tools.py | SessionRuntime.activate_event() | D12 旧事件系统融合 |
| FlashCPU TRIGGER_NARRATIVE_EVENT | flash_cpu_service.py | SessionRuntime.activate_event() | D12 旧事件系统融合 |
| REST trigger-event 端点 | game_v2.py | SessionRuntime.activate_event() | D12 旧事件系统融合 |
| 队友好感度直写 | teammate_agentic_tools.py | SessionRuntime.update_disposition() | 队友系统接入 SessionRuntime |

### 删除策略

1. **D12 融合后**：删除 trigger_event 相关 3 条旧路径（MCP/FlashCPU/REST）
2. **D11 落地后**：队友好感度改走 SessionRuntime
3. **MCP Server 改造后**：删除所有 MCP 直写工具（或改为调用 SessionRuntime）
4. **REST 端点改造后**：删除 AdminCoordinator/WorldRuntime 旁路方法

---

## 验证标准

- [ ] 所有 Agent 通过沉浸式接口与世界交互
- [ ] RoleRegistry 生效（不同角色看到不同工具集）
- [ ] SceneBus 成员模型工作正常（接触制 + 场景切换清空）
- [ ] 私聊记录在 SessionHistory 中可查
- [ ] 回合时间系统正确推进
- [ ] `pytest tests/ -v` 全量通过

---

## 变更日志

| 日期 | 操作 |
|------|------|
| 2026-02-20 | 创建骨架 |
| 2026-02-20 | 重写：加入架构决策（D5-D11）、预期体验、沉浸式接口设计、SceneBus 成员模型、翻译层 |
| 2026-02-21 | 补充 D11（私聊影响全局状态）+ 新增 D12（trigger_event 旧路径融合），均从阶段 3 推迟而来 |
| 2026-02-21 | 重写 4.5：详细列出阶段 3 遗留的废弃路径删除清单（12 条）+ 删除策略 |
