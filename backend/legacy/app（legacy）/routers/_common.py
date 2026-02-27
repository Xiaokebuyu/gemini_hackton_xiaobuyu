"""Shared utilities for game router sub-modules."""
import asyncio

from fastapi import HTTPException
from pydantic import ValidationError

from app.exceptions import (
    CATCHABLE_EXCEPTIONS,
    EventConditionError,
    FirestoreIOError,
    LLMServiceError,
    MCPServiceUnavailableError,
    SessionRestoreError,
    WorldGraphError,
)

# Re-export for sub-routers
__all__ = ["CATCHABLE_EXCEPTIONS", "map_exception_to_http"]


def map_exception_to_http(exc: Exception) -> HTTPException:
    """Map known exceptions to appropriate HTTP status codes."""
    if isinstance(exc, SessionRestoreError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (EventConditionError, ValidationError)):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc))
    # TimeoutError 必须在 OSError 前（Python 3.11+: TimeoutError 是 OSError 子类）
    if isinstance(exc, asyncio.TimeoutError):
        return HTTPException(status_code=504, detail="上游服务超时")
    if isinstance(exc, (FirestoreIOError, LLMServiceError, MCPServiceUnavailableError, OSError)):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, WorldGraphError):
        return HTTPException(status_code=500, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))
