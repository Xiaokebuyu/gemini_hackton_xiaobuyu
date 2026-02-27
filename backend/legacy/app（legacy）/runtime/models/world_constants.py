"""世界常量模型 — Layer 0 静态数据。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class FactionInfo(BaseModel):
    """势力/组织信息。"""
    id: str
    name: str
    description: str = ""
    alignment: str = ""
    territory: List[str] = Field(default_factory=list)
    relations: Dict[str, str] = Field(default_factory=dict)


class GeographyInfo(BaseModel):
    """地理概况。"""
    regions: List[Dict[str, Any]] = Field(default_factory=list)
    climate: str = ""
    notable_landmarks: List[str] = Field(default_factory=list)


class WorldConstants(BaseModel):
    """世界级静态常量，游戏运行期间不变。

    对应 Firestore 路径: worlds/{world_id}/meta/info
    """
    world_id: str
    name: str = ""
    description: str = ""
    background: str = ""
    setting: str = ""
    geography: Optional[GeographyInfo] = None
    factions: List[FactionInfo] = Field(default_factory=list)
    rules_summary: str = ""
    tone: str = ""
    era: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_context(self) -> Dict[str, Any]:
        """转化为 Layer 0 上下文字典。"""
        ctx: Dict[str, Any] = {
            "world_name": self.name,
            "background": self.background,
            "setting": self.setting,
            "tone": self.tone,
        }
        if self.geography:
            ctx["geography"] = self.geography.model_dump(exclude_none=True)
        if self.factions:
            ctx["factions"] = [f.model_dump(exclude_none=True) for f in self.factions]
        if self.era:
            ctx["era"] = self.era
        return ctx
