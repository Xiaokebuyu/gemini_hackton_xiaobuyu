# NPC 实例池架构设计计划

## 目标

实现**双层认知系统**的 NPC 实例池，每个 NPC 拥有独立的 Flash + Pro 实例：

- **Pro（工作记忆）**：200K 上下文窗口，处理当前对话
- **Flash（潜意识记忆）**：图谱检索 + 激活扩散，长期记忆存储
- **自动图谱化**：工作记忆满载时，自动转换为图谱节点/边
- **Flash-Pro 通信**：Pro 遇到未知记忆时，向 Flash 请求检索

---

## 架构概览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           NPC Instance Pool                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                    │
│   │ NPC: 女神官  │    │ NPC: 哥布林杀手│    │ NPC: 妖精弓手│    ...          │
│   ├─────────────┤    ├─────────────┤    ├─────────────┤                    │
│   │ Pro (200K)  │    │ Pro (200K)  │    │ Pro (200K)  │                    │
│   │ ↕ 通信      │    │ ↕ 通信      │    │ ↕ 通信      │                    │
│   │ Flash       │    │ Flash       │    │ Flash       │                    │
│   │   └→ Graph  │    │   └→ Graph  │    │   └→ Graph  │                    │
│   └─────────────┘    └─────────────┘    └─────────────┘                    │
│                                                                             │
│   InstanceManager: 生命周期管理、懒加载、LRU 淘汰                            │
│   ContextWindowManager: Token 计数、满载检测、图谱化触发                     │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 核心组件设计

### 1. NPC Instance（NPC 实例）

```python
@dataclass
class NPCInstance:
    """单个 NPC 的完整认知实例"""
    npc_id: str
    world_id: str

    # Pro 层（工作记忆）
    pro: ProService           # 独立的 Pro 实例
    context_window: ContextWindow  # 200K 上下文管理

    # Flash 层（潜意识）
    flash: FlashService       # 独立的 Flash 实例
    memory_graph: MemoryGraph # 角色专属图谱

    # 状态
    is_active: bool           # 是否活跃（在对话中）
    last_access: datetime     # 最后访问时间（LRU）

    # 配置
    config: NPCConfig         # 性格、说话风格等
```

### 2. Instance Manager（实例管理器）

**职责**：
- 懒加载 NPC 实例（首次交互时创建）
- LRU 淘汰策略（内存不足时清理不活跃实例）
- 持久化状态（实例销毁前保存到 Firestore）

```python
class InstanceManager:
    """NPC 实例池管理器"""

    def __init__(self, max_instances: int = 20):
        self.instances: Dict[str, NPCInstance] = {}
        self.max_instances = max_instances
        self.access_order: List[str] = []  # LRU 追踪

    async def get_or_create(self, npc_id: str, world_id: str) -> NPCInstance:
        """获取或创建 NPC 实例（懒加载）"""
        key = f"{world_id}:{npc_id}"

        if key not in self.instances:
            # 检查是否需要淘汰
            if len(self.instances) >= self.max_instances:
                await self._evict_lru()

            # 创建新实例
            self.instances[key] = await self._create_instance(npc_id, world_id)

        # 更新 LRU
        self._touch(key)
        return self.instances[key]

    async def _evict_lru(self):
        """淘汰最久未使用的实例"""
        if not self.access_order:
            return

        lru_key = self.access_order.pop(0)
        instance = self.instances.pop(lru_key, None)
        if instance:
            # 持久化状态后销毁
            await instance.persist()
```

### 3. Context Window Manager（上下文窗口管理器）

**职责**：
- 维护 200K token 窗口
- 实时 token 计数
- 满载检测与图谱化触发

```python
class ContextWindow:
    """Pro 的 200K 上下文窗口"""

    MAX_TOKENS = 200_000
    GRAPHIZE_THRESHOLD = 0.9  # 90% 时触发图谱化

    def __init__(self, npc_id: str):
        self.npc_id = npc_id
        self.messages: List[Message] = []
        self.current_tokens: int = 0
        self.system_prompt_tokens: int = 0

    def add_message(self, message: Message) -> GraphizeTrigger:
        """添加消息，返回是否需要图谱化"""
        msg_tokens = count_tokens(message.content)
        self.messages.append(message)
        self.current_tokens += msg_tokens

        # 检查是否达到阈值
        usage_ratio = self.current_tokens / self.MAX_TOKENS
        if usage_ratio >= self.GRAPHIZE_THRESHOLD:
            return GraphizeTrigger(
                should_graphize=True,
                messages_to_graphize=self._select_old_messages(),
                urgency=usage_ratio
            )

        return GraphizeTrigger(should_graphize=False)

    def _select_old_messages(self) -> List[Message]:
        """选择需要图谱化的旧消息（保留最近的）"""
        # 保留最近 50K tokens，其余图谱化
        keep_tokens = 50_000
        to_graphize = []
        accumulated = 0

        for msg in reversed(self.messages):
            if accumulated >= keep_tokens:
                to_graphize.append(msg)
            accumulated += count_tokens(msg.content)

        return list(reversed(to_graphize))
```

