"""
MCP Server 主入口

上下文处理 MCP Server 实现，提供：
- 消息处理主流程
- 工具调用处理
- 会话管理
"""

import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime
import uuid

from .models import APIMessage, AssembledContext
from .message_stream import MessageStream
from .message_assembler import MessageAssembler
from .truncate_archiver import TruncateArchiver
from .topic_state import TopicStateManager
TOOLS: List[Dict[str, Any]] = []

from app.services.firestore_service import FirestoreService
from app.services.llm_service import LLMService
from app.services.embedding_service import EmbeddingService


class ContextMCPServer:
    """
    上下文处理 MCP Server
    
    核心功能：
    1. 管理消息流（每个会话一个）
    2. 处理用户消息，组装上下文，调用 LLM
    3. 处理工具调用，更新话题状态
    4. 异步执行归档操作
    
    架构：
    ┌─────────────────────────────────────────┐
    │              ContextMCPServer           │
    ├─────────────────────────────────────────┤
    │  streams: Dict[session_id, MessageStream]│
    │  assembler: MessageAssembler            │
    │  archiver: TruncateArchiver             │
    │  topic_states: Dict[session_id, Manager]│
    └─────────────────────────────────────────┘
    """
    
    def __init__(self):
        """初始化 MCP Server"""
        self.firestore = FirestoreService()
        self.llm = LLMService()
        self.embedding = EmbeddingService()
        
        # 每个会话的消息流
        self.streams: Dict[str, MessageStream] = {}
        
        # 共享的组装器和归档器
        self.assembler = MessageAssembler(self.firestore)
        self.archiver = TruncateArchiver(self.firestore, self.llm, self.embedding)
        
        # 每个会话的话题状态
        self.topic_states: Dict[str, TopicStateManager] = {}
    
    # ========== 主处理流程 ==========
    
    async def process_message(
        self,
        user_id: str,
        session_id: str,
        user_message: str,
        thinking_level: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        处理用户消息的主流程
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            user_message: 用户消息内容
            thinking_level: 思考层级（可选）
            
        Returns:
            响应字典，包含：
            - response: 助手回复
            - thinking: 思考元数据（如果启用）
            - tool_calls: 工具调用信息（如果有）
            - topic_info: 当前话题信息
        """
        # 1. 获取或创建消息流
        stream = self._get_or_create_stream(session_id)
        topic_state = self._get_or_create_topic_state(session_id)
        
        # 2. 追加用户消息
        user_msg = stream.append_user_message(user_message)
        
        # 3. 组装上下文
        context = await self.assembler.assemble(
            stream=stream,
            user_id=user_id,
            session_id=session_id,
            retrieved_thread_id=topic_state.get_current_thread_id()
        )
        
        # 4. 调用 LLM
        api_messages = context.to_api_messages()
        llm_response = await self.llm.generate_with_tools(
            messages=api_messages,
            tools=TOOLS,
            thinking_level=thinking_level
        )
        
        # 5. 处理响应和工具调用
        tool_calls = self._extract_tool_calls(llm_response.raw_response)
        
        # 6. 如果有工具调用，执行并重新生成
        if tool_calls:
            tool_results = await self._execute_tool_calls(
                user_id, session_id, tool_calls, topic_state
            )
            
            # 重新组装上下文（包含工具结果）
            context = await self.assembler.assemble(
                stream=stream,
                user_id=user_id,
                session_id=session_id,
                retrieved_thread_id=topic_state.get_current_thread_id()
            )
            
            # 重新生成响应
            api_messages = context.to_api_messages()
            # 添加工具结果到消息
            for result in tool_results:
                api_messages.append({
                    "role": "function",
                    "content": result["result"]
                })
            
            llm_response = await self.llm.generate_response(
                context="\n".join([m["content"] for m in api_messages]),
                user_query=user_message,
                thinking_level=thinking_level
            )
        
        # 7. 追加助手消息
        assistant_msg = stream.append_assistant_message(llm_response.text)
        
        # 8. 异步执行归档（不阻塞响应）
        asyncio.create_task(
            self._process_archive(stream, user_id, session_id)
        )
        
        # 9. 构建响应
        return {
            "response": llm_response.text,
            "thinking": {
                "enabled": llm_response.thinking.thinking_enabled,
                "level": llm_response.thinking.thinking_level,
                "summary": llm_response.thinking.thoughts_summary,
                "tokens": llm_response.thinking.thoughts_token_count,
            },
            "tool_calls": tool_calls,
            "topic_info": {
                "current_topic_id": topic_state.get_current_topic_id(),
                "current_thread_id": topic_state.get_current_thread_id(),
                "retrieval_count": topic_state.get_retrieval_count(),
            },
            "stream_stats": stream.get_stats(),
        }
    
    # ========== 工具执行 ==========
    
    async def execute_tool(
        self,
        user_id: str,
        session_id: str,
        tool_name: str,
        params: Dict[str, Any]
    ) -> str:
        """
        执行单个工具
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            tool_name: 工具名称
            params: 工具参数
            
        Returns:
            工具执行结果
        """
        topic_state = self._get_or_create_topic_state(session_id)
        
        if tool_name == "retrieve_thread_history":
            return await self._tool_retrieve_thread_history(
                user_id, session_id, params, topic_state
            )
        elif tool_name == "list_topics":
            return await self._tool_list_topics(user_id, session_id)
        elif tool_name == "get_insight_evolution":
            return await self._tool_get_insight_evolution(
                user_id, session_id, params, topic_state
            )
        elif tool_name == "search_topics":
            return await self._tool_search_topics(
                user_id, session_id, params
            )
        else:
            return f"未知工具: {tool_name}"
    
    async def _tool_retrieve_thread_history(
        self,
        user_id: str,
        session_id: str,
        params: Dict[str, Any],
        topic_state: TopicStateManager
    ) -> str:
        """检索话题历史"""
        thread_id = params.get("thread_id")
        if not thread_id:
            return "错误：缺少 thread_id 参数"
        
        # 查找话题
        thread_info = await self.firestore.find_thread_by_id(
            user_id, session_id, thread_id
        )
        
        if not thread_info:
            return f"未找到话题: {thread_id}"
        
        topic_id = thread_info.get("topic_id")
        
        # 更新话题状态
        topic_state.on_tool_call(
            "retrieve_thread_history",
            params,
            topic_id=topic_id
        )
        
        # 获取见解历史
        insights = await self.firestore.get_thread_insights(
            user_id, session_id, topic_id, thread_id
        )
        
        if not insights:
            return f"话题「{thread_info.get('title', thread_id)}」暂无历史记录"
        
        # 格式化输出
        parts = [f"## 话题：{thread_info.get('title', '未命名')}\n"]
        for insight in insights:
            version = insight.get("version", "?")
            content = insight.get("content", "")
            evolution = insight.get("evolution_note", "首次记录")
            parts.append(f"### 版本 {version}\n{content}\n**演变说明**: {evolution}")
        
        return "\n\n---\n\n".join(parts)
    
    async def _tool_list_topics(
        self,
        user_id: str,
        session_id: str
    ) -> str:
        """列出所有主题和话题"""
        topics = await self.firestore.get_all_mcp_topics(user_id, session_id)
        
        if not topics:
            return "暂无已归档的主题和话题"
        
        parts = ["# 已归档的主题和话题\n"]
        
        for topic in topics:
            topic_id = topic.get("topic_id", "")
            topic_title = topic.get("title", "未命名")
            topic_summary = topic.get("summary", "")
            
            parts.append(f"## {topic_title}")
            if topic_summary:
                parts.append(f"摘要: {topic_summary}")
            
            # 获取话题
            threads = await self.firestore.get_topic_threads(
                user_id, session_id, topic_id
            )
            
            if threads:
                parts.append("话题列表:")
                for thread in threads:
                    thread_id = thread.get("thread_id", "")
                    thread_title = thread.get("title", "未命名")
                    thread_summary = thread.get("summary", "")
                    parts.append(f"  - {thread_title} (ID: {thread_id})")
                    if thread_summary:
                        parts.append(f"    摘要: {thread_summary}")
            
            parts.append("")
        
        return "\n".join(parts)
    
    async def _tool_get_insight_evolution(
        self,
        user_id: str,
        session_id: str,
        params: Dict[str, Any],
        topic_state: TopicStateManager
    ) -> str:
        """获取见解演变历程"""
        thread_id = params.get("thread_id")
        if not thread_id:
            return "错误：缺少 thread_id 参数"
        
        # 查找话题
        thread_info = await self.firestore.find_thread_by_id(
            user_id, session_id, thread_id
        )
        
        if not thread_info:
            return f"未找到话题: {thread_id}"
        
        topic_id = thread_info.get("topic_id")
        
        # 更新话题状态
        topic_state.on_tool_call(
            "get_insight_evolution",
            params,
            topic_id=topic_id
        )
        
        # 获取见解历史
        insights = await self.firestore.get_thread_insights(
            user_id, session_id, topic_id, thread_id
        )
        
        if not insights:
            return f"话题「{thread_info.get('title', thread_id)}」暂无见解记录"
        
        # 格式化演变历程
        parts = [f"# 话题「{thread_info.get('title', '未命名')}」的见解演变\n"]
        
        for i, insight in enumerate(insights):
            version = insight.get("version", i + 1)
            content = insight.get("content", "")
            evolution = insight.get("evolution_note", "")
            created_at = insight.get("created_at", "")
            
            parts.append(f"## 版本 {version}")
            if created_at:
                parts.append(f"时间: {created_at}")
            parts.append(f"\n{content}")
            if evolution:
                parts.append(f"\n**与上一版本的变化**: {evolution}")
            parts.append("")
        
        return "\n".join(parts)
    
    async def _tool_search_topics(
        self,
        user_id: str,
        session_id: str,
        params: Dict[str, Any]
    ) -> str:
        """搜索话题"""
        keyword = params.get("keyword", "").lower()
        if not keyword:
            return "错误：缺少 keyword 参数"
        
        topics = await self.firestore.get_all_mcp_topics(user_id, session_id)
        
        results = []
        for topic in topics:
            topic_id = topic.get("topic_id", "")
            topic_title = topic.get("title", "")
            topic_summary = topic.get("summary", "")
            
            # 检查主题是否匹配
            topic_match = (
                keyword in topic_title.lower() or
                keyword in topic_summary.lower()
            )
            
            # 获取并检查话题
            threads = await self.firestore.get_topic_threads(
                user_id, session_id, topic_id
            )
            
            matching_threads = []
            for thread in threads:
                thread_title = thread.get("title", "")
                thread_summary = thread.get("summary", "")
                if (keyword in thread_title.lower() or
                    keyword in thread_summary.lower()):
                    matching_threads.append(thread)
            
            if topic_match or matching_threads:
                results.append({
                    "topic": topic,
                    "threads": matching_threads if matching_threads else threads
                })
        
        if not results:
            return f"未找到与「{keyword}」相关的话题"
        
        # 格式化结果
        parts = [f"# 搜索结果：「{keyword}」\n"]
        for item in results:
            topic = item["topic"]
            parts.append(f"## {topic.get('title', '未命名')}")
            for thread in item["threads"]:
                parts.append(
                    f"  - {thread.get('title', '未命名')} "
                    f"(ID: {thread.get('thread_id', '')})"
                )
            parts.append("")
        
        return "\n".join(parts)
    
    # ========== 私有方法 ==========
    
    def _get_or_create_stream(self, session_id: str) -> MessageStream:
        """获取或创建消息流"""
        if session_id not in self.streams:
            self.streams[session_id] = MessageStream(session_id)
        return self.streams[session_id]
    
    def _get_or_create_topic_state(self, session_id: str) -> TopicStateManager:
        """获取或创建话题状态管理器"""
        if session_id not in self.topic_states:
            self.topic_states[session_id] = TopicStateManager()
        return self.topic_states[session_id]
    
    def _extract_tool_calls(self, raw_response: Any) -> List[Dict[str, Any]]:
        """从 LLM 响应中提取工具调用"""
        tool_calls = []
        
        if not raw_response:
            return tool_calls
        
        try:
            if hasattr(raw_response, 'candidates') and raw_response.candidates:
                for part in raw_response.candidates[0].content.parts:
                    if hasattr(part, 'function_call') and part.function_call:
                        fc = part.function_call
                        tool_calls.append({
                            "name": fc.name,
                            "args": dict(fc.args) if fc.args else {}
                        })
        except Exception:
            pass
        
        return tool_calls
    
    async def _execute_tool_calls(
        self,
        user_id: str,
        session_id: str,
        tool_calls: List[Dict[str, Any]],
        topic_state: TopicStateManager
    ) -> List[Dict[str, Any]]:
        """执行工具调用列表"""
        results = []
        
        for call in tool_calls:
            tool_name = call.get("name", "")
            params = call.get("args", {})
            
            result = await self.execute_tool(
                user_id, session_id, tool_name, params
            )
            
            results.append({
                "tool": tool_name,
                "params": params,
                "result": result
            })
        
        return results
    
    async def _process_archive(
        self,
        stream: MessageStream,
        user_id: str,
        session_id: str
    ) -> None:
        """处理归档（异步）"""
        try:
            await self.archiver.process(stream, user_id, session_id)
        except Exception as e:
            print(f"归档处理失败: {str(e)}")
    
    # ========== 会话管理 ==========
    
    def get_stream(self, session_id: str) -> Optional[MessageStream]:
        """获取消息流"""
        return self.streams.get(session_id)
    
    def get_topic_state(self, session_id: str) -> Optional[TopicStateManager]:
        """获取话题状态"""
        return self.topic_states.get(session_id)
    
    def clear_session(self, session_id: str) -> None:
        """清除会话数据"""
        if session_id in self.streams:
            del self.streams[session_id]
        if session_id in self.topic_states:
            del self.topic_states[session_id]
    
    def get_session_info(self, session_id: str) -> Dict[str, Any]:
        """获取会话信息"""
        stream = self.streams.get(session_id)
        topic_state = self.topic_states.get(session_id)
        
        return {
            "session_id": session_id,
            "has_stream": stream is not None,
            "stream_stats": stream.get_stats() if stream else None,
            "topic_state": topic_state.to_dict() if topic_state else None,
        }


# 全局 MCP Server 实例
_mcp_server: Optional[ContextMCPServer] = None


def get_mcp_server() -> ContextMCPServer:
    """获取全局 MCP Server 实例"""
    global _mcp_server
    if _mcp_server is None:
        _mcp_server = ContextMCPServer()
    return _mcp_server
