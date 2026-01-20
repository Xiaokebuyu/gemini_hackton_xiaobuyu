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


def _get_services():
    """延迟初始化服务（避免启动时的依赖问题）"""
    global _firestore, _llm, _internal_server
    
    if _firestore is None:
        from app.services.firestore_service import FirestoreService
        from app.services.llm_service import LLMService
        from .server import ContextMCPServer
        
        _firestore = FirestoreService()
        _llm = LLMService()
        _internal_server = ContextMCPServer()
    
    return _firestore, _llm, _internal_server


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

适用场景：
- 需要长期记忆的 AI 助手
- 知识管理和学习追踪
- 多轮对话的上下文管理
""",
)


# ============================================================
# MCP Tools（工具）
# ============================================================

@mcp.tool()
async def retrieve_thread_history(
    user_id: str,
    session_id: str,
    thread_id: str
) -> str:
    """
    检索特定话题的历史对话和见解演变
    
    当用户询问之前讨论过的具体内容时使用此工具。
    返回该话题的所有见解版本，包括每次讨论的演变说明。
    
    Args:
        user_id: 用户ID
        session_id: 会话ID
        thread_id: 话题ID（可从 list_topics 获取）
        
    Returns:
        话题的历史记录，包含所有见解版本
    """
    firestore, _, internal_server = _get_services()
    
    result = await internal_server.execute_tool(
        user_id=user_id,
        session_id=session_id,
        tool_name="retrieve_thread_history",
        params={"thread_id": thread_id}
    )
    
    return result


@mcp.tool()
async def list_topics(
    user_id: str,
    session_id: str
) -> str:
    """
    列出所有已讨论的主题和话题
    
    用于了解有哪些历史内容可以检索。
    返回主题列表及其下的话题，包含话题ID供后续检索使用。
    
    Args:
        user_id: 用户ID
        session_id: 会话ID
        
    Returns:
        主题和话题的层级列表
    """
    firestore, _, internal_server = _get_services()
    
    result = await internal_server.execute_tool(
        user_id=user_id,
        session_id=session_id,
        tool_name="list_topics",
        params={}
    )
    
    return result


@mcp.tool()
async def get_insight_evolution(
    user_id: str,
    session_id: str,
    thread_id: str
) -> str:
    """
    获取某话题的见解演变历程
    
    展示用户对某话题理解的变化过程。
    可以看到每次讨论后理解是如何演变的，包括演变说明。
    
    Args:
        user_id: 用户ID
        session_id: 会话ID
        thread_id: 话题ID
        
    Returns:
        见解的演变历程，按版本排列
    """
    firestore, _, internal_server = _get_services()
    
    result = await internal_server.execute_tool(
        user_id=user_id,
        session_id=session_id,
        tool_name="get_insight_evolution",
        params={"thread_id": thread_id}
    )
    
    return result


@mcp.tool()
async def search_topics(
    user_id: str,
    session_id: str,
    keyword: str
) -> str:
    """
    根据关键词搜索相关话题
    
    用于快速找到相关的历史讨论。
    搜索范围包括主题标题、话题标题和摘要。
    
    Args:
        user_id: 用户ID
        session_id: 会话ID
        keyword: 搜索关键词
        
    Returns:
        匹配的主题和话题列表
    """
    firestore, _, internal_server = _get_services()
    
    result = await internal_server.execute_tool(
        user_id=user_id,
        session_id=session_id,
        tool_name="search_topics",
        params={"keyword": keyword}
    )
    
    return result


@mcp.tool()
async def process_message(
    user_id: str,
    session_id: str,
    message: str,
    thinking_level: str = "medium"
) -> str:
    """
    处理用户消息并生成回复（核心对话接口）
    
    完整的对话处理流程：
    1. 管理消息流（自动追踪 32k token 窗口）
    2. 组装上下文（系统提示 + 话题总结 + 活跃消息）
    3. 调用 LLM 生成回复
    4. 异步执行归档（超出窗口的内容）
    
    Args:
        user_id: 用户ID
        session_id: 会话ID
        message: 用户消息内容
        thinking_level: 思考层级 (lowest/low/medium/high)
        
    Returns:
        JSON 格式的响应，包含回复内容和元数据
    """
    _, _, internal_server = _get_services()
    
    result = await internal_server.process_message(
        user_id=user_id,
        session_id=session_id,
        user_message=message,
        thinking_level=thinking_level
    )
    
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
async def get_session_stats(
    session_id: str
) -> str:
    """
    获取会话的消息流统计信息
    
    查看当前会话的状态，包括消息数量、token 使用情况、归档状态等。
    
    Args:
        session_id: 会话ID
        
    Returns:
        JSON 格式的会话统计信息
    """
    _, _, internal_server = _get_services()
    
    info = internal_server.get_session_info(session_id)
    return json.dumps(info, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
async def clear_session(
    session_id: str
) -> str:
    """
    清除会话的内存数据
    
    清除消息流和话题状态（不影响 Firestore 中的归档数据）。
    用于重置会话状态或释放内存。
    
    Args:
        session_id: 会话ID
        
    Returns:
        操作结果
    """
    _, _, internal_server = _get_services()
    
    internal_server.clear_session(session_id)
    return f"会话 {session_id} 的内存数据已清除"


# ============================================================
# MCP Resources（资源）
# ============================================================

@mcp.resource("topics://{user_id}/{session_id}")
async def resource_list_topics(user_id: str, session_id: str) -> str:
    """
    获取会话的所有主题和话题（资源形式）
    
    提供结构化的主题列表数据，适合客户端展示或进一步处理。
    """
    firestore, _, _ = _get_services()
    
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
    firestore, _, _ = _get_services()
    
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
    firestore, _, _ = _get_services()
    
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
    firestore, _, _ = _get_services()
    
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
    _, _, internal_server = _get_services()
    
    info = internal_server.get_session_info(session_id)
    return json.dumps(info, ensure_ascii=False, indent=2, default=str)


# ============================================================
# MCP Prompts（提示词模板）
# ============================================================

@mcp.prompt()
def memory_assistant_prompt(
    user_id: str,
    session_id: str,
    task_description: str = "帮助用户回答问题"
) -> str:
    """
    生成具有记忆能力的助手提示词
    
    使用此提示词模板来初始化一个具有长期记忆能力的 AI 助手。
    助手会自动管理对话历史，并在需要时检索相关内容。
    
    Args:
        user_id: 用户ID
        session_id: 会话ID
        task_description: 任务描述
    """
    return f"""你是一个具有长期记忆能力的智能助手。

