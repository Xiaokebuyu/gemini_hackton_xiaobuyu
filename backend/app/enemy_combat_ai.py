"""LLM-driven elite/boss enemy combat AI.

Application-layer module:
- reads current battlefield state
- asks the LLM for one structured combat decision
- returns a decision dict suitable for ``cmd.params["decision"]``

Failure contract: any error returns ``None`` so callers can fall back to the
rules AI inside ``combat_npc_turn``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.companion_combat_ai import build_battlefield_summary
from app.game_core.adapters.llm import LlmPort

logger = logging.getLogger(__name__)


def _build_system_prompt(
    *,
    unit: dict[str, Any],
    summary: str,
    monster_name: str,
    decision_tier: str,
    personality_hint: str,
    tactics_notes: str,
    preferred_terrain: list[str],
) -> str:
    max_attack_index = max(0, len(list(unit.get("attacks", []))) - 1)
    terrain_hint = ", ".join(item for item in preferred_terrain if str(item).strip()) or "无特别偏好"
    tactics_hint = tactics_notes.strip() or "无额外战术说明"

    return f"""你是 {monster_name}，正在参与一场 SRPG 回合制战斗。

你的身份层级：{decision_tier}
战斗性格：{personality_hint}
战术备注：{tactics_hint}
偏好地形：{terrain_hint}

{summary}

请根据当前战场给出你这个回合的决定。你只能选择：
- 移动到某个格子（move_to）
- 攻击某个敌人（action="attack"，必须指定 target_id 和 attack_index）
- 防御（action="defend"）
- 按兵不动（action="hold"）
- 尝试撤离（action="flee"）

硬约束：
- 只能输出单行 JSON，不要解释
- attack_index 必须在 0 到 {max_attack_index} 之间
- move_to 必须是 [col, row] 或 null
- target_id 必须是当前战场中真实存在的目标 unit_id；不攻击时为 null
- 不要发明技能、法术、道具、召唤物或额外字段

输出格式：
{{"move_to": [col, row] 或 null, "action": "attack"/"defend"/"hold"/"flee", "target_id": "unit_id" 或 null, "attack_index": 0}}
"""


def _parse_decision_response(text: str) -> dict[str, Any] | None:
    if not text or not text.strip():
        return None

    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, dict) and "action" in parsed:
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, dict) and "action" in parsed:
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    logger.debug("enemy_combat_ai: could not parse LLM response: %r", text[:200])
    return None


async def decide_enemy_combat_turn(
    *,
    unit: dict[str, Any],
    grid_data: dict[str, Any],
    all_units: list[dict[str, Any]],
    monster_name: str,
    decision_tier: str,
    personality_hint: str,
    tactics_notes: str,
    preferred_terrain: list[str],
    llm_provider: LlmPort,
) -> dict[str, Any] | None:
    """Return one elite/boss enemy decision dict, or ``None`` on any failure."""
    current_unit_id = str(unit.get("unit_id", "")).strip()

    try:
        summary = build_battlefield_summary(grid_data, all_units, current_unit_id)
        system_prompt = _build_system_prompt(
            unit=unit,
            summary=summary,
            monster_name=monster_name,
            decision_tier=decision_tier,
            personality_hint=personality_hint,
            tactics_notes=tactics_notes,
            preferred_terrain=preferred_terrain,
        )

        response = await llm_provider.generate(
            system_prompt=system_prompt,
            history=[{"role": "user", "parts": [{"text": "根据战场局势，给出你的本回合行动。"}]}],
            tool_declarations=[],
        )

        decision = _parse_decision_response(response.text)
        if decision is None:
            logger.debug(
                "enemy_combat_ai: no valid decision from LLM for unit %s",
                current_unit_id,
            )
        return decision
    except Exception:  # noqa: BLE001
        logger.exception(
            "enemy_combat_ai: LLM call failed for unit %s",
            current_unit_id,
        )
        return None
