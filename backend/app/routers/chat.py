"""
聊天 API 路由 - 支持 Gemini 3 思考功能
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, Literal
import json

from app.config import settings
from app.services.firestore_service import FirestoreService
from app.services.archive_service import ArchiveService
from app.services.context_loop import ContextLoop
from app.services.context_builder import ContextBuilder
from app.services.llm_service import LLMService
from app.models import MessageCreate, MessageRole

router = APIRouter()

# 初始化服务
firestore_service = FirestoreService()
archive_service = ArchiveService()
context_loop = ContextLoop()
context_builder = ContextBuilder()
llm_service = LLMService()


class ThinkingInfo(BaseModel):
    """思考信息 (Gemini 3)"""
    enabled: bool = False
    level: str = "medium"
    summary: str = ""
    thoughts_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


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
    archived: bool
    archive_topic: Optional[str]
    context_loads: int
    # 思考相关字段
    thinking: ThinkingInfo


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    主对话接口（支持思考功能）
    
    处理流程:
    1. 创建或获取会话
    2. 保存用户消息
    3. 检查并执行归档
    4. 运行上下文循环生成回复（带思考）
    5. 保存助手消息
    6. 返回响应（含思考摘要）
    """
    try:
        user_id = request.user_id
        message = request.message
        
        # 1. 创建或获取会话
        if request.session_id:
            session = await firestore_service.get_session(user_id, request.session_id)
            if not session:
                raise HTTPException(status_code=404, detail="会话不存在")
            session_id = request.session_id
        else:
            session = await firestore_service.create_session(user_id)
            session_id = session.session_id
        
        # 2. 保存用户消息
        user_message = MessageCreate(
            role=MessageRole.USER,
            content=message,
            is_excluded=False,
            is_archived=False,
        )
        await firestore_service.add_message(user_id, session_id, user_message)
        
        # 3. 检查并执行归档
        archived = False
        archive_topic = None
        if await archive_service.check_should_archive(user_id, session_id):
            thread_id = await archive_service.execute_archive(user_id, session_id)
            if thread_id:
                archived = True
                topic = await firestore_service.get_topic(user_id, session_id, thread_id)
                archive_topic = topic.title if topic else None
        
        # 4. 运行上下文循环生成回复（带思考）
        result = await context_loop.run(
            user_id, 
            session_id, 
            message,
            thinking_level=request.thinking_level
        )
        
        # 5. 保存助手回复
        assistant_message = MessageCreate(
            role=MessageRole.ASSISTANT,
            content=result.response,
            is_excluded=False,
            is_archived=False,
        )
        await firestore_service.add_message(user_id, session_id, assistant_message)
        await firestore_service.update_session_timestamp(user_id, session_id)
        
        # 6. 构建思考信息
        thinking_info = ThinkingInfo(
            enabled=result.thinking.thinking_enabled,
            level=result.thinking.thinking_level,
            summary=result.thinking.thoughts_summary,
            thoughts_tokens=result.thinking.thoughts_token_count,
            output_tokens=result.thinking.output_token_count,
            total_tokens=result.thinking.total_token_count,
        )
        
        # 7. 返回响应
        return ChatResponse(
            session_id=session_id,
            response=result.response,
            archived=archived,
            archive_topic=archive_topic,
            context_loads=result.retry_count,
            thinking=thinking_info,
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
    流式对话接口（支持思考功能）
    
    返回 Server-Sent Events 格式的流式响应
    事件类型:
    - thought: 思考摘要片段
    - answer: 回答片段
    - done: 完成信号（包含元数据）
    - error: 错误信息
    """
    async def generate():
        try:
            user_id = request.user_id
            message = request.message
            
            # 1. 创建或获取会话
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
            
            # 2. 保存用户消息
            user_message = MessageCreate(
                role=MessageRole.USER,
                content=message,
                is_excluded=False,
                is_archived=False,
            )
            await firestore_service.add_message(user_id, session_id, user_message)
            
            # 3. 检查并执行归档
            archived = False
            archive_topic = None
            if await archive_service.check_should_archive(user_id, session_id):
                thread_id = await archive_service.execute_archive(user_id, session_id)
                if thread_id:
                    archived = True
                    topic = await firestore_service.get_topic(user_id, session_id, thread_id)
                    archive_topic = topic.title if topic else None
            
            # 4. 构建上下文
            context = await context_builder.build(user_id, session_id)
            
            # 5. 流式生成回复
            full_response = ""
            full_thoughts = ""
            
            async for chunk in llm_service.generate_response_stream(
                context, 
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
            
            # 6. 保存助手回复
            if full_response:
                assistant_message = MessageCreate(
                    role=MessageRole.ASSISTANT,
                    content=full_response,
                    is_excluded=False,
                    is_archived=False,
                )
                await firestore_service.add_message(user_id, session_id, assistant_message)
                await firestore_service.update_session_timestamp(user_id, session_id)
            
            # 7. 发送完成信号
            done_data = {
                "type": "done",
                "session_id": session_id,
                "archived": archived,
                "archive_topic": archive_topic,
                "thinking_summary": full_thoughts,
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
