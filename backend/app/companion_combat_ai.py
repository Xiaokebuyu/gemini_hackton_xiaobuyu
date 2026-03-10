"""LLM-driven companion combat AI — application layer module.

Responsibility: Given a companion unit's combat state, call the LLM and
return a parsed decision dict suitable for passing as cmd.params["decision"]
to the combat_npc_turn command.

Fallback contract: Any error (LLM failure, parse failure, network error) →
return None → caller omits decision → combat_npc_turn falls back to rules AI.

This module lives in the application layer (app/) and may import game_core
types, but game_core must never import this module.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.game_core.adapters.llm import LlmPort

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Approval score → qualitative label
# ---------------------------------------------------------------------------

_APPROVAL_LABELS = [
    (80, "非常信任你，会为你全力以赴"),
    (50, "比较信任你，会积极配合"),
    (20, "态度中立，会履行职责"),
    (-20, "有些疑虑，但仍会参战"),
    (-999, "关系紧张，行动消极"),
]


def _approval_label(approval: int) -> str:
    for threshold, label in _APPROVAL_LABELS:
        if approval >= threshold:
            return label
    return _APPROVAL_LABELS[-1][1]


# ---------------------------------------------------------------------------
# Terrain code → readable name (mirrors TERRAIN_REGISTRY codes)
# ---------------------------------------------------------------------------

_TERRAIN_NAMES: dict[str, str] = {
    "G": "草地",
    "F": "林地",
    "H": "丘陵",
    "S": "沼泽",
    "W": "水域",
    "R": "石地",
    "B": "墙",
    "M": "山",
}


# ---------------------------------------------------------------------------
# 3a. build_battlefield_summary
# ---------------------------------------------------------------------------


def build_battlefield_summary(
    grid_data: dict[str, Any],
    units: list[dict[str, Any]],
    current_unit_id: str,
) -> str:
    """Build a text summary of the battlefield for the LLM to read.

    Produces a grid map (terrain codes), a unit list, the current unit's
    attacks, and its movement speed.
    """
    width: int = int(grid_data.get("width", 8))
    height: int = int(grid_data.get("height", 6))
    terrain_rows: list[str] = list(grid_data.get("terrain", []))

    # --- Grid map ---
    grid_lines = [f"=== 战场 ({width}x{height}) ==="]
    for row_str in terrain_rows:
        grid_lines.append(str(row_str))

    # --- Units ---
    unit_lines = ["", "=== 单位 ==="]
    current_unit: dict[str, Any] | None = None
    for u in units:
        if not u.get("alive", True):
            continue
        uid = str(u.get("unit_id", "?"))
        side = str(u.get("side", "?"))
        name = str(u.get("name", uid))
        hp = int(u.get("hp", 0))
        max_hp = int(u.get("max_hp", hp))
        ac = int(u.get("ac", 10))
        pos = u.get("position", [0, 0])
        pos_str = f"({pos[0]},{pos[1]})" if len(pos) >= 2 else "(?)"
        marker = " ← 你" if uid == current_unit_id else ""
        unit_lines.append(
            f"[{side}] {name} (HP:{hp}/{max_hp} AC:{ac}) @ {pos_str}{marker}"
        )
        if uid == current_unit_id:
            current_unit = u

    # --- Attacks ---
    attack_lines = ["", "=== 你的攻击 ==="]
    if current_unit is not None:
        attacks: list[dict[str, Any]] = list(current_unit.get("attacks", []))
        for i, atk in enumerate(attacks):
            a_name = str(atk.get("name", f"攻击{i}"))
            hit_bonus = int(atk.get("hit_bonus", 0))
            damage_dice = str(atk.get("damage_dice", "1d4"))
            damage_type = str(atk.get("damage_type", "物理"))
            a_range = int(atk.get("range", 1))
            attack_lines.append(
                f"{i}: {a_name} (命中+{hit_bonus}, {damage_dice} {damage_type}, 射程{a_range})"
            )
        if not attacks:
            attack_lines.append("(无可用攻击)")

    # --- Movement ---
    speed_lines = ["", "=== 移动范围 ==="]
    if current_unit is not None:
        speed = int(current_unit.get("speed", 3))
        dashed = bool(current_unit.get("dashed", False))
        effective_speed = speed * 2 if dashed else speed
        speed_lines.append(f"速度: {effective_speed}格" + (" (冲刺中)" if dashed else ""))
    else:
        speed_lines.append("速度: 未知")

    return "\n".join(grid_lines + unit_lines + attack_lines + speed_lines)


# ---------------------------------------------------------------------------
# 3b. _build_system_prompt
# ---------------------------------------------------------------------------


def _build_system_prompt(
    unit: dict[str, Any],
    summary: str,
    character_name: str,
    personality_hint: str,
    approval: int,
) -> str:
    """Construct the system prompt for the companion LLM decision."""
    approval_desc = _approval_label(approval)
    attacks: list[dict[str, Any]] = list(unit.get("attacks", []))
    max_attack_index = max(0, len(attacks) - 1)

    return f"""你是 {character_name}，正在参与一场 SRPG 回合制战斗。

