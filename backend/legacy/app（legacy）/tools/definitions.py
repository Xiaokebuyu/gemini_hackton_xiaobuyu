"""
工具声明定义

定义所有可用工具的 JSON Schema（符合 OpenAPI 规范）
"""
from enum import Enum
from typing import List, Dict, Any
from google.genai import types


class ToolName(str, Enum):
    """工具名称枚举"""
    SEARCH_MEMORY = "search_memory"
    GET_ARTIFACT = "get_artifact"
    LIST_TOPICS = "list_topics"
    GET_MESSAGES = "get_messages"


# ==================== 工具声明 ====================

SEARCH_MEMORY_DECLARATION = {
    "name": ToolName.SEARCH_MEMORY.value,
    "description": """搜索用户的长期记忆和知识库，根据关键词查找相关内容。
    适用场景：
    - 用户询问之前讨论过的话题
    - 需要回顾历史对话的具体细节
    - 查找特定主题的知识文档""",
    "parameters": {
        "type": "object",
        "properties": {
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "搜索关键词列表，例如 ['Python', '装饰器']"
            },
            "search_type": {
                "type": "string",
                "enum": ["artifact", "message", "all"],
                "description": "搜索范围：artifact=知识文档，message=历史消息，all=全部"
            },
            "limit": {
                "type": "integer",
                "description": "返回结果数量限制，默认5"
            }
        },
        "required": ["keywords"]
    }
}


GET_ARTIFACT_DECLARATION = {
    "name": ToolName.GET_ARTIFACT.value,
    "description": """获取指定主题的完整知识文档（Artifact）。
    适用场景：
    - 需要查看某个主题的完整内容
    - 已知主题ID，需要获取详细信息""",
    "parameters": {
        "type": "object",
        "properties": {
            "topic_id": {
                "type": "string",
                "description": "主题ID（thread_id）"
            },
            "section": {
                "type": "string",
                "description": "可选，指定获取的章节标题"
            }
        },
        "required": ["topic_id"]
    }
}


LIST_TOPICS_DECLARATION = {
    "name": ToolName.LIST_TOPICS.value,
    "description": """列出当前会话中的所有主题。
    适用场景：
    - 需要了解有哪些已归档的话题
    - 为后续的 get_artifact 调用获取主题ID""",
    "parameters": {
        "type": "object",
        "properties": {
            "include_summary": {
                "type": "boolean",
                "description": "是否包含主题摘要，默认 true"
            }
        },
        "required": []
    }
}


GET_MESSAGES_DECLARATION = {
    "name": ToolName.GET_MESSAGES.value,
    "description": """获取指定的历史消息。
    适用场景：
    - 需要查看原始对话内容
    - 根据消息ID获取具体消息""",
    "parameters": {
        "type": "object",
        "properties": {
            "message_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "消息ID列表"
            },
            "recent_count": {
                "type": "integer",
                "description": "获取最近N条消息（与message_ids二选一）"
            }
        },
        "required": []
    }
}


# ==================== 工具集合 ====================

TOOL_DECLARATIONS: List[Dict[str, Any]] = [
    SEARCH_MEMORY_DECLARATION,
    GET_ARTIFACT_DECLARATION,
    LIST_TOPICS_DECLARATION,
    GET_MESSAGES_DECLARATION,
]


def get_tools_config(
    enabled_tools: List[ToolName] = None,
    mode: str = "AUTO"
) -> types.GenerateContentConfig:
    """
    构建工具配置
    
    Args:
        enabled_tools: 启用的工具列表，None 表示全部启用
        mode: 函数调用模式 (AUTO/ANY/NONE/VALIDATED)
        
    Returns:
        GenerateContentConfig 配置对象
    """
    if enabled_tools is None:
        declarations = TOOL_DECLARATIONS
    else:
        enabled_names = {t.value for t in enabled_tools}
        declarations = [d for d in TOOL_DECLARATIONS if d["name"] in enabled_names]
    
    tools = types.Tool(function_declarations=declarations)
    tool_config = types.ToolConfig(
        function_calling_config=types.FunctionCallingConfig(mode=mode)
    )
    
    return types.GenerateContentConfig(
        tools=[tools],
        tool_config=tool_config
    )


def get_tool_declaration(name: ToolName) -> Dict[str, Any]:
    """获取单个工具声明"""
    for decl in TOOL_DECLARATIONS:
        if decl["name"] == name.value:
            return decl
    raise ValueError(f"Unknown tool: {name}")
