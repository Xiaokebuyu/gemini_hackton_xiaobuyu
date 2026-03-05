# ❺ NPC 与队友运行时规范

创建时间：2026-02-26
状态：设计中
**隶属于 ❺ AI 叙事层**。本文档是 `叙事层设计规范.md` 的子文档，定义 Agent 的运行时基础设施；叙事行为（语气、风格、对话表达）见主文档。
前置文档：`叙事层设计规范.md`（主文档：Agent 架构 + 叙事行为），`编排层设计规范.md`（interact/private-chat 管线 + P60 日程 Hook），`叙事规划子系统设计规范.md`（NarrativePlanner direct_npc/spawn_quest_npc 指令来源 + §十一 DynamicSubAreaManager），`内容层设计规范.md`（CharacterRegistry 模板），`状态层设计规范.md`（RelationSlice/PartySlice），`AI-Osiris设计规范.md`（add_knowledge 指令）

> NPC 是人，不是功能菜单。队友是伙伴，不是跟班。
> 本规范定义 NPC/队友的**运行时基础设施**——实例池、认知架构、好感机制、日程、路人、记忆图谱。
> 叙事表达（NPC 在各阶段怎么说话、私聊语气风格、GM 旁白等）见主文档 `叙事层设计规范.md`。

---

## 一、设计原则

| 原则 | 说明 |
|------|------|
| **自然生长** | 不预设"路人/次要/核心"层级。记忆深度随互动频率自然变化，玩家用脚投票决定谁重要 |
| **双层认知** | 所有 NPC 共享同一套架构：工作记忆（近期对话）+ 长期记忆（语义图谱） |
| **四维好感** | approval / trust / fear / romance 四个独立维度，不向玩家暴露数值 |
| **资源动态分配** | InstanceManager 根据互动频率 / 场景重要性动态分配模型和上下文窗口 |
| **记忆主观性** | 同一事件，不同角色记录不同版本。NPC 的记忆受性格和情感影响 |
| **关系影响机制** | 关系不是叙事装饰——它真实改变 NPC 行为、交易折扣、战斗 AI、任务可用性 |

---

## 二、整体架构

```
                  ❶ CharacterRegistry
                    (NPC 模板：性格/属性/日程/base_disposition)
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ NPC 运行时子系统                                              │
│                                                              │
│  InstanceManager（实例池管理器）                                │
│    │                                                         │
│    ├─ NPCInstance（活跃实例）                                  │
│    │    ├─ 双层认知                                           │
│    │    │    ├─ ContextWindow（工作记忆，200K token）           │
│    │    │    └─ MemoryGraph（长期记忆，扩散激活检索）            │
│    │    ├─ NPC Directive Queue（NarrativePlanner 下发的指令）   │
│    │    └─ Schedule State（当前日程状态）                       │
│    │                                                         │
│    ├─ PasserbyPool（路人实例池）                                │
│    │    ├─ 模板生成 / NarrativePlanner 投递生成                 │
│    │    └─ 轻量实例（无长期记忆，临时生命周期）                   │
│    │                                                         │
│    └─ CompanionManager（队友管理）                              │
│         ├─ 队伍同步（位置跟随玩家）                              │
│         ├─ 营火系统（长休时回忆共同经历）                         │
│         └─ 个人线索管理（trust/romance 阈值触发）                │
│                                                              │
│  ❸ 状态层交互                                                 │
│    ├─ RelationSlice ← 四维好感度 + 关系阶段 + NPC 认知          │
│    └─ PartySlice   ← 队友列表 + 审批值 + 共同经历               │
│                                                              │
│  ❺ 叙事层交互                                                 │
│    ├─ AgenticExecutor（NPC Agent / Teammate Agent）            │
│    └─ RoleToolRegistry（npc_tools / teammate_tools）           │
└─────────────────────────────────────────────────────────────┘
```

---

## 三、InstanceManager — 实例池管理

### 3.1 职责

InstanceManager 是 NPC 运行时的核心管理器。它负责：

1. **实例池化**：维护活跃 NPC 实例池，LRU 淘汰
2. **资源动态分配**：根据互动频率和场景重要性分配模型/上下文窗口
3. **生命周期管理**：创建 → 激活 → 空闲 → 淘汰（淘汰前强制图谱化）

### 3.2 核心接口

```python
class InstanceManager:
    """NPC 实例池管理器。全局单例。"""

    MAX_INSTANCES = 20              # 池上限（环境变量可覆盖）
    CONTEXT_WINDOW_SIZE = 200_000   # 工作记忆上限（token）

    async def get_or_create(self, npc_id: str, world: WorldInstance,
                            state: StateContainer) -> NPCInstance:
        """获取或创建 NPC 实例。
        1. 池中有 → 直接返回，更新 LRU
        2. 池中无 → 创建新实例，加载模板 + 恢复记忆
        3. 池满 → LRU 淘汰最冷实例（淘汰前强制图谱化）
        """

    async def evict(self, npc_id: str):
        """手动淘汰实例。淘汰前强制图谱化工作记忆。"""

    def get_active_instances(self) -> list[NPCInstance]:
        """获取所有活跃实例。"""

    def get_instance_stats(self) -> dict:
        """返回池状态（数量、内存占用、LRU 排序）。"""
```

### 3.3 NPCInstance 结构

```python
class NPCInstance:
    """单个 NPC 的运行时实例"""

    # === 标识 ===
    character_id: str                    # 对应 ❶ CharacterRegistry 的模板 ID
    template: CharacterTemplate          # ❶ 模板引用（只读）

    # === 双层认知 ===
    context_window: ContextWindow        # 工作记忆
    memory_graph: MemoryGraph            # 长期记忆图谱（引用，持久化在 graphs/ 目录）

    # === 运行时状态 ===
    directive_queue: list[NpcDirective]  # NarrativePlanner 下发的行为指令
    last_interaction_tick: int           # 上次交互的时间格（LRU 依据）
    interaction_count: int               # 累计交互次数

    # === 资源分配 ===
    model_tier: str                      # 当前分配的模型层级（见 §3.4）
    thinking_level: str                  # 当前 thinking 级别

    # === 方法 ===
    async def build_context_with_injection(self, topic_keywords: list[str]) -> str:
        """组装带记忆注入的系统提示：
        1. 基础系统提示（性格/背景/对话风格）
        2. 工作记忆摘要
        3. 扩散激活检索相关长期记忆 → 注入
        4. Directive 注入（如有）
        """

    async def graphize_overflow(self):
        """工作记忆达 90% 时触发：旧对话 → MemoryGraphizer → 图谱节点"""

    def consume_directive(self) -> NpcDirective | None:
        """弹出最高优先级的待执行指令"""
```

