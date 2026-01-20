"""
MCP (Model Context Protocol) 核心模块

上下文处理 MCP 架构实现，包含：
- MessageStream: 标准API消息流管理
- MessageAssembler: 上下文组装器
- TruncateArchiver: 截断归档组件
- TopicStateManager: 话题状态管理
"""

from .models import (
    APIMessage,
    Topic,
    Thread,
    Insight,
    ArchivedMessage,
    TopicClassification,
    ArchiveResult,
    AssembledContext,
    TopicState,
    count_tokens,
)

from .message_stream import MessageStream
from .truncate_archiver import TruncateArchiver
from .message_assembler import MessageAssembler
from .topic_state import TopicStateManager, TopicStateData
from .tools import TOOLS, get_tool_by_name, get_all_tools, is_retrieval_tool
from .server import ContextMCPServer, get_mcp_server

__all__ = [
    # 数据模型
    "APIMessage",
    "Topic",
    "Thread",
    "Insight",
    "ArchivedMessage",
    "TopicClassification",
    "ArchiveResult",
    "AssembledContext",
    "TopicState",
    "TopicStateData",
    "count_tokens",
    # 组件
    "MessageStream",
    "TruncateArchiver",
    "MessageAssembler",
    "TopicStateManager",
    # 工具
    "TOOLS",
    "get_tool_by_name",
    "get_all_tools",
    "is_retrieval_tool",
    # Server
    "ContextMCPServer",
    "get_mcp_server",
]