## 用户信息
- 用户ID: {user_id}
- 会话ID: {session_id}

## 任务
{task_description}

## 记忆系统

你连接到了一个上下文记忆系统，具有以下能力：

### 自动功能
- 对话内容会自动按 32k token 窗口管理
- 超出窗口的内容会自动归档并分类
- 归档按 主题 → 话题 → 见解 三层结构组织

### 可用工具
1. `list_topics` - 查看所有已归档的主题和话题
2. `retrieve_thread_history` - 检索特定话题的历史内容
3. `get_insight_evolution` - 查看某话题理解的演变过程
4. `search_topics` - 按关键词搜索相关话题

### 使用指南
- 当用户询问之前讨论过的内容时，先用 `list_topics` 查看有哪些话题
- 找到相关话题后，用 `retrieve_thread_history` 获取详细内容
- 如果用户继续当前话题，直接回答即可，无需检索
- 只有需要回顾具体细节时才需要使用检索工具

请基于以上能力，为用户提供连贯、有记忆的对话体验。"""


@mcp.prompt()
def knowledge_tracker_prompt(
    user_id: str,
    session_id: str,
    learning_topic: str
) -> str:
    """
    生成知识追踪助手提示词
    
    用于追踪用户对某个主题的学习进度和理解演变。
    
    Args:
        user_id: 用户ID
        session_id: 会话ID
        learning_topic: 学习主题
    """
    return f"""你是一个知识追踪助手，帮助用户学习和追踪对「{learning_topic}」的理解。

## 用户信息
- 用户ID: {user_id}
- 会话ID: {session_id}
- 学习主题: {learning_topic}

## 追踪功能

### 记忆系统
系统会自动记录每次讨论的要点，并追踪用户理解的演变：
- 每次讨论会创建新的「见解版本」
- 系统会对比前后版本，生成「演变说明」
- 你可以使用 `get_insight_evolution` 查看用户理解的变化过程

### 你的职责
1. 帮助用户理解 {learning_topic} 的概念
2. 关注用户理解的变化，指出进步或仍需改进的地方
3. 定期使用 `get_insight_evolution` 回顾用户的学习历程
4. 基于用户的理解层次调整讲解深度

### 互动建议
- 用简单的问题检验用户的理解
- 发现误解时温和地纠正
- 建立知识之间的关联
- 适时总结学习进度

请开始帮助用户学习 {learning_topic}。"""


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
