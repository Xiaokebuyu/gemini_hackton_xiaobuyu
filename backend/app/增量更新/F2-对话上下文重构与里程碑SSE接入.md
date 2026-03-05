# F2 — 对话上下文重构与里程碑 SSE 接入

日期：2026-03-05
性质：架构修正（替换 F1 的表面补丁，对齐设计文档）

---

## 背景

F1 轮采用了若干临时手段解决实测问题，但深度调查后发现与设计文档存在偏差：

| F1 的做法 | 问题 |
|-----------|------|
| `optionStore.talkTargetNpcId` 追踪对话 NPC | 对话状态不该挂在选项 store 上 |
| `onLeaveDialogue` prop 从 GamePage→DialogueArea→OptionPanel 层层传递 | 不必要的 prop drilling |
| `buildTalkPrompt()` 硬编码 `"你好。"` 作为开场白 | 每次进入对话 LLM 都收到无意义的固定消息 |
| "继续交谈"/"打个招呼" 按钮发送硬编码消息 | 绕过了玩家真实意图 |
| `location_overview` 只检查 `hasDialogueOptions` 决定是否覆盖选项 | 对话上下文中 overview 可能会错误覆盖选项 |
| 里程碑 SSE 事件 (`milestone_unlocked`/`milestone_failed`) 完全未处理 | 进入 `default:` 被静默忽略 |

---

## 修改总览

| # | 改动 | 影响文件 |
|---|------|----------|
| A | `sceneStore` 新增 `activeNpcId` + `setActiveNpc()` | `sceneStore.ts` |
| B | `optionStore` 回滚 F1 的 `talkTargetNpcId`，恢复 2 参数签名 | `optionStore.ts` |
| C | `useGameStream` 重构 talk_snapshot/dialogue_options/location_overview/scene_change handler；删除 buildTalkPrompt；新增 milestone handler | `useGameStream.ts` |
| D | `OptionPanel` 改从 sceneStore 读 `activeNpcId`，`handleLeaveDialogue` 内联为局部函数 | `OptionPanel.tsx` |
| E | `DialogueArea` 移除 `onLeaveDialogue` prop，新增 `overviewHandlers` prop | `DialogueArea.tsx` |
| F | `GamePage` 移除 `handleLeaveDialogue`/`useCallback`，`DialogueArea` 传入 `overviewHandlers` | `GamePage.tsx` |
| G | `sse.ts` 新增 `MilestoneUnlockedData`/`MilestoneFailedData` 类型 | `types/sse.ts` |

---

## 详细改动记录

### A. sceneStore — 对话上下文锚点

新增字段和方法：
- `activeNpcId: string | null` — 当前对话 NPC ID，作为对话上下文的唯一真相源
- `setActiveNpc(npcId: string | null)` — setter

**设计原则**：`activeNpcId` 是路由标志，不是"对话模式"标志。游戏本身没有对话模式（见设计文档），对话在统一的视觉小说界面中进行。

### B. optionStore — 回滚 F1 改动

移除：
- `talkTargetNpcId: string | null` 字段
- `setOptions` 第 3 参数 `talkTargetNpcId`
- `setFromDialogueOptions` 第 3 参数 `npcId`
- 所有 `talkTargetNpcId: null` 清零操作

`setOptions` / `setFromDialogueOptions` / `buildFromOverview` / `clearOptions` 回归简洁签名。

### C. useGameStream — 核心重构

**删除** `buildTalkPrompt()`（硬编码开场白）

**`talk_snapshot` handler**：
- 新增：`scene.setActiveNpc(npcId)` — 进入对话上下文
- 移除："继续交谈"/"打个招呼" 按钮（发送硬编码消息）
- 保留："看看货物" 按钮（browse intent，有实际功能意义）
- `setOptions(nextOptions, true)` 标记对话上下文已激活（`hasDialogueOptions=true`）

**`dialogue_options` handler**：
- 移除：`resolvedNpcId` 计算和传入 `setFromDialogueOptions` 第 3 参数
- 改为：`const activeNpcId = useSceneStore.getState().activeNpcId` 在 callback 内按需读取

**`scene_change` handler**：
- 新增：`scene.setActiveNpc(null)` — 场景转换清除对话上下文

