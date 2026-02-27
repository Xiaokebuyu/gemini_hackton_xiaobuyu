"""L1: Passerby NPC LLM response generation.

Extracted from TieredAIService (FAST path only).
L2 passerby_service calls this; no game-state logic here.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from app.exceptions import LLMServiceError


async def generate_passerby_response(
    llm_service: Any,
    query: str,
    npc_profile: Dict[str, Any],
    location_id: str = "",
    sub_location_id: Optional[str] = None,
    model: str = "",
) -> Dict[str, Any]:
    """Generate a fast, lightweight response for passerby NPCs.

    No tools, no thinking — just a quick in-character reply.
    Returns dict: {content, cache_hit, latency_ms}.
    """
    start = time.perf_counter()
    prompt = _build_fast_prompt(query, npc_profile, location_id, sub_location_id)
    try:
        content = await llm_service.generate_simple(
            prompt,
            model_override=model or None,
            thinking_level="none",
        )
    except LLMServiceError:
        content = f"（{npc_profile.get('name', 'NPC')}似乎在思考...）"
    return {
        "content": content or "",
        "cache_hit": False,
        "latency_ms": (time.perf_counter() - start) * 1000,
    }


def _build_fast_prompt(
    query: str,
    npc_profile: Dict[str, Any],
    location_id: str,
    sub_location_id: Optional[str] = None,
) -> str:
    name = npc_profile.get("name", "NPC")
    personality = npc_profile.get("personality", "")
    speech_pattern = npc_profile.get("speech_pattern", "")
    occupation = npc_profile.get("occupation", "")
    appearance = npc_profile.get("appearance", "")
    shared_context = npc_profile.get("shared_context", "")

    location_hint = location_id
    if sub_location_id:
        location_hint = f"{location_id}/{sub_location_id}"

    prompt_parts = [
        f"你是{name}。",
        f"职业: {occupation}",
        f"性格: {personality}",
        f"说话方式: {speech_pattern}",
        f"外貌: {appearance}",
        "",
        f"当前地点: {location_hint}",
    ]

    if shared_context:
        prompt_parts.extend([
            "",
            "你听说的最近消息:",
            shared_context,
        ])

    prompt_parts.extend([
        "",
        f"玩家说: {query}",
        "",
        "请简短回应（1-2句话），保持角色特点:",
    ])

    return "\n".join([p for p in prompt_parts if p is not None])