### 3.4 资源动态分配

不在 ❶ 模板中硬编码 NPC "等级"——InstanceManager 根据运行时数据动态调配：

| 条件 | 模型 | Thinking | 上下文窗口 |
|------|------|----------|-----------|
| 当前正在对话的 NPC | 主力模型 | medium | 完整 200K |
| 队友（在队） | 主力模型 | low | 完整 200K |
| 最近 3 格有交互 | 主力模型 | low | 完整 200K |
| 场景中但未交互 | Flash | lowest | 50K |
| 池中闲置 | — | — | 工作记忆保持，不活跃调用 |

> 自然生长效果：和商人聊了 20 次的玩家，商人实例常驻池中、记忆丰富、回应细腻。从没说过话的 NPC，第一次交互时新建实例、记忆为空、回应泛泛——这就是"记忆深度决定角色深度"。

### 3.5 淘汰流程

```
池满（20 个实例）+ 需要创建新实例
    │
    ├─ 1. 选择 LRU 最冷实例（last_interaction_tick 最早）
    │     ※ 队友实例不参与 LRU 淘汰（永久保活）
    │     ※ 有 Directive 的实例优先保活
    │
    ├─ 2. 强制图谱化
    │     MemoryGraphizer.graphize(instance.context_window)
    │     → 工作记忆中的对话 → parse → encode → transform_perspective
    │     → 写入 NPC 的 MemoryGraph（长期记忆永不丢失）
    │
    ├─ 3. 释放实例
    │     context_window 清空
    │     从池中移除
    │
    └─ 4. 创建新实例
          从 ❶ 模板加载
          从本地图谱文件恢复 MemoryGraph
          context_window 初始化（空）
```

---

## 四、双层认知架构

### 4.1 工作记忆（ContextWindow）

```python
class ContextWindow:
    """NPC 工作记忆。最近对话的完整记录。"""

    max_tokens: int = 200_000        # 上限
    overflow_threshold: float = 0.9  # 触发图谱化的阈值

    messages: list[Message]          # 对话历史
    current_tokens: int              # 当前占用 token 数

    def add_message(self, message: Message):
        """添加新消息。达到阈值时触发 overflow。"""
        self.messages.append(message)
        self.current_tokens += message.token_count
        if self.current_tokens >= self.max_tokens * self.overflow_threshold:
            self._trigger_overflow()

    def _trigger_overflow(self):
        """溢出处理：取最旧的 1/3 消息 → MemoryGraphizer 图谱化 → 释放 token"""

    def get_recent(self, n: int = None) -> list[Message]:
        """获取最近 N 条消息。"""

    def get_summary(self) -> str:
        """生成工作记忆摘要（用于上下文组装）。"""
```

### 4.2 长期记忆（MemoryGraph）

NPC 的长期记忆存储在知识图谱中，通过扩散激活检索：

```
graphs/{world_id}/characters/{character_id}/
    ├─ nodes.json    ← 记忆节点（事件、认知、情感）
    └─ edges.json    ← 关联关系（因果、时间、情感）
```

**记忆节点类型**：

| 节点类型 | 示例 | 来源 |
|---------|------|------|
| `event` | "玩家在第 3 天帮我找回了失窃的货物" | 对话图谱化 |
| `impression` | "玩家是个值得信赖的人" | 多次交互积累后 LLM 提炼 |
| `knowledge` | "西部牧场最近不安全" | NPC `remember` 工具 / 对话图谱化 |
| `emotion` | "我对玩家很感激" | 情感标签累积 |

> **与 RelationSlice.npc_impressions 的分工**：
> - `npc_impressions`（❸ RelationSlice）：NPC 对玩家的**简短印象摘要**，由 AI Osiris `add_knowledge` 写入。消费者：关系系统（阶段判定）、UI（印象展示）、ContextAssembler。
> - `MemoryGraph`（Firestore 图谱）：NPC 的**完整语义记忆**，由对话图谱化（MemoryGraphizer）和 NPC `remember` 工具写入。消费者：NPC Agent（扩散激活检索注入 prompt）。
> - 两者面向不同消费者，写入路径不同，**不需要同步**。
| `secret` | "我知道地下室有暗门" | ❶ 模板中的 secrets（trust 阈值后解锁） |

### 4.3 记忆注入流程

```
NPC Agent 被调用时：
    │
    ├─ 1. 从 SceneBus 提取当前话题关键词
    │     例：["哥布林", "牧场", "袭击"]
    │
    ├─ 2. 扩散激活检索 MemoryGraph
    │     起始节点 = 关键词匹配的节点
    │     沿边传播（正向 ×1.0，反向 ×0.7，跨章节 ×0.5）
    │     返回 top-K 相关记忆节点
    │
    ├─ 3. 记忆注入系统提示（L6 层）
    │     "你有以下相关记忆：
    │      - 玩家上次提到过对哥布林的担忧
    │      - 你听说西部牧场遭到了袭击
    │      - 玩家买过一把短匕首（可能在准备战斗）"
    │
    └─ 4. LLM 生成回应（自然融入记忆）
          NPC 可能说："你上次也问过哥布林的事...看来你真的很在意。
          我听说西部牧场的情况越来越严重了。你买那把匕首，不会是想去那边吧？"
```

### 4.4 图谱化管线（MemoryGraphizer）

工作记忆溢出时，旧对话通过 LLM 三步管线转为图谱节点：

