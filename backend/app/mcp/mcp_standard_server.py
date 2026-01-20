"""
标准 MCP (Model Context Protocol) 服务器实现

使用官方 MCP SDK 实现标准协议，可被任何 MCP 客户端连接使用。

传输方式支持：
- stdio: 本地进程通信（适合 Claude Desktop 等）
- streamable-http: HTTP 传输（适合远程调用）
- sse: Server-Sent Events（适合 Web 应用）

使用方法：
1. 独立运行: python -m app.mcp.mcp_standard_server
2. 作为模块导入: from app.mcp.mcp_standard_server import mcp
"""

import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime
import json
import os
import sys

# 添加项目路径（独立运行时需要）
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from mcp.server.fastmcp import FastMCP, Context

# 导入内部服务（延迟导入避免循环依赖）
_firestore = None
_llm = None
_internal_server = None
_embedding = None
_memory_gateway = None


def _get_services():
    """延迟初始化服务（避免启动时的依赖问题）"""
    global _firestore, _llm, _internal_server, _embedding, _memory_gateway
    
    if _firestore is None:
        from app.services.firestore_service import FirestoreService
        from app.services.llm_service import LLMService
        from app.services.embedding_service import EmbeddingService
        from .server import ContextMCPServer
        from .memory_gateway import MemoryGateway
        
        _firestore = FirestoreService()
        _llm = LLMService()
        _embedding = EmbeddingService()
        _internal_server = ContextMCPServer()
        _memory_gateway = MemoryGateway(_firestore, _llm, _embedding)
    
    return _firestore, _llm, _internal_server, _memory_gateway


# ============================================================
# MCP Server 初始化
# ============================================================

mcp = FastMCP(
    name="Context Memory MCP Server",
    instructions="""
上下文记忆 MCP 服务器 - 提供智能对话记忆管理能力

核心功能：
- 自动归档超出活跃窗口的对话内容
- 按 主题(Topic) → 话题(Thread) → 见解(Insight) 三层结构组织记忆
- 追踪用户理解的演变过程
- 支持历史内容的检索和搜索
- 通过 memory_request 提供整理好的上下文片段

适用场景：
- 需要长期记忆的 AI 助手
- 知识管理和学习追踪
- 多轮对话的上下文管理
""",
)


# ============================================================
# Memory Gateway Tools (single entry for external LLM)
# ============================================================

@mcp.tool()
async def memory_request(
    user_id: str,
    session_id: str,
    need: str,
    user_message: Optional[str] = None,
    window_tokens: int = 120000,
    insert_budget_tokens: int = 20000,
) -> str:
    """
    Memory gateway request (LLM entry).

    Returns assembled memory context for the given need.
    """
    _, _, _, memory_gateway = _get_services()
    result = await memory_gateway.memory_request(
        user_id=user_id,
        session_id=session_id,
        need=need,
        user_message=user_message,
        window_tokens=window_tokens,
        insert_budget_tokens=insert_budget_tokens,
    )
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
async def session_snapshot(
    user_id: str,
    session_id: str,
    window_tokens: int = 120000,
    insert_budget_tokens: int = 20000,
) -> str:
    """
    Get a session snapshot for LLM bootstrap.
    """
    _, _, _, memory_gateway = _get_services()
    result = await memory_gateway.session_snapshot(
        user_id=user_id,
        session_id=session_id,
        window_tokens=window_tokens,
        insert_budget_tokens=insert_budget_tokens,
    )
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
async def memory_commit(
    user_id: str,
    session_id: str,
    messages: List[Dict[str, Any]],
    window_tokens: int = 120000,
) -> str:
    """
    Commit messages to memory store (internal orchestrator).
    """
    _, _, _, memory_gateway = _get_services()
    result = await memory_gateway.memory_commit(
        user_id=user_id,
        session_id=session_id,
        messages=messages,
        window_tokens=window_tokens,
    )
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)



# ============================================================
# MCP Resources（资源）
# ============================================================