性格：{personality_hint}
与玩家的关系：{approval_desc}（好感度 {approval}）

{summary}

请决定你这个回合的行动。你可以：
- 移动到某个格子（move_to）
- 攻击某个敌人（action="attack"，指定 target_id 和 attack_index）
- 防御（action="defend"）
- 按兵不动（action="hold"）
- 尝试逃跑（action="flee"）

注意事项：
- attack_index 必须在 0 到 {max_attack_index} 之间
- move_to 格式为 [col, row] 的整数列表，或 null（不移动）
- target_id 是目标单位的 unit_id 字符串，不攻击时为 null

**输出格式**：严格 JSON，一行，包含以下字段：
{{"move_to": [col, row] 或 null, "action": "attack"/"defend"/"hold"/"flee", "target_id": "unit_id" 或 null, "attack_index": 0}}

只输出 JSON，不要任何说明文字。"""


# ---------------------------------------------------------------------------
# 3c. _parse_decision_response
# ---------------------------------------------------------------------------


def _parse_decision_response(text: str) -> dict[str, Any] | None:
    """Extract and validate a decision dict from LLM response text.

    Tries direct JSON parse first, then falls back to regex extraction.
    Returns None when no valid decision can be extracted.
    """
    if not text or not text.strip():
        return None

    # Attempt 1: direct parse
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, dict) and "action" in parsed:
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # Attempt 2: extract first {...} block via regex
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, dict) and "action" in parsed:
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    logger.debug("companion_combat_ai: could not parse LLM response: %r", text[:200])
    return None


# ---------------------------------------------------------------------------
# 3d. decide_companion_combat_turn
# ---------------------------------------------------------------------------


async def decide_companion_combat_turn(
    unit: dict[str, Any],
    grid_data: dict[str, Any],
    all_units: list[dict[str, Any]],
    character_name: str,
    personality_hint: str,
    approval: int,
    llm_provider: LlmPort,
) -> dict[str, Any] | None:
    """Use the LLM to decide a companion's combat turn action.

    Returns a decision dict with keys: move_to, action, target_id,
    attack_index — suitable for cmd.params["decision"] in combat_npc_turn.

    Returns None on any failure (LLM error, parse error, invalid response).
    The caller should then omit decision from params, causing combat_npc_turn
    to fall back to rules AI.
    """
    current_unit_id = str(unit.get("unit_id", ""))

    try:
        summary = build_battlefield_summary(grid_data, all_units, current_unit_id)
        system_prompt = _build_system_prompt(
            unit=unit,
            summary=summary,
            character_name=character_name,
            personality_hint=personality_hint,
            approval=approval,
        )

        response = await llm_provider.generate(
            system_prompt=system_prompt,
            history=[{"role": "user", "parts": [{"text": "请决定你的行动。"}]}],
            tool_declarations=[],
        )

        decision = _parse_decision_response(response.text)
        if decision is None:
            logger.debug(
                "companion_combat_ai: no valid decision from LLM for unit %s",
                current_unit_id,
            )
        return decision

    except Exception:  # noqa: BLE001
        logger.exception(
            "companion_combat_ai: LLM call failed for unit %s", current_unit_id
        )
        return None
