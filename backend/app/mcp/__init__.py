"""
MCP (Model Context Protocol) 核心模块

上下文处理 MCP 架构实现，包含：
- MessageStream: 标准API消息流管理
- MessageAssembler: 上下文组装器
- TruncateArchiver: 截断归档组件
- TopicStateManager: 话题状态管理
- mcp_standard_server: 标准 MCP 协议服务器（可被外部 MCP 客户端连接）
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

# 标准 MCP 服务器（延迟导入，避免启动时依赖问题）
def get_standard_mcp_server():
    """
    获取标准 MCP 服务器实例
    
    Returns:
        FastMCP 实例，可用于启动标准 MCP 服务
    """
    from .mcp_standard_server import mcp
    return mcp


def run_mcp_server(transport: str = "stdio"):
    """
    启动标准 MCP 服务器
    
    Args:
        transport: 传输方式
            - "stdio": 标准输入输出（本地进程通信，适合 Claude Desktop）
            - "streamable-http": HTTP 传输（适合远程调用）
            - "sse": Server-Sent Events（适合 Web 应用）
    """
    from .mcp_standard_server import run_server
    run_server(transport)


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
    # 工具定义
    "TOOLS",
    "get_tool_by_name",
    "get_all_tools",
    "is_retrieval_tool",
    # 内部 Server（用于 FastAPI 集成）
    "ContextMCPServer",
    "get_mcp_server",
    # 标准 MCP Server（用于独立运行或被 MCP 客户端连接）
    "get_standard_mcp_server",
    "run_mcp_server",
]
