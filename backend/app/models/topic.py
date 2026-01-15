"""
主题线程数据模型
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class TopicBase(BaseModel):
    """主题基础模型"""
    title: str
    current_artifact: str = ""
    parent_thread_ids: List[str] = Field(default_factory=list)
    child_thread_ids: List[str] = Field(default_factory=list)


class TopicCreate(TopicBase):
    """创建主题请求"""
    summary_embedding: Optional[List[float]] = None


class TopicThread(TopicBase):
    """主题完整模型"""
    thread_id: str
    last_active_at: datetime = Field(default_factory=datetime.now)
    summary_embedding: Optional[List[float]] = None
    
    class Config:
        from_attributes = True


class ArtifactVersion(BaseModel):
    """Artifact 历史版本"""
    version_id: str
    content: str
    created_at: datetime = Field(default_factory=datetime.now)
    message_ids: List[str] = Field(default_factory=list)
    
    class Config:
        from_attributes = True