```
原始对话片段
    │
    ├─ Step 1: parse（事件提取）
    │     "提取这段对话中的关键事件/信息/情感变化"
    │     → 结构化事件列表
    │
    ├─ Step 2: encode（图谱编码）
    │     事件 → 节点 + 边（关联关系）
    │     → MemoryNode + MemoryEdge
    │
    └─ Step 3: transform_perspective（视角转换）
          客观事件 → NPC 第一人称主观记忆
          受 NPC 性格和当时好感度影响
          → "他帮了我" vs "他似乎有目的地接近我"
```

---

## 五、四维好感度模型

### 5.1 四维定义

| 维度 | 含义 | 范围 | 初始值来源 |
|------|------|------|-----------|
| **approval** | 好感/厌恶 — "我喜不喜欢你" | -100 ~ +100 | ❶ CharacterTemplate.base_disposition |
| **trust** | 信任/戒心 — "我能不能依赖你" | -100 ~ +100 | ❶ CharacterTemplate.base_disposition |
| **fear** | 恐惧/安心 — "我怕不怕你" | 0 ~ 100 | ❶ CharacterTemplate.base_disposition |
| **romance** | 爱慕/无感 — "我对你有没有心动" | 0 ~ 100 | ❶ CharacterTemplate.base_disposition |

### 5.2 变化规则

NPC 在对话中自然表达感受，系统翻译为数值变化：

```python
FEELING_MAP = {
    "slight":   5,    # "有点感动"
    "moderate": 10,   # "我挺感激的"
    "strong":   20,   # "我永远不会忘记你做的事"
}
```

**限幅**：
- 单次变化：±20 上限
- 每格累计：±30 上限
- 防止极端波动

### 5.3 好感度 → 行为映射

| 维度/阈值 | 行为变化 |
|----------|---------|
| approval > 50 | 主动提供帮助、分享额外信息、交易折扣 |
| approval < -30 | 冷淡、拒绝非必要请求 |
| trust > 50 | 分享秘密、委托重要任务、私聊触发 |
| trust < -30 | 隐瞒信息、有防备 |
| fear > 70 | 表面服从但暗中反抗 |
| romance > 60 | 流露感情、特殊对话选项 |

### 5.4 玩家感知

**不显示数值**，通过模糊描述传达：

```
approval > 30  → "她似乎对你挺有好感"
trust > 70     → "他看起来完全信任你"
trust < -20    → "她对你有些戒备"
fear > 40      → "他似乎有点怕你"
romance > 50   → "她看你的眼神有些不一样"
```

这些描述由 GM Agent 在合适时机自然插入叙述，不是 UI 弹窗。

---

## 六、关系阶段系统

### 6.1 正面关系线

```
陌生人 ──→ 相识 ──→ 朋友 ──→ 挚友 ──→ 灵魂伴侣
```

| 阶段 | 升阶条件 | 解锁内容 |
|------|---------|---------|
| **陌生人** | 初始 | 基础交易/对话 |
| **相识** | approval > 10 且 1+ 次对话 | NPC 认出你、记得名字 |
| **朋友** | approval > 30 + trust > 20 + 3+ 共同经历 | 私聊解锁、主动分享信息、交易折扣 |
| **挚友** | trust > 60 + 1+ 危机选择 + 10+ 共同经历 | 同伴个人线解锁、战斗中拼死保护 |
| **灵魂伴侣** | trust > 80 + romance > 60 + 完成同伴个人线 | 专属结局、最强战斗配合 |

### 6.2 负面关系线

```
冷淡 ──→ 厌恶 ──→ 敌对 ──→ 宿敌
```

| 阶段 | 触发条件 | 后果 |
|------|---------|------|
| **冷淡** | approval < -20 | 拒绝非必要请求 |
| **厌恶** | approval < -50 + 伤害事件 | 拒绝入队、传播坏评价 |
| **敌对** | trust < -60 + 背叛事件 | 主动阻碍、极端情况加入敌方 |
| **宿敌** | 不可逆伤害（如害死对方在乎的人） | 永久敌对、可能成为 Boss |

### 6.3 质变判定

关系质变不是纯数值触发——还需要叙事条件：

```python
class RelationStageChecker:
    """关系阶段质变检查器。由 ❷ RulesEngine 在相关事件后调用。"""

    def check_transition(self, npc_id: str, state: StateContainer) -> str | None:
        disposition = state.relations.get_disposition(npc_id)
        stage = state.relations.get_stage(npc_id)
        experiences = state.party.get_shared_experiences(npc_id)
        crisis_count = state.party.count_critical_moments(npc_id)

        if stage == "acquaintance" and disposition.approval > 30 \
                and disposition.trust > 20 and len(experiences) >= 3:
            return "friend"

        if stage == "friend" and disposition.trust > 60 \
                and crisis_count >= 1 and len(experiences) >= 10:
            return "close_friend"

        # 负面质变可以很快
        if disposition.trust < -60 and self._has_betrayal_event(npc_id, state):
            return "hostile"

        return None
```

> **修复比建立更难**：从"厌恶"回到"相识"需要的努力远大于从"陌生人"到"朋友"。

---

## 七、私聊系统——机械层

私聊是关系深化的核心场景。本节定义私聊的**机械规则**（进入条件、场景生成、退出流程）。私聊中的**叙事行为**（语气、对话风格、romance 表达）见 `叙事层设计规范.md` §7.4。

### 7.1 进入条件

| 条件 | 说明 |
|------|------|
| 关系阶段 >= 相识 | 陌生人不可发起私聊 |
| NPC 可达 | NPC 在同一区域/子地点，且非战斗/忙碌状态 |
| 每格限 1 次 | 防止无限刷好感。每次私聊消耗 time_cost = 1/6 格 |
| 不在私密子地点内 | 已处于私聊场景中时不可再发起 |

**NPC 主动发起**：trust > 60 的 NPC 有小概率在营火/休息时主动邀请私聊（由 P60 NpcScheduleHook 判定，写入 SceneBus 供 GM/队友叙述"她走过来，想和你单独聊聊"）。

