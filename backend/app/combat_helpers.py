"""Pure combat helper functions (no FastAPI dependency).

These functions are extracted from routers/combat.py so they can be unit-tested
independently without requiring a FastAPI process.

All functions are either synchronous or async but have no HTTP framework imports.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from app.game_core.rules.models import Command

logger = logging.getLogger(__name__)

# D-1 (W3-1): Frontend action → v2 command type mapping
COMBAT_ACTION_TO_COMMAND: dict[str, str] = {
    "attack": "combat_attack",
    "defend": "combat_defend",
    "disengage": "combat_disengage",
    "dash": "combat_dash",
    "shove": "combat_attack",        # shove走 attack handler，params 区分
    "flee": "combat_disengage",      # flee 走 disengage
    "offhand_attack": "combat_attack",
    "stand_up": "combat_end_turn",   # stand_up 走 end_turn
    "use_combat_item": "use_item",
}


def get_current_unit(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the combat unit whose turn it currently is.

    Returns None if current_unit_id is absent or doesn't match any unit.
    """
    current_uid = (payload.get("current_unit_id") or "").strip()
    if not current_uid:
        return None
    units = payload.get("units", [])
    if not isinstance(units, list):
        return None
    for u in units:
        if isinstance(u, dict) and u.get("unit_id") == current_uid:
            return dict(u)
    return None


def _non_empty_string(value: Any) -> str | None:
    normalized = str(value).strip() if value is not None else ""
    return normalized or None


def _normalized_tag_set(raw_tags: Any) -> set[str]:
    if not isinstance(raw_tags, (list, tuple, set, frozenset)):
        return set()
    return {
        str(tag).strip().lower()
        for tag in raw_tags
        if str(tag).strip()
    }


def _group_role(group: Any) -> str | None:
    if isinstance(group, Mapping):
        return _non_empty_string(group.get("role"))
    return _non_empty_string(getattr(group, "role", None))


def _group_monster_ids(group: Any) -> list[str]:
    raw = group.get("monster_ids") if isinstance(group, Mapping) else getattr(group, "monster_ids", [])
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _sub_location_has_boss_signal(
    *,
    session: Any,
    payload: Mapping[str, Any],
    sub_area_id: str,
    monster_id: str,
) -> bool:
    world = getattr(getattr(session, "runtime", None), "world", None)
    if world is None or not world.has_registry("maps"):
        return False

    area_id = _non_empty_string(payload.get("area_id"))
    if area_id is None:
        return False

    sub_location = world.maps.get_sub_location(area_id, sub_area_id)
    if sub_location is None:
        return False

    sub_tags = _normalized_tag_set(getattr(sub_location, "tags", None))
    if {"boss_room", "boss", "mini_boss"} & sub_tags:
        return True

    hostile_config = getattr(sub_location, "hostile_config", None)
    hostile_groups = getattr(hostile_config, "hostile_groups", None)
    if not isinstance(hostile_groups, list):
        return False
    for group in hostile_groups:
        role = (_group_role(group) or "").lower()
        if role not in {"boss", "mini_boss"}:
            continue
        if monster_id in _group_monster_ids(group):
            return True
    return False


def resolve_enemy_ai_tier(
    *,
    session: Any,
    unit: Mapping[str, Any],
    payload: Mapping[str, Any],
    sub_area_id: str,
) -> str | None:
    """Return ``boss`` / ``elite`` when the enemy should use LLM AI."""
    if _non_empty_string(unit.get("source")) != "monster":
        return None

    monster_id = _non_empty_string(unit.get("monster_id"))
    if monster_id is None:
        return None

    world = getattr(getattr(session, "runtime", None), "world", None)
    if world is None or not world.has_registry("monsters"):
        return None

    template = world.monsters.get(monster_id)
    if template is None:
        return None

    if _sub_location_has_boss_signal(
        session=session,
        payload=payload,
        sub_area_id=sub_area_id,
        monster_id=monster_id,
    ):
        return "boss"

    tags = _normalized_tag_set(getattr(template, "tags", None))
    if {"boss", "mini_boss"} & tags:
        return "boss"
    if "elite" in tags:
        return "elite"
    return None


