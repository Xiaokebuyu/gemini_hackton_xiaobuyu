"""
Pro DM service - narration/interaction layer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from app.config import settings
from app.models.admin_protocol import ProResponse
from app.services.llm_service import LLMService


class ProDMService:
    """Pro DM service with lightweight narration support."""

    def __init__(
        self,
        llm_service: Optional[LLMService] = None,
        system_prompt_path: Optional[Path] = None,
    ) -> None:
        self.llm_service = llm_service or LLMService()
        self.system_prompt_path = system_prompt_path or Path("app/prompts/pro_dm_system.md")

    def _load_system_prompt(self) -> str:
        if self.system_prompt_path.exists():
            return self.system_prompt_path.read_text(encoding="utf-8")
        return "你是叙述者（GM），负责以沉浸式、简洁的方式描述世界与事件。"

    async def narrate(
        self,
        player_input: str,
        flash_result: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> ProResponse:
        """Generate narration. If flash_result already contains narration, passthrough."""
        if flash_result and flash_result.get("response"):
            return ProResponse(
                narration=flash_result.get("response", ""),
                speaker=flash_result.get("speaker", "GM"),
                metadata={"source": "legacy_gm"},
            )

        system_prompt = self._load_system_prompt()
        extra_context = ""
        if context:
            location = context.get("location") or {}
            time_info = context.get("time") or {}
            loc_name = location.get("location_name") or location.get("location_id") or "未知地点"
            atmosphere = location.get("atmosphere") or ""
            time_text = time_info.get("formatted") or time_info.get("formatted_time") or ""
            extra_context = (
                f"当前地点: {loc_name}\n"
                f"环境氛围: {atmosphere}\n"
                f"当前时间: {time_text}\n"
            )

        full_context = f"""{system_prompt}

{extra_context}
"""
        response = await self.llm_service.generate_response(
            context=full_context,
            user_query=player_input,
            thinking_level=getattr(settings, "admin_pro_thinking_level", None),
        )

        return ProResponse(narration=response.text, metadata={"source": "pro_dm"})