### 4. Memory Graphizer（记忆图谱化器）

**职责**：
- 将对话/事件转换为图谱节点和边
- 使用 Flash 进行结构化提取
- 合并到角色记忆图谱

```python
class MemoryGraphizer:
    """将工作记忆转换为图谱"""

    async def graphize(
        self,
        messages: List[Message],
        flash: FlashService,
        target_graph: MemoryGraph,
        npc_context: NPCContext
    ) -> GraphizeResult:
        """将消息序列图谱化"""

        # 1. 让 Flash 提取结构化数据
        extraction = await flash.extract_graph_elements(
            messages=messages,
            npc_context=npc_context,
            existing_nodes=target_graph.get_important_nodes(limit=50)
        )

        # 2. 合并到图谱（去重、更新）
        merge_result = target_graph.merge(
            nodes=extraction.nodes,
            edges=extraction.edges,
            strategy="update_if_newer"
        )

        # 3. 持久化到 Firestore
        await target_graph.persist()

        return GraphizeResult(
            nodes_added=merge_result.new_nodes,
            nodes_updated=merge_result.updated_nodes,
            edges_added=merge_result.new_edges
        )
```

### 5. Flash-Pro 通信协议

**Pro 请求 Flash 的场景**：
1. 对话中提到未知实体/事件
2. 需要回忆特定记忆
3. 场景进入时预加载相关记忆

```python
class FlashProBridge:
    """Flash 与 Pro 之间的通信桥接"""

    async def pro_requests_memory(
        self,
        flash: FlashService,
        query: str,
        context: ConversationContext
    ) -> MemoryInjection:
        """Pro 向 Flash 请求记忆"""

        # 1. Flash 理解查询意图
        query_understanding = await flash.understand_query(
            query=query,
            context=context
        )

        # 2. 激活扩散检索
        activated_nodes = flash.memory_graph.spread_activation(
            seeds=query_understanding.seed_nodes,
            config=SpreadingConfig(
                max_iterations=3,
                decay=0.6,
                output_threshold=0.15
            )
        )

        # 3. 提取子图
        subgraph = flash.memory_graph.extract_subgraph(activated_nodes)

        # 4. Flash 翻译为自然语言
        memory_text = await flash.translate_memory(
            subgraph=subgraph,
            query_intent=query_understanding.intent,
            npc_personality=flash.npc_config.personality
        )

        return MemoryInjection(
            text=memory_text,
            source_nodes=[n.id for n in subgraph.nodes],
            confidence=query_understanding.confidence
        )
```

---

## 数据流

### 对话流程

```
玩家输入
    │
    ▼
┌──────────────────────────────────────┐
│ InstanceManager.get_or_create(npc)   │
└──────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────┐
│ ContextWindow.add_message(input)     │
│  → 检查是否需要图谱化                 │
│  → 如需要: MemoryGraphizer.graphize()│
└──────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────┐
│ Pro 生成响应                          │
│  → 如遇到未知实体: 调用 Flash         │
│  → FlashProBridge.pro_requests_memory│
│  → 注入检索到的记忆                   │
│  → 继续生成                           │
└──────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────┐
│ ContextWindow.add_message(response)  │
│ 返回响应给玩家                        │
└──────────────────────────────────────┘
```

### 图谱化触发流程

```
ContextWindow 达到 90%
    │
    ▼
┌──────────────────────────────────────┐
│ 选择旧消息（保留最近 50K）            │
└──────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────┐
│ MemoryGraphizer.graphize()           │
│  1. Flash 提取节点/边                 │
│  2. 合并到角色图谱                    │
│  3. 持久化到 Firestore               │
└──────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────┐
│ 从 ContextWindow 移除已图谱化的消息   │
│ 释放 token 空间                       │
└──────────────────────────────────────┘
```

---

## 文件结构