### 7.2 私聊场景生成

私聊不是抽象的"切换到私聊模式"——它发生在一个**具体的私密场所**。系统通过 DynamicSubAreaManager（`叙事规划子系统设计规范.md` §十一）生成临时子地点。

**流程**：

```
玩家发起私聊（POST .../private-chat/stream, target=npc_id）
    │
    ├─ 1. 条件检查（§7.1）
    │
    ├─ 2. 场景生成
    │     current_area = state.player.current_area
    │     current_sub = state.player.current_location  # 可能为 None
    │     scene = PrivateChatSceneBuilder.generate(current_area, current_sub, npc)
    │     sub_area_id = DynamicSubAreaManager.create(scene)
    │     → SSE: scene_change（新背景 + 氛围）
    │
    ├─ 3. 进入私聊管线
    │     PlayerSlice.current_location = sub_area_id  # 受控例外 B: 临时位置调度（进出成对）
    │     → PrivateChatCoordinator.process()（编排层 §六）
    │     → NPC Agent 使用私聊上下文（§7.4）
    │
    ├─ 4. 私聊进行中（多轮对话）
    │     每轮：NPC Agent 回应 → 对话选项 → 玩家选择
    │     SceneBus visibility = "private"
    │
    └─ 5. 退出私聊
          玩家主动结束 / NPC 提出告辞 / 外部事件打断
          PlayerSlice.current_location = 恢复原位
          DynamicSubAreaManager.expire(sub_area_id)
          → SSE: scene_change（恢复原场景）
```

### 7.3 PrivateChatSceneBuilder — 场景模板

根据当前地点的 tags/type 生成匹配的私密场所描述：

```python
class PrivateChatSceneBuilder:
    """私聊场景生成器。模板化，不调用 LLM。"""

    # 按地点类型的场景模板
    SCENE_TEMPLATES: dict[str, list[dict]] = {
        "tavern": [
            {"name": "酒馆二楼的小包间", "description": "一间昏暗的小房间，只有一盏油灯和两把椅子。楼下的喧闹声被厚重的木门隔绝在外。", "ambiance": "tavern_warm", "lighting": "warm_indoor"},
            {"name": "后门外的僻静角落", "description": "酒馆后门外的小巷，头顶的木质屋檐遮住了月光。远处传来微弱的酒馆歌声。", "ambiance": "none", "lighting": "night"},
        ],
        "forest": [
            {"name": "远离营地的老树下", "description": "一棵巨大的橡树，粗壮的根部形成了天然的座椅。篝火的光在这里只剩下微弱的橙色。", "ambiance": "forest_calm", "lighting": "night"},
            {"name": "溪流旁的石台", "description": "一块平坦的大石头伸入浅浅的溪流中。流水声恰好掩盖了你们的对话。", "ambiance": "forest_calm", "lighting": "cool_outdoor"},
        ],
        "town": [
            {"name": "城墙上的角落", "description": "城墙的一处凹角，可以俯瞰整个小镇的灯火。风很大，但没有人会来这里。", "ambiance": "none", "lighting": "night"},
            {"name": "小教堂后的花园", "description": "修剪整齐的灌木围出一片安静的小天地。石凳上落着几片枯叶。", "ambiance": "none", "lighting": "cool_outdoor"},
        ],
        "camp": [
            {"name": "篝火另一侧", "description": "远离营地中心的位置，只能看到篝火的微光。其他人的说话声在这里只是模糊的低语。", "ambiance": "forest_calm", "lighting": "night"},
        ],
        "dungeon": [
            {"name": "岔道尽头的安全角落", "description": "一条死胡同，但至少不会有怪物从背后偷袭。石壁上还残留着某种矿物的微光。", "ambiance": "cave_damp", "lighting": "dungeon"},
        ],
    }

    FALLBACK = {"name": "僻静处", "description": "一个远离人群的安静角落。", "ambiance": "none", "lighting": "cool_outdoor"}

    @classmethod
    def generate(cls, area_id: str, current_sub: str | None, npc: NPCInstance) -> TemporarySubArea:
        """根据当前地点生成私聊场景。

        匹配规则：area.tags → SCENE_TEMPLATES key → 随机选一个模板
        """
        area_tags = world.maps.get_area(area_id).tags
        template = cls._match_template(area_tags)

        return TemporarySubArea(
            id=f"_private_{npc.character_id}_{tick}",
            name=template["name"],
            description=template["description"],
            tags=["PRIVATE_CHAT", "TEMPORARY"],
            type="private",
            available_hours=(0, 24),
            resident_npcs=[npc.character_id],
            interactables=[],
            target_area=area_id,
            content_hints={"mood": "intimate", "npc_name": npc.name},
            discovery_mode="auto",
            discovery_dc=0,
            discovery_check="none",
            linked_quest_id=None,
            linked_milestone=None,
            source="private_chat",
            created_at_tick=tick,
            expiry_ticks=-1,  # 私聊结束时手动 expire，不自动过期
            on_expire="remove",
            status="entered",  # 直接进入，跳过 active/discovered
        )
```

> **AssetResolver 集成**：私聊场景的 background 资产 key = `"temp/_private_{npc_id}_{tick}/background.png"`，走 AssetResolver 三级解析链（`表现层设计规范.md` P2）。首次生成时从 template.description 构造生图 prompt。

### 7.4 私聊上下文注入

NPC Agent 在私聊模式下接收额外上下文（由 ContextAssembler 组装）：

```python
private_chat_context = {
    # 关系状态
    "relationship": {
        "stage": "friend",
        "disposition": {"approval": 42, "trust": 35, "fear": 5, "romance": 28},
        "shared_experiences_count": 5,
        "last_private_chat_tick": 120,
    },

    # 解锁的秘密（trust 门槛已过的，见 §7.5）
    "unlocked_secrets": [
        "你其实并不是自愿加入冒险者公会的——是为了逃避家族的安排。"
    ],

    # 私聊行为指引（写入 NPC 系统提示）
    "behavior_guide": (
        "你现在和玩家单独在一起。没有其他人能听到你们的对话。"
        "你可以比平时更真实——不需要维持公众形象。"
        "如果对话氛围合适，你可以提及自己的秘密，但不要生硬。"
    ),

    # 记忆注入（按私聊权重调整）
    "recalled_memories": [...],
}
```

