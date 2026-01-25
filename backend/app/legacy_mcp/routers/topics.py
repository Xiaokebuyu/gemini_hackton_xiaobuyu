"""
主题管理 API 路由 - 基于 MCP 架构
"""
from fastapi import APIRouter, HTTPException
from typing import List, Optional

from app.legacy_mcp.firestore_service import FirestoreService
from app.legacy_mcp import get_mcp_server

router = APIRouter()

# 初始化服务
firestore_service = FirestoreService()


@router.get("/sessions/{user_id}/{session_id}/topics")
async def get_session_topics(user_id: str, session_id: str):
    """
    获取会话内所有主题（MCP 新结构）
    
    返回主题列表，每个主题包含其下的话题
    """
    try:
        topics = await firestore_service.get_all_mcp_topics(user_id, session_id)
        
        result = []
        for topic in topics:
            topic_id = topic.get("topic_id", "")
            
            # 获取主题下的话题
            threads = await firestore_service.get_topic_threads(
                user_id, session_id, topic_id
            )
            
            result.append({
                "topic_id": topic_id,
                "title": topic.get("title", ""),
                "summary": topic.get("summary", ""),
                "created_at": topic.get("created_at"),
                "threads": [
                    {
                        "thread_id": t.get("thread_id", ""),
                        "title": t.get("title", ""),
                        "summary": t.get("summary", ""),
                        "created_at": t.get("created_at"),
                    }
                    for t in threads
                ]
            })
        
        return {
            "user_id": user_id,
            "session_id": session_id,
            "topic_count": len(result),
            "topics": result
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取主题失败: {str(e)}")


@router.get("/sessions/{user_id}/{session_id}/topics/{topic_id}")
async def get_topic_detail(user_id: str, session_id: str, topic_id: str):
    """
    获取主题详细信息
    """
    try:
        topic = await firestore_service.get_mcp_topic(user_id, session_id, topic_id)
        
        if not topic:
            raise HTTPException(status_code=404, detail="主题不存在")
        
        # 获取主题下的话题
        threads = await firestore_service.get_topic_threads(
            user_id, session_id, topic_id
        )
        
        return {
            "topic_id": topic.get("topic_id", ""),
            "title": topic.get("title", ""),
            "summary": topic.get("summary", ""),
            "created_at": topic.get("created_at"),
            "threads": [
                {
                    "thread_id": t.get("thread_id", ""),
                    "title": t.get("title", ""),
                    "summary": t.get("summary", ""),
                }
                for t in threads
            ]
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取主题详情失败: {str(e)}")


@router.get("/sessions/{user_id}/{session_id}/threads/{thread_id}")
async def get_thread_detail(user_id: str, session_id: str, thread_id: str):
    """
    获取话题详细信息（包含所有见解版本）
    """
    try:
        # 查找话题
        thread = await firestore_service.find_thread_by_id(
            user_id, session_id, thread_id
        )
        
        if not thread:
            raise HTTPException(status_code=404, detail="话题不存在")
        
        topic_id = thread.get("topic_id", "")
        
        # 获取见解版本
        insights = await firestore_service.get_thread_insights(
            user_id, session_id, topic_id, thread_id
        )
        
        return {
            "thread_id": thread_id,
            "topic_id": topic_id,
            "title": thread.get("title", ""),
            "summary": thread.get("summary", ""),
            "created_at": thread.get("created_at"),
            "insight_count": len(insights),
            "insights": [
                {
                    "insight_id": i.get("insight_id", ""),
                    "version": i.get("version", 0),
                    "content": i.get("content", ""),
                    "evolution_note": i.get("evolution_note", ""),
                    "retrieval_count": i.get("retrieval_count", 0),
                    "created_at": i.get("created_at"),
                    "source_message_ids": i.get("source_message_ids", []),
                }
                for i in insights
            ]
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取话题详情失败: {str(e)}")


@router.get("/sessions/{user_id}/{session_id}/threads/{thread_id}/insights")
async def get_thread_insights(user_id: str, session_id: str, thread_id: str):
    """
    获取话题的所有见解版本
    """
    try:
        # 查找话题
        thread = await firestore_service.find_thread_by_id(
            user_id, session_id, thread_id
        )
        
        if not thread:
            raise HTTPException(status_code=404, detail="话题不存在")
        
        topic_id = thread.get("topic_id", "")
        
        # 获取见解版本
        insights = await firestore_service.get_thread_insights(
            user_id, session_id, topic_id, thread_id
        )
        
        return {
            "thread_id": thread_id,
            "thread_title": thread.get("title", ""),
            "insight_count": len(insights),
            "insights": [
                {
                    "insight_id": i.get("insight_id", ""),
                    "version": i.get("version", 0),
                    "content": i.get("content", ""),
                    "evolution_note": i.get("evolution_note", ""),
                    "retrieval_count": i.get("retrieval_count", 0),
                    "created_at": i.get("created_at"),
                }
                for i in insights
            ]
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取见解版本失败: {str(e)}")


@router.get("/sessions/{user_id}/{session_id}/threads/{thread_id}/evolution")
async def get_insight_evolution(user_id: str, session_id: str, thread_id: str):
    """
    获取话题的见解演变历程
    
    展示用户对某话题理解的变化过程
    """
    try:
        mcp_server = get_mcp_server()
        
        result = await mcp_server.execute_tool(
            user_id=user_id,
            session_id=session_id,
            tool_name="get_insight_evolution",
            params={"thread_id": thread_id}
        )
        
        return {
            "thread_id": thread_id,
            "evolution": result
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取见解演变失败: {str(e)}")


@router.get("/sessions/{user_id}/{session_id}/search")
async def search_topics(user_id: str, session_id: str, keyword: str):
    """
    搜索话题
    """
    try:
        mcp_server = get_mcp_server()
        
        result = await mcp_server.execute_tool(
            user_id=user_id,
            session_id=session_id,
            tool_name="search_topics",
            params={"keyword": keyword}
        )
        
        return {
            "keyword": keyword,
            "result": result
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"搜索失败: {str(e)}")


# ==================== 兼容旧 API（可选保留）====================

@router.get("/sessions/{user_id}/{session_id}/topics-legacy")
async def get_session_topics_legacy(user_id: str, session_id: str):
    """
    获取会话内所有主题（旧格式，兼容用）
    """
    try:
        topics = await firestore_service.get_all_topics(user_id, session_id)
        
        return {
            "user_id": user_id,
            "session_id": session_id,
            "topic_count": len(topics),
            "topics": [
                {
                    "thread_id": topic.thread_id,
                    "title": topic.title,
                    "summary": topic.summary,
                    "created_at": topic.created_at.isoformat(),
                    "has_artifact": bool(topic.current_artifact),
                }
                for topic in topics
            ]
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取主题失败: {str(e)}")