```
app/
├── services/
│   ├── instance_manager.py      # 新增: 实例池管理
│   ├── context_window.py        # 新增: 200K 上下文窗口
│   ├── memory_graphizer.py      # 新增: 记忆图谱化
│   ├── flash_pro_bridge.py      # 新增: Flash-Pro 通信
│   ├── flash_service.py         # 重构: 支持独立实例
│   ├── pro_service.py           # 重构: 支持独立实例
│   ├── memory_graph.py          # 现有: 图谱操作
│   ├── graph_store.py           # 现有: Firestore 持久化
│   └── spreading_activation.py  # 现有: 激活扩散算法
│
├── models/
│   ├── npc_instance.py          # 新增: NPC 实例数据模型
│   ├── context_window.py        # 新增: 窗口状态模型
│   └── graph_elements.py        # 新增: 图谱化结果模型
│
└── config.py                    # 更新: 添加实例池配置
```

---

## 关键修改点

### 1. flash_service.py 重构

**当前问题**：
- 单例模式，所有 NPC 共享
- 没有独立图谱绑定

**修改方案**：
```python
class FlashService:
    """每个 NPC 独立的 Flash 实例"""

    def __init__(
        self,
        npc_id: str,
        world_id: str,
        memory_graph: MemoryGraph,  # 绑定独立图谱
        npc_config: NPCConfig
    ):
        self.npc_id = npc_id
        self.world_id = world_id
        self.memory_graph = memory_graph
        self.npc_config = npc_config
        self.client = genai.Client()

    # 三个核心任务
    async def ingest_event(self, event: Event) -> IngestResult: ...
    async def understand_query(self, query: str, context: Context) -> QueryUnderstanding: ...
    async def translate_memory(self, subgraph: SubGraph, ...) -> str: ...
```

### 2. pro_service.py 重构

**当前问题**：
- 没有 token 计数
- 没有与 Flash 的通信机制

**修改方案**：
```python
class ProService:
    """每个 NPC 独立的 Pro 实例"""

    def __init__(
        self,
        npc_id: str,
        context_window: ContextWindow,
        flash_bridge: FlashProBridge,
        npc_config: NPCConfig
    ):
        self.npc_id = npc_id
        self.context_window = context_window
        self.flash_bridge = flash_bridge
        self.npc_config = npc_config

    async def chat(self, user_input: str) -> ProResponse:
        # 1. 添加到窗口，检查图谱化触发
        trigger = self.context_window.add_message(...)
        if trigger.should_graphize:
            await self._handle_graphize(trigger)

        # 2. 组装上下文
        context = self._build_context()

        # 3. 生成响应（支持 recall_memory 工具调用）
        response = await self._generate_with_tools(context, user_input)

        return response
```

### 3. game_master_service.py 集成

```python
class GameMasterService:
    def __init__(self):
        self.instance_manager = InstanceManager(max_instances=20)
        # ...

    async def process_player_input(self, world_id, session_id, player_input, npc_id=None):
        if npc_id:
            # 与特定 NPC 对话
            instance = await self.instance_manager.get_or_create(npc_id, world_id)
            return await instance.pro.chat(player_input)
        else:
            # GM 叙述
            return await self.gm_pro.narrate(player_input)
```

---

## 配置项

```python
# app/config.py

class InstancePoolConfig:
    MAX_INSTANCES = 20              # 最大同时活跃实例数
    CONTEXT_WINDOW_SIZE = 200_000   # 200K tokens
    GRAPHIZE_THRESHOLD = 0.9        # 90% 触发图谱化
    KEEP_RECENT_TOKENS = 50_000     # 图谱化后保留的 tokens
    LRU_EVICT_AFTER = timedelta(minutes=30)  # 30 分钟不活跃则可淘汰
```

---

## 实施步骤

### Phase 1: 基础设施（估计工作量较大）

1. **创建 `context_window.py`**
   - Token 计数（使用 tiktoken 或 Gemini tokenizer）
   - 消息管理
   - 阈值检测

2. **创建 `instance_manager.py`**
   - 实例池管理
   - 懒加载逻辑
   - LRU 淘汰

3. **创建 `npc_instance.py` 数据模型**

### Phase 2: 图谱化机制

1. **创建 `memory_graphizer.py`**
   - 消息 → 图谱提取 Prompt
   - 合并逻辑
   - 持久化

2. **更新 `flash_service.py`**
   - 添加 `extract_graph_elements()` 方法
   - 支持独立实例化

### Phase 3: Flash-Pro 通信

1. **创建 `flash_pro_bridge.py`**
   - 查询理解
   - 记忆检索
   - 记忆翻译

2. **更新 `pro_service.py`**
   - 添加 `recall_memory` 工具支持
   - 集成 context_window

