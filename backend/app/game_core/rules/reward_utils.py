"""Shared reward helpers for quest and milestone completion."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.state import StateChange, StateContainer


def build_reward_changes(
    state: StateContainer,
    rewards: Any,
) -> list[StateChange]:
    """Build StateChange list for gold/xp/item rewards."""
    if not isinstance(rewards, Mapping):
        return []
    if not state.has_slice("player"):
        return []

    changes: list[StateChange] = []

    raw_gold = rewards.get("gold")
    try:
        gold = int(raw_gold) if raw_gold is not None else 0
    except (TypeError, ValueError):
        gold = 0
    if gold > 0:
        changes.append(
            StateChange("player", "set", "gold", max(0, int(state.player.gold) + gold))
        )

    raw_xp = rewards.get("xp")
    try:
        xp = int(raw_xp) if raw_xp is not None else 0
    except (TypeError, ValueError):
        xp = 0
    if xp > 0:
        changes.append(
            StateChange("player", "set", "xp", max(0, int(state.player.xp) + xp))
        )

    items = rewards.get("items")
    if isinstance(items, list) and items:
        inventory = [stack.snapshot() for stack in state.player.inventory]
        for raw_item in items:
            if not isinstance(raw_item, Mapping):
                continue
            item_id = str(raw_item.get("item_id", "")).strip()
            if not item_id:
                continue
            try:
                count = max(1, int(raw_item.get("count", 1)))
            except (TypeError, ValueError):
                count = 1
            for stack in inventory:
                if stack.get("item_id") == item_id:
                    stack["count"] = int(stack.get("count", 0)) + count
                    break
            else:
                inventory.append({"item_id": item_id, "count": count, "tags": []})
        changes.append(StateChange("player", "set", "inventory", inventory))

    return changes


def build_reward_summary(rewards: Any) -> dict[str, Any]:
    """Build a stable metadata summary for reward payloads."""
    if not isinstance(rewards, Mapping):
        return {}

    summary: dict[str, Any] = {}

    raw_gold = rewards.get("gold")
    raw_xp = rewards.get("xp")
    try:
        summary["gold"] = int(raw_gold) if raw_gold is not None else 0
    except (TypeError, ValueError):
        summary["gold"] = 0
    try:
        summary["xp"] = int(raw_xp) if raw_xp is not None else 0
    except (TypeError, ValueError):
        summary["xp"] = 0

    items = rewards.get("items")
    if isinstance(items, list):
        summary["items"] = [
            {
                "item_id": str(item.get("item_id", "")).strip(),
                "count": _coerce_item_count(item.get("count", 1)),
            }
            for item in items
            if isinstance(item, Mapping) and str(item.get("item_id", "")).strip()
        ]

    return summary


def _coerce_item_count(value: Any) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1
