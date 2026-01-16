"""
归档服务 - 负责消息归档和 Artifact 创建
"""
from typing import List, Dict, Any, Optional
from app.config import settings
from app.models import Message, TopicCreate, TopicThread
from app.services.firestore_service import FirestoreService
from app.services.llm_service import LLMService


class ArchiveService:
    """归档服务"""
    
    def __init__(self):
        self.firestore = FirestoreService()
        self.llm = LLMService()
    
    async def check_should_archive(self, user_id: str, session_id: str) -> bool:
        """
        检查是否需要归档
        条件：未归档消息数 >= archive_threshold
        """
        count = await self.firestore.count_active_messages(user_id, session_id)
        return count >= settings.archive_threshold
    
    async def execute_archive(self, user_id: str, session_id: str) -> Optional[str]:
        """
        执行归档流程
        
        返回: thread_id（新建或合并的）
        """
        pending_messages = await self.firestore.get_pending_archive_messages(
            user_id, session_id, settings.active_window_size
        )
        if not pending_messages:
            return None
        
        analysis = await self.analyze_messages(pending_messages)
        title = analysis.get("title") or "未分类主题"
        summary = analysis.get("summary") or ""
        artifact = analysis.get("artifact") or f"# {title}\n\n"
        message_ids = [msg.message_id for msg in pending_messages]
        
        merge_target = await self.find_mergeable_topic(
            user_id, session_id, title, summary
        )
        
        if merge_target:
            thread_id = merge_target.thread_id
            await self.merge_into_topic(
                user_id, session_id, thread_id, artifact, summary, message_ids
            )
        else:
            topic = TopicCreate(
                title=title,
                summary=summary,
                current_artifact=artifact,
            )
            thread_id = await self.firestore.create_topic(user_id, session_id, topic)
            await self.firestore.save_artifact_version(
                user_id, session_id, thread_id, artifact, message_ids
            )
        
        await self.firestore.mark_messages_archived(
            user_id, session_id, message_ids, thread_id
        )
        
        return thread_id
    
    async def analyze_messages(self, messages: List[Message]) -> Dict[str, Any]:
        """
        子代理分析消息块
        """
        payload = [
            {
                "role": msg.role.value,
                "content": msg.content,
                "message_id": msg.message_id,
            }
            for msg in messages
        ]
        return await self.llm.analyze_messages_for_archive(payload)
    
    async def find_mergeable_topic(
        self,
        user_id: str,
        session_id: str,
        new_title: str,
        new_summary: str,
    ) -> Optional[TopicThread]:
        """
        查找可合并的现有 Topic
        """
        topics = await self.firestore.get_all_topics(user_id, session_id)
        if not topics:
            return None
        
        normalized_new = new_title.strip().lower()
        for topic in topics:
            if topic.title.strip().lower() == normalized_new:
                return topic
        
        for topic in topics:
            should_merge = await self.llm.should_merge_topics(
                topic.title, topic.summary, new_title, new_summary
            )
            if should_merge:
                return topic
        
        return None
    
    async def merge_into_topic(
        self,
        user_id: str,
        session_id: str,
        thread_id: str,
        new_artifact: str,
        new_summary: str,
        message_ids: List[str],
    ) -> None:
        """
        合并新内容到现有 Topic
        """
        topic = await self.firestore.get_topic(user_id, session_id, thread_id)
        if not topic:
            return
        
        merged_artifact = await self.llm.merge_artifacts(
            topic.current_artifact, new_artifact
        )
        await self.firestore.update_topic_artifact(
            user_id, session_id, thread_id, merged_artifact
        )
        
        if new_summary and not topic.summary:
            await self.firestore.update_topic_summary(
                user_id, session_id, thread_id, new_summary
            )
        
        await self.firestore.save_artifact_version(
            user_id, session_id, thread_id, merged_artifact, message_ids
        )