**记忆召回权重调整**：私聊中扩散激活的起点偏向个人/共同记忆，而非世界知识。

```
公开交互权重：world 0.4 / task 0.3 / personal 0.3
私聊权重：    personal 0.5 / shared_experience 0.3 / world 0.2
```

### 7.5 秘密吐露机制

NPC 角色数据中可标记秘密列表，每条秘密有 trust 门槛：

```python
# ❶ CharacterTemplate 扩展字段
secrets: list[Secret]
# Secret = {
#     content: str,          # "你其实是某贵族的私生子"
#     trust_threshold: int,  # 需要 trust >= 此值才可能在私聊中吐露
#     revealed: bool,        # 是否已被吐露（❸ RelationSlice 跟踪）
#     tags: list[str],       # [PERSONAL, FAMILY, DARK_PAST]
# }

romance_eligible: bool       # 是否有 romance 线（不是所有 NPC 都可以）
```

私聊上下文注入时，只注入 `trust >= threshold` 且 `revealed == false` 的秘密。NPC Agent 的 LLM 自行判断对话中是否自然提起。秘密被提及后，通过 `remember` 工具标记 `revealed = true`。

### 7.6 外部事件打断

私聊不是安全气泡——世界事件可以打断：

| 打断来源 | 触发条件 | 效果 |
|---------|---------|------|
| 遭遇事件 | P40 EncounterHook 触发战斗 | 私聊中断，切换到战斗流程 |
| NPC 日程 | P60 时段变化，NPC 需要离开 | NPC 提出告辞（通过 Directive） |
| 紧急剧情 | NarrativePlanner L4+ 紧迫事件 | GM 内心旁白警告 → 选择继续或中断 |
| 队友干预 | 队友有紧急事务（低概率） | 队友走来打断（叙事事件） |

打断时流程：中断当前私聊 → expire 临时子地点 → 恢复原场景 → 处理打断事件。

---

## 八、NPC 日程系统

### 7.1 日程定义

每个 NPC 在 ❶ CharacterTemplate 中定义四时段位置：

```python
schedule: dict[str, str]  # {period → location_id}
# 例：{
#   "dawn": "home",
#   "day": "general_store",
#   "dusk": "general_store",
#   "night": "home"
# }
```

### 7.2 NpcScheduleHook（P60）

格结算时更新 NPC 位置和可用性：

```python
class NpcScheduleHook(SettlementHook):
    """NPC 日程更新。P60，时间推进前执行。"""
    priority = 60
    name = "npc_schedule"

    async def execute(self, context: SettlementContext) -> HookResult:
        current_period = context.state.time.period
        next_period = self._predict_next_period(context.state.time)

        # 时段即将变化时，更新 NPC 位置
        if current_period != next_period:
            for char_id, template in context.world.characters.list_all():
                if char_id in context.state.party.members:
                    continue  # 队友不走日程，跟随玩家
                new_location = template.schedule.get(next_period)
                if new_location:
                    # 受控例外 B: 编排层内部调度（NpcScheduleHook P60）
                    context.state.areas.update_npc_location(char_id, new_location)

            # 写入 SceneBus 供 GM 叙述
            context.scene_bus.add_entry(SceneEntry(
                source="ENGINE",
                content=f"时段从 {current_period} 转为 {next_period}，NPC 开始移动",
                visibility="system",
                tags=["PERIOD_CHANGE", "NPC_MOVEMENT"],
            ))

        return HookResult()
```

### 7.3 玩家交互

想找某个 NPC → 得在对的时间去对的地方：

```
玩家想找 Priestess（日程：dawn=temple, day=temple, dusk=guild_hall, night=temple_quarters）
    → 白天去神殿 ✓
    → 黄昏去公会大厅 ✓
    → 夜晚...她在宿舍休息，不方便打扰
```

这和时间经济联动：花时间跑去特定地点找特定 NPC，本身就是一个决策。

---

## 九、NPC Directive 机制

### 8.1 概念

NarrativePlanner 通过 `direct_npc` 指令给 NPC 下发行为指令，让 NPC 在下次交互时主动表现出特定行为。

### 8.2 Directive 数据结构

```python
@dataclass
class NpcDirective:
    """NarrativePlanner 下发给 NPC 的行为指令"""

    npc_id: str                   # 目标 NPC
    directive: str                # 自然语言指令（注入 NPC 系统提示）
    priority: str                 # low / medium / high
    linked_quest_id: str | None   # 关联的动态任务 ID
    created_at_tick: int          # 创建时间
    expires_at_tick: int          # 过期时间
    consumed: bool = False        # 是否已被消费
```

### 8.3 执行流程

```
NarrativePlanner 输出 direct_npc 指令
    │
    ├─ 1. 写入 NarrativePlanSlice.npc_directives
    │
    ├─ 2. 如果目标 NPC 有活跃实例
    │     → 直接注入 NPCInstance.directive_queue
    │
    └─ 3. 如果目标 NPC 无活跃实例
          → 保留在 NarrativePlanSlice 中
          → 下次 InstanceManager.get_or_create(npc_id) 时注入

NPC Agent 被调用时：
    │
    ├─ instance.consume_directive()
    │     → 弹出最高优先级的未过期指令
    │
    └─ 注入系统提示（优先级高于一般上下文）：
          "【重要行为指令】下次和玩家交谈时，你应该主动提到西部牧场的
           紧急委托。表现出担忧，暗示这是一个适合瓷级冒险者证明自己的机会。"
```

---

## 十、路人系统（PasserbyPool）

### 9.1 定位

路人是轻量级临时 NPC，没有 ❶ 模板、没有长期记忆、有限生命周期。两种来源：

