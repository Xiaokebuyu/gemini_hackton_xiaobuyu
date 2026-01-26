"""
Context Window data models.

200K 上下文窗口的数据模型，支持：
- 消息管理
- Token 计数
- 窗口状态追踪
"""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class WindowMessage(BaseModel):
    """上下文窗口中的消息"""

    id: str  # 消息唯一 ID
    role: Literal["user", "assistant", "system"] = "user"
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)

    # Token 信息
    token_count: int = 0  # 消息的 token 数

    # 元数据
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # 图谱化状态
    is_graphized: bool = False  # 是否已被图谱化
    graphized_at: Optional[datetime] = None  # 图谱化时间


class ContextWindowState(BaseModel):
    """上下文窗口状态"""

    npc_id: str
    world_id: str

    # 容量配置
    max_tokens: int = 200_000  # 最大 token 容量
    graphize_threshold: float = 0.9  # 图谱化阈值（90%）
    keep_recent_tokens: int = 50_000  # 图谱化后保留的 token 数

    # 当前状态
    current_tokens: int = 0  # 当前使用的 token 数
    system_prompt_tokens: int = 0  # 系统提示词占用的 token 数

    # 消息列表
    messages: List[WindowMessage] = Field(default_factory=list)

    # 统计
    total_messages_processed: int = 0  # 处理过的消息总数
    total_messages_graphized: int = 0  # 被图谱化的消息数

    # 时间戳
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @property
    def usage_ratio(self) -> float:
        """计算 token 使用率"""
        if self.max_tokens == 0:
            return 0.0
        return self.current_tokens / self.max_tokens

    @property
    def available_tokens(self) -> int:
        """计算剩余可用 token 数"""
        return max(0, self.max_tokens - self.current_tokens)

    @property
    def should_graphize(self) -> bool:
        """检查是否需要图谱化"""
        return self.usage_ratio >= self.graphize_threshold


class ContextWindowSnapshot(BaseModel):
    """上下文窗口快照（用于持久化）"""

    npc_id: str
    world_id: str

    # 配置
    max_tokens: int
    graphize_threshold: float
    keep_recent_tokens: int

    # 状态
    current_tokens: int
    system_prompt_tokens: int
    message_count: int

    # 消息 ID 列表（不包含完整内容）
    message_ids: List[str] = Field(default_factory=list)

    # 统计
    total_messages_processed: int
    total_messages_graphized: int

    # 时间戳
    snapshot_at: datetime = Field(default_factory=datetime.now)


class AddMessageResult(BaseModel):
    """添加消息的结果"""

    message_id: str
    token_count: int  # 消息的 token 数
    current_tokens: int  # 添加后的总 token 数
    usage_ratio: float  # 添加后的使用率

    # 图谱化触发
    should_graphize: bool = False
    messages_to_graphize_count: int = 0


class GraphizeRequest(BaseModel):
    """图谱化请求"""

    npc_id: str
    world_id: str

    # 需要图谱化的消息
    messages: List[WindowMessage]

    # 上下文信息
    conversation_summary: Optional[str] = None  # 对话摘要
    current_scene: Optional[str] = None  # 当前场景
    game_day: int = 1  # 游戏日


class RemoveGraphizedResult(BaseModel):
    """移除已图谱化消息的结果"""

    removed_count: int  # 移除的消息数
    tokens_freed: int  # 释放的 token 数
    current_tokens: int  # 移除后的总 token 数
    usage_ratio: float  # 移除后的使用率