### Phase 4: 集成测试

1. **更新 `game_master_service.py`**
   - 集成 InstanceManager
   - 支持 NPC 对话路由

2. **更新 `play.py`**
   - 测试 /talk 命令的完整流程
   - 验证图谱化触发

---

## 验证方案

1. **单元测试**
   - ContextWindow token 计数准确性
   - 图谱化阈值触发
   - LRU 淘汰逻辑

2. **集成测试**
   - 创建 NPC 实例 → 对话 → 检查图谱更新
   - 模拟满载 → 验证图谱化执行
   - Pro 调用 Flash → 验证记忆注入

3. **端到端测试（使用 play.py）**
   ```bash
   # 启动游戏
   python play.py -w goblin_slayer

   # 测试场景
   /talk 女神官
   > 你好，你还记得我们第一次见面吗？
   # 预期：Pro 调用 Flash，检索相关记忆

   # 持续对话直到接近 200K
   # 预期：自动触发图谱化，消息被压缩
   ```

---

## 风险与应对

| 风险 | 应对措施 |
|------|----------|
| Token 计数不准确 | 使用 Gemini 官方 tokenizer，添加 10% 安全边际 |
| 图谱化丢失重要信息 | Flash 提取时包含摘要，保留关键事件的完整描述 |
| 实例池内存压力 | 监控内存使用，动态调整 max_instances |
| Flash-Pro 延迟 | 添加缓存层，预加载常用记忆 |
| 图谱合并冲突 | 使用 timestamp 作为版本，最新优先 |

---

## 层级事件结构设计

### 问题背景

当前的 `event` 节点是扁平结构：
- 只保存摘要（summary），不保存完整对话
- 所有事件同级，无法表达"大事件包含小事件"的层级关系
- 检索时无法追溯原始对话上下文

### 设计目标

```
大事件（event_group 节点）
├── transcript: 完整对话记录
├── summary: 事件摘要
├── 子事件1（event 节点，通过 part_of 边关联）
│   ├── summary: 子事件摘要
│   └── transcript_range: [start_idx, end_idx]  # 指向父节点 transcript 的片段
├── 子事件2
│   └── ...
└── 相关实体（通过 participated/witnessed 边关联）
```

### 节点类型扩展

#### 1. event_group（事件组/大事件）

表示一个完整的交互回合或一系列相连的事件。

```python
{
    "id": "event_group_20260126_与女神官的对话",
    "type": "event_group",
    "name": "与女神官讨论哥布林巢穴",
    "importance": 0.8,
    "properties": {
        "day": 15,                    # 游戏日
        "location": "冒险者公会",      # 发生地点
        "duration_minutes": 30,       # 持续时间（估算）
        "summary": "玩家与女神官讨论了即将进行的哥布林巢穴讨伐任务...",
        "emotion": "determined",      # 整体情绪基调
        "participants": ["player", "person_女神官"],

        # 完整对话记录
        "transcript": [
            {"role": "player", "content": "女神官，关于明天的任务..."},
            {"role": "女神官", "content": "是的，我已经准备好了圣光和治愈术。"},
            {"role": "player", "content": "这次的哥布林巢穴似乎比较大..."},
            # ... 完整对话
        ],

        # 元数据
        "message_count": 24,
        "token_count": 3500,
        "created_at": "2026-01-26T10:30:00Z"
    }
}
```

#### 2. event（子事件/原子事件）

表示大事件中的一个具体发生点。

```python
{
    "id": "event_20260126_女神官表达担忧",
    "type": "event",
    "name": "女神官对任务表达担忧",
    "importance": 0.6,
    "properties": {
        "day": 15,
        "summary": "女神官提到她感应到巢穴中有不寻常的魔力波动",
        "emotion": "worried",
        "participants": ["person_女神官"],

        # 指向父事件的对话片段
        "transcript_range": {
            "parent_id": "event_group_20260126_与女神官的对话",
            "start_idx": 8,
            "end_idx": 12
        },

        # 或者直接保存片段（冗余但检索更快）
        "transcript_snippet": [
            {"role": "女神官", "content": "不过...我有些担心。"},
            {"role": "player", "content": "怎么了？"},
            {"role": "女神官", "content": "我感应到巢穴深处有不寻常的魔力波动..."}
        ]
    }
}
```

### 边关系设计

```
event_group ──part_of──→ event        # 子事件属于大事件
event ──caused──→ event               # 事件因果链
event ──participated──→ person        # 参与者
event ──witnessed──→ person           # 目击者
event ──located_in──→ location        # 发生地点
event ──mentions──→ *                 # 提及的实体
```

