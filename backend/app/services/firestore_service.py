"""
Firestore 数据库服务
"""
from datetime import datetime
from typing import List, Optional
from google.cloud import firestore
from app.config import settings
from app.models import (
    Session,
    TopicThread,
    TopicCreate,
    ArtifactVersion,
    Message,
    MessageCreate,
)
import uuid


class FirestoreService:
    """Firestore 数据库服务类"""
    
    def __init__(self):
        """初始化 Firestore 客户端"""
        self.db = firestore.Client(database=settings.firestore_database)
    
    # ==================== Session 操作 ====================
    
    async def create_session(self, user_id: str) -> Session:
        """创建新会话"""
        session_id = f"sess_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        now = datetime.now()
        session_dict = {
            "session_id": session_id,
            "created_at": now,
            "updated_at": now,
        }
        
        session_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
        )
        session_ref.set(session_dict)
        
        return Session(**session_dict)
    
    async def get_session(self, user_id: str, session_id: str) -> Optional[Session]:
        """获取会话"""
        session_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
        )
        doc = session_ref.get()
        if not doc.exists:
            return None
        return Session(**doc.to_dict())
    
    async def update_session_timestamp(self, user_id: str, session_id: str) -> None:
        """更新会话时间戳"""
        session_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
        )
        session_ref.update({"updated_at": datetime.now()})
    
    # ==================== Message 操作 ====================
    
    async def add_message(self, user_id: str, session_id: str, message: MessageCreate) -> str:
        """添加消息"""
        message_id = f"msg_{uuid.uuid4().hex[:12]}"
        message_dict = {
            "message_id": message_id,
            "role": message.role.value,
            "content": message.content,
            "thread_id": message.thread_id,
            "is_excluded": message.is_excluded,
            "is_archived": message.is_archived,
            "timestamp": datetime.now(),
        }
        
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
    
    async def get_messages_by_session(self, user_id: str, session_id: str, limit: int = 100) -> List[Message]:
        """获取会话消息（按时间正序）"""
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
        return [Message(**doc.to_dict()) for doc in docs]
    
    async def get_active_messages(self, user_id: str, session_id: str, limit: int) -> List[Message]:
        """获取未归档消息（最新 N 条，按时间正序）"""
        messages_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("messages")
            .order_by("timestamp")
        )
        
        docs = messages_ref.stream()
        messages = [Message(**doc.to_dict()) for doc in docs]
        active = [msg for msg in messages if not msg.is_archived and not msg.is_excluded]
        return active[-limit:] if limit else active
    
    async def count_active_messages(self, user_id: str, session_id: str) -> int:
        """统计未归档消息数量"""
        messages_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("messages")
        )
        
        count = 0
        for doc in messages_ref.stream():
            msg = Message(**doc.to_dict())
            if not msg.is_archived and not msg.is_excluded:
                count += 1
        return count
    
    async def get_pending_archive_messages(
        self,
        user_id: str,
        session_id: str,
        active_window_size: int,
    ) -> List[Message]:
        """获取待归档消息（活跃窗口之外的未归档消息）"""
        messages_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("messages")
            .order_by("timestamp")
        )
        
        docs = list(messages_ref.stream())
        messages = [Message(**doc.to_dict()) for doc in docs]
        active = [msg for msg in messages if not msg.is_archived and not msg.is_excluded]
        
        if len(active) <= active_window_size:
            return []
        
        return active[: len(active) - active_window_size]
    
    async def mark_messages_archived(
        self,
        user_id: str,
        session_id: str,
        message_ids: List[str],
        thread_id: str,
    ) -> None:
        """批量标记消息为已归档"""
        if not message_ids:
            return
        
        batch = self.db.batch()
        for message_id in message_ids:
            message_ref = (
                self.db.collection("users")
                .document(user_id)
                .collection("sessions")
                .document(session_id)
                .collection("messages")
                .document(message_id)
            )
            batch.update(message_ref, {"is_archived": True, "thread_id": thread_id})
        
        batch.commit()
    
    async def get_messages_by_ids(
        self,
        user_id: str,
        session_id: str,
        message_ids: List[str],
    ) -> List[Message]:
        """根据 ID 列表批量获取消息"""
        messages: List[Message] = []
        
        for message_id in message_ids:
            message_ref = (
                self.db.collection("users")
                .document(user_id)
                .collection("sessions")
                .document(session_id)
                .collection("messages")
                .document(message_id)
            )
            doc = message_ref.get()
            if doc.exists:
                messages.append(Message(**doc.to_dict()))
        
        messages.sort(key=lambda msg: msg.timestamp)
        return messages
    
    async def get_message_by_id(self, user_id: str, session_id: str, message_id: str) -> Optional[Message]:
        """根据消息ID获取单条消息"""
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
    
    async def create_topic(self, user_id: str, session_id: str, topic: TopicCreate) -> str:
        """创建主题"""
        thread_id = f"thread_{uuid.uuid4().hex[:12]}"
        topic_dict = {
            "thread_id": thread_id,
            "title": topic.title,
            "summary": topic.summary,
            "current_artifact": topic.current_artifact,
            "created_at": datetime.now(),
        }
        
        topic_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("topics")
            .document(thread_id)
        )
        topic_ref.set(topic_dict)
        
        return thread_id
    
    async def get_topic(self, user_id: str, session_id: str, thread_id: str) -> Optional[TopicThread]:
        """获取主题"""
        topic_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("topics")
            .document(thread_id)
        )
        
        doc = topic_ref.get()
        if not doc.exists:
            return None
        
        return TopicThread(**doc.to_dict())
    
    async def get_all_topics(self, user_id: str, session_id: str) -> List[TopicThread]:
        """获取所有主题"""
        topics_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("topics")
            .order_by("created_at", direction=firestore.Query.DESCENDING)
        )
        
        docs = topics_ref.stream()
        return [TopicThread(**doc.to_dict()) for doc in docs]
    
    async def update_topic_artifact(
        self,
        user_id: str,
        session_id: str,
        thread_id: str,
        artifact: str,
    ) -> None:
        """更新 Artifact"""
        topic_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("topics")
            .document(thread_id)
        )
        
        topic_ref.update({"current_artifact": artifact})
    
    async def update_topic_summary(
        self,
        user_id: str,
        session_id: str,
        thread_id: str,
        summary: str,
    ) -> None:
        """更新主题摘要"""
        topic_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("topics")
            .document(thread_id)
        )
        
        topic_ref.update({"summary": summary})
    
    # ==================== Artifact Version 操作 ====================
    
    async def save_artifact_version(
        self,
        user_id: str,
        session_id: str,
        thread_id: str,
        artifact: str,
        message_ids: List[str],
    ) -> str:
        """保存版本"""
        version_id = f"v_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        version_dict = {
            "version_id": version_id,
            "content": artifact,
            "created_at": datetime.now(),
            "message_ids": message_ids,
        }
        
        version_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("topics")
            .document(thread_id)
            .collection("artifact_versions")
            .document(version_id)
        )
        version_ref.set(version_dict)
        
        return version_id
    
    async def get_artifact_versions(
        self,
        user_id: str,
        session_id: str,
        thread_id: str,
        limit: int = 10,
    ) -> List[ArtifactVersion]:
        """获取版本列表"""
        versions_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("topics")
            .document(thread_id)
            .collection("artifact_versions")
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        
        docs = versions_ref.stream()
        return [ArtifactVersion(**doc.to_dict()) for doc in docs]
