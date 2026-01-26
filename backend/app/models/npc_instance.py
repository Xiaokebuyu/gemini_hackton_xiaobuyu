"""
NPC Instance Pool data models.

双层认知系统的 NPC 实例数据模型：
- Pro（工作记忆）：200K 上下文窗口，处理当前对话
- Flash（潜意识记忆）：图谱检索 + 激活扩散，长期记忆存储
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class NPCConfig(BaseModel):
    """NPC 配置信息"""

    # 基本信息
    npc_id: str
    name: str
    occupation: Optional[str] = None
    age: Optional[int] = None

    # 性格与行为
    personality: str = ""  # 性格描述
    speech_pattern: str = ""  # 说话风格
    example_dialogue: Optional[str] = None  # 示例对话

    # 系统提示词
    system_prompt: Optional[str] = None

    # 扩展属性
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GraphizeTrigger(BaseModel):
    """图谱化触发信号"""

    should_graphize: bool = False  # 是否需要图谱化
    messages_to_graphize: List[Any] = Field(default_factory=list)  # 需要图谱化的消息
    urgency: float = 0.0  # 紧急程度（token 使用率）
    reason: str = ""  # 触发原因


class MemoryInjection(BaseModel):
    """记忆注入数据（Flash -> Pro）"""

    text: str  # 翻译后的自然语言记忆
    source_nodes: List[str] = Field(default_factory=list)  # 来源节点 ID
    confidence: float = 1.0  # 检索置信度
    query_intent: str = ""  # 原始查询意图

    # 可选的完整上下文
    full_context: Optional[List[Dict[str, Any]]] = None  # 完整对话片段


class QueryUnderstanding(BaseModel):
    """Flash 对查询的理解结果"""

    intent: str  # 查询意图
    seed_nodes: List[str] = Field(default_factory=list)  # 识别出的种子节点
    confidence: float = 1.0  # 理解置信度
    search_scope: Literal["recent", "all", "specific"] = "all"  # 搜索范围


@dataclass
class NPCInstanceState:
    """NPC 实例运行时状态"""

    npc_id: str
    world_id: str

    # 活跃状态
    is_active: bool = True  # 是否活跃（在对话中）
    last_access: datetime = field(default_factory=datetime.now)  # 最后访问时间（LRU）

    # 对话状态
    current_conversation_id: Optional[str] = None  # 当前对话 ID
    conversation_turn_count: int = 0  # 对话轮次计数

    # Token 使用统计
    total_tokens_used: int = 0  # 总共使用的 token 数
    graphize_count: int = 0  # 图谱化次数

    # 持久化状态
    is_dirty: bool = False  # 是否有未保存的更改
    last_persist: Optional[datetime] = None  # 最后持久化时间


class NPCInstanceInfo(BaseModel):
    """NPC 实例信息（用于 API 返回）"""

    npc_id: str
    world_id: str
    name: str
    is_active: bool
    last_access: datetime
    context_tokens: int  # 当前上下文 token 数
    context_usage_ratio: float  # 上下文使用率
    graphize_count: int  # 图谱化次数

    class Config:
        from_attributes = True


class NPCInstanceCreateRequest(BaseModel):
    """创建 NPC 实例请求"""

    npc_id: str
    world_id: str
    config: Optional[NPCConfig] = None
    preload_memory: bool = True  # 是否预加载记忆


class NPCInstanceChatRequest(BaseModel):
    """NPC 实例对话请求"""

    message: str
    conversation_id: Optional[str] = None  # 可选的对话 ID（用于恢复）
    inject_memory: bool = True  # 是否自动注入相关记忆


class NPCInstanceChatResponse(BaseModel):
    """NPC 实例对话响应"""

    response: str
    npc_id: str

    # 记忆相关
    memory_recalled: bool = False
    recalled_memory: Optional[str] = None
    memory_source_nodes: List[str] = Field(default_factory=list)

    # 图谱化相关
    graphize_triggered: bool = False
    nodes_graphized: int = 0

    # 调试信息
    thinking: Optional[str] = None
    context_usage_ratio: float = 0.0
