"""
上下文构建服务
"""
from typing import List, Optional
from app.config import settings
from app.services.firestore_service import FirestoreService
from app.models import Message, MessageRole


class ContextBuilder:
    """上下文构建器"""
    
    def __init__(self):
        """初始化服务"""
        self.firestore = FirestoreService()
    
    async def build(
        self,
        user_id: str,
        session_id: str,
        additional_messages: Optional[List[Message]] = None,
    ) -> str:
        """
        构建完整上下文
        
        结构:
        1. System Prompt
        2. 所有 Artifact
        3. 额外加载的历史消息（如果有）
        4. 活跃窗口消息
        """
        parts: List[str] = []
        
        parts.append(self.build_system_prompt())
        
        artifacts_text = await self.load_all_artifacts(user_id, session_id)
        if artifacts_text:
            parts.append(f"## 知识文档\n\n{artifacts_text}")
        
        if additional_messages:
            history_text = self.format_messages(additional_messages, "加载的历史对话")
            parts.append(history_text)
        
        active_messages = await self.firestore.get_active_messages(
            user_id, session_id, settings.active_window_size
        )
        if active_messages:
            active_text = self.format_messages(active_messages, "最近对话")
            parts.append(active_text)
        
        return "\n\n---\n\n".join(parts)
    
    def build_system_prompt(self) -> str:
        """构建系统提示词"""
        return """## 系统角色

你是一个具有长期记忆能力的智能助手。

**关于知识文档:**
- 你可以访问之前对话积累的知识文档（Artifact）
- 文档以 Markdown 格式组织，包含历史讨论的总结

**关于历史回溯:**
- 如果你需要回顾之前讨论的具体细节（而不仅仅是总结）
- 请在回复中使用 [NEED_CONTEXT: 关键词] 标记
- 例如：[NEED_CONTEXT: 列表推导式的嵌套用法]
- 系统会为你加载相关的原始对话，然后你再继续回答

**注意:**
- 只有在需要具体细节时才使用这个标记
- 如果知识文档中的信息已经足够，直接回答即可"""
    
    async def load_all_artifacts(self, user_id: str, session_id: str) -> str:
        """加载 Session 内所有 Artifact"""
        topics = await self.firestore.get_all_topics(user_id, session_id)
        if not topics:
            return ""
        
        parts: List[str] = []
        for topic in topics:
            if topic.current_artifact:
                parts.append(f"### {topic.title}\n\n{topic.current_artifact}")
        
        return "\n\n".join(parts)
    
    def format_messages(self, messages: List[Message], title: str) -> str:
        """格式化消息列表"""
        lines = [f"## {title}\n"]
        
        for msg in messages:
            if msg.role == MessageRole.USER:
                role = "USER"
            elif msg.role == MessageRole.ASSISTANT:
                role = "ASSISTANT"
            else:
                role = "SYSTEM"
            lines.append(f"**{role}**: {msg.content}\n")
        
        return "\n".join(lines)
