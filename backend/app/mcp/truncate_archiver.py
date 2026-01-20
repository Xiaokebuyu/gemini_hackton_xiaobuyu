"""
截断归档组件

职责：
- 检测消息流溢出（超出 32k tokens）
- 将溢出消息按主题-话题-见解三层分类
- 每次讨论创建新的 Insight 版本
- 只对 Firestore 提供服务，不修改消息流
"""

from typing import List, Optional, TYPE_CHECKING
from datetime import datetime
import uuid
import json
import re

from .models import (
    APIMessage,
    TopicClassification,
    ArchiveResult,
    Topic,
    Thread,
    Insight,
)
from .message_stream import MessageStream

if TYPE_CHECKING:
    from app.services.firestore_service import FirestoreService
    from app.services.llm_service import LLMService


class TruncateArchiver:
    """
    截断归档组件
    
    核心功能：
    1. 检测消息流溢出
    2. 使用 LLM 进行主题-话题分类
    3. 创建见解版本，追踪用户理解的演变
    4. 保存归档数据到 Firestore
    
    三层分类结构：
    - 主题 (Topic): 大类，如"Python编程"、"项目架构"
    - 话题 (Thread): 具体讨论点，如"列表推导式"、"装饰器"
    - 见解 (Insight): 每次讨论的理解版本
    """
    
    def __init__(
        self, 
        firestore: "FirestoreService", 
        llm: "LLMService"
    ):
        """
        初始化归档组件
        
        Args:
            firestore: Firestore 服务实例
            llm: LLM 服务实例
        """
        self.firestore = firestore
        self.llm = llm
    
    # ========== 主流程 ==========
    
    async def process(
        self, 
        stream: MessageStream,
        user_id: str,
        session_id: str
    ) -> Optional[ArchiveResult]:
        """
        处理消息流，检测并执行归档
        
        调用时机：每次 LLM 响应后
        
        Args:
            stream: 消息流
            user_id: 用户ID
            session_id: 会话ID
            
        Returns:
            归档结果，如果没有需要归档的内容则返回 None
        """
        # 获取未归档的溢出消息
        overflow = stream.get_unarchived_overflow()
        
        if not overflow:
            return None
        
        # 过滤已在数据库中归档的消息
        new_overflow = await self._filter_already_archived(
            user_id, session_id, overflow
        )
        
        if not new_overflow:
            return None
        
        # 执行归档
        result = await self._archive_messages(
            user_id, session_id, new_overflow
        )
        
        # 标记消息为已归档（在消息流中）
        if result:
            stream.mark_as_archived(result.archived_message_ids)
        
        return result
    
    # ========== 分类逻辑 ==========
    
    async def classify_messages(
        self,
        messages: List[APIMessage],
        user_id: str,
        session_id: str
    ) -> TopicClassification:
        """
        使用 LLM 对消息进行主题-话题分类
        
        Args:
            messages: 待分类的消息列表
            user_id: 用户ID
            session_id: 会话ID
            
        Returns:
            分类结果
        """
        # 获取现有主题列表
        existing_topics = await self.firestore.get_all_mcp_topics(user_id, session_id)
        
        # 获取每个主题下的话题
        topics_with_threads = []
        for topic in existing_topics:
            topic_id = topic.get("topic_id")
            if topic_id:
                threads = await self.firestore.get_topic_threads(
                    user_id, session_id, topic_id
                )
                topics_with_threads.append({
                    "topic": topic,
                    "threads": threads
                })
        
        # 构建分类提示
        prompt = self._build_classification_prompt(messages, topics_with_threads)
        
        # 调用 LLM 进行分类
        try:
            result = await self.llm.classify_for_archive(prompt)
        except Exception:
            # 如果 LLM 分类失败，使用默认分类
            result = self._default_classification()
        
        return TopicClassification(
            topic_id=result.get("topic_id") or self._generate_id("topic"),
            topic_title=result.get("topic_title", "未分类"),
            thread_id=result.get("thread_id") or self._generate_id("thread"),
            thread_title=result.get("thread_title", "通用讨论"),
            is_new_topic=result.get("is_new_topic", True),
            is_new_thread=result.get("is_new_thread", True)
        )
    
    # ========== 见解版本管理 ==========
    
    async def create_insight_version(
        self,
        user_id: str,
        session_id: str,
        topic_id: str,
        thread_id: str,
        messages: List[APIMessage]
    ) -> str:
        """
        为话题创建新的见解版本
        
        每次讨论都会创建新版本，记录用户理解的演变
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            topic_id: 主题ID
            thread_id: 话题ID
            messages: 相关消息列表
            
        Returns:
            见解ID
        """
        # 获取该话题的现有见解版本
        existing_insights = await self.firestore.get_thread_insights(
            user_id, session_id, topic_id, thread_id
        )
        version = len(existing_insights) + 1
        
        # 提取本次讨论的见解
        insight_content = await self._extract_insight(messages)
        
        # 生成演变说明（与上一版本对比）
        evolution_note = ""
        if existing_insights:
            last_insight = existing_insights[-1]
            evolution_note = await self._generate_evolution_note(
                last_insight.get("content", ""),
                insight_content
            )
        
        # 创建新见解
        insight_id = self._generate_id("insight")
        await self.firestore.create_insight(
            user_id=user_id,
            session_id=session_id,
            topic_id=topic_id,
            thread_id=thread_id,
            insight_id=insight_id,
            version=version,
            content=insight_content,
            source_message_ids=[m.message_id for m in messages],
            evolution_note=evolution_note
        )
        
        return insight_id
    
    # ========== 私有方法 ==========
    
    async def _archive_messages(
        self,
        user_id: str,
        session_id: str,
        messages: List[APIMessage]
    ) -> ArchiveResult:
        """
        执行归档流程
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            messages: 待归档消息列表
            
        Returns:
            归档结果
        """
        # 1. 分类
        classification = await self.classify_messages(
            messages, user_id, session_id
        )
        
        # 2. 创建/更新主题
        if classification.is_new_topic:
            await self.firestore.create_mcp_topic(
                user_id=user_id,
                session_id=session_id,
                topic_id=classification.topic_id,
                title=classification.topic_title
            )
        
        # 3. 创建/更新话题
        if classification.is_new_thread:
            await self.firestore.create_thread(
                user_id=user_id,
                session_id=session_id,
                topic_id=classification.topic_id,
                thread_id=classification.thread_id,
                title=classification.thread_title
            )
        
        # 4. 创建见解版本
        insight_id = await self.create_insight_version(
            user_id, session_id,
            classification.topic_id,
            classification.thread_id,
            messages
        )
        
        # 5. 保存归档消息索引
        message_ids = [m.message_id for m in messages]
        for msg in messages:
            await self.firestore.save_archived_message(
                user_id=user_id,
                session_id=session_id,
                message_id=msg.message_id,
                topic_id=classification.topic_id,
                thread_id=classification.thread_id,
                role=msg.role,
                content=msg.content
            )
        
        # 6. 标记消息为已归档
        await self.firestore.mark_messages_archived_mcp(
            user_id, session_id, message_ids,
            classification.topic_id,
            classification.thread_id
        )
        
        # 7. 更新话题总结
        await self._update_thread_summary(
            user_id, session_id,
            classification.topic_id,
            classification.thread_id
        )
        
        # 获取见解版本号
        insights = await self.firestore.get_thread_insights(
            user_id, session_id,
            classification.topic_id,
            classification.thread_id
        )
        
        return ArchiveResult(
            archived_message_ids=message_ids,
            topic_id=classification.topic_id,
            thread_id=classification.thread_id,
            insight_id=insight_id,
            insight_version=len(insights)
        )
    
    async def _filter_already_archived(
        self,
        user_id: str,
        session_id: str,
        messages: List[APIMessage]
    ) -> List[APIMessage]:
        """过滤已归档的消息"""
        result = []
        for msg in messages:
            is_archived = await self.firestore.is_message_archived(
                user_id, session_id, msg.message_id
            )
            if not is_archived:
                result.append(msg)
        return result
    
    def _build_classification_prompt(
        self,
        messages: List[APIMessage],
        topics_with_threads: List[dict]
    ) -> str:
        """构建分类提示词"""
        # 构建现有主题和话题的描述
        if topics_with_threads:
            topics_desc_parts = []
            for item in topics_with_threads:
                topic = item["topic"]
                threads = item["threads"]
                topic_id = topic.get("topic_id", "")
                topic_title = topic.get("title", "")
                topic_summary = topic.get("summary", "")
                
                threads_desc = ""
                if threads:
                    thread_items = [
                        f"    - {t.get('title', '')} (ID: {t.get('thread_id', '')})"
                        for t in threads
                    ]
                    threads_desc = "\n" + "\n".join(thread_items)
                
                topics_desc_parts.append(
                    f"- {topic_title} (ID: {topic_id}): {topic_summary}{threads_desc}"
                )
            topics_desc = "\n".join(topics_desc_parts)
        else:
            topics_desc = "无（这是第一次归档）"
        
        # 构建消息文本
        messages_text = "\n".join([
            f"[{m.role}]: {m.content[:500]}{'...' if len(m.content) > 500 else ''}"
            for m in messages
        ])
        
        return f"""请分析以下对话内容，确定其主题和话题分类。

## 现有主题和话题列表
{topics_desc}

## 待分类的对话
{messages_text}

## 分类规则
1. 如果对话内容属于已有主题，使用该主题的 ID
2. 如果对话内容属于已有主题下的已有话题，使用该话题的 ID
3. 如果是全新的主题或话题，设置对应的 is_new_xxx 为 true
4. 主题应该是大类（如"Python编程"），话题应该是具体讨论点（如"列表推导式"）

请返回 JSON 格式（不要包含任何其他文字）：
{{
    "topic_id": "已有主题ID或null（新主题时为null）",
    "topic_title": "主题标题",
    "thread_id": "已有话题ID或null（新话题时为null）",
    "thread_title": "话题标题",
    "is_new_topic": true或false,
    "is_new_thread": true或false
}}"""
    
    def _default_classification(self) -> dict:
        """返回默认分类结果"""
        return {
            "topic_id": None,
            "topic_title": "未分类",
            "thread_id": None,
            "thread_title": "通用讨论",
            "is_new_topic": True,
            "is_new_thread": True
        }
    
    async def _extract_insight(self, messages: List[APIMessage]) -> str:
        """
        从消息中提取见解
        
        Args:
            messages: 消息列表
            
        Returns:
            提取的见解内容
        """
        messages_text = "\n".join([
            f"[{m.role}]: {m.content}"
            for m in messages
        ])
        
        prompt = f"""请从以下对话中提取关键见解和要点：

{messages_text}

请总结：
1. 讨论的主要内容
2. 达成的结论或理解
3. 关键的知识点
4. 用户的疑问或关注点

请用简洁的 Markdown 格式输出。"""
        
        try:
            response = await self.llm.generate_simple(prompt)
            return response
        except Exception:
            # 如果提取失败，返回简单摘要
            return self._simple_summarize(messages)
    
    def _simple_summarize(self, messages: List[APIMessage]) -> str:
        """简单摘要（备用方案）"""
        user_messages = [m for m in messages if m.role == "user"]
        if not user_messages:
            return "对话记录"
        
        # 取第一条用户消息的前200字符作为摘要
        first_msg = user_messages[0].content[:200]
        return f"用户讨论：{first_msg}..."
    
    async def _generate_evolution_note(
        self,
        previous_content: str,
        new_content: str
    ) -> str:
        """
        生成见解演变说明
        
        Args:
            previous_content: 上一版本的见解内容
            new_content: 新版本的见解内容
            
        Returns:
            演变说明
        """
        prompt = f"""请比较以下两个版本的理解，简要说明发生了什么变化：

## 之前的理解
{previous_content}

## 现在的理解
{new_content}

请用一两句话说明用户理解的演变（如：深化了对XX的认识、纠正了之前的误解、扩展了XX方面的知识等）："""
        
        try:
            response = await self.llm.generate_simple(prompt)
            return response.strip()
        except Exception:
            return "理解有所更新"
    
    async def _update_thread_summary(
        self,
        user_id: str,
        session_id: str,
        topic_id: str,
        thread_id: str
    ) -> None:
        """
        更新话题总结
        
        基于所有见解版本生成话题总结
        """
        # 获取所有见解
        insights = await self.firestore.get_thread_insights(
            user_id, session_id, topic_id, thread_id
        )
        
        if not insights:
            return
        
        # 构建总结提示
        insights_text = "\n\n".join([
            f"### 版本 {i.get('version', '?')}\n{i.get('content', '')}"
            for i in insights
        ])
        
        prompt = f"""请基于以下见解版本，生成一个简短的话题总结（50字以内）：

{insights_text}

总结："""
        
        try:
            summary = await self.llm.generate_simple(prompt)
            await self.firestore.update_thread_summary(
                user_id, session_id, topic_id, thread_id,
                summary.strip()[:100]  # 限制长度
            )
        except Exception:
            pass  # 更新失败不影响主流程
    
    def _generate_id(self, prefix: str) -> str:
        """生成唯一ID"""
        return f"{prefix}_{uuid.uuid4().hex[:12]}"