**`location_overview` handler**：
- 从 `if (!hasDialogueOptions)` 改为 `if (!hasDialogueOptions && !sceneState.activeNpcId)`
- 确保在对话上下文中（即使 dialogue_options 还未到来），overview 不会覆盖选项

**新增 milestone handler**：
```typescript
case 'milestone_unlocked': → 通知"里程碑解锁：{id}"
case 'milestone_failed':   → 通知"里程碑失败：{id}"
```

### D. OptionPanel — 消费 sceneStore.activeNpcId

- 新增 `overviewHandlers: OverviewHandlers` prop（用于结束对话时重建选项）
- `activeNpcId` 从 `useSceneStore` 读取，不再从 `optionStore`
- `handleLeaveDialogue` 内联为局部函数：
  1. `sceneStore.setActiveNpc(null)` — 清除对话上下文
  2. `optStore.clearOptions()` — 清空选项
  3. `optStore.buildFromOverview(lastOverview, overviewHandlers)` — 从最近 overview 重建
- "结束对话" 按钮：当 `activeNpcId` 非空时显示（无论 options 列表是否为空）
- 自由输入路由：`activeNpcId` → `/interact/stream`，否则 → `/input/stream`

### E. DialogueArea — prop 调整

移除：`onLeaveDialogue` prop
新增：`overviewHandlers: OverviewHandlers` prop（透传给 OptionPanel）

### F. GamePage — 清理

移除：
- `handleLeaveDialogue` useCallback（逻辑迁至 OptionPanel）
- `useCallback` import（不再有其他使用点）
- `DialogueArea` 的 `onLeaveDialogue` prop

新增：
- `DialogueArea` 传入 `overviewHandlers`（稳定 ref，无性能影响）

### G. types/sse.ts

新增：
```typescript
interface MilestoneUnlockedData { milestone_id: string; unlocked_by: string }
interface MilestoneFailedData { milestone_id: string; failure_fallback: string }
```

---

## 保留的 F1 改动

| 文件 | 改动 | 理由 |
|------|------|------|
| `frontend/vite.config.ts` | `/static` 代理 | 正确修复，图片必须代理 |
| `backend/app/routers/panels.py` | REST 里程碑充实 title/description | REST 面板端点确实需要内容层数据 |
| `frontend/src/game/overlays/QuestPanel.tsx` | 里程碑渲染 | 正确补齐 |
| `frontend/src/game/Portrait.tsx` | `opacity-100` | 用户明确要求 |

---

## 文件变更清单

| 文件 | 变更类型 |
|------|----------|
| `frontend/src/stores/sceneStore.ts` | 修改（+activeNpcId, +setActiveNpc） |
| `frontend/src/stores/optionStore.ts` | 修改（移除 talkTargetNpcId，恢复 2 参数签名） |
| `frontend/src/hooks/useGameStream.ts` | 修改（删 buildTalkPrompt + 重构 4 个 handler + 新增 milestone） |
| `frontend/src/game/OptionPanel.tsx` | 修改（sceneStore.activeNpcId + 内联 handleLeaveDialogue + overviewHandlers prop） |
| `frontend/src/game/DialogueArea.tsx` | 修改（移除 onLeaveDialogue，新增 overviewHandlers prop） |
| `frontend/src/pages/GamePage.tsx` | 修改（移除 handleLeaveDialogue/useCallback，传 overviewHandlers） |
| `frontend/src/types/sse.ts` | 修改（+MilestoneUnlockedData, +MilestoneFailedData） |

---

## 已知局限

1. **"结束对话"是前端单侧机制**：点击后前端清除对话上下文，但后端不知道玩家离开了，NPC 的 ContextWindow 仍保留历史。设计上应通过后端 `dialogue_options` 中的"告别"选项自然结束。
2. **里程碑通知只显示 ID**：`milestone_id` 是内部标识符，对玩家不友好。后续可从 QuestRegistry 查询 title 后显示。
3. **`/input/stream` 仍只支持命令别名**：非对话上下文的自由文字（"search the room"）仍会被拒绝，InputPort 未扩展。
4. **`llm_gemini.py:132` crash 未修复**：`candidate.content.parts` 为 None 时崩溃，不在本轮范围。
