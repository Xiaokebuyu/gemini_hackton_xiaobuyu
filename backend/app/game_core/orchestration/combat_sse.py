"""Convert v2 combat metadata to SSE presentation events."""

from __future__ import annotations

from typing import Any

from app.game_core.orchestration.models import SSEEvent


_COMBAT_SSE_TYPES: frozenset[str] = frozenset({
    "start_combat",
    "combat_move",
    "combat_attack",
    "combat_end_turn",
    "combat_npc_turn",
    "combat_defend",
    "combat_disengage",
    "combat_dash",
})


def extract_combat_sse(action_type: str, metadata: Any) -> list[SSEEvent]:
    """Extract SSE events from a combat PipelineResult metadata dict."""
    if action_type not in _COMBAT_SSE_TYPES:
        return []
    if not isinstance(metadata, dict):
        return []

    events: list[SSEEvent] = []

    if action_type == "start_combat":
        events.append(SSEEvent("combat_started", {
            "grid": metadata.get("grid"),
            "units": metadata.get("units"),
            "turn_order": metadata.get("turn_order"),
            "current_unit_id": metadata.get("current_unit_id"),
            "environment": metadata.get("environment"),
        }))

    elif action_type == "combat_move":
        events.append(SSEEvent("unit_moved", {
            "unit_id": metadata.get("unit_id"),
            "from": metadata.get("from"),
            "to": metadata.get("to"),
        }))

    elif action_type in ("combat_attack", "combat_npc_turn"):
        # combat_attack: attack fields live directly in metadata
        # combat_npc_turn: attack info lives in metadata["attack"] sub-dict
        atk = metadata if action_type == "combat_attack" else (metadata.get("attack") or {})
        if atk.get("target_id"):
            events.append(SSEEvent("unit_attacked", {
                "attacker_id": metadata.get("attacker_id") or metadata.get("unit_id"),
                "target_id": atk.get("target_id"),
                "attack_name": atk.get("attack_name"),
                "hit": atk.get("hit"),
                "damage": atk.get("damage"),
                "target_hp": atk.get("target_hp"),
                "target_alive": atk.get("target_alive"),
            }))
            if atk.get("target_alive") is False:
                events.append(SSEEvent("unit_defeated", {
                    "unit_id": atk.get("target_id"),
                }))
        # Combat-cleared detection (both handlers expose combat_cleared in metadata)
        if metadata.get("combat_cleared"):
            events.append(SSEEvent("combat_ended", {
                "result": "victory",
                "xp_awarded": metadata.get("xp_awarded", 0),
            }))

    elif action_type == "combat_end_turn":
        events.append(SSEEvent("turn_changed", {
            "unit_id": metadata.get("next_unit_id"),
            "combat_round": metadata.get("combat_round"),
            "round_advanced": metadata.get("round_advanced"),
        }))

    return events
