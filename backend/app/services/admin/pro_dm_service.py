"""
Pro DM service - narration/interaction layer.

Pro-First 架构：
1. Flash 一次性分析
2. Pro 生成叙述
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from app.config import settings
from app.models.admin_protocol import ProResponse
from app.services.llm_service import LLMService


class ProDMService:
    """Pro DM service with narration only."""

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
        context: Dict[str, Any],
        execution_summary: str,
        player_input: Optional[str] = None,
    ) -> ProResponse:
        """基于执行结果生成沉浸式叙述"""
        system_prompt = self._load_system_prompt()
        execution_summary = execution_summary or "无系统操作执行"

        location = context.get("location") or {}
        time_info = context.get("time") or {}
        memory_summary = context.get("memory_summary") or ""

        memory_block = f"\n## 相关记忆\n{memory_summary}\n" if memory_summary else ""
        player_block = f"\n## 玩家行动\n{player_input}\n" if player_input else ""

        user_prompt = f"""## 当前场景
- 地点：{location.get('location_name', '未知')}
- 氛围：{location.get('atmosphere', '')}
- 时间：{time_info.get('formatted', '未知')}

## 发生的事情
{execution_summary}
{memory_block}{player_block}
请用2-4句话生动描述当前场景和发生的事情，保持沉浸感。"""

        response = await self.llm_service.generate_response(
            context=f"{system_prompt}\n\n{user_prompt}",
            user_query="请生成叙述",
            thinking_level=settings.admin_pro_thinking_level,
        )

        return ProResponse(
            narration=response.text,
            speaker="GM",
            metadata={"source": "pro_dm", "model": "pro"},
        )

    async def narrate_legacy(
        self,
        player_input: str,
        flash_result: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> ProResponse:
        """Legacy narration path."""
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

        return ProResponse(narration=response.text, metadata={"source": "pro_dm_legacy"})
