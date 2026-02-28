# ❸ 状态层施工记录

**设计文档**：状态层设计规范（已读）.md
**代码路径**：`app/game_core/state/`
**Phase**：0B（基类）+ 3（具体 Slice）

## 模块状态

| 组件 | 状态 | 说明 |
|------|------|------|
| `StateSlice` ABC | [完成] | restore/serialize/snapshot/dirty 完整 |
| `StateContainer` | [完成] | typed properties + apply(delta) |
| `StateDelta` / `StateChange` | [完成] | changes + reason + metadata |
| `SupportsStateChange` Protocol | [完成] | runtime_checkable，所有 Slice 隐式满足 |
| `TimeSlice` | [完成] | day/slot/period + 时段映射 + apply_state_change |
| `PlayerSlice` | [完成] | 属性/背包/装备/法术/效果/职业资源 完整 |
| `RelationSlice` | [完成] | dispositions/stages/factions/impressions/shops |
| `QuestSlice` | [完成] | MilestoneState dataclass + dynamic_quests 双区模型 |
| `FlagSlice` | [完成] | 通用 key-value |
| `AreaSlice` | [完成] | 探索/危险/容器/敌对区/临时子区域 |
| `EventSlice` | [完成] | active/pending/rumors + pop_due_pending |
| `PartySlice` | [完成] | members/approval/shared_experiences |
| `SceneSlice` | [完成] | entries + state_changes + visibility 过滤 |
| `NarrativePlanSlice` | [完成] | 规划元数据 + 行为窗口 |

## 决策记录

### [D-S01] typed properties via TYPE_CHECKING + cast

见 `骨架搭建.md` [D-002]。避免循环导入，运行时零开销。

### [D-S02] SupportsStateChange 用 Protocol 而非继承

`internal.py` 中定义 `@runtime_checkable class SupportsStateChange(Protocol)`。
所有 Slice 通过结构子类型自动匹配，无需显式继承。
好处：StateSlice ABC 保持精简，apply_state_change 不污染公共接口。

### [D-S03] QuestSlice MilestoneState dataclass

见 `骨架搭建.md` [D-004]。
- `MilestoneState(state, activated_tick, completed_tick)`
- `from_dict()` 兼容 str 和 dict 两种输入
- `advance_milestone()` 自动记录 ACTIVE/COMPLETED 时间戳

### [D-S04] NarrativePlanSlice 额外字段保留

`play_style_tags`、`behavior_window`、`ticks_since_milestone_progress`、`next_scheduled_tick`
来源于叙事规划子系统设计规范的更详细设计。保留但标记为"来自子系统规范"。

### [D-S05] SceneEntry visibility 三值模型

```
public  → 所有人可见
system  → 仅引擎内部（GM/NPC/队友 Agent 不可见）
private → 按 audience 列表精确控制
```

`get_for_character()` 过滤逻辑：排除 system，private 需 audience 包含该角色。

## 接口变更

### [I-S01] milestone_states 类型变更

- **Before**：`dict[str, str]`（milestone_id → 状态字符串）
- **After**：`dict[str, MilestoneState]`（milestone_id → 结构化状态）
- **序列化兼容**：snapshot 输出 dict，restore 接受 str 或 dict

## 填充 TODO

- [ ] 各 Slice 的 `validate()` 实现（当前全返空列表）
- [ ] PlayerSlice：等级提升阈值表（当前简化为 `level * 1000`）
- [ ] AreaSlice：临时子区域过期清理逻辑
- [ ] EventSlice：6 状态事件状态机完整转换校验
