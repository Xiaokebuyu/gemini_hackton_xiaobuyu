"""
工具执行器

负责执行模型返回的工具调用，返回结果
这是一个基础框架，具体实现需要对接实际数据源
"""
from typing import Dict, Any, Optional, Callable, Awaitable
from dataclasses import dataclass, field
from app.tools.definitions import ToolName


@dataclass
class ToolResult:
    """工具执行结果"""
    success: bool
    data: Any = None
    error: str = ""
    tool_name: str = ""


class ToolExecutor:
    """
    工具执行器
    
    提供工具注册和执行功能，支持同步和异步工具函数
    """
    
    def __init__(self):
        """初始化执行器"""
        self._handlers: Dict[str, Callable] = {}
        self._register_default_handlers()
    
    def _register_default_handlers(self):
        """注册默认的工具处理器（演示用）"""
        self.register(ToolName.SEARCH_MEMORY, self._demo_search_memory)
        self.register(ToolName.GET_ARTIFACT, self._demo_get_artifact)
        self.register(ToolName.LIST_TOPICS, self._demo_list_topics)
        self.register(ToolName.GET_MESSAGES, self._demo_get_messages)
    
    def register(self, tool_name: ToolName, handler: Callable) -> None:
        """
        注册工具处理器
        
        Args:
            tool_name: 工具名称
            handler: 处理函数，可以是同步或异步函数
        """
        self._handlers[tool_name.value] = handler
    
    async def execute(self, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        """
        执行工具调用
        
        Args:
            tool_name: 工具名称
            args: 工具参数
            
        Returns:
            ToolResult: 执行结果
        """
        handler = self._handlers.get(tool_name)
        
        if not handler:
            return ToolResult(
                success=False,
                error=f"Unknown tool: {tool_name}",
                tool_name=tool_name
            )
        
        try:
            # 支持同步和异步处理器
            import asyncio
            if asyncio.iscoroutinefunction(handler):
                result = await handler(**args)
            else:
                result = handler(**args)
            
            return ToolResult(
                success=True,
                data=result,
                tool_name=tool_name
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                error=str(e),
                tool_name=tool_name
            )
    
    # ==================== 演示用默认处理器 ====================
    
    def _demo_search_memory(
        self,
        keywords: list,
        search_type: str = "all",
        limit: int = 5
    ) -> Dict[str, Any]:
        """演示：搜索记忆"""
        # 这里返回模拟数据，实际使用时对接真实数据源
        return {
            "results": [
                {
                    "type": "artifact",
                    "topic_id": "thread_demo001",
                    "title": f"关于 {keywords[0]} 的讨论",
                    "snippet": f"这是一段关于 {', '.join(keywords)} 的内容摘要...",
                    "relevance": 0.95
                }
            ],
            "total": 1,
            "search_type": search_type
        }
    
    def _demo_get_artifact(
        self,
        topic_id: str,
        section: str = None
    ) -> Dict[str, Any]:
        """演示：获取 Artifact"""
        content = f"""# {topic_id} 的知识文档

## 概述
这是一个演示用的知识文档。

## 详细内容
这里是详细的内容...
<!-- sources: msg_001, msg_002 -->
"""
        return {
            "topic_id": topic_id,
            "title": f"主题 {topic_id}",
            "content": content if not section else f"## {section}\n\n内容片段...",
            "section": section
        }
    
    def _demo_list_topics(
        self,
        include_summary: bool = True
    ) -> Dict[str, Any]:
        """演示：列出主题"""
        topics = [
            {
                "topic_id": "thread_demo001",
                "title": "Python 基础",
                "summary": "关于 Python 语法、数据类型等基础知识" if include_summary else None,
                "created_at": "2025-01-15T10:00:00Z"
            },
            {
                "topic_id": "thread_demo002", 
                "title": "Web 开发",
                "summary": "FastAPI、前端开发相关内容" if include_summary else None,
                "created_at": "2025-01-16T14:30:00Z"
            }
        ]
        return {
            "topics": topics,
            "total": len(topics)
        }
    
    def _demo_get_messages(
        self,
        message_ids: list = None,
        recent_count: int = None
    ) -> Dict[str, Any]:
        """演示：获取消息"""
        messages = [
            {
                "message_id": "msg_001",
                "role": "user",
                "content": "这是用户的一条消息",
                "timestamp": "2025-01-17T09:00:00Z"
            },
            {
                "message_id": "msg_002",
                "role": "assistant", 
                "content": "这是助手的回复",
                "timestamp": "2025-01-17T09:01:00Z"
            }
        ]
        
        if message_ids:
            messages = [m for m in messages if m["message_id"] in message_ids]
        elif recent_count:
            messages = messages[-recent_count:]
        
        return {
            "messages": messages,
            "total": len(messages)
        }