| 来源 | 触发 | 生成方式 |
|------|------|---------|
| **随机生成** | 进入有 passerby_spawn_rate 的区域 | 从区域路人模板随机组合 |
| **NarrativePlanner 投递** | `spawn_quest_npc` 指令 | NarrativePlanner 提供完整 profile + 对话钩子 |

### 9.2 PasserbyInstance

```python
@dataclass
class PasserbyInstance:
    """路人实例。轻量，无长期记忆。"""

    id: str                           # 自动生成
    name: str                         # "受伤的农民" / "旅行商人" / "醉酒的矮人"
    appearance: str                   # 外观描述
    personality: str                  # 性格简述
    tags: list[str]                   # [CIVILIAN, FARMER, WOUNDED]

    # 位置
    location: str                     # 所在地点 ID
    spawn_tick: int                   # 生成时间
    despawn_tick: int                 # 消失时间

    # NarrativePlanner 注入（仅 spawn_quest_npc）
    dialogue_hook: str | None         # 开场对话钩子
    linked_quest_id: str | None       # 关联的动态任务
    interaction_style: str | None     # 交互风格提示

    # 状态
    interacted: bool = False          # 是否已被交互
    interaction_count: int = 0        # 交互次数
```

### 9.3 路人生命周期

```
生成 ──→ 存在于地点 ──→ 被交互 / 超时 ──→ 消失
  │                          │
  │   玩家进入地点时            │
  │   GM 叙述中提及            │   路人不进入 InstanceManager 主池
  │                          │   交互时临时创建轻量 Agent（无记忆注入）
  │                          │
  └──────────────────────────┘
```

### 9.4 随机路人生成

```python
class PasserbyPool:
    """路人实例池。独立于 InstanceManager 主池。"""

    MAX_PASSERSBY_PER_AREA = 3       # 每个区域最多同时存在 3 个路人

    def spawn_random(self, area_id: str, world: WorldInstance) -> PasserbyInstance | None:
        """随机生成路人。概率 = area.passerby_spawn_rate。
        从区域的路人模板池随机组合：名字 + 外观 + 性格 + Tag。
        """

    def spawn_quest_npc(self, params: dict) -> PasserbyInstance:
        """由 NarrativePlanner spawn_quest_npc 指令触发。
        使用 NarrativePlanner 提供的完整 profile。
        """

    def despawn_expired(self, current_tick: int):
        """清理过期路人。"""

    def get_at_location(self, location_id: str) -> list[PasserbyInstance]:
        """获取某地点的所有路人。"""
```

### 9.5 路人对话

路人对话走简化路径，不进入完整的 NPC 交互管线：

```
玩家和路人说话
    │
    ├─ 如果是 NarrativePlanner 投递的路人（有 dialogue_hook）
    │     → AgenticExecutor.run(role="npc", 系统提示含 dialogue_hook)
    │     → 可触发动态任务发现
    │
    └─ 如果是随机路人
          → Flash 模型 + 无 thinking + 简短回应
          → 可能提供环境信息、闲聊、氛围
```

---

## 十一、队友系统（CompanionManager）

### 10.1 概述

队友既是 NPC（有性格、记忆、好感度）又是战斗同伴。队友实例**永久保活**，不参与 LRU 淘汰。

### 10.2 招募与离队

```python
class CompanionManager:
    """队友管理器。"""

    MAX_PARTY_SIZE = 4  # 最多 4 个队友 + 玩家 = 5 人

    async def recruit(self, npc_id: str, state: StateContainer) -> bool:
        """招募队友。
        前置条件：
        - NPC 关系阶段 >= acquaintance
        - NPC 有 RECRUITABLE tag
        - 队伍未满
        - NPC 同意（approval > 0 且无敌对关系）

        效果：
        - PartySlice.add_member(npc_id)  # 受控例外 C: 队伍管理（建议未来升级为 Command）
        - NPC 位置锁定为跟随玩家
        - NPC 日程暂停（在队期间不走日程）
        - NPC 实例标记为队友（不参与 LRU）
        """

    async def dismiss(self, npc_id: str, state: StateContainer):
        """让队友离队。
        效果：
        - PartySlice.remove_member(npc_id)
        - NPC 恢复日程
        - NPC 返回默认位置（按当前时段）
        - approval 可能小幅变化（取决于理由和关系深度）
        """

    async def force_leave(self, npc_id: str, state: StateContainer, reason: str):
        """队友主动离队（关系恶化、个人原因）。
        触发条件：approval < -30 或 特定叙事事件
        """
```

### 10.3 队伍行为

#### 10.3.1 位置同步

```
玩家 navigate(town → forest)
    │
    └─ 所有队友自动跟随
       PartySlice.members.forEach → 位置同步
       ※ 队友不消耗额外时间格
```

#### 10.3.2 发言决策（response_tendency）

每次动作后，队友依次决定是否发言：

```python
def should_respond(self, teammate: CharacterTemplate, scene: SceneSlice) -> bool:
    """队友发言决策。"""
    base_chance = teammate.response_tendency  # 0.0 ~ 1.0（性格决定）

    # 场景修正
    if scene.has_tag("COMBAT_END"):
        base_chance += 0.3        # 战后更愿意发言
    if scene.has_tag("CRISIS"):
        base_chance += 0.4        # 危机时刻更愿意发言
    if scene.has_tag("TRIVIAL"):
        base_chance -= 0.2        # 日常琐事减少发言

    # 最近发言频率抑制（防止话痨）
    recent_speaks = scene.count_entries(source=f"TEAMMATE:{teammate.id}")
    base_chance -= recent_speaks * 0.15

    return random.random() < clamp(base_chance, 0.05, 0.95)
```

### 10.4 共同经历系统

#### 10.4.1 SharedExperience 记录

经历由编排层在关键时刻自动记录：

```python
@dataclass
class SharedExperience:
    """一次共同经历的结构化记录"""
    id: str
    participants: list[str]           # 参与者 ID
    type: str                         # combat / exploration / dialogue / crisis /
                                      # celebration / loss / discovery / betrayal
    summary: str                      # 客观描述
    day: int                          # 发生日期
    location: str                     # 地点
    emotion_tags: list[str]           # 情感标签 [danger, trust, relief, ...]
    critical_moments: list[CriticalMoment]  # 关键时刻
```

