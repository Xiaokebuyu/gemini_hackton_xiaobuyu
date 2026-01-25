"""
GM API routes.

提供：
1. 结构化事件摄入和分发
2. 自然语言事件摄入和智能视角分发（Phase 5）
"""
from fastapi import APIRouter, HTTPException

from app.models.event import (
    GMEventIngestRequest,
    GMEventIngestResponse,
    NaturalEventIngestRequest,
    NaturalEventIngestResponse,
)
from app.services.gm_flash_service import GMFlashService


router = APIRouter()
gm_service = GMFlashService()


@router.post("/gm/{world_id}/events/ingest")
async def ingest_event(
    world_id: str,
    payload: GMEventIngestRequest,
) -> GMEventIngestResponse:
    """GM ingest event and dispatch."""
    try:
        return await gm_service.ingest_event(world_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ==================== 自然语言接口（Phase 5）====================


@router.post("/gm/{world_id}/events/ingest-natural")
async def ingest_event_natural(
    world_id: str,
    payload: NaturalEventIngestRequest,
) -> NaturalEventIngestResponse:
    """
    自然语言事件摄入和智能视角分发

    这是Phase 5的核心功能：GM可以用自然语言描述事件，系统自动：
    1. 解析事件结构（参与者、目击者、地点等）
    2. 编码为GM图谱的客观记录
    3. 根据每个角色与事件的关系，生成不同视角的记忆
    4. 写入各角色的记忆图谱

    请求体：
    - event_description: 事件的自然语言描述
    - game_day: 游戏日（用于时间标记）
    - known_characters: 已知角色列表（帮助解析）
    - known_locations: 已知地点列表（帮助解析）
    - character_locations: 角色当前位置映射（用于确定旁观者）
    - distribute: 是否分发给角色（默认True）
    - write_indexes: 是否写入搜索索引

    响应：
    - event_id: 事件ID
    - parsed_event: 解析后的事件结构
    - gm_node_count: GM图谱节点数
    - gm_edge_count: GM图谱边数
    - dispatched: 是否进行了分发
    - recipients: 各角色的分发结果（包含视角类型和写入统计）
    """
    try:
        return await gm_service.ingest_event_natural(world_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
