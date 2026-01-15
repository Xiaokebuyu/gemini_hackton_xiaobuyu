"""
上下文构建服务
"""
from typing import List, Dict, Any, Optional
from app.services.firestore_service import FirestoreService
from app.services.artifact_service import ArtifactService
from app.models import Message


class ContextBuilder:
    """上下文构建器"""
    
    def __init__(self):
        """初始化服务"""
        self.firestore = FirestoreService()
        self.artifact_service = ArtifactService()
    
    async def build_full_context(
        self,
        user_id: str,
        session_id: str,
        thread_id: str,
        user_query: str,
        include_recent_messages: bool = True
    ) -> str:
        """
        构建完整的上下文
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            thread_id: 主题ID
            user_query: 用户问题
            include_recent_messages: 是否包含最近的对话历史
            
        Returns:
            str: 完整的上下文文本
        """
        context_parts = []
        
        # 1. 系统角色定义
        system_prompt = self._get_system_prompt()
        context_parts.append(system_prompt)
        
        # 2. 从 Artifact 构建上下文（骨架 + 相关血肉）
        artifact_context = await self.artifact_service.build_context_from_artifact(
            user_id,
            session_id,
            thread_id,
            user_query
        )
        
        if artifact_context:
            context_parts.append(artifact_context)
        
        # 3. 最近的对话历史（如果需要）
        if include_recent_messages:
            recent_context = await self._build_recent_messages_context(
                user_id,
                session_id,
                thread_id
            )
            if recent_context:
                context_parts.append(recent_context)
        
        # 组合所有部分
        full_context = "\n\n---\n\n".join(context_parts)
        
        return full_context
    
    def _get_system_prompt(self) -> str:
        """
        获取系统提示词
        
        Returns:
            str: 系统提示词
        """
        return """## 系统角色

你是一个具有长期记忆能力的智能助手。你可以访问之前对话的知识积累（Artifact），这些知识以结构化的 Markdown 文档形式组织。

**你的能力:**
- 记住之前讨论过的话题和决策
- 基于历史知识回答问题
- 识别新信息并更新知识库
- 保持对话的连贯性和一致性

**你的目标:**
- 提供准确、有帮助的回答
- 引用相关的历史知识（如果有）
- 保持回答简洁明了
- 在必要时承认不知道的事情"""
    
    async def _build_recent_messages_context(
        self,
        user_id: str,
        session_id: str,
        thread_id: str,
        limit: int = 10
    ) -> str:
        """
        构建最近消息的上下文
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            thread_id: 主题ID
            limit: 最多返回多少条消息
            
        Returns:
            str: 最近消息的上下文
        """
        # 获取该主题下最近的消息
        messages = await self.firestore.get_messages_by_thread(
            user_id,
            session_id,
            thread_id,
            limit=limit
        )
        
        if not messages:
            return ""
        
        context = "## 最近对话\n\n"
        
        for msg in messages:
            role_display = "用户" if msg.role.value == "user" else "助手"
            context += f"**{role_display}**: {msg.content}\n\n"
        
        return context
    
    async def get_conversation_history(
        self,
        user_id: str,
        session_id: str,
        thread_id: Optional[str] = None,
        limit: int = 20
    ) -> List[Dict[str, str]]:
        """
        获取对话历史（用于传递给 LLM）
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            thread_id: 主题ID（可选，如果不提供则获取整个会话）
            limit: 最多返回多少条消息
            
        Returns:
            List[Dict]: 对话历史，格式 [{"role": "user", "content": "..."}]
        """
        if thread_id:
            messages = await self.firestore.get_messages_by_thread(
                user_id,
                session_id,
                thread_id,
                limit=limit
            )
        else:
            messages = await self.firestore.get_messages_by_session(
                user_id,
                session_id,
                limit=limit
            )
        
        # 转换为对话格式
        conversation = []
        for msg in messages:
            conversation.append({
                "role": msg.role.value,
                "content": msg.content,
                "message_id": msg.message_id
            })
        
        return conversation
    
    async def build_context_for_artifact_update(
        self,
        user_id: str,
        session_id: str,
        thread_id: str,
        recent_message_count: int = 4
    ) -> tuple[List[Dict[str, str]], List[str]]:
        """
        构建用于 Artifact 更新的上下文
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            thread_id: 主题ID
            recent_message_count: 最近消息数量
            
        Returns:
            tuple: (对话历史, 消息ID列表)
        """
        # 获取最近的对话
        messages = await self.firestore.get_messages_by_thread(
            user_id,
            session_id,
            thread_id,
            limit=recent_message_count
        )
        
        conversation = []
        message_ids = []
        
        for msg in messages:
            conversation.append({
                "role": msg.role.value,
                "content": msg.content
            })
            message_ids.append(msg.message_id)
        
        return conversation, message_ids