#### 10.4.2 危机选择（CriticalMoment）

关系质变的试金石。在需要做出抉择的时刻，选择被永久记录：

| 场景 | 选择 | 后果 |
|------|------|------|
| 队友倒下，附近有敌人 | 冒险去救 vs 继续打 Boss | 救 → trust 大幅提升；不救 → trust 下降 |
| 两个队友同时需要帮助 | 选择帮谁 | 被帮的 trust++，没被帮的 trust-- 或理解 |
| NPC 求你保守秘密 | 保密 vs 告诉别人 | 保密 → trust++；泄露 → trust 崩塌 |
| 任务目标和队友安全冲突 | 完成任务 vs 撤退保人 | 因人而异 |

**不是每个选择都有正确答案。**

### 10.5 营火系统

#### 10.5.1 触发条件

长休息时队友围在营火旁：

| 触发类型 | 条件 | 优先级 |
|---------|------|--------|
| **重大经历后** | 当天有 Boss 战 / 危机选择 / 完成大任务 / 关系质变 | 最高（必触发） |
| **随机触发** | 无重大经历，~30% 概率 | 低 |
| **沉默** | 关系太浅（相识以下）或 approval 太低 | 不触发 |

#### 10.5.2 回忆选择算法

```
从共同经历图谱中选择 →
    1. 今天的重大经历（最高优先）
    2. 最近 7 天内未被回忆过的经历（按情感权重排序）
    3. 随机旧经历（偏好高情感权重的）
→ 注入队友上下文
→ 队友 Agent 自主决定怎么聊
```

#### 10.5.3 营火对话效果

| 效果 | 说明 |
|------|------|
| **巩固关系** | 回忆共同经历 → 双方好感度小幅提升 |
| **展现性格** | Goblin Slayer 简短评价 vs Priestess 情感丰富 |
| **伏笔出口** | "对了，那个巢穴深处的符文...你不觉得奇怪吗？" |
| **个人线触发** | 高好感队友可能开启同伴个人线："...有件事我想跟你说" |

### 10.6 同伴个人线

高好感队友的专属剧情线：

**触发条件**：trust > 50 或 romance > 40（具体阈值因角色而异）

**形式**：
- 队友在私聊/营火中透露心事或请求帮忙
- 不是标准的"去打怪"——可能是拜访故乡、面对过去、解决心结
- 完成后**永久改变**队友性格/战斗偏好/可用技能

**实现**：通过 NarrativePlanner 生成关联里程碑（同伴个人里程碑），动态任务投递走标准 NarrativePlanner 流程。

### 10.7 记忆驱动战斗行为

关系深度真实影响队友战斗 AI：

| 关系阶段 | 战斗行为变化 |
|---------|------------|
| 陌生人/相识 | 标准战术 AI，不特别照顾玩家 |
| 朋友 | 偶尔优先治疗/保护玩家（权重 +20%） |
| 挚友 | 主动保护侧翼，危险时优先救援（+50%） |
| 灵魂伴侣 | 自我牺牲行为（挡在玩家前面吃攻击） |
| 厌恶/敌对 | 不再主动保护，甚至"不小心"忽略玩家危险 |

**关键记忆 → 具体行为偏好**：

| 记忆 | 行为影响 |
|------|---------|
| "你曾经救过我" | 低血量时更积极治疗/保护 |
| "我们一起清过 3 个巢穴" | 更好的走位配合 |
| "你上次没有救我" | 不信任侧翼保护，自己找安全位置 |
| "你在私聊里说别让我打前排" | 调整战术性格 |

---

## 十二、视角感知——同一事件，不同记忆

### 11.1 分发规则

事件发生时，按"谁知道什么"分发到各角色图谱：

| 参与层级 | 获得的记忆 | 权重 |
|---------|-----------|------|
| **亲身参与** | 完整事件 + 情感体验 | 0.9 |
| **在场目击** | 事件概要 + 旁观视角 | 0.7 |
| **事后听说** | 简化版本（可能被扭曲） | 0.4 |
| **不知道** | 无记忆 | 0 |

### 11.2 可见性控制

通过 SceneBus 的 visibility 控制：
- **public** 事件 → 所有在场角色感知
- **private** 事件 → 只有当事双方知道

**秘密是有意义的**：你和某个 NPC 私下达成的协议，队友不知道。

### 11.3 主观记忆示例

```
【事件】玩家在战斗中冒险救了倒下的 Priestess

玩家图谱:      "我冲过去救了 Priestess，差点被哥布林包围"
Priestess:     "他在我快要绝望的时候出现了...我不会忘记这一刻"
Goblin Slayer: "他做了冒险但正确的判断。可以信赖。"
路过的商人:     （不在场，不知道这件事）
```

---

## 十三、三层记忆图谱

### 12.1 架构

```
┌──────────────────────────────────────────────┐
│ 世界观图谱 (Ontology)                          │
│   "世界是什么样的" — 只读百科                    │
│   所有角色共享，不会变                           │
│   来源：❶ WorldInstance 加载时构建               │
├──────────────────────────────────────────────┤
│ GM 图谱 (God's Eye)                           │
│   "世界发生了什么" — 全知视角                     │
│   客观事件记录 + 隐藏信息（阴谋、伏笔、未揭露真相） │
│   只有 GM + NarrativePlanner 可读写              │
├──────────────────────────────────────────────┤
│ 角色记忆图谱 (Per-Character)                    │
│   "我经历了什么" — 第一人称主观视角                │
│   每个 NPC / 队友 / 玩家各自独立                  │
│   同一事件 → 不同角色记录不同版本                  │
│   来源：MemoryGraphizer 图谱化 + AI Osiris       │
└──────────────────────────────────────────────┘
```

### 12.2 存储路径

```
graphs/{world_id}/
    ├─ world/                          ← 世界观图谱（只读）
    │   ├─ nodes.json
    │   └─ edges.json
    ├─ gm/                             ← GM 图谱（God's Eye）
    │   ├─ nodes.json
    │   └─ edges.json
    └─ characters/{char_id}/           ← 角色记忆图谱（每个角色独立）
        ├─ nodes.json
        └─ edges.json
```

