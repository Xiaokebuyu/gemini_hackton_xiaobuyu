"""
聊天 API 路由
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from app.services.firestore_service import FirestoreService
from app.services.router_service import RouterService
from app.services.artifact_service import ArtifactService
from app.services.context_builder import ContextBuilder
from app.services.llm_service import LLMService
from app.models import MessageCreate, MessageRole

router = APIRouter()

# 初始化服务
firestore_service = FirestoreService()
router_service = RouterService()
artifact_service = ArtifactService()
context_builder = ContextBuilder()
llm_service = LLMService()


class ChatRequest(BaseModel):
    """聊天请求"""
    user_id: str
    session_id: Optional[str] = None
    message: str


class ChatResponse(BaseModel):
    """聊天响应"""
    session_id: str
    thread_id: str
    thread_title: str
    response: str
    is_new_topic: bool
    artifact_updated: bool


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    主对话接口
    
    处理流程:
    1. 创建或获取会话
    2. 路由到合适的主题
    3. 构建上下文
    4. 调用 LLM 生成回复
    5. 保存消息
    6. 更新 Artifact（如需要）
    7. 返回响应
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
            # 创建新会话
            session = await firestore_service.create_session(user_id)
            session_id = session.session_id
        
        # 2. 路由到合适的主题
        thread_id, is_new_topic = await router_service.route_message(
            user_id,
            message,
            session_id
        )
        
        # 获取主题信息
        topic = await firestore_service.get_topic(user_id, thread_id)
        if not topic:
            raise HTTPException(status_code=500, detail="主题路由失败")
        
        # 更新会话的当前主题
        await firestore_service.update_session(user_id, session_id, thread_id)
        
        # 3. 保存用户消息
        user_message = MessageCreate(
            role=MessageRole.USER,
            content=message,
            thread_id=thread_id,
            is_excluded=False
        )
        user_msg_id = await firestore_service.add_message(user_id, session_id, user_message)
        
        # 4. 构建上下文
        context = await context_builder.build_full_context(
            user_id,
            session_id,
            thread_id,
            message,
            include_recent_messages=True
        )
        
        # 5. 获取对话历史
        conversation_history = await context_builder.get_conversation_history(
            user_id,
            session_id,
            thread_id,
            limit=10
        )
        
        # 6. 调用 LLM 生成回复
        response_text = await llm_service.generate_response(
            user_query=message,
            context=context,
            conversation_history=conversation_history
        )
        
        # 7. 保存助手回复
        assistant_message = MessageCreate(
            role=MessageRole.ASSISTANT,
            content=response_text,
            thread_id=thread_id,
            is_excluded=False
        )
        assistant_msg_id = await firestore_service.add_message(
            user_id,
            session_id,
            assistant_message
        )
        
        # 8. 判断是否需要更新 Artifact
        artifact_updated = False
        
        # 获取最近对话用于 Artifact 更新判断
        recent_conv, recent_msg_ids = await context_builder.build_context_for_artifact_update(
            user_id,
            session_id,
            thread_id,
            recent_message_count=4
        )
        
        should_update = await artifact_service.should_update_artifact(
            topic.current_artifact,
            recent_conv
        )
        
        if should_update:
            # 更新 Artifact
            await artifact_service.update_artifact(
                user_id,
                thread_id,
                topic.current_artifact,
                recent_conv,
                recent_msg_ids
            )
            artifact_updated = True
        
        # 9. 返回响应
        return ChatResponse(
            session_id=session_id,
            thread_id=thread_id,
            thread_title=topic.title,
            response=response_text,
            is_new_topic=is_new_topic,
            artifact_updated=artifact_updated
        )
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"聊天处理错误: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"处理消息时出错: {str(e)}")


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
                    "is_excluded": msg.is_excluded
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
