"""
消息数据模型
"""
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class MessageRole(str, Enum):
    """消息角色"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MessageBase(BaseModel):
    """消息基础模型"""
    role: MessageRole
    content: str
    thread_id: Optional[str] = None
    is_excluded: bool = False


class MessageCreate(MessageBase):
    """创建消息请求"""
    pass


class Message(MessageBase):
    """消息完整模型"""
    message_id: str
    timestamp: datetime = Field(default_factory=datetime.now)
    
    class Config:
        from_attributes = True
