# 前端 (Frontend)

> 路径：`frontend/src/`
> 技术栈：React + TypeScript + Zustand + Tailwind CSS + SSE 实时通信

---

## 一、整体结构

```
frontend/src/
├── pages/
│   └── GamePage.tsx         — 主游戏页面（路由入口）
├── game/
│   ├── DialogueArea.tsx     — 对话历史展示
│   ├── OptionPanel.tsx      — 玩家行动选项
│   ├── QuickBar.tsx         — 快捷操作栏
│   ├── PlayerHud.tsx        — 玩家状态（HP/金/时间）
│   ├── SceneBackground.tsx  — 动态背景图
│   ├── NotificationLayer.tsx — Toast 通知
│   ├── overlays/
│   │   ├── CharacterPanel.tsx   — 角色面板
│   │   ├── InventoryPanel.tsx   — 背包面板
│   │   ├── MenuOverlay.tsx      — 菜单
│   │   └── QuestPanel.tsx       — 任务面板
│   └── vn/                  — 视觉小说模式组件
├── hooks/
│   └── useGameStream.ts     — SSE 网络层 + 事件分发（唯一网络入口）
├── stores/
│   ├── playerStore.ts       — 玩家状态
│   ├── dialogueStore.ts     — 对话历史
│   ├── sceneStore.ts        — 场景/地点
│   ├── optionStore.ts       — 行动选项
│   ├── notificationStore.ts — 通知
│   ├── combatStore.ts       — 战斗状态
│   ├── vnStore.ts           — 视觉小说消息
│   ├── sessionStore.ts      — 会话元数据
│   ├── partyStore.ts        — 队伍成员
│   ├── overlayStore.ts      — UI overlay 显隐
│   ├── audioStore.ts        — 音频状态
│   └── streamStore.ts       — SSE 流状态
├── types/
│   ├── sse.ts               — 40+ SSE 事件接口定义
│   ├── game.ts              — 游戏状态类型
│   └── api.ts               — API 请求/响应接口
├── lib/
│   └── sse-client.ts        — SSE 流式 HTTP 客户端
└── styles/
    └── globals.css
```

---

## 二、SSE 客户端（`sse-client.ts`）

### streamSSE()

```typescript
async function streamSSE(
    url: string,
    body: object,
    onEvent: (event: SSEEvent) => void,
    onDone: () => void,
    onError: (err: Error) => void,
    signal: AbortSignal,
): Promise<void>
```

### SSE 解析逻辑

```typescript
// POST 请求 + ReadableStream 逐行解析
while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    for (const line of lines) {
        if (line.startsWith('event: ')) currentEvent = line.slice(7).trim()
        else if (line.startsWith('data: ')) currentData = line.slice(6)
        else if (line === '') {        // 空行 = 事件分隔符
            if (currentEvent && currentData) {
                let parsed = currentData
                try { parsed = JSON.parse(currentData) } catch {}
                onEvent({ event: currentEvent, data: parsed })
            }
            currentEvent = ''
            currentData = ''
        }
    }
}
```

**错误处理**：
- HTTP 非 200 → `extractErrorMessage()` → `onError`
- `AbortError` → 静默返回（用户主动取消）
- 解析失败 → `onError(Error)`

---

## 三、useGameStream — 唯一网络层

```typescript
function useGameStream(
    worldId: string,
    sessionId: string,
    phase: GamePhase,
    // ...action trigger params
): void
```

**职责**：只做网络 I/O + 分发到各 store，不持有业务状态。

### 事件分发（核心逻辑）

