"""域异常类 — Phase 3 宽异常收窄专项。"""
import asyncio

from pydantic import ValidationError


class WorldGraphError(RuntimeError):
    """图操作失败（add_edge / snapshot / build）。"""


class LLMServiceError(RuntimeError):
    """LLM 调用失败（SDK 异常统一包装）。"""


class FirestoreIOError(OSError):
    """Firestore 读写失败。"""


class SessionRestoreError(RuntimeError):
    """会话反序列化失败。"""


class EventConditionError(ValueError):
    """条件评估遇到畸形数据。"""


class MCPServiceUnavailableError(RuntimeError):
    """Raised when an MCP service endpoint is unavailable."""

    def __init__(self, server_type: str, endpoint: str, detail: str) -> None:
        self.server_type = server_type
        self.endpoint = endpoint
        self.detail = detail
        super().__init__(
            f"MCP service unavailable ({server_type}) at {endpoint or '<unset>'}: {detail}"
        )


CATCHABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    WorldGraphError,
    LLMServiceError,
    FirestoreIOError,
    SessionRestoreError,
    EventConditionError,
    MCPServiceUnavailableError,
    ValidationError,
    ValueError,
    KeyError,
    asyncio.TimeoutError,
    OSError,
)