### 图谱化流程（更新）

```
工作记忆达到阈值
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ 1. 切分对话为事件组                                           │
│    - 按话题/场景切分                                          │
│    - 每个事件组保存完整 transcript                            │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. Flash 识别子事件                                           │
│    - 从对话中提取关键事件点                                   │
│    - 标记 transcript_range                                   │
│    - 提取情绪、参与者等属性                                   │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. 创建节点和边                                               │
│    - 创建 event_group 节点（含完整 transcript）               │
│    - 创建 event 子节点                                        │
│    - 创建 part_of 边关联层级                                  │
│    - 创建与其他实体的关联边                                   │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ 4. 持久化到 Firestore                                         │
└──────────────────────────────────────────────────────────────┘
```

### Flash 提取 Prompt（事件结构化）

```markdown
# 任务：从对话中提取层级事件结构

## 输入对话
{transcript}

## 当前 NPC 信息
- 名字: {npc_name}
- 视角: 第一人称

## 输出要求

1. 创建一个 event_group 节点表示整个对话
2. 识别对话中的关键事件点，创建 event 子节点
3. 每个子事件标记对应的对话片段索引

## 输出格式
```json
{
  "event_group": {
    "id": "event_group_{timestamp}_{topic}",
    "name": "简短描述整个对话主题",
    "summary": "从我的视角总结这段对话",
    "emotion": "整体情绪",
    "transcript": [...],  // 原样保留
    "participants": [...]
  },
  "sub_events": [
    {
      "id": "event_{timestamp}_{description}",
      "name": "子事件名",
      "summary": "从我的视角描述这个事件",
      "emotion": "当时的情绪",
      "importance": 0.0-1.0,
      "transcript_range": [start_idx, end_idx],
      "participants": [...]
    }
  ],
  "edges": [
    {"source": "event_group_id", "target": "event_id", "relation": "part_of"},
    {"source": "event_id", "target": "person_xxx", "relation": "participated"}
  ]
}
```

### 检索优化

当 Pro 请求记忆时，Flash 可以：

1. **粗检索**：通过 event_group 的 summary 快速定位相关对话
2. **细检索**：通过 event 子节点定位具体事件
3. **上下文还原**：需要时从 transcript 中提取完整对话片段

```python
async def recall_with_context(self, query: str) -> MemoryWithContext:
    # 1. 激活扩散找到相关事件
    activated = self.spread_activation(query_seeds)

    # 2. 找到关联的 event_group
    event_groups = []
    for node_id, activation in activated.items():
        node = self.graph.get_node(node_id)
        if node.type == "event":
            # 找父节点
            parent = self.graph.find_parent(node_id, relation="part_of")
            if parent:
                event_groups.append(parent)
        elif node.type == "event_group":
            event_groups.append(node)

    # 3. 提取相关对话片段
    context_snippets = []
    for eg in event_groups:
        transcript = eg.properties.get("transcript", [])
        # 可以提取全部或相关片段
        context_snippets.append({
            "summary": eg.properties["summary"],
            "transcript": transcript,
            "day": eg.properties["day"]
        })

    return MemoryWithContext(
        summary=self.translate_memory(activated),
        full_context=context_snippets
    )
```

### 存储估算

假设一次对话 50 轮，平均每轮 200 tokens：
- transcript 原文: ~10,000 tokens ≈ 40KB
- 压缩后（JSONL）: ~25KB
- 每个角色每天 5 次重要对话: 125KB/天

这个量级对 Firestore 来说完全可以接受。

### 节点类型汇总（更新后）

| 类型 | 用途 | 关键属性 |
|------|------|----------|
| `identity` | 角色自我认知 | personality, goals, fears |
| `person` | 认识的人 | relation, trust_level, last_seen |
| `location` | 知道的地点 | visited, familiarity |
| `event_group` | **大事件/对话回合** | **transcript, summary, day, participants** |
| `event` | **子事件/事件点** | **summary, transcript_range, emotion** |
| `rumor` | 听说的传闻 | source, reliability, content |
| `knowledge` | 掌握的知识/技能 | domain, proficiency |
| `item` | 拥有/知道的物品 | possession_status, value |
| `goal` | 当前目标 | priority, progress, deadline |
| `emotion` | 情感状态 | target, intensity, cause |
| `organization` | 组织/团体 | role, standing |
# 注意：本文档基于旧架构（GameMasterService / gm_flash_service），当前已迁移至 Admin Layer（admin_coordinator / admin/event_service）。仅供历史参考。