```typescript
const handleEvent = (event: SSEEvent) => {
    const data = event.data as any

    switch (event.event) {
        // 叙事类
        case 'gm_narration':      dialogueStore.addMessage({ type: 'gm', content: data.content }); break
        case 'npc_response':      dialogueStore.addMessage({ type: 'npc', ...data }); break
        case 'teammate_response': dialogueStore.addMessage({ type: 'teammate', ...data }); break
        case 'text_chunk':        dialogueStore.appendStreamChunk(data.text); break

        // 选项类
        case 'dialogue_options':  optionStore.setOptions(data.options); break

        // 场景类
        case 'scene_change':      sceneStore.updateScene(data); playerStore.updateFromStatus(data); break
        case 'discovery_reveal':  notificationStore.push({ type: 'discovery', ...data }); break

        // 战斗类
        case 'combat_start':      sceneStore.setCombat(data); break
        case 'combat_update':     sceneStore.updateCombat(data); break
        case 'combat_end':        sceneStore.clearCombat(); notificationStore.push(...); break
        case 'dice_roll':         sceneStore.addDiceRoll(data); break

        // 社交类
        case 'relationship_stage_changed': notificationStore.push({ type: 'relation', ...data }); break
        case 'npc_wants_to_chat':          notificationStore.push({ type: 'chat_request', ...data }); break
        case 'campfire_dialogue':          dialogueStore.addMessage({ type: 'campfire', ...data }); break

        // 系统类
        case 'time_advanced':     playerStore.updateTime(data); break
        case 'stream_end':        streamStore.setStreaming(false); break
        case 'stream_error':      handleStreamError(data); break
        // ... 100+ handlers
    }
}
```

**生命周期**：
```typescript
useEffect(() => {
    const controller = new AbortController()
    streamSSE(url, body, handleEvent, onDone, onError, controller.signal)
    return () => controller.abort()   // 清理：取消流
}, [worldId, sessionId, phase])
```

---

## 四、Zustand Stores（12个）

### playerStore

```typescript
interface PlayerState {
    name: string
    characterClass: string
    level: number
    hp: number
    maxHp: number
    gold: number
    asiAvailable: boolean
    area: string
    location: string | null
    day: number
    slot: number
    period: string            // "dawn"|"day"|"dusk"|"night"
}

// 关键方法
updateFromPanel(data)          // 全量更新（角色面板加载时）
updateFromStatus(data)         // 增量更新 hp/gold/time（SSE 事件）
updateTime({ day, slot, period })
```

### dialogueStore

```typescript
interface DialogueState {
    messages: DialogueEntry[]     // 当前场景（滚动缓冲，max 150）
    log: DialogueEntry[]          // 完整历史
    pendingStreamId: string | null
}

// 关键方法
addMessage(entry)                 // 自动生成 id + timestamp，超限裁剪
appendStreamChunk(text)           // token-by-token 追加到 pendingStream
resolveStreamMessage(entry)       // pending → 完整条目替换
clearForSceneChange()             // 清空 messages（不清 log）
batchAddToLog(entries)            // VN 模式消息批量写入 log
```

### sceneStore

```typescript
interface SceneState {
    locationId: string | null
    locationName: string
    backgroundUrl: string
    npcs: PresentNpc[]
    subLocations: SubLocation[]
    interactables: Interactable[]
    exits: Exit[]
    combat: CombatState | null
    diceRolls: DiceRollEntry[]
}
```

### optionStore

```typescript
interface OptionState {
    options: DialogueOption[]      // 当前可选行动
    isLoading: boolean
}

setOptions(options)
clearOptions()
```

### combatStore

```typescript
interface CombatState {
    active: boolean
    subAreaId: string
    round: number
    participants: CombatParticipant[]
    playerUnit: CombatUnit
    surpriseState: string
}
```

### streamStore

```typescript
interface StreamState {
    isStreaming: boolean
    lastError: string | null
}
```

### 其他 Store

| Store | 主要状态 |
|-------|---------|
| **notificationStore** | Toast 通知队列 |
| **vnStore** | 视觉小说模式消息列表 |
| **sessionStore** | world_id, session_id, phase |
| **partyStore** | `members: PartyMember[]` |
| **overlayStore** | `{ character, inventory, quest, menu }: boolean` |
| **audioStore** | `{ bgm, sfx, ambience }` |

---

## 五、类型系统

### SSE 事件类型（`sse.ts`，40+ 接口）

**叙事类**：
```typescript
interface GmNarrationData { content: string }
interface NpcResponseData { npc_id: string; content: string; type: "speech"|"refuse"|"emote" }
interface TeammateResponseData { character_id: string; content?: string; type?: "speech"|"emote" }
interface TextChunkData { text: string }  // 流式 token
```

