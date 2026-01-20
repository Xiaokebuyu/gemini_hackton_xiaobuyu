"""
聊天 API 路由 - 基于 MCP 上下文处理架构
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, Literal, Dict, Any
import json

from app.config import settings
from app.services.firestore_service import FirestoreService
from app.services.llm_service import LLMService
from app.mcp import get_mcp_server, ContextMCPServer

router = APIRouter()

# 初始化服务
firestore_service = FirestoreService()
llm_service = LLMService()


class ThinkingInfo(BaseModel):
    """思考信息 (Gemini 3)"""
    enabled: bool = False
    level: str = "medium"
    summary: str = ""
    thoughts_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class StreamStats(BaseModel):
    """消息流统计"""
    total_messages: int = 0
    total_tokens: int = 0
    active_window_messages: int = 0
    active_window_tokens: int = 0
    has_overflow: bool = False


class TopicInfo(BaseModel):
    """话题信息"""
    current_topic_id: Optional[str] = None
    current_thread_id: Optional[str] = None
    retrieval_count: int = 0


class ChatRequest(BaseModel):
    """聊天请求"""
    user_id: str
    session_id: Optional[str] = None
    message: str
    # 思考配置（可选覆盖）
    thinking_level: Optional[Literal["lowest", "low", "medium", "high"]] = None
    stream: bool = False  # 是否使用流式输出


class ChatResponse(BaseModel):
    """聊天响应"""
    session_id: str
    response: str
    thinking: ThinkingInfo
    topic_info: TopicInfo
    stream_stats: Optional[StreamStats] = None


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    主对话接口（基于 MCP 架构）
    
    处理流程:
    1. 获取 MCP Server 实例
    2. 创建或获取会话
    3. 使用 MCP Server 处理消息
       - 自动管理消息流
       - 自动组装上下文（不计入 32k）
       - 异步执行归档
    4. 返回响应
    """
    try:
        user_id = request.user_id
        message = request.message
        
        # 获取 MCP Server
        mcp_server = get_mcp_server()
        
        # 确保会话存在
        if request.session_id:
            session = await firestore_service.get_session(user_id, request.session_id)
            if not session:
                raise HTTPException(status_code=404, detail="会话不存在")
            session_id = request.session_id
        else:
            session = await firestore_service.create_session(user_id)
            session_id = session.session_id
        
        # 使用 MCP Server 处理消息
        result = await mcp_server.process_message(
            user_id=user_id,
            session_id=session_id,
            user_message=message,
            thinking_level=request.thinking_level
        )
        
        # 构建响应
        thinking_info = ThinkingInfo(
            enabled=result["thinking"]["enabled"],
            level=result["thinking"]["level"],
            summary=result["thinking"]["summary"],
            thoughts_tokens=result["thinking"]["tokens"],
        )
        
        topic_info = TopicInfo(
            current_topic_id=result["topic_info"]["current_topic_id"],
            current_thread_id=result["topic_info"]["current_thread_id"],
            retrieval_count=result["topic_info"]["retrieval_count"],
        )
        
        stream_stats = None
        if result.get("stream_stats"):
            stats = result["stream_stats"]
            stream_stats = StreamStats(
                total_messages=stats.get("total_messages", 0),
                total_tokens=stats.get("total_tokens", 0),
                active_window_messages=stats.get("active_window_messages", 0),
                active_window_tokens=stats.get("active_window_tokens", 0),
                has_overflow=stats.get("has_overflow", False),
            )
        
        return ChatResponse(
            session_id=session_id,
            response=result["response"],
            thinking=thinking_info,
            topic_info=topic_info,
            stream_stats=stream_stats,
        )
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"聊天处理错误: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"处理消息时出错: {str(e)}")


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    流式对话接口（基于 MCP 架构）
    
    返回 Server-Sent Events 格式的流式响应
    事件类型:
    - session: 会话信息
    - thought: 思考摘要片段
    - answer: 回答片段
    - done: 完成信号（包含元数据）
    - error: 错误信息
    """
    async def generate():
        try:
            user_id = request.user_id
            message = request.message
            
            # 获取 MCP Server
            mcp_server = get_mcp_server()
            
            # 确保会话存在
            if request.session_id:
                session = await firestore_service.get_session(user_id, request.session_id)
                if not session:
                    yield f"data: {json.dumps({'type': 'error', 'text': '会话不存在'})}\n\n"
                    return
                session_id = request.session_id
            else:
                session = await firestore_service.create_session(user_id)
                session_id = session.session_id
            
            # 发送会话ID
            yield f"data: {json.dumps({'type': 'session', 'session_id': session_id})}\n\n"
            
            # 获取消息流和组装上下文
            stream = mcp_server._get_or_create_stream(session_id)
            topic_state = mcp_server._get_or_create_topic_state(session_id)
            
            # 追加用户消息
            stream.append_user_message(message)
            
            # 组装上下文
            context = await mcp_server.assembler.assemble(
                stream=stream,
                user_id=user_id,
                session_id=session_id,
                retrieved_thread_id=topic_state.get_current_thread_id()
            )
            
            # 流式生成回复
            full_response = ""
            full_thoughts = ""
            
            # 将组装的上下文转换为文本
            api_messages = context.to_api_messages()
            context_text = "\n".join([m["content"] for m in api_messages])
            
            async for chunk in llm_service.generate_response_stream(
                context_text, 
                message,
                thinking_level=request.thinking_level
            ):
                if chunk["type"] == "thought":
                    full_thoughts += chunk["text"]
                    yield f"data: {json.dumps({'type': 'thought', 'text': chunk['text']})}\n\n"
                elif chunk["type"] == "answer":
                    full_response += chunk["text"]
                    yield f"data: {json.dumps({'type': 'answer', 'text': chunk['text']})}\n\n"
                elif chunk["type"] == "error":
                    yield f"data: {json.dumps({'type': 'error', 'text': chunk['text']})}\n\n"
            
            # 保存助手回复
            if full_response:
                stream.append_assistant_message(full_response)
                
                # 异步执行归档
                import asyncio
                asyncio.create_task(
                    mcp_server.archiver.process(stream, user_id, session_id)
                )
            
            # 发送完成信号
            done_data = {
                "type": "done",
                "session_id": session_id,
                "thinking_summary": full_thoughts,
                "topic_info": {
                    "current_topic_id": topic_state.get_current_topic_id(),
                    "current_thread_id": topic_state.get_current_thread_id(),
                },
                "stream_stats": stream.get_stats(),
            }
            yield f"data: {json.dumps(done_data)}\n\n"
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@router.get("/thinking/config")
async def get_thinking_config():
    """
    获取当前 Gemini 3 思考配置
    """
    return {
        "enabled": settings.thinking_enabled,
        "level": settings.thinking_level,
        "include_thoughts": settings.include_thoughts,
        "available_levels": ["lowest", "low", "medium", "high"],
        "level_descriptions": {
            "lowest": "最低延迟，几乎不思考，适合简单查询",
            "low": "低延迟，轻度思考，适合简单指令",
            "medium": "平衡模式，适合大多数任务",
            "high": "深度推理，适合复杂问题",
        }
    }


@router.get("/sessions/{user_id}/{session_id}/messages")
async def get_session_messages(user_id: str, session_id: str):
    """
    获取会话的所有消息
    """
    try:
        messages = await firestore_service.get_messages_by_session(
            user_id,
            session_id,
            limit=100
        )
        
        return {
            "session_id": session_id,
            "message_count": len(messages),
            "messages": [
                {
                    "message_id": msg.message_id,
                    "role": msg.role.value,
                    "content": msg.content,
                    "thread_id": msg.thread_id,
                    "timestamp": msg.timestamp.isoformat(),
                    "is_excluded": msg.is_excluded,
                    "is_archived": msg.is_archived,
                }
                for msg in messages
            ]
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取消息失败: {str(e)}")


@router.post("/sessions/{user_id}/create")
async def create_session(user_id: str):
    """
    创建新会话
    """
    try:
        session = await firestore_service.create_session(user_id)
        
        return {
            "session_id": session.session_id,
            "created_at": session.created_at.isoformat()
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建会话失败: {str(e)}")


# ==================== MCP 专用端点 ====================

@router.post("/mcp/tools/{tool_name}")
async def execute_mcp_tool(
    tool_name: str,
    user_id: str,
    session_id: str,
    params: Dict[str, Any] = {}
):
    """
    执行 MCP 工具
    
    可用工具:
    - retrieve_thread_history: 检索话题历史
    - list_topics: 列出所有主题和话题
    - get_insight_evolution: 获取见解演变
    - search_topics: 搜索话题
    """
    try:
        mcp_server = get_mcp_server()
        result = await mcp_server.execute_tool(
            user_id=user_id,
            session_id=session_id,
            tool_name=tool_name,
            params=params
        )
        
        return {
            "tool": tool_name,
            "result": result
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"工具执行失败: {str(e)}")


@router.get("/mcp/session/{user_id}/{session_id}/info")
async def get_mcp_session_info(user_id: str, session_id: str):
    """
    获取 MCP 会话信息
    
    包含消息流统计和话题状态
    """
    try:
        mcp_server = get_mcp_server()
        info = mcp_server.get_session_info(session_id)
        
        return info
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取会话信息失败: {str(e)}")


@router.delete("/mcp/session/{session_id}")
async def clear_mcp_session(session_id: str):
    """
    清除 MCP 会话数据（消息流和话题状态）
    
    注意：这只清除内存中的状态，不影响 Firestore 中的数据
    """
    try:
        mcp_server = get_mcp_server()
        mcp_server.clear_session(session_id)
        
        return {"message": f"会话 {session_id} 已清除"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"清除会话失败: {str(e)}")
