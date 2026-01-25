"""
消息组装器

职责：
- 组装发送给 LLM 的完整上下文
- 组装区域不计入 32k 限制
- 支持话题连续性（AI 不切换就用当前话题）
"""

from typing import List, Optional, Dict, Any, TYPE_CHECKING
from datetime import datetime

from .models import APIMessage, AssembledContext
from .message_stream import MessageStream

if TYPE_CHECKING:
    from app.legacy_mcp.firestore_service import FirestoreService


class MessageAssembler:
    """
    消息组装器
    
    组装结构（发送给 LLM）：
    ┌────────────────────────────────────────────┐
    │ 1. 系统提示                                 │
    │    - 角色定义                               │
    │    - 可用工具说明                           │
    │    - 当前话题上下文（如有）                   │
    ├────────────────────────────────────────────┤
    │ 2. 话题总结区                               │
    │    - 所有归档话题的摘要列表                   │
    │    - 让 AI 知道"讨论过什么"                  │
    ├────────────────────────────────────────────┤
    │ 3. 检索历史区（可选）                        │
    │    - AI 调用工具请求的历史记录                │
    │    - 按需加载                               │
    ├────────────────────────────────────────────┤
    │ 4. 活跃窗口                                 │
    │    - 消息流末尾 32k tokens                  │
    │    - 最近的对话内容                          │
    └────────────────────────────────────────────┘
    
    关键特性：
    - 组装内容（1-3）独立于 32k 计算
    - 支持被动式话题连续（AI 不切换就继续）
    - 话题总结让 AI 知道"讨论过什么"
    """
    
    def __init__(self, firestore: "FirestoreService"):
        """
        初始化组装器
        
        Args:
            firestore: Firestore 服务实例
        """
        self.firestore = firestore
        self._current_topic_id: Optional[str] = None
        self._current_thread_id: Optional[str] = None
    
    # ========== 主组装方法 ==========
    
    async def assemble(
        self,
        stream: MessageStream,
        user_id: str,
        session_id: str,
        retrieved_thread_id: Optional[str] = None
    ) -> AssembledContext:
        """
        组装完整上下文
        
        Args:
            stream: 消息流
            user_id: 用户ID
            session_id: 会话ID
            retrieved_thread_id: AI 请求检索的话题ID（可选）
            
        Returns:
            组装后的上下文
        """
        # 1. 构建系统提示
        system_prompt = self._build_system_prompt()
        
        # 2. 加载话题总结
        topic_summaries = await self._load_topic_summaries(user_id, session_id)
        
        # 3. 加载检索历史（如果 AI 请求了）
        retrieved_history = None
        if retrieved_thread_id:
            retrieved_history = await self._load_thread_history(
                user_id, session_id, retrieved_thread_id
            )
            # 更新当前话题为检索的话题
            await self._update_current_topic(
                user_id, session_id, retrieved_thread_id
            )
        
        # 4. 获取活跃窗口
        active_messages = stream.get_active_window()
        
        return AssembledContext(
            system_prompt=system_prompt,
            topic_summaries=topic_summaries,
            retrieved_history=retrieved_history,
            active_messages=active_messages,
            current_topic_id=self._current_thread_id
        )
    
    # ========== 话题状态管理 ==========
    
    def set_current_topic(self, topic_id: str, thread_id: str) -> None:
        """
        设置当前话题（由 AI 工具调用触发）
        
        Args:
            topic_id: 主题ID
            thread_id: 话题ID
        """
        self._current_topic_id = topic_id
        self._current_thread_id = thread_id
    
    def get_current_topic(self) -> Optional[str]:
        """
        获取当前主题ID
        
        Returns:
            当前主题ID或None
        """
        return self._current_topic_id
    
    def get_current_thread(self) -> Optional[str]:
        """
        获取当前话题ID
        
        Returns:
            当前话题ID或None
        """
        return self._current_thread_id
    
    def clear_current_topic(self) -> None:
        """清除当前话题（AI 明确切换话题时）"""
        self._current_topic_id = None
        self._current_thread_id = None
    
    def has_current_topic(self) -> bool:
        """
        是否有当前话题
        
        Returns:
            True 如果有活跃话题
        """
        return self._current_thread_id is not None
    
    # ========== 私有方法 ==========
    
    def _build_system_prompt(self) -> str:
        """
        构建系统提示
        
        Returns:
            系统提示文本
        """
        base_prompt = """你是一个具有长期记忆能力的智能助手。

## 可用工具

你可以使用以下工具来访问历史记忆：

1. `retrieve_thread_history(thread_id)` - 检索特定话题的历史对话
   - 当用户询问之前讨论过的内容时使用
   - 参数：thread_id（话题ID，从话题列表中获取）

2. `list_topics()` - 列出所有已讨论的主题和话题
   - 用于了解有哪些历史内容可以检索
   - 无需参数

3. `get_insight_evolution(thread_id)` - 查看某话题的理解演变历程
   - 展示用户对某话题理解的变化过程
   - 参数：thread_id（话题ID）

4. `search_topics(keyword)` - 根据关键词搜索相关话题
   - 用于快速找到相关的历史讨论
   - 参数：keyword（搜索关键词）

## 使用指南

- 当用户询问之前讨论过的内容时，先使用 `list_topics()` 查看有哪些话题
- 找到相关话题后，使用 `retrieve_thread_history(thread_id)` 获取详细内容
- 如果用户继续当前话题，无需切换，直接回答即可
- 只有当需要回顾具体细节时才需要检索"""
        
        # 添加当前话题信息
        if self._current_thread_id:
            base_prompt += f"""

## 当前话题状态

当前正在讨论的话题ID: {self._current_thread_id}
- 你可以继续在此话题上下文中回答
- 如果用户切换话题，你可以调用工具检索新话题"""
        
        return base_prompt
    
    async def _load_topic_summaries(
        self,
        user_id: str,
        session_id: str
    ) -> str:
        """
        加载所有话题的摘要
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            
        Returns:
            话题摘要文本
        """
        topics = await self.firestore.get_all_mcp_topics(user_id, session_id)
        
        if not topics:
            return ""
        
        summaries = []
        for topic in topics:
            topic_id = topic.get("topic_id")
            topic_title = topic.get("title", "未命名主题")
            topic_summary = topic.get("summary", "")
            
            # 获取主题下的话题
            threads = []
            if topic_id:
                threads = await self.firestore.get_topic_threads(
                    user_id, session_id, topic_id
                )
            
            thread_list = ", ".join([
                f"{t.get('title', '未命名')} (ID: {t.get('thread_id', '')})"
                for t in threads
            ])
            
            if thread_list:
                summaries.append(
                    f"### {topic_title}\n"
                    f"**包含话题**: {thread_list}\n"
                    f"**摘要**: {topic_summary or '暂无摘要'}"
                )
            else:
                summaries.append(
                    f"### {topic_title}\n"
                    f"**摘要**: {topic_summary or '暂无摘要'}"
                )
        
        return "\n\n".join(summaries)
    
    async def _load_thread_history(
        self,
        user_id: str,
        session_id: str,
        thread_id: str
    ) -> str:
        """
        加载特定话题的历史记录
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            thread_id: 话题ID
            
        Returns:
            历史记录文本
        """
        # 先找到话题所属的主题
        thread_info = await self.firestore.find_thread_by_id(
            user_id, session_id, thread_id
        )
        
        if not thread_info:
            return "未找到相关历史记录"
        
        topic_id = thread_info.get("topic_id")
        thread_title = thread_info.get("title", "未命名话题")
        
        # 获取该话题的所有见解版本
        insights = await self.firestore.get_thread_insights(
            user_id, session_id, topic_id, thread_id
        )
        
        if not insights:
            return f"话题「{thread_title}」暂无历史记录"
        
        history_parts = [f"## 话题：{thread_title}\n"]
        
        for insight in insights:
            version = insight.get("version", "?")
            content = insight.get("content", "")
            created_at = insight.get("created_at")
            evolution_note = insight.get("evolution_note", "")
            
            # 格式化时间
            time_str = ""
            if created_at:
                if isinstance(created_at, datetime):
                    time_str = created_at.strftime("%Y-%m-%d %H:%M")
                else:
                    time_str = str(created_at)
            
            history_parts.append(
                f"### 版本 {version}" + (f" ({time_str})" if time_str else "") + "\n"
                f"{content}\n"
                f"**演变说明**: {evolution_note or '首次记录'}"
            )
        
        return "\n\n---\n\n".join(history_parts)
    
    async def _update_current_topic(
        self,
        user_id: str,
        session_id: str,
        thread_id: str
    ) -> None:
        """
        更新当前话题状态
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            thread_id: 话题ID
        """
        # 找到话题所属的主题
        thread_info = await self.firestore.find_thread_by_id(
            user_id, session_id, thread_id
        )
        
        if thread_info:
            self._current_topic_id = thread_info.get("topic_id")
            self._current_thread_id = thread_id
    
    # ========== 静态方法 ==========
    
    @staticmethod
    def format_for_display(context: AssembledContext) -> str:
        """
        格式化上下文用于显示（调试用）
        
        Args:
            context: 组装后的上下文
            
        Returns:
            格式化的文本
        """
        parts = [
            "=" * 50,
            "ASSEMBLED CONTEXT",
            "=" * 50,
            "",
            "## System Prompt",
            context.system_prompt[:500] + "..." if len(context.system_prompt) > 500 else context.system_prompt,
            "",
        ]
        
        if context.topic_summaries:
            parts.extend([
                "## Topic Summaries",
                context.topic_summaries[:500] + "..." if len(context.topic_summaries) > 500 else context.topic_summaries,
                "",
            ])
        
        if context.retrieved_history:
            parts.extend([
                "## Retrieved History",
                context.retrieved_history[:500] + "..." if len(context.retrieved_history) > 500 else context.retrieved_history,
                "",
            ])
        
        parts.extend([
            f"## Active Messages ({len(context.active_messages)} messages)",
            f"Current Topic ID: {context.current_topic_id or 'None'}",
            f"Total Tokens: {context.get_total_tokens()}",
            "=" * 50,
        ])
        
        return "\n".join(parts)