**交互类**：
```typescript
interface DialogueOptionsData {
    npc_id?: string
    options: DialogueOptionItem[]   // { id, text, functional_type?, dc?, tags? }
}
interface TalkSnapshotData { target_id: string; profile?: {...}; available_intents?: string[] }
interface ShopSnapshotData { npc_id: string; player_gold: number; stock: [...]; player_sellable_items: [...] }
```

**场景类**：
```typescript
interface SceneChangeData {
    location_id: string | null
    location_name: string
    background: string
    transition: string
    ambient_preset: string | null
    background_url?: string
}
interface DiscoveryRevealData { name: string; description?: string }
```

**时间/状态类**：
```typescript
interface TimeAdvancedData { day: number; slot: number; period: string; rest_info?: {...} }
interface StatusUpdateData { kind?: string; target_id?: string; hp_delta?: number }
```

**社交类**：
```typescript
interface RelationshipStageChangedData { npc_id: string; npc_name?: string; new_stage: string; old_stage?: string }
interface NpcWantsToChatData { npc_id: string; npc_name?: string }
interface CampfireDialogueData { teammate_id: string; content: string; memory_type?: string }
interface CompanionRecruitedData { npc_id: string; reason: string; party_members: string[] }
interface CompanionDismissedData { npc_id: string; reason: string; party_members: string[] }
```

**战斗类**：
```typescript
interface EncounterSpottedData {
    sub_area_id: string
    name: string
    threat_level: "easy"|"moderate"|"hard"|"deadly"
}
interface CombatStartData {
    sub_area_id: string; round: number; surprise_state: string
    participants: CombatParticipant[]; player: CombatUnit
}
interface CombatUpdateData { sub_area_id: string; round: number; participants: [...]; combat_cleared: boolean }
interface CombatEndData { result: "victory"|"defeat"|"fled"; xp_gained: number; gold_gained: number }
interface DiceRollData { type: string; result: number; total: number; dc: number; passed: boolean }
```

**任务类**：
```typescript
interface QuestBriefData { target_kind: string; target_id: string; quest: {...} }
interface MilestoneUnlockedData { milestone_id: string; unlocked_by: string }
interface MilestoneFailedData { milestone_id: string; failure_fallback: string }
interface QuestObjectiveUpdatedData { quest_id: string; objective_id: string; completed: boolean }
```

**系统类**：
```typescript
interface StreamEndData { reason: string; completed: boolean }
interface StreamErrorData { message: string; code?: string }
interface ActionResultData { executed: boolean; action_type: string; time_cost: number; errors: string[] }
interface InputRejectedData { text: string; code: string; message: string }
interface InteractionRejectedData { target_kind?: string; target_id?: string; code: string; message: string }
```

### 游戏状态类型（`game.ts`）

```typescript
interface PresentNpc { id: string; name: string; tags: string[]; location: string | null }
interface LocationOverview { area_id: string; location_id: string | null; npcs: PresentNpc[]; sub_locations: SubLocation[]; exits: Exit[] }
type GamePhase = "character_creation" | "gameplay" | "combat" | "dialogue" | "private_chat"
interface DialogueEntry { id: string; type: "gm"|"npc"|"teammate"|"campfire"|"system"; content: string; timestamp: number }
```

---

## 六、设计模式

| 模式 | 说明 |
|------|------|
| **唯一网络层** | `useGameStream` 是唯一 HTTP/SSE 入口，stores 是纯 reducer，无网络调用 |
| **AbortController 清理** | 组件卸载时 `controller.abort()` 取消流，无内存泄漏 |
| **消息缓冲 + 历史分离** | `messages`（当前场景，max 150）vs `log`（完整历史），场景切换清空前者 |
| **流式 token 追加** | `appendStreamChunk` → `pendingStream`，`resolveStreamMessage` 替换为完整条目 |
| **100+ SSE 事件类型** | TypeScript 接口严格类型化每种事件，switch-case 精确分发 |
| **Overlay 状态中心化** | `overlayStore` 统一管理所有 panel 开关，避免组件内 local state 冲突 |