---

## 十四、与各层集成点

### 13.1 与 ❶ 内容层

| ❶ 组件 | NPC 子系统如何使用 |
|--------|------------------|
| CharacterRegistry | NPCInstance 创建时加载模板（性格/属性/日程/base_disposition） |
| MapRegistry | 日程系统查询地点信息 + 路人生成查询 spawn_rate |
| LoreRegistry | 记忆注入时可引用世界观知识 |

### 13.2 与 ❸ 状态层

| ❸ 切片 | NPC 子系统如何交互 |
|--------|------------------|
| RelationSlice | 四维好感度 + 关系阶段 + NPC 认知记录的读写 |
| PartySlice | 队友列表 + 审批值 + 共同经历的读写 |
| NarrativePlanSlice | NPC Directive 队列的读写 |
| SceneSlice | SceneBus 感知 + 可见性控制 |

### 13.3 与 ❺ 叙事层

| ❺ 组件 | NPC 子系统如何交互 |
|--------|------------------|
| AgenticExecutor | NPC Agent / Teammate Agent 执行器 |
| RoleToolRegistry | npc_tools（speak/emote/update_feeling/remember/offer_quest/reveal_secret） |
| | teammate_tools（speak/emote/express_opinion/suggest_tactic/share_memory） |
| AgentContextBuilder | build_npc_context() / build_teammate_context()（7 层上下文 + 记忆注入） |

### 13.4 与编排层

| 编排组件 | NPC 子系统如何交互 |
|---------|------------------|
| NpcInteractionCoordinator | 调用 InstanceManager.get_or_create() → NPC Agent 响应 |
| PrivateChatCoordinator | 完整双层认知 + 记忆注入 |
| P60 NpcScheduleHook | 时段切换时更新 NPC 位置 |
| P35 NarrativePlannerHook | 输出 direct_npc / spawn_quest_npc 指令 |
| TeammateResponseService | 队友发言决策 + Agent 执行 |

---

## 十五、与现有系统的映射

| 现有组件 | 演化方向 |
|---------|---------|
| `instance_manager.py` | 保留核心——演化为本规范的 InstanceManager |
| `context_window.py` | 保留——工作记忆管理 |
| `memory_graphizer.py` | 保留——对话自动图谱化 |
| `spreading_activation.py` | 保留——扩散激活检索 |
| `passerby_service.py` (508行) | 重构为 PasserbyPool，新增 NarrativePlanner 投递接口 |
| `npc_reactor.py` | 保留——NPC 相关度推荐 |
| `teammate_response_service.py` | 保留决策逻辑，工具层 → Command 模式 |
| `companion_instance.py` | 整合进 CompanionManager |
| `party_service.py` | 核心逻辑迁入 CompanionManager，PartySlice 持有状态 |

---

## 补遗：增量实现中确立的 NPC/队友系统（2026-03-05 追记）

### A. 负面关系跃迁

原始设计定义了 cold→hostile→enemy 阶段存在但未指定跃迁条件。实现中 `RelationshipHook` 基于 approval + trust 双维度阈值判定渐进跃迁。进入 hostile/enemy 时自动调用 `CompanionManager.force_leave()`。

### B. CompanionManager 公共 API

`orchestration/companion_manager.py`，独立编排组件（非 Hook）：

- `recruit(npc_id) -> RecruitResult` — 前置检查 tag / 关系阶段 / 队伍容量
- `dismiss(npc_id) -> RecruitResult` — 从队伍移除
- `force_leave(npc_id, reason) -> RecruitResult` — NPC 主动离队，由 RelationshipHook 调用

**HTTP 端点**：`POST .../companion/recruit`、`POST .../companion/dismiss`（streaming SSE）

### C. 营火对话

`CampfireHook`（P63）— 长休后触发，从 SharedExperience 中选择经历回忆，巩固队伍关系，emit `campfire_dialogue` SSE。

### D. SharedExperience 录入

`SharedExperienceHook`（P55）— 从 action_log + SceneBus tags 自动检测共同经历（combat/quest/rest），写入 PartySlice。CampfireHook 消费。

### E. Directive GC

`NarrativePlannerHook.execute()` 开头清理已消费 + 已过期的 directives。

---

## 变更日志

| 日期 | 变更 |
|------|------|
| 2026-03-05 | 补遗 A-E：负面跃迁 + CompanionManager API + 营火对话 + SharedExperience + Directive GC |
| 2026-02-26 | 创建。InstanceManager 实例池（LRU 淘汰 + 资源动态分配）+ 双层认知（ContextWindow + MemoryGraph）+ 四维好感度模型 + 关系阶段系统（正面 5 阶 + 负面 4 阶）+ NPC 日程（P60 Hook）+ NPC Directive 机制 + 路人系统（PasserbyPool + NarrativePlanner 投递）+ 队友系统（招募/离队/发言决策/共同经历/危机选择/营火回忆/个人线/记忆驱动战斗行为）+ 视角感知 + 三层记忆图谱 |
| 2026-02-26 | 文档更名为"❺ NPC与队友运行时规范"，明确隶属于 ❺ AI 叙事层子文档。新增 §七 私聊机械层：进入条件 + PrivateChatSceneBuilder 场景模板（复用 DynamicSubAreaManager 生成临时私密子地点）+ 上下文注入结构 + 记忆召回权重调整 + 秘密吐露机制（CharacterTemplate.secrets + trust_threshold）+ romance_eligible 字段 + 外部事件打断机制。后续章节编号 +1 |
| 2026-02-27 | 文档统一修订：§4.2 新增 npc_impressions 与 MemoryGraph 分工说明（两者面向不同消费者，不需同步）。knowledge 节点来源改为 NPC remember 工具/对话图谱化。NpcScheduleHook/私聊/CompanionManager 标注受控例外类别。NpcScheduleHook.execute() 签名同步 SettlementContext |
