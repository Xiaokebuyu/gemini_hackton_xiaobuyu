"""
Firestore 数据库服务
"""
from datetime import datetime
from typing import List, Optional, Dict, Any
from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter
from app.config import settings
from app.models import (
    Session, SessionCreate,
    TopicThread, TopicCreate, ArtifactVersion,
    Message, MessageCreate
)
import uuid


class FirestoreService:
    """Firestore 数据库服务类"""
    
    def __init__(self):
        """初始化 Firestore 客户端"""
        self.db = firestore.Client(database=settings.firestore_database)
    
    # ==================== Session 操作 ====================
    
    async def create_session(self, user_id: str, session_data: Optional[SessionCreate] = None) -> Session:
        """
        创建新会话
        
        Args:
            user_id: 用户ID
            session_data: 会话数据
            
        Returns:
            Session: 创建的会话对象
        """
        session_id = f"sess_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        
        now = datetime.now()
        session_dict = {
            "session_id": session_id,
            "created_at": now,
            "updated_at": now,
            "current_thread_id": session_data.current_thread_id if session_data else None
        }
        
        # 保存到 Firestore
        session_ref = self.db.collection("users").document(user_id).collection("sessions").document(session_id)
        session_ref.set(session_dict)
        
        return Session(**session_dict)
    
    async def get_session(self, user_id: str, session_id: str) -> Optional[Session]:
        """
        获取会话信息
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            
        Returns:
            Optional[Session]: 会话对象，如果不存在返回 None
        """
        session_ref = self.db.collection("users").document(user_id).collection("sessions").document(session_id)
        doc = session_ref.get()
        
        if not doc.exists:
            return None
        
        return Session(**doc.to_dict())
    
    async def update_session(self, user_id: str, session_id: str, current_thread_id: str) -> None:
        """
        更新会话的当前主题
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            current_thread_id: 当前主题ID
        """
        session_ref = self.db.collection("users").document(user_id).collection("sessions").document(session_id)
        session_ref.update({
            "current_thread_id": current_thread_id,
            "updated_at": datetime.now()
        })
    
    # ==================== Message 操作 ====================
    
    async def add_message(
        self, 
        user_id: str, 
        session_id: str, 
        message: MessageCreate
    ) -> str:
        """
        添加消息到会话
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            message: 消息数据
            
        Returns:
            str: 消息ID
        """
        message_id = f"msg_{uuid.uuid4().hex[:12]}"
        
        message_dict = {
            "message_id": message_id,
            "role": message.role.value,
            "content": message.content,
            "thread_id": message.thread_id,
            "is_excluded": message.is_excluded,
            "timestamp": datetime.now()
        }
        
        # 保存到 messages 子集合
        message_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("messages")
            .document(message_id)
        )
        message_ref.set(message_dict)
        
        return message_id
    
    async def get_messages_by_session(
        self, 
        user_id: str, 
        session_id: str,
        limit: int = 100
    ) -> List[Message]:
        """
        获取会话的所有消息
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            limit: 最大返回数量
            
        Returns:
            List[Message]: 消息列表
        """
        messages_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("messages")
            .order_by("timestamp")
            .limit(limit)
        )
        
        docs = messages_ref.stream()
        messages = [Message(**doc.to_dict()) for doc in docs]
        
        return messages
    
    async def get_messages_by_thread(
        self, 
        user_id: str,
        session_id: str,
        thread_id: str,
        limit: int = 50
    ) -> List[Message]:
        """
        根据主题ID获取消息
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            thread_id: 主题ID
            limit: 最大返回数量
            
        Returns:
            List[Message]: 消息列表
        """
        messages_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("messages")
            .where(filter=FieldFilter("thread_id", "==", thread_id))
            .where(filter=FieldFilter("is_excluded", "==", False))
            .order_by("timestamp")
            .limit(limit)
        )
        
        docs = messages_ref.stream()
        messages = [Message(**doc.to_dict()) for doc in docs]
        
        return messages
    
    async def get_message_by_id(
        self,
        user_id: str,
        session_id: str,
        message_id: str
    ) -> Optional[Message]:
        """
        根据消息ID获取单条消息
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            message_id: 消息ID
            
        Returns:
            Optional[Message]: 消息对象
        """
        message_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("messages")
            .document(message_id)
        )
        
        doc = message_ref.get()
        if not doc.exists:
            return None
        
        return Message(**doc.to_dict())
    
    # ==================== Topic 操作 ====================
    
    async def create_topic(self, user_id: str, topic: TopicCreate) -> str:
        """
        创建新主题
        
        Args:
            user_id: 用户ID
            topic: 主题数据
            
        Returns:
            str: 主题ID
        """
        thread_id = f"thread_{uuid.uuid4().hex[:12]}"
        
        topic_dict = {
            "thread_id": thread_id,
            "title": topic.title,
            "current_artifact": topic.current_artifact,
            "summary_embedding": topic.summary_embedding,
            "parent_thread_ids": topic.parent_thread_ids,
            "child_thread_ids": topic.child_thread_ids,
            "last_active_at": datetime.now()
        }
        
        # 保存到 Firestore
        topic_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("topic_threads")
            .document(thread_id)
        )
        topic_ref.set(topic_dict)
        
        return thread_id
    
    async def get_topic(self, user_id: str, thread_id: str) -> Optional[TopicThread]:
        """
        获取主题信息
        
        Args:
            user_id: 用户ID
            thread_id: 主题ID
            
        Returns:
            Optional[TopicThread]: 主题对象
        """
        topic_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("topic_threads")
            .document(thread_id)
        )
        
        doc = topic_ref.get()
        if not doc.exists:
            return None
        
        return TopicThread(**doc.to_dict())
    
    async def get_all_topics(self, user_id: str) -> List[TopicThread]:
        """
        获取用户的所有主题
        
        Args:
            user_id: 用户ID
            
        Returns:
            List[TopicThread]: 主题列表
        """
        topics_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("topic_threads")
            .order_by("last_active_at", direction=firestore.Query.DESCENDING)
        )
        
        docs = topics_ref.stream()
        topics = [TopicThread(**doc.to_dict()) for doc in docs]
        
        return topics
    
    async def update_artifact(
        self, 
        user_id: str, 
        thread_id: str, 
        artifact: str
    ) -> None:
        """
        更新主题的 Artifact
        
        Args:
            user_id: 用户ID
            thread_id: 主题ID
            artifact: 新的 Artifact 内容
        """
        topic_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("topic_threads")
            .document(thread_id)
        )
        
        topic_ref.update({
            "current_artifact": artifact,
            "last_active_at": datetime.now()
        })
    
    async def update_topic_embedding(
        self,
        user_id: str,
        thread_id: str,
        embedding: List[float]
    ) -> None:
        """
        更新主题的 embedding
        
        Args:
            user_id: 用户ID
            thread_id: 主题ID
            embedding: embedding 向量
        """
        topic_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("topic_threads")
            .document(thread_id)
        )
        
        topic_ref.update({
            "summary_embedding": embedding,
            "last_active_at": datetime.now()
        })
    
    # ==================== Artifact Version 操作 ====================
    
    async def save_artifact_version(
        self,
        user_id: str,
        thread_id: str,
        artifact: str,
        message_ids: List[str]
    ) -> str:
        """
        保存 Artifact 历史版本
        
        Args:
            user_id: 用户ID
            thread_id: 主题ID
            artifact: Artifact 内容
            message_ids: 相关消息ID列表
            
        Returns:
            str: 版本ID
        """
        version_id = f"v_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        version_dict = {
            "version_id": version_id,
            "content": artifact,
            "created_at": datetime.now(),
            "message_ids": message_ids
        }
        
        version_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("topic_threads")
            .document(thread_id)
            .collection("artifact_versions")
            .document(version_id)
        )
        version_ref.set(version_dict)
        
        return version_id
    
    async def get_artifact_versions(
        self,
        user_id: str,
        thread_id: str,
        limit: int = 10
    ) -> List[ArtifactVersion]:
        """
        获取 Artifact 历史版本
        
        Args:
            user_id: 用户ID
            thread_id: 主题ID
            limit: 最大返回数量
            
        Returns:
            List[ArtifactVersion]: 版本列表
        """
        versions_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("topic_threads")
            .document(thread_id)
            .collection("artifact_versions")
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        
        docs = versions_ref.stream()
        versions = [ArtifactVersion(**doc.to_dict()) for doc in docs]
        
        return versions
