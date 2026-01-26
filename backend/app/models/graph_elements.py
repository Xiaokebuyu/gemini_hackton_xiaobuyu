"""
Graph Elements data models.

层级事件结构设计：
- event_group（大事件/对话回合）：包含完整对话记录
- event（子事件/事件点）：具体发生点，引用父事件的 transcript 片段

节点类型汇总：
| 类型 | 用途 | 关键属性 |
|------|------|----------|
| `identity` | 角色自我认知 | personality, goals, fears |
| `person` | 认识的人 | relation, trust_level, last_seen |
| `location` | 知道的地点 | visited, familiarity |
| `event_group` | 大事件/对话回合 | transcript, summary, day, participants |
| `event` | 子事件/事件点 | summary, transcript_range, emotion |
| `rumor` | 听说的传闻 | source, reliability, content |
| `knowledge` | 掌握的知识/技能 | domain, proficiency |
| `item` | 拥有/知道的物品 | possession_status, value |
| `goal` | 当前目标 | priority, progress, deadline |
| `emotion` | 情感状态 | target, intensity, cause |
| `organization` | 组织/团体 | role, standing |
"""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ==================== 对话记录模型 ====================


class TranscriptMessage(BaseModel):
    """对话记录中的单条消息"""

    role: str  # "player", "npc_name", "narrator" 等
    content: str
    timestamp: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TranscriptRange(BaseModel):
    """指向父事件 transcript 的片段范围"""

    parent_id: str  # 父事件组 ID
    start_idx: int  # 起始索引（含）
    end_idx: int  # 结束索引（含）


# ==================== 事件节点模型 ====================


class EventGroupNode(BaseModel):
    """
    事件组/大事件节点

    表示一个完整的交互回合或一系列相连的事件。
    """

    id: str  # e.g. "event_group_20260126_与女神官的对话"
    type: Literal["event_group"] = "event_group"
    name: str  # 简短描述整个对话主题

    # 重要性
    importance: float = 0.5

    # 核心属性
    day: int  # 游戏日
    location: Optional[str] = None  # 发生地点
    duration_minutes: Optional[int] = None  # 持续时间（估算）
    summary: str  # 从 NPC 视角的事件摘要
    emotion: Optional[str] = None  # 整体情绪基调
    participants: List[str] = Field(default_factory=list)  # 参与者 ID

    # 完整对话记录
    transcript: List[TranscriptMessage] = Field(default_factory=list)

    # 元数据
    message_count: int = 0
    token_count: int = 0
    created_at: datetime = Field(default_factory=datetime.now)


class EventNode(BaseModel):
    """
    子事件/原子事件节点

    表示大事件中的一个具体发生点。
    """

    id: str  # e.g. "event_20260126_女神官表达担忧"
    type: Literal["event"] = "event"
    name: str  # 子事件名

    # 重要性
    importance: float = 0.5

    # 核心属性
    day: int  # 游戏日
    summary: str  # 从 NPC 视角的子事件描述
    emotion: Optional[str] = None  # 当时的情绪
    participants: List[str] = Field(default_factory=list)  # 参与者 ID

    # 指向父事件的对话片段
    transcript_range: Optional[TranscriptRange] = None

    # 或者直接保存片段（冗余但检索更快）
    transcript_snippet: List[TranscriptMessage] = Field(default_factory=list)

    # 元数据
    created_at: datetime = Field(default_factory=datetime.now)


# ==================== 边关系模型 ====================


class GraphEdgeSpec(BaseModel):
    """图谱边规格"""

    id: str
    source: str  # 源节点 ID
    target: str  # 目标节点 ID
    relation: str  # 关系类型

    # 可选属性
    weight: float = 1.0
    properties: Dict[str, Any] = Field(default_factory=dict)


# 边关系类型枚举
EDGE_RELATIONS = {
    # 事件层级关系
    "part_of": "子事件属于大事件",
    "caused": "事件因果链",
    "followed_by": "时间顺序",

    # 参与关系
    "participated": "参与者",
    "witnessed": "目击者",

    # 空间关系
    "located_in": "发生地点",
    "took_place_at": "事件发生于",

    # 引用关系
    "mentions": "提及的实体",
    "concerns": "涉及",
    "affects": "影响",

    # 人物关系
    "knows": "认识",
    "trusts": "信任",
    "distrusts": "不信任",
    "allied_with": "同盟",
    "enemy_of": "敌对",

    # 知识关系
    "learned_from": "从...学到",
    "teaches": "教授",

    # 所有权
    "owns": "拥有",
    "wants": "想要",
}


# ==================== 图谱提取结果模型 ====================


class ExtractedElements(BaseModel):
    """Flash 提取的图谱元素"""

    # 事件组（大事件）
    event_group: Optional[EventGroupNode] = None

    # 子事件列表
    sub_events: List[EventNode] = Field(default_factory=list)

    # 其他新节点（人物、地点、物品等）
    new_nodes: List[Dict[str, Any]] = Field(default_factory=list)

    # 边关系
    edges: List[GraphEdgeSpec] = Field(default_factory=list)

    # 状态更新（如情绪变化、关系变化）
    state_updates: Dict[str, Any] = Field(default_factory=dict)


class GraphizeResult(BaseModel):
    """图谱化结果"""

    # 成功状态
    success: bool = True
    error: Optional[str] = None

    # 节点统计
    nodes_added: int = 0
    nodes_updated: int = 0
    event_groups_created: int = 0
    sub_events_created: int = 0

    # 边统计
    edges_added: int = 0

    # 处理的消息
    messages_processed: int = 0
    tokens_processed: int = 0

    # 创建的节点 ID
    created_node_ids: List[str] = Field(default_factory=list)

    # 处理时间
    processing_time_ms: int = 0


class MergeResult(BaseModel):
    """图谱合并结果"""

    new_nodes: int = 0
    updated_nodes: int = 0
    new_edges: int = 0
    conflicts_resolved: int = 0

    # 详细信息
    new_node_ids: List[str] = Field(default_factory=list)
    updated_node_ids: List[str] = Field(default_factory=list)
    new_edge_ids: List[str] = Field(default_factory=list)


# ==================== 记忆检索结果模型 ====================


class MemoryWithContext(BaseModel):
    """带上下文的记忆检索结果"""

    # 摘要（自然语言）
    summary: str

    # 完整上下文片段
    full_context: List[Dict[str, Any]] = Field(default_factory=list)

    # 来源节点
    source_node_ids: List[str] = Field(default_factory=list)

    # 激活值
    activation_scores: Dict[str, float] = Field(default_factory=dict)

    # 置信度
    confidence: float = 1.0


# ==================== LLM 提取 Prompt 相关模型 ====================


class GraphExtractionPrompt(BaseModel):
    """图谱提取 Prompt 的输入"""

    # 对话记录
    transcript: List[TranscriptMessage]

    # NPC 信息
    npc_id: str
    npc_name: str
    npc_personality: Optional[str] = None

    # 上下文
    game_day: int = 1
    location: Optional[str] = None
    scene_description: Optional[str] = None

    # 已有的重要节点（供引用）
    existing_nodes: List[Dict[str, Any]] = Field(default_factory=list)


class GraphExtractionOutput(BaseModel):
    """图谱提取 Prompt 的预期输出"""

    event_group: Dict[str, Any]  # EventGroupNode 的字典形式
    sub_events: List[Dict[str, Any]]  # EventNode 的字典形式列表
    new_nodes: List[Dict[str, Any]] = Field(default_factory=list)
    edges: List[Dict[str, Any]] = Field(default_factory=list)
    state_updates: Dict[str, Any] = Field(default_factory=dict)
