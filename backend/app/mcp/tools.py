"""
MCP 工具定义

定义上下文处理 MCP 提供的所有工具
"""

from typing import List, Dict, Any


# MCP 工具定义列表
TOOLS: List[Dict[str, Any]] = [
    {
        "name": "retrieve_thread_history",
        "description": "检索特定话题的历史对话和见解演变。当用户询问之前讨论过的具体内容时使用此工具。",
        "parameters": {
            "type": "object",
            "properties": {
                "thread_id": {
                    "type": "string",
                    "description": "话题ID，可以从 list_topics 的结果中获取"
                }
            },
            "required": ["thread_id"]
        }
    },
    {
        "name": "list_topics",
        "description": "列出所有已讨论的主题和话题。用于了解有哪些历史内容可以检索。返回主题列表及其下的话题。",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "get_insight_evolution",
        "description": "获取某话题的见解演变历程，展示用户理解的变化过程。可以看到每次讨论后理解的演变。",
        "parameters": {
            "type": "object",
            "properties": {
                "thread_id": {
                    "type": "string",
                    "description": "话题ID"
                }
            },
            "required": ["thread_id"]
        }
    },
    {
        "name": "search_topics",
        "description": "根据关键词搜索相关话题。用于快速找到相关的历史讨论。",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "搜索关键词"
                }
            },
            "required": ["keyword"]
        }
    }
]


def get_tool_by_name(name: str) -> Dict[str, Any]:
    """
    根据名称获取工具定义
    
    Args:
        name: 工具名称
        
    Returns:
        工具定义字典，如果不存在则返回空字典
    """
    for tool in TOOLS:
        if tool["name"] == name:
            return tool
    return {}


def get_all_tools() -> List[Dict[str, Any]]:
    """
    获取所有工具定义
    
    Returns:
        工具定义列表
    """
    return TOOLS.copy()


def get_tool_names() -> List[str]:
    """
    获取所有工具名称
    
    Returns:
        工具名称列表
    """
    return [tool["name"] for tool in TOOLS]


# 工具分类
RETRIEVAL_TOOLS = {"retrieve_thread_history", "get_insight_evolution"}
LISTING_TOOLS = {"list_topics", "search_topics"}


def is_retrieval_tool(name: str) -> bool:
    """
    判断是否为检索工具（会触发话题切换）
    
    Args:
        name: 工具名称
        
    Returns:
        True 如果是检索工具
    """
    return name in RETRIEVAL_TOOLS


def is_listing_tool(name: str) -> bool:
    """
    判断是否为列表工具（不触发话题切换）
    
    Args:
        name: 工具名称
        
    Returns:
        True 如果是列表工具
    """
    return name in LISTING_TOOLS
