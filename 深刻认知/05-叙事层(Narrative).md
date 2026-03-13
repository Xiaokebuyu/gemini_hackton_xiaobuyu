# L5 叙事层 (Narrative Layer)

> 路径：`backend/app/game_core/narrative/`（内核）+ `backend/app/agent_orchestration.py` + `backend/app/narrators.py`（应用层）
> 职责：LLM Agent 的接入层——三角色（GM/NPC/Teammate）的工具执行、上下文装配、记忆管理。

---

## 一、整体结构

```
narrative/
├── executor.py          — AgenticExecutor（单次/多轮 LLM 执行）
├── context_builder.py   — AgentContextBuilder（L0-L7 上下文装配，2064行）
├── context_window.py    — ContextWindow（FIFO 工作记忆缓冲）
├── instance_manager.py  — InstanceManager（NPC LRU 实例池）
├── companion_runtime.py — CompanionInstance（同伴 Tick 历史）
├── memory_retriever.py  — MemoryRetriever Protocol（+ NullMemoryRetriever）
├── role_proxy.py        — RoleStateProxy（角色权限读保护）
├── tools.py             — AgentTool 抽象基类
├── registry.py          — RoleToolRegistry（按角色+traits过滤工具）
├── character_tools.py   — NPC/Teammate 工具（8个）
├── gm_tools.py          — GM 工具（5个）
├── planner_tools.py     — Planner 工具（2个）
├── context.py           — AgentContext（执行上下文冻结 dataclass）
├── models.py            — ToolResult / AgentResult
└── __init__.py

app/
├── agent_orchestration.py — AgentOrchestrationService（编排 + SSE 转换）
└── narrators.py           — AgenticGmNarrator（结算叙事）
```

---

## 二、AgenticExecutor — LLM 执行核心

### 两种执行模式

```python
class AgenticExecutor:

    # 模式 1：单次执行（pre-built tool_calls list）
    async run(role, tool_calls, context, *, state, world) → AgentResult

    # 模式 2：多轮 Agent 循环（LLM 自主决策）
    async run_agentic(
        role,                          # "gm" | "npc" | "teammate"
        system_prompt,
        conversation_history,          # 前序对话历史
        context_layers,                # L0-L7 字典
        context,                       # AgentContext
        *,
        text_chunk_sink = None,        # 最终轮流式 chunk 回调
        traits = None,                 # NPC tags → 工具过滤
        max_turns = 5,
    ) → AgentResult
```

### 多轮循环流程

```
Turn N:
  LLM.generate(system_prompt, history, tool_declarations)
    ↓
  Parse tool_calls from response
    ↓
  Protocol validation（NPC/Teammate：max 1 speak/refuse，max 1 emote）
    ↓
  Execute tools via RoleToolRegistry
    ↓
  Append tool results → history
    ↓
  Check finish: 有可见输出(speak/narrate/comment)? → 结束
              : max_turns 耗尽? → 结束
    ↓
  Loop or return AgentResult
```

### AgentResult

```python
@dataclass(slots=True)
class AgentResult:
    text: str = ""
    tool_results: list[ToolResult] = []
    turns_used: int = 0
    metadata: dict = {}
    # metadata.status: "completed"|"protocol_error"|"max_turns_reached"|"no_llm"
    # metadata.finish_reason: "visible_output_emitted"|"pass_turn"|"max_turns"|"text_fallback"
```

### 关键设计

- **无状态设计**：每次调用接受完整 history，不保存循环状态
- **思维签名保留**：`response.raw_model_parts` 保留 Gemini SDK 原生响应结构
- **真流式支持**：最终轮通过 `text_chunk_sink` 回调透传 text_chunk
- **合成语音降级**：NPC 返回纯文本（无 speak tool 调用）时自动包装为合成 ToolResult

---

## 三、L0-L7 上下文层系统

### 各层内容与角色可见性

| 层 | 内容 | GM | NPC | Teammate |
|----|------|:--:|:---:|:--------:|
| **L0** | 世界常量（lore ×3 + factions ×5）| ✓ | ✓ | ✓ |
| **L1** | 章节状态（quests/milestones/escalation）| ✓ | ✗ | 仅可用里程碑 |
| **L2** | 区域环境（地图模板/危险等级/动态子区域数）| ✓ | ✓ | ✓ |
| **L3** | 位置详情（子地点模板/已发现物/可交互物）| ✓ | ✓ | ✓ |
| **L4** | 动态状态：GM全局/NPC自身倾向+关系/Teammate自身+队伍好感 | ✓ | 部分 | 部分 |
| **L5** | 场景总线（最近10条，按 visibility/audience 过滤）| ✓ | 部分 | 部分 |
| **L6** | 记忆召回（MemoryRetriever，关键词提取）| ✗ | ✓ | ✓ |
| **L7** | 叙事提示 + 规则引擎结果 | ✓ | ✗ | ✗ |

