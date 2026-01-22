"""
主题线程数据模型
"""
from datetime import datetime
from typing import List
from pydantic import BaseModel, Field, ConfigDict


class TopicBase(BaseModel):
    """主题基础模型"""
    title: str
    summary: str = ""
    current_artifact: str = ""


class TopicCreate(TopicBase):
    """创建主题请求"""
    pass


class TopicThread(TopicBase):
    """主题完整模型"""
    thread_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    
    model_config = ConfigDict(from_attributes=True)


class ArtifactVersion(BaseModel):
    """Artifact 历史版本"""
    version_id: str
    content: str
    created_at: datetime = Field(default_factory=datetime.now)
    message_ids: List[str] = Field(default_factory=list)
    
    model_config = ConfigDict(from_attributes=True)
