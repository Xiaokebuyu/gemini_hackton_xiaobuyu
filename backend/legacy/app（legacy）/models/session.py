"""
会话数据模型
"""
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


class SessionBase(BaseModel):
    """会话基础模型"""
    pass


class SessionCreate(SessionBase):
    """创建会话请求"""
    pass


class Session(SessionBase):
    """会话完整模型"""
    session_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    
    model_config = ConfigDict(from_attributes=True)