**L4/L6 注入位置**：不序列化到用户消息，直接嵌入 **system_prompt**（L4 倾向值，L6 `## Relevant world knowledge`，cap=5）

### 关键方法

```python
class AgentContextBuilder:
    # 单次调用，避免 retriever 被调用两次（N-7 模式）
    async build_npc_full_context(npc_id, memory_retriever) → NpcFullContext
    async build_teammate_full_context(char_id, memory_retriever) → TeammateFull

    @dataclass(slots=True)
    class NpcFullContext:
        system_prompt: str
        layers: dict[str, Any]    # 7层字典

    # 关键词提取（用于 L6 检索）
    _extract_scene_keywords() → list[str]
    # = actor_id + area + location + quest milestones + scene NPC IDs，max 20
```

---

## 四、角色工具集

### NPC / Teammate 工具（8个）— `character_tools.py`

| 工具 | 角色 | traits 限制 | 行为 |
|------|------|------------|------|
| **speak** | NPC + Teammate | — | 写 SceneEntry（tags=["speech"]）|
| **emote** | NPC + Teammate | — | 写 SceneEntry（tags=["emote"]）|
| **update_feeling** | NPC | — | modify_disposition（±50）|
| **remember** | NPC | — | 调用 memory_writer 回调 |
| **offer_quest** | NPC | ["receptionist"] | 任务转为 available |
| **offer_trade** | NPC | ["merchant"] | 触发商店交互 |
| **refuse** | NPC | — | 写拒绝 SceneEntry |
| **express_opinion** | Teammate | — | modify_disposition（±10）|
| **leave_party** | Teammate | — | dismiss_companion |

### GM 工具（5个）— `gm_tools.py`

| 工具 | 行为 |
|------|------|
| **describe_environment** | 只读：读取区域状态/NPC/地图/时间（不改状态）|
| **narrate** | 输出客观场景叙事 → SSEEvent |
| **comment** | 输出 GM 旁白评论 → SSEEvent |
| **suggest_options** | 生成 2-4 个玩家对话选项（含 functional type 注入）|
| **pass_turn** | 跳过本轮（无操作）|

**GM 完全不改状态**，只写 SSE 事件。

### Planner 工具（2个）— `planner_tools.py`

| 工具 | 行为 |
|------|------|
| **read_design_skill** | 读取设计模板 markdown（任务/NPC/遭遇模式）|
| **list_design_skills** | 列举可用设计模板 |

### RoleToolRegistry — 工具过滤

```python
class RoleToolRegistry:
    get_tools_for(role, traits) → list[AgentTool]
    # traits 过滤：tool.applicable_traits 中所有 tag 都必须在 traits 中
    # 例：OfferQuestTool.applicable_traits = ["receptionist"]
    #     → 只有 tags 含 "receptionist" 的 NPC 能调用
```

---

## 五、实例管理

### InstanceManager — NPC LRU 实例池

```python
class NPCInstance:
    actor_id: str
    context_window: ContextWindow     # 工作记忆
    directive_queue: list[dict]       # 规划层指令队列
    last_interaction_tick: int
    interaction_count: int

class InstanceManager:
    _pool: OrderedDict[str, NPCInstance]  # LRU 顺序
    max_instances: int = 200

    get_or_create(actor_id, directives, tick) → NPCInstance
    _evict(actor_id)
        # 驱逐策略：优先驱逐没有待处理指令的 NPC
        # 驱逐时收集未图化消息 → pending_writebacks
    sync_directives(instance, directives, current_tick)
        # 注入活跃指令 + 清理过期/已消费指令
    consume_directive(instance) → dict | None
        # 弹出最高优先级指令
```

### CompanionRuntimeManager — Teammate Tick 历史

```python
class TickRecord:
    tick: int
    action_type: str
    executed: bool
    summary: str
    tags: list[str]         # ["combat","quest","rest","dialogue",...]
    has_rolls: bool
    event_transitions: list[str]

class CompanionInstance:
    actor_id: str
    context_window: ContextWindow
    event_log: list[TickRecord]     # 滑动窗口，max 50

    receive_tick(record)            # 追加 + 强制上限
    get_recent_events(n)
    get_events_by_tag(tag)
```

