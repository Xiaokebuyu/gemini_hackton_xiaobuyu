"""
主题管理 API 路由
"""
from fastapi import APIRouter, HTTPException

from app.services.firestore_service import FirestoreService
from app.services.artifact_service import ArtifactService

router = APIRouter()

# 初始化服务
firestore_service = FirestoreService()
artifact_service = ArtifactService()


@router.get("/sessions/{user_id}/{session_id}/topics")
async def get_session_topics(user_id: str, session_id: str):
    """
    获取会话内所有主题
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


@router.get("/sessions/{user_id}/{session_id}/topics/{thread_id}")
async def get_topic_detail(user_id: str, session_id: str, thread_id: str):
    """
    获取主题详细信息
    """
    try:
        topic = await firestore_service.get_topic(user_id, session_id, thread_id)
        
        if not topic:
            raise HTTPException(status_code=404, detail="主题不存在")
        
        return {
            "thread_id": topic.thread_id,
            "title": topic.title,
            "summary": topic.summary,
            "current_artifact": topic.current_artifact,
            "created_at": topic.created_at.isoformat(),
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取主题详情失败: {str(e)}")


@router.get("/sessions/{user_id}/{session_id}/topics/{thread_id}/artifact")
async def get_topic_artifact(user_id: str, session_id: str, thread_id: str):
    """
    获取主题的 Artifact
    """
    try:
        topic = await firestore_service.get_topic(user_id, session_id, thread_id)
        
        if not topic:
            raise HTTPException(status_code=404, detail="主题不存在")
        
        # 解析 Artifact 源索引
        sources_map = artifact_service.parse_artifact_sources(topic.current_artifact)
        
        return {
            "thread_id": thread_id,
            "title": topic.title,
            "artifact": topic.current_artifact,
            "sources_map": sources_map
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取 Artifact 失败: {str(e)}")


@router.get("/sessions/{user_id}/{session_id}/topics/{thread_id}/versions")
async def get_artifact_versions(user_id: str, session_id: str, thread_id: str, limit: int = 10):
    """
    获取 Artifact 历史版本
    """
    try:
        versions = await firestore_service.get_artifact_versions(
            user_id,
            session_id,
            thread_id,
            limit=limit
        )
        
        return {
            "thread_id": thread_id,
            "version_count": len(versions),
            "versions": [
                {
                    "version_id": v.version_id,
                    "created_at": v.created_at.isoformat(),
                    "message_ids": v.message_ids,
                    "content_preview": v.content[:200] + "..." if len(v.content) > 200 else v.content
                }
                for v in versions
            ]
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取版本历史失败: {str(e)}")


@router.get("/sessions/{user_id}/{session_id}/topics/{thread_id}/versions/{version_id}")
async def get_artifact_version_detail(user_id: str, session_id: str, thread_id: str, version_id: str):
    """
    获取特定版本的完整内容
    """
    try:
        versions = await firestore_service.get_artifact_versions(
            user_id,
            session_id,
            thread_id,
            limit=100
        )
        
        # 查找指定版本
        target_version = None
        for v in versions:
            if v.version_id == version_id:
                target_version = v
                break
        
        if not target_version:
            raise HTTPException(status_code=404, detail="版本不存在")
        
        return {
            "version_id": target_version.version_id,
            "created_at": target_version.created_at.isoformat(),
            "content": target_version.content,
            "message_ids": target_version.message_ids
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取版本详情失败: {str(e)}")
