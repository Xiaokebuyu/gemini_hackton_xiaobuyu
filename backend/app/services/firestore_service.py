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
    
    # ==================== MCP Topic 操作（新三层结构）====================
    
    async def create_mcp_topic(
        self,
        user_id: str,
        session_id: str,
        topic_id: str,
        title: str,
        summary: str = "",
    ) -> str:
        """
        创建 MCP 主题（大类）
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            topic_id: 主题ID
            title: 主题标题
            summary: 主题摘要
            
        Returns:
            主题ID
        """
        topic_dict = {
            "topic_id": topic_id,
            "title": title,
            "summary": summary,
            "created_at": datetime.now(),
        }
        
        topic_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("topics")
            .document(topic_id)
        )
        topic_ref.set(topic_dict)
        
        return topic_id
    
    async def get_mcp_topic(
        self,
        user_id: str,
        session_id: str,
        topic_id: str,
    ) -> Optional[dict]:
        """获取 MCP 主题"""
        topic_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("topics")
            .document(topic_id)
        )
        
        doc = topic_ref.get()
        if not doc.exists:
            return None
        
        return doc.to_dict()
    
    async def get_all_mcp_topics(
        self,
        user_id: str,
        session_id: str,
    ) -> List[dict]:
        """获取所有 MCP 主题"""
        topics_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("topics")
            .order_by("created_at", direction=firestore.Query.DESCENDING)
        )
        
        docs = topics_ref.stream()
        return [doc.to_dict() for doc in docs]
    
    async def update_mcp_topic_summary(
        self,
        user_id: str,
        session_id: str,
        topic_id: str,
        summary: str,
    ) -> None:
        """更新 MCP 主题摘要"""
        topic_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("topics")
            .document(topic_id)
        )
        
        topic_ref.update({"summary": summary})
    
    # ==================== MCP Thread 操作（话题）====================
    
    async def create_thread(
        self,
        user_id: str,
        session_id: str,
        topic_id: str,
        thread_id: str,
        title: str,
        summary: str = "",
    ) -> str:
        """
        创建话题（Thread）
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            topic_id: 所属主题ID
            thread_id: 话题ID
            title: 话题标题
            summary: 话题摘要
            
        Returns:
            话题ID
        """
        thread_dict = {
            "thread_id": thread_id,
            "topic_id": topic_id,
            "title": title,
            "summary": summary,
            "created_at": datetime.now(),
        }
        
        thread_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("topics")
            .document(topic_id)
            .collection("threads")
            .document(thread_id)
        )
        thread_ref.set(thread_dict)
        
        return thread_id
    
    async def get_thread(
        self,
        user_id: str,
        session_id: str,
        topic_id: str,
        thread_id: str,
    ) -> Optional[dict]:
        """获取话题"""
        thread_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("topics")
            .document(topic_id)
            .collection("threads")
            .document(thread_id)
        )
        
        doc = thread_ref.get()
        if not doc.exists:
            return None
        
        return doc.to_dict()
    
    async def get_topic_threads(
        self,
        user_id: str,
        session_id: str,
        topic_id: str,
    ) -> List[dict]:
        """获取主题下的所有话题"""
        threads_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("topics")
            .document(topic_id)
            .collection("threads")
            .order_by("created_at", direction=firestore.Query.DESCENDING)
        )
        
        docs = threads_ref.stream()
        return [doc.to_dict() for doc in docs]
    
    async def update_thread_summary(
        self,
        user_id: str,
        session_id: str,
        topic_id: str,
        thread_id: str,
        summary: str,
    ) -> None:
        """更新话题摘要"""
        thread_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("topics")
            .document(topic_id)
            .collection("threads")
            .document(thread_id)
        )
        
        thread_ref.update({"summary": summary})
    
    async def find_thread_by_id(
        self,
        user_id: str,
        session_id: str,
        thread_id: str,
    ) -> Optional[dict]:
        """
        根据 thread_id 查找话题（遍历所有主题）
        
        Returns:
            话题字典（包含 topic_id）或 None
        """
        topics = await self.get_all_mcp_topics(user_id, session_id)
        
        for topic in topics:
            topic_id = topic.get("topic_id")
            if not topic_id:
                continue
            
            thread = await self.get_thread(user_id, session_id, topic_id, thread_id)
            if thread:
                return thread
        
        return None
    
    # ==================== MCP Insight 操作（见解）====================
    
    async def create_insight(
        self,
        user_id: str,
        session_id: str,
        topic_id: str,
        thread_id: str,
        insight_id: str,
        version: int,
        content: str,
        source_message_ids: List[str],
        evolution_note: str = "",
    ) -> str:
        """
        创建见解（Insight）
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            topic_id: 所属主题ID
            thread_id: 所属话题ID
            insight_id: 见解ID
            version: 版本号
            content: 见解内容
            source_message_ids: 来源消息ID列表
            evolution_note: 演变说明
            
        Returns:
            见解ID
        """
        insight_dict = {
            "insight_id": insight_id,
            "thread_id": thread_id,
            "version": version,
            "content": content,
            "source_message_ids": source_message_ids,
            "evolution_note": evolution_note,
            "retrieval_count": 0,
            "created_at": datetime.now(),
        }
        
        insight_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("topics")
            .document(topic_id)
            .collection("threads")
            .document(thread_id)
            .collection("insights")
            .document(insight_id)
        )
        insight_ref.set(insight_dict)
        
        return insight_id
    
    async def get_thread_insights(
        self,
        user_id: str,
        session_id: str,
        topic_id: str,
        thread_id: str,
    ) -> List[dict]:
        """
        获取话题的所有见解版本
        
        按版本号升序排列
        """
        insights_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("topics")
            .document(topic_id)
            .collection("threads")
            .document(thread_id)
            .collection("insights")
            .order_by("version")
        )
        
        docs = insights_ref.stream()
        return [doc.to_dict() for doc in docs]
    
    async def get_latest_insight(
        self,
        user_id: str,
        session_id: str,
        topic_id: str,
        thread_id: str,
    ) -> Optional[dict]:
        """获取话题的最新见解"""
        insights_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("topics")
            .document(topic_id)
            .collection("threads")
            .document(thread_id)
            .collection("insights")
            .order_by("version", direction=firestore.Query.DESCENDING)
            .limit(1)
        )
        
        docs = list(insights_ref.stream())
        if not docs:
            return None
        
        return docs[0].to_dict()
    
    async def increment_insight_retrieval_count(
        self,
        user_id: str,
        session_id: str,
        topic_id: str,
        thread_id: str,
        insight_id: str,
    ) -> None:
        """增加见解的调取计数"""
        insight_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("topics")
            .document(topic_id)
            .collection("threads")
            .document(thread_id)
            .collection("insights")
            .document(insight_id)
        )
        
        insight_ref.update({
            "retrieval_count": firestore.Increment(1)
        })
    
    # ==================== MCP 归档消息操作 ====================
    
    async def save_archived_message(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
        topic_id: str,
        thread_id: str,
        role: str,
        content: str,
    ) -> None:
        """
        保存归档消息索引
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            message_id: 消息ID
            topic_id: 所属主题ID
            thread_id: 所属话题ID
            role: 消息角色
            content: 消息内容
        """
        archived_dict = {
            "message_id": message_id,
            "topic_id": topic_id,
            "thread_id": thread_id,
            "role": role,
            "content": content,
            "archived_at": datetime.now(),
        }
        
        archived_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("archived_messages")
            .document(message_id)
        )
        archived_ref.set(archived_dict)
    
    async def get_archived_messages_by_thread(
        self,
        user_id: str,
        session_id: str,
        thread_id: str,
    ) -> List[dict]:
        """获取话题的归档消息"""
        archived_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("archived_messages")
            .where("thread_id", "==", thread_id)
            .order_by("archived_at")
        )
        
        docs = archived_ref.stream()
        return [doc.to_dict() for doc in docs]
    
    async def is_message_archived(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
    ) -> bool:
        """检查消息是否已归档"""
        archived_ref = (
            self.db.collection("users")
            .document(user_id)
            .collection("sessions")
            .document(session_id)
            .collection("archived_messages")
            .document(message_id)
        )
        
        doc = archived_ref.get()
        return doc.exists
    
    async def mark_messages_archived_mcp(
        self,
        user_id: str,
        session_id: str,
        message_ids: List[str],
        topic_id: str,
        thread_id: str,
    ) -> None:
        """
        批量标记消息为已归档（MCP 版本）
        
        同时保存归档消息索引
        """
        if not message_ids:
            return
        
        batch = self.db.batch()
        
        for message_id in message_ids:
            # 更新原消息状态
            message_ref = (
                self.db.collection("users")
                .document(user_id)
                .collection("sessions")
                .document(session_id)
                .collection("messages")
                .document(message_id)
            )
            batch.update(message_ref, {
                "is_archived": True,
                "thread_id": thread_id
            })
        
        batch.commit()
