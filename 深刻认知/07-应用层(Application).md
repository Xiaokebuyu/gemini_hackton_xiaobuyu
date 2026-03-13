# 应用层 (Application Layer)

> 路径：`backend/app/`（非 game_core 部分）
> 职责：依赖注入、HTTP 路由、LLM 适配、知识图谱、因果评估、视图构建。

---

## 一、整体结构

```
app/
├── main.py                 — FastAPI 应用入口 + lifespan
├── deps.py                 — 依赖注入根（组合根）
├── llm_gemini.py           — GeminiLlmAdapter（LLM 适配器）
├── world_knowledge_graph.py — WorldKnowledgeGraph（NetworkX + BFS）
├── evaluators.py           — GeminiAIOsirisProvider（因果评估）
├── agent_orchestration.py  — AgentOrchestrationService（三角色编排）
├── narrators.py            — AgenticGmNarrator（结算叙事）
├── memory_retriever_impl.py — KnowledgeGraphMemoryRetriever
├── interaction_service.py  — InteractionService（前置校验 + 视图）
├── interaction_views.py    — 交互视图构建（NPC/商店/任务快照）
├── scene_views.py          — 场景视图（位置总览/场景切换）
├── quest_views.py          — 任务视图
├── api_models.py           — Pydantic 请求/响应模型
└── routers/
    ├── sessions.py   — 会话管理
    ├── character.py  — 角色创建
    ├── gameplay.py   — 核心游戏玩法（SSE 流式）
    ├── combat.py     — 战斗
    ├── panels.py     — UI 面板
    └── images.py     — 资源图片
```

---

## 二、deps.py — 依赖注入根

### 运行时构建（`_build_game_runtime()`）

```python
# 1. LLM 检测（懒加载）
api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
llm_provider = GeminiLlmAdapter() if api_key else None

# 2. 知识图谱（无论有无 LLM 都启用）
world_knowledge_graph = WorldKnowledgeGraph(llm=llm_provider)
memory_retriever = KnowledgeGraphMemoryRetriever(world_knowledge_graph)
instance_manager = InstanceManager()

# 3. Agent 编排（仅有 LLM 时）
if llm_provider:
    agent_orchestration = AgentOrchestrationService(
        executor=AgenticExecutor(tool_registry, llm_provider),
        memory_retriever=memory_retriever,
        instance_manager=instance_manager,
    )

# 4. 注入工厂（函数引用，按需调用）
osiris_factory      = _build_osiris           # temperature=0.2, thinking_level="medium"
gm_narrator_factory = _build_gm_narrator      # per-world-per-session
planner_factory     = _build_planner_system   # 6 个 AgenticNarrativePlanner 子代理
                    / _build_fallback_planner  # 无 LLM 时确定性 fallback
```

### 全局 Getter

```python
get_game_runtime()       → GameRuntime         # 单例
get_llm_provider()       → LlmPort | None
get_agent_orchestration() → AgentOrchestrationService | None
get_interaction_service() → InteractionService  # 懒创建（首次请求时）
```

### lifespan 启动钩子

```python
@asynccontextmanager
async def _lifespan(app):
    runtime = _build_game_runtime()
    app.state.game_runtime = runtime
    app.state.admin_coordinator = AdminCoordinator(runtime)
    yield
```

---

## 三、GeminiLlmAdapter — LLM 适配器

```python
class GeminiLlmAdapter:
    def __init__(
        model: str = "gemini-3-flash-preview",
        temperature: float = 1.0,
        thinking_level: str = "low",   # "low"|"medium"|"high"
        profile_name: str = "default",
    )

    # 普通调用（含工具调用）
    async generate(
        history: list[dict],
        tool_declarations: list[dict] | None,
        system_prompt: str,
    ) → LlmResponse(text, tool_calls, finish_reason, metadata, raw_model_parts)

    # 流式调用（纯文本，禁用工具）
    async generate_stream(
        history: list[dict],
        system_prompt: str,
    ) → AsyncIterator[str]
```