---

## 六、ContextWindow — 工作记忆

```python
@dataclass
class WindowMessage:
    role: str           # "user" | "assistant" | "system"
    content: str
    token_count: int
    metadata: dict
    is_graphized: bool  # collect_for_graphize() 后标记

class ContextWindow:
    actor_id: str
    max_tokens: int = 32_768           # FIFO 上限
    messages: list[WindowMessage]
    current_tokens: int
    graphize_counter: int              # 自上次图化以来的累计 token
    graphize_threshold: int = 32_768

    # FIFO 驱逐：current_tokens > max_tokens 时弹出最旧消息
    should_graphize → bool             # graphize_counter >= graphize_threshold
    collect_for_graphize() → list[WindowMessage]  # 标记已图化，重置计数器
    export_messages() → dict           # JSON 序列化（含 graphize_counter）
    import_messages(data)              # 向后兼容还原
```

---

## 七、MemoryRetriever Protocol

```python
class MemoryRetriever(Protocol):
    async def retrieve(
        actor_id: str,
        keywords: list[str],         # 场景关键词 + 角色上下文
        context: dict,
    ) → {"hits": list[dict], "source": str}
```

- game_core 内只有 `NullMemoryRetriever`（永远返回空）
- 真实实现在应用层：`app/memory_retriever_impl.py`（NetworkX + BFS 扩散激活）
- L6 hits 注入 system_prompt 的 `## Relevant world knowledge` 节，cap=5

---

## 八、RoleStateProxy — 角色权限保护

```python
class RoleStateProxy:
    # 各角色允许读取的切片
    allowed_slices_npc = {"scene", "relations", "player", "time", "areas"}
    allowed_slices_teammate = {"scene", "relations", "player", "time", "areas", "party"}
    allowed_slices_gm = <all>  # 无限制

    __getattr__  → 检查白名单，不在则 AttributeError
    __setattr__  → 永远 AttributeError（只读代理）
```

NPC 无法访问 `quests` 或 `narrative_plan` 切片，防止 NPC agent 知晓未应揭露的剧情。

---

## 九、应用层集成

### AgentOrchestrationService（`agent_orchestration.py`）

```python
class AgentOrchestrationService:
    async run_npc_interaction(npc_id, player_input, ...) → list[SSEEvent]
    async run_private_chat(npc_id, player_input, ...) → list[SSEEvent]
    async run_gm_reaction(summary, scene_snapshot, ...) → PipelineResult

    # 图化路径
    1. 检查 instance.context_window.should_graphize
    2. collect_for_graphize() → 收集未图化消息
    3. _write_episode(messages) → memory_writer
```

### AgenticGmNarrator（`narrators.py`）

```python
class AgenticGmNarrator:
    # 实现 GmNarrator Protocol（结算叙事）
    async compose(summary: dict, scene_snapshot: dict) → GmNarrationDecision
    # 流程：
    # 1. 构建 AgentContext（scene_entries from snapshot）
    # 2. executor.run_agentic(role="gm", ...)
    # 3. 提取 narration/comment/pass_turn 结果
    # 4. 维护滑动 ContextWindow（跨结算连续性）
    # 5. 达到图化阈值时 graphize episodes
```

---

## 十、GM 系统 Prompt 类型（7种）

| Prompt 常量 | 使用场景 |
|-------------|---------|
| `GM_REACTION_PROMPT` | 结算叙事（简短刻薄的叙述者）|
| `GM_INTERACTION_OBSERVATION_PROMPT` | NPC 对话中 GM 观察 |
| `GM_DIALOGUE_OPTIONS_PROMPT` | 对话选项生成 |
| `GM_OPENING_PROMPT` | 游戏开始叙事 |
| `GM_PRIVATE_CHAT_INTROSPECTIVE_PROMPT` | 私聊静思时刻 |
| `GM_PARTY_CHAT_PROMPT` | 队伍群聊 |
| `TEAMMATE_INTERACTION_PROMPT_TEMPLATE` | 同伴观察 NPC 对话 |

---

## 十一、已知系统断点

| 断点 | 说明 |
|------|------|
| `write_episode` 不自动触发 | graphize_threshold=32K，实际对话永远不达到 |
| `_extract_scene_keywords()` 中文失效 | 当前退化为 ID 查询（无中文分词）|
| `escalate` 指令 | 只修改内部计数器，未走 RulesEngine |
| ContextWindow 不持久化 | 每次会话内存重建 |