async def compute_companion_decision(
    *,
    session: Any,
    unit: Mapping[str, Any],
    payload: Mapping[str, Any],
    llm_provider: Any,
) -> dict[str, Any] | None:
    """Pre-compute a companion LLM combat decision (D-6 / W4-1).

    Returns a decision dict suitable for cmd.params["decision"], or None to fall
    back to the rules AI inside combat_npc_turn.

    This function is application-layer — it may import companion_combat_ai.
    """
    if llm_provider is None:
        return None

    try:
        from app.companion_combat_ai import decide_companion_combat_turn

        character_id = (
            (unit.get("character_id") or unit.get("unit_id") or "")
        ).strip()
        template = None
        if character_id and session.runtime.world.has_registry("characters"):
            template = session.runtime.world.characters.get(character_id)
        name = template.name if template is not None else character_id
        personality = (unit.get("ai_personality") or "aggressive").strip()

        approval = 0
        if session.runtime.state.has_slice("relations"):
            approval = session.runtime.state.relations.get_approval(character_id)

        return await decide_companion_combat_turn(
            unit=dict(unit),
            grid_data=dict(payload.get("grid", {})),
            all_units=list(payload.get("units", [])),
            character_name=name,
            personality_hint=personality,
            approval=approval,
            llm_provider=llm_provider,
        )
    except Exception:
        logger.warning(
            "companion AI failed for unit %s, falling back to rules AI",
            unit.get("unit_id"),
            exc_info=True,
        )
        return None


async def compute_enemy_decision(
    *,
    session: Any,
    unit: Mapping[str, Any],
    payload: Mapping[str, Any],
    decision_tier: str,
    llm_provider: Any,
) -> dict[str, Any] | None:
    """Pre-compute one elite/boss enemy LLM decision.

    Returns ``None`` when no LLM is available, the monster template is missing,
    or the model output cannot be parsed into the existing decision schema.
    """
    if llm_provider is None:
        return None

    try:
        from app.enemy_combat_ai import decide_enemy_combat_turn

        monster_id = _non_empty_string(unit.get("monster_id"))
        if monster_id is None:
            return None

        world = session.runtime.world
        if not world.has_registry("monsters"):
            return None
        template = world.monsters.get(monster_id)
        if template is None:
            return None

        personality = (
            _non_empty_string(unit.get("ai_personality"))
            or _non_empty_string(getattr(template, "ai_personality", None))
            or "aggressive"
        )
        tactics_notes = _non_empty_string(getattr(template, "tactics_notes", None)) or ""
        preferred_terrain = list(getattr(template, "preferred_terrain", []) or [])

        return await decide_enemy_combat_turn(
            unit=dict(unit),
            grid_data=dict(payload.get("grid", {})),
            all_units=list(payload.get("units", [])),
            monster_name=_non_empty_string(getattr(template, "name", None)) or monster_id,
            decision_tier=decision_tier,
            personality_hint=personality,
            tactics_notes=tactics_notes,
            preferred_terrain=preferred_terrain,
            llm_provider=llm_provider,
        )
    except Exception:
        logger.warning(
            "enemy AI failed for unit %s, falling back to rules AI",
            unit.get("unit_id"),
            exc_info=True,
        )
        return None


async def restore_fallen_companions(
    session: Any,
    payload: Mapping[str, Any],
    *,
    execute_command_fn: Any,
) -> list[str]:
    """Restore fallen companions to 50% max HP after a combat victory (D-9 / W3-6).

    :param session:   ManagedSession with .runtime.state
    :param payload:   combat payload (before or after last round) — used to find
                      which companions were alive/fallen
    :param execute_command_fn: awaitable called with one internal companion
                               restore command.
    """
    units = payload.get("units", [])
    if not isinstance(units, list):
        return []

    restored_ids: list[str] = []

    for unit in units:
        if not isinstance(unit, Mapping):
            continue
        if str(unit.get("source", "")) != "companion":
            continue
        if bool(unit.get("alive", True)):
            continue  # still alive — no restoration needed
        char_id = (unit.get("character_id") or unit.get("unit_id") or "").strip()
        if not char_id:
            continue
        max_hp = int(unit.get("max_hp") or unit.get("hp") or 1)
        restored_hp = max(1, max_hp // 2)
        if not session.runtime.state.has_slice("party"):
            continue
        if char_id not in (session.runtime.state.party.members or {}):
            continue
        if execute_command_fn is None:
            continue
        result = await execute_command_fn(
            Command(
                type="restore_companion_after_combat",
                params={"npc_id": char_id, "restored_hp": restored_hp},
                source="system",
            )
        )
        if bool(getattr(result, "executed", False)):
            restored_ids.append(char_id)

    return restored_ids
