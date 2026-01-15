"""
会话数据模型
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class SessionBase(BaseModel):
    """会话基础模型"""
    current_thread_id: Optional[str] = None


class SessionCreate(SessionBase):
    """创建会话请求"""
    pass


class Session(SessionBase):
    """会话完整模型"""
    session_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    
    class Config:
        from_attributes = True