@mcp.resource("topics://{user_id}/{session_id}")
async def resource_list_topics(user_id: str, session_id: str) -> str:
    """
    获取会话的所有主题和话题（资源形式）
    
    提供结构化的主题列表数据，适合客户端展示或进一步处理。
    """
    firestore, _, _, _ = _get_services()
    
    topics = await firestore.get_all_mcp_topics(user_id, session_id)
    
    result = []
    for topic in topics:
        topic_id = topic.get("topic_id", "")
        threads = await firestore.get_topic_threads(user_id, session_id, topic_id)
        
        result.append({
            "topic_id": topic_id,
            "title": topic.get("title", ""),
            "summary": topic.get("summary", ""),
            "created_at": str(topic.get("created_at", "")),
            "threads": [
                {
                    "thread_id": t.get("thread_id", ""),
                    "title": t.get("title", ""),
                    "summary": t.get("summary", ""),
                }
                for t in threads
            ]
        })
    
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.resource("topic://{user_id}/{session_id}/{topic_id}")
async def resource_get_topic(user_id: str, session_id: str, topic_id: str) -> str:
    """
    获取单个主题的详细信息
    """
    firestore, _, _, _ = _get_services()
    
    topic = await firestore.get_mcp_topic(user_id, session_id, topic_id)
    
    if not topic:
        return json.dumps({"error": "主题不存在"})
    
    threads = await firestore.get_topic_threads(user_id, session_id, topic_id)
    
    result = {
        "topic_id": topic.get("topic_id", ""),
        "title": topic.get("title", ""),
        "summary": topic.get("summary", ""),
        "created_at": str(topic.get("created_at", "")),
        "threads": [
            {
                "thread_id": t.get("thread_id", ""),
                "title": t.get("title", ""),
                "summary": t.get("summary", ""),
            }
            for t in threads
        ]
    }
    
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.resource("thread://{user_id}/{session_id}/{thread_id}")
async def resource_get_thread(user_id: str, session_id: str, thread_id: str) -> str:
    """
    获取话题的详细信息，包括所有见解版本
    """
    firestore, _, _, _ = _get_services()
    
    thread = await firestore.find_thread_by_id(user_id, session_id, thread_id)
    
    if not thread:
        return json.dumps({"error": "话题不存在"})
    
    topic_id = thread.get("topic_id", "")
    insights = await firestore.get_thread_insights(user_id, session_id, topic_id, thread_id)
    
    result = {
        "thread_id": thread_id,
        "topic_id": topic_id,
        "title": thread.get("title", ""),
        "summary": thread.get("summary", ""),
        "created_at": str(thread.get("created_at", "")),
        "insights": [
            {
                "insight_id": i.get("insight_id", ""),
                "version": i.get("version", 0),
                "content": i.get("content", ""),
                "evolution_note": i.get("evolution_note", ""),
                "retrieval_count": i.get("retrieval_count", 0),
                "created_at": str(i.get("created_at", "")),
            }
            for i in insights
        ]
    }
    
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.resource("insight://{user_id}/{session_id}/{thread_id}/{version}")
async def resource_get_insight(
    user_id: str, 
    session_id: str, 
    thread_id: str, 
    version: str
) -> str:
    """
    获取特定版本的见解内容
    """
    firestore, _, _, _ = _get_services()
    
    thread = await firestore.find_thread_by_id(user_id, session_id, thread_id)
    
    if not thread:
        return json.dumps({"error": "话题不存在"})
    
    topic_id = thread.get("topic_id", "")
    insights = await firestore.get_thread_insights(user_id, session_id, topic_id, thread_id)
    
    target_version = int(version)
    for insight in insights:
        if insight.get("version") == target_version:
            return json.dumps({
                "insight_id": insight.get("insight_id", ""),
                "thread_id": thread_id,
                "version": target_version,
                "content": insight.get("content", ""),
                "evolution_note": insight.get("evolution_note", ""),
                "source_message_ids": insight.get("source_message_ids", []),
                "created_at": str(insight.get("created_at", "")),
            }, ensure_ascii=False, indent=2)
    
    return json.dumps({"error": f"版本 {version} 不存在"})


@mcp.resource("session://{session_id}/stream")
async def resource_get_session_stream(session_id: str) -> str:
    """
    获取会话的消息流状态
    """
    _, _, internal_server, _ = _get_services()
    
    info = internal_server.get_session_info(session_id)
    return json.dumps(info, ensure_ascii=False, indent=2, default=str)


# ============================================================
# 服务器启动
# ============================================================

def run_server(transport: str = "stdio"):
    """
    启动 MCP 服务器
    
    Args:
        transport: 传输方式
            - "stdio": 标准输入输出（本地进程通信）
            - "streamable-http": HTTP 传输
            - "sse": Server-Sent Events
    """
    print(f"Starting Context Memory MCP Server with {transport} transport...")
    mcp.run(transport=transport)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Context Memory MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="Transport method (default: stdio)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for HTTP transport (default: 8080)"
    )
    
    args = parser.parse_args()
    
    # 设置环境变量（HTTP 传输时使用）
    if args.transport in ["streamable-http", "sse"]:
        os.environ["MCP_HTTP_PORT"] = str(args.port)
    
    run_server(args.transport)
