"""
MCP 数据模型

定义上下文处理 MCP 使用的所有数据结构：
- APIMessage: 标准API消息格式
- Topic: 主题（大类）
- Thread: 话题（具体讨论点）
- Insight: 见解版本
- ArchivedMessage: 归档消息
- TopicClassification: 主题分类结果
- ArchiveResult: 归档结果
- AssembledContext: 组装后的上下文
- TopicState: 话题状态
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
import tiktoken


# ============================================================
# Token 计数工具
# ============================================================

def count_tokens(text: str) -> int:
    """
    计算文本的 token 数（使用 tiktoken）
    
    Args:
        text: 输入文本
        
    Returns:
        token 数量
    """
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception:
        # 降级：按字符估算（约4字符=1token）
        return len(text) // 4


# ============================================================
# 消息相关模型
# ============================================================

@dataclass
class APIMessage:
    """
    标准 API 消息格式
    
    用于消息流中的消息存储，包含 token 计数缓存
    """
    message_id: str
    role: str  # "user" | "assistant" | "system"
    content: str
    timestamp: datetime
    token_count: int = 0  # 缓存的 token 数
    
    def __post_init__(self):
        """初始化后自动计算 token 数"""
        if self.token_count == 0:
            self.token_count = count_tokens(self.content)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "message_id": self.message_id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "token_count": self.token_count,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "APIMessage":
        """从字典创建实例"""
        timestamp = data.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        elif timestamp is None:
            timestamp = datetime.now()
            
        return cls(
            message_id=data["message_id"],
            role=data["role"],
            content=data["content"],
            timestamp=timestamp,
            token_count=data.get("token_count", 0),
        )


# ============================================================
# 主题 - 话题 - 见解 三层结构
# ============================================================

class Topic(BaseModel):
    """
    主题（大类）
    
    例如："Python编程"、"项目架构"、"数据库设计"
    """
    topic_id: str
    title: str
    summary: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    
    class Config:
        from_attributes = True


class TopicCreate(BaseModel):
    """创建主题请求"""
    topic_id: str
    title: str
    summary: str = ""


class Thread(BaseModel):
    """
    话题（具体讨论点）
    
    属于某个主题下的具体话题
    例如：主题"Python编程"下的话题"列表推导式"、"装饰器"
    """
    thread_id: str
    topic_id: str
    title: str
    summary: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    
    class Config:
        from_attributes = True


class ThreadCreate(BaseModel):
    """创建话题请求"""
    thread_id: str
    topic_id: str
    title: str
    summary: str = ""


class Insight(BaseModel):
    """
    见解版本
    
    记录用户在某时刻对某话题的理解
    每次讨论创建新版本，追踪理解的演变
    """
    insight_id: str
    thread_id: str
    version: int
    content: str
    source_message_ids: List[str] = Field(default_factory=list)
    evolution_note: str = ""  # 与前次理解的变化说明
    retrieval_count: int = 0  # 被调取次数
    created_at: datetime = Field(default_factory=datetime.now)
    
    class Config:
        from_attributes = True


class InsightCreate(BaseModel):
    """创建见解请求"""
    insight_id: str
    thread_id: str
    version: int
    content: str
    source_message_ids: List[str] = Field(default_factory=list)
    evolution_note: str = ""


class ArchivedMessage(BaseModel):
    """
    归档消息
    
    记录已归档消息的索引信息，用于追溯
    """
    message_id: str
    topic_id: str
    thread_id: str
    role: str
    content: str
    archived_at: datetime = Field(default_factory=datetime.now)
    
    class Config:
        from_attributes = True


# ============================================================
# 分类与归档结果
# ============================================================

@dataclass
class TopicClassification:
    """
    主题分类结果
    
    由 LLM 分析消息后返回的分类信息
    """
    topic_id: str           # 主题ID（新建或已有）
    topic_title: str        # 主题标题
    thread_id: str          # 话题ID（新建或已有）
    thread_title: str       # 话题标题
    is_new_topic: bool      # 是否新主题
    is_new_thread: bool     # 是否新话题


@dataclass
class ArchiveResult:
    """
    归档结果
    
    归档操作完成后返回的信息
    """
    archived_message_ids: List[str]
    topic_id: str
    thread_id: str
    insight_id: str
    insight_version: int


# ============================================================
# 上下文组装
# ============================================================

@dataclass
class AssembledContext:
    """
    组装后的上下文
    
    包含发送给 LLM 的完整上下文信息
    """
    system_prompt: str
    topic_summaries: str
    retrieved_history: Optional[str]
    active_messages: List[APIMessage]
    current_topic_id: Optional[str]
    
    def to_api_messages(self) -> List[Dict[str, str]]:
        """
        转换为 API 消息格式
        
        Returns:
            符合 LLM API 格式的消息列表
        """
        messages = []
        
        # 系统消息（包含组装的上下文）
        system_content = self.system_prompt
        if self.topic_summaries:
            system_content += f"\n\n## 已讨论的话题\n{self.topic_summaries}"
        if self.retrieved_history:
            system_content += f"\n\n## 检索的历史记录\n{self.retrieved_history}"
        
        messages.append({
            "role": "system",
            "content": system_content
        })
        
        # 活跃窗口消息
        for msg in self.active_messages:
            messages.append({
                "role": msg.role,
                "content": msg.content
            })
        
        return messages
    
    def get_total_tokens(self) -> int:
        """计算组装后的总 token 数"""
        total = count_tokens(self.system_prompt)
        if self.topic_summaries:
            total += count_tokens(self.topic_summaries)
        if self.retrieved_history:
            total += count_tokens(self.retrieved_history)
        total += sum(msg.token_count for msg in self.active_messages)
        return total


# ============================================================
# 话题状态
# ============================================================

@dataclass
class TopicState:
    """
    话题状态
    
    追踪当前活跃的话题信息
    """
    current_topic_id: Optional[str] = None
    current_thread_id: Optional[str] = None
    last_retrieval_at: Optional[datetime] = None
    
    def is_active(self) -> bool:
        """是否有活跃话题"""
        return self.current_thread_id is not None
    
    def clear(self) -> None:
        """清除状态"""
        self.current_topic_id = None
        self.current_thread_id = None
        self.last_retrieval_at = None