**关键实现**：
- `_to_content()` — history entry → Gemini Content（支持 text/function_call/function_response/thought_signature）
- `_parse_response()` — 提取 text parts + tool_calls，保留 `raw_model_parts` 维持多轮思维签名
- `tool_config=NONE` 强制流式模式不输出工具调用

---

## 四、WorldKnowledgeGraph — 知识图谱

### 架构

```python
class WorldKnowledgeGraph:
    _graph: nx.DiGraph              # 全局图（静态内容）
    _actor_graphs: dict[str, nx.DiGraph]  # 每 NPC 的私有记忆图
    _seeded_worlds: set[str]        # 已 seed 的 world_id（幂等）
    _enriched_worlds: set[str]      # 已 lore 丰富的 world_id
```

### 节点类型（EdgeType）

```
character, faction, area, location, item, monster, skill,
milestone, lore_concept, memory_note
```

### 边关系类型

**静态（内容层 seed）**：
`LOCATED_IN`, `BELONGS_TO`, `HAS_CLASS`, `CARRIES`, `SELLS`, `FACTION_REL`, `DROPS`, `ADJACENT_TO`, `CONTAINS`, `REQUIRES`, `LEADS_TO`

**动态（对话提取，Phase 3b）**：
`KNOWS_ABOUT`, `INTERACTED_WITH`, `MADE_PROMISE`, `RELATED_TO`, `HAS_OPINION_OF`

### 惰性 Seed（`ensure_seeded(world)`）

```
加载顺序：items → skills → factions → areas → characters → monsters → quests
每个 world_id 只 seed 一次（幂等）
```

### BFS 扩散激活查询

```python
async query_spread(
    actor_id: str,
    keywords: list[str],
    context: dict,
    max_depth: int = 2,
    decay: float = 0.8,
    top_k: int = 10,
) → list[dict]

# 流程：
# 1. 找匹配关键词的种子节点（id/label/description/tags 大小写不敏感）
# 2. BFS：每个节点 activation 从 1.0 开始
#         neighbors → activation × decay × edge_weight
# 3. 返回 top_k（降序），排除种子节点（除 memory 节点）
```

### LLM 三元组提取（Phase 3b）

```python
write_episode(actor_id, messages, context)
# - ContextWindow 溢出时调用
# - 格式化对话消息 → RECORD_TRIPLE_TOOL
# - LLM 输出 [subject, relation, object, weight] 元组
# - 写入 actor 私有图

ensure_lore_enriched(world)
# - 收集 lore + 角色描述（最多10条）
# - 单次 LLM 批量提取三元组
# - 写入全局图（幂等）
```

### Actor 私有记忆

```python
async remember(actor_id, knowledge, context)
# - 创建 memory 节点：memory:{actor_id}:{counter}
# - 添加 KNOWS_ABOUT 边
# - 递增 memory counter
```

---

## 五、GeminiAIOsirisProvider — 因果评估

```python
class GeminiAIOsirisProvider:  # 别名 AgenticAIOsirisEvaluator
    # 参数：temperature=0.2, thinking_level="medium"（独立 LLM 实例）

    async evaluate(
        summary: dict,       # 当前 tick 摘要
        snapshot: dict,      # 状态快照
        rules_context: dict, # 世界规则上下文
    ) → AIOsirisDecision
```

### Osiris 系统 Prompt 核心约束

- 分析单时段内的事件涟漪后果
- 输出跨系统边界的后果（不直接改 HP/gold/inventory）
- `modify_disposition` 只对 `present_character_ids` 中的 NPC 操作
- 按 tick 类型（旅行/休息/对话/战斗结算）给出不同后果模式
- 响应语言匹配输入语言

### 工具：SUBMIT_CONSEQUENCES_TOOL

```json
{
  "name": "submit_consequences",
  "parameters": {
    "reasoning": "string",
    "visible_change": "boolean",
    "consequences": [{
      "type": "string",         // 命令类型
      "params": "object",       // 命令参数
      "reason": "string",       // 可选
      "visibility_hint": "string",
      "confidence": "string"
    }]
  }
}
```

---

## 六、SSE 端到端流式

