"""
Pro service models.
"""
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from app.models.flash import RecallRequest, RecallResponse


class CharacterProfile(BaseModel):
    """Character profile data."""
    name: str = ""
    occupation: Optional[str] = None
    age: Optional[int] = None
    personality: Optional[str] = None
    speech_pattern: Optional[str] = None
    example_dialogue: Optional[str] = None
    system_prompt: Optional[str] = None
    metadata: Dict = Field(default_factory=dict)


class ChatMessage(BaseModel):
    """对话消息"""
    role: Literal["user", "assistant"] = "user"
    content: str = ""


class SceneContext(BaseModel):
    """Scene context for Pro."""
    description: str = ""
    location: Optional[str] = None
    present_characters: List[str] = Field(default_factory=list)
    environment: Optional[str] = None


class ProContextRequest(BaseModel):
    """Request to assemble Pro context."""
    scene: SceneContext
    recent_conversation: Optional[str] = None
    recall: Optional[RecallRequest] = None
    include_prompt: bool = True


class ProContextResponse(BaseModel):
    """Response with assembled Pro context."""
    profile: CharacterProfile
    state: Dict = Field(default_factory=dict)
    scene: SceneContext
    memory: Optional[RecallResponse] = None
    assembled_prompt: Optional[str] = None


# ==================== 对话相关模型 ====================


class ChatRequest(BaseModel):
    """对话请求"""
    message: str
    scene: Optional[SceneContext] = None
    conversation_history: List[ChatMessage] = Field(default_factory=list)
    injected_memory: Optional[str] = None


class ChatResponse(BaseModel):
    """对话响应"""
    response: str
    tool_called: bool = False
    recalled_memory: Optional[str] = None
    recall_query: Optional[str] = None
    thinking: Optional[str] = None