### 后端：SSEPresentationPort（内存缓冲）

```python
class SSEPresentationPort:
    _buffer: list[str] = []

    async def publish(event) → None
        # 格式化为 "event: {type}\ndata: {json}\n\n"
        # 追加到 _buffer

    def drain() → list[str]
        # 返回并清空 _buffer
```

### 后端：Queue 驱动的流式响应

```python
async def _stream_with_lock(world_id, session_id, execute_fn):
    async with session.runtime.session_lock():     # 每会话 asyncio.Lock
        queue = asyncio.Queue[SSEEvent | None]()

        # 后台任务：执行游戏逻辑，事件入队
        async def background():
            result = await execute_fn(
                session,
                event_sink=lambda evt: queue.put(evt),
            )
            await queue.put(None)  # 完成信号

        asyncio.create_task(background())

        # 前台：排空队列，yield SSE chunks
        while True:
            evt = await queue.get()
            if evt is None:
                break
            for chunk in sse_port.drain():
                yield chunk

        yield stream_end_event
```

---

## 七、API 端点

### Sessions（`routers/sessions.py`）

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/api/game/worlds` | 世界列表 |
| GET | `/api/game/{world_id}/sessions` | 会话列表 |
| POST | `/api/game/{world_id}/sessions` | 创建会话 |
| POST | `/api/game/{world_id}/sessions/{sid}/resume` | 恢复会话 |
| DELETE | `/api/game/{world_id}/sessions/{sid}` | 删除会话 |

### Gameplay（`routers/gameplay.py`，600+ 行）

| 方法 | 路径 | 请求体 | 流式 |
|------|------|--------|:----:|
| POST | `.../act` | `StructuredActionRequest{action_type, params, source}` | ✓ |
| POST | `.../navigate` | `NavigateRequest{target_area_id}` | ✓ |
| POST | `.../interact` | `InteractRequest{intent, target_kind, target_id, item_id, quest_id}` | ✓ |
| POST | `.../private_chat` | `PrivateChatRequest{target_id, utterance}` | ✓ |
| POST | `.../text_input` | `TextInputRequest{text}` | ✓ |
| GET | `.../scene` | — | ✗ |
| GET | `.../opening` | — | ✗ |

### Combat（`routers/combat.py`）

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `.../encounter` | 遭遇选择（战/逃/偷袭）→ SSE |
| POST | `.../combat` | 战斗行动 → SSE |
| GET | `.../combat` | 当前战斗状态 |

---

## 八、InteractionService

```python
class InteractionViewContext:  # frozen dataclass
    current_area: str
    current_location: str | None
    npc_positions: dict[str, tuple[str|None, str|None]]
    npc_names / npc_tags / npc_dispositions / npc_impressions: dict
    relationship_stages: dict[str, str]
    shop_states / dynamic_quest_views / milestone_states: dict
    board_quest_metadata: dict
    current_day / current_slot: int
    current_period: str
    player_gold: int
    player_inventory: list[dict]
    item_catalog: dict[str, dict]

# 构建函数（纯数据提取，无副作用）
build_interaction_view_context(state, world) → InteractionViewContext
```

**`InteractionService`** 职责：
1. NPC/公告栏 在场校验
2. 前置条件校验（意图/物品/任务）
3. 构建视图快照（商店/对话/任务）
4. 调用 tick_coordinator 执行

---

## 九、设计模式

| 模式 | 说明 |
|------|------|
| **工厂函数引用** | 所有 LLM 组件以函数引用注入，按需延迟实例化 |
| **条件组装** | 有 LLM key → 完整 agentic；无 key → 确定性降级，零报错 |
| **Per-session Lock** | `asyncio.Lock` 防止并发修改，SSE 期间持有锁 |
| **Queue 分离** | 后台任务生成事件 + 前台 generator 排空，避免 HTTP timeout |
| **幂等 Seed** | WorldKnowledgeGraph 每个 world_id 只 seed/enrich 一次 |
| **独立 Osiris LLM** | AIOsiris 用独立低温（0.2）中等推理实例，与叙事 LLM 参数分离 |
