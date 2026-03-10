"""Application-layer quest read-model helpers.

These helpers build presentation-oriented quest views on top of the core
runtime projection in ``app.game_core.state.quest_runtime``.

Contract split:
- persisted quest-state fields: ``requires_report``, ``reported``
- derived runtime fact: ``can_report``
- application read-model fields: ``ui_state``, ``badge``, report guidance text
"""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.state.quest_runtime import (
    normalize_runtime_dynamic_quest,
    quest_can_report as runtime_quest_can_report,
)


_REPORT_CURRENT_STEP = "Return to the guild receptionist and report the completed lead."
_REPORT_NEXT_STEP = "Speak to the receptionist to turn in the quest."
_REPORT_HINT = "Completed quests that require a report must be confirmed at the guild counter."
_BADGE_LABELS = {
    "active": "进行中",
    "ready_to_report": "待汇报",
    "available": "可接取",
    "completed": "已完成",
    "failed": "失败",
    "retired": "已撤回",
    "expired": "已过期",
    "unknown": "未知",
}


def normalize_dynamic_quest_view(
    quest_id: str,
    raw_quest: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a stable quest-panel / snapshot view for one dynamic quest.

    This is the application-layer canonical quest view used by:
    - ``/quests`` panel payloads
    - NPC ``ask_*`` quest snapshots

    It may add presentation-only fields such as ``ui_state`` and ``badge``,
    but it does not change the underlying persisted quest schema.
    """

    quest_map = raw_quest if isinstance(raw_quest, Mapping) else {}
    runtime_quest = normalize_runtime_dynamic_quest(quest_id, quest_map)

    status = str(runtime_quest.get("status", "")).strip()
    requires_report = bool(runtime_quest.get("requires_report"))
    reported = bool(runtime_quest.get("reported"))
    can_report = bool(runtime_quest.get("can_report"))
    ui_state = _quest_ui_state(status, can_report=can_report)
    badge = {
        "key": ui_state,
        "label": _BADGE_LABELS[ui_state],
    }

    current_step = _coerce_optional_text(quest_map.get("current_step"))
    next_steps = _coerce_text_list(quest_map.get("next_steps"))
    hints = _coerce_text_list(quest_map.get("hints"))
    rewards = normalize_dynamic_quest_rewards(quest_map)

    if can_report:
        # Completed-but-unreported quests get deterministic receptionist guidance
        # in the read model. This is display-only and is not persisted.
        if current_step is None:
            current_step = _REPORT_CURRENT_STEP
        if not next_steps:
            next_steps = [_REPORT_NEXT_STEP]
        if not hints:
            hints = [_REPORT_HINT]

    quest_view = {
        key: value
        for key, value in quest_map.items()
        if key not in {"reward_gold", "reward_items", "reward_summary"}
    }
    quest_view["quest_id"] = str(quest_map.get("quest_id", quest_id)).strip() or quest_id
    quest_view["status"] = status
    quest_view["title"] = str(quest_map.get("title", "")).strip()
    quest_view["summary"] = str(quest_map.get("summary", "")).strip()
    quest_view["current_step"] = current_step
    quest_view["next_steps"] = next_steps
    quest_view["hints"] = hints
    quest_view["requires_report"] = requires_report
    quest_view["reported"] = reported
    quest_view["can_report"] = can_report
    quest_view["ui_state"] = ui_state
    quest_view["badge"] = badge
    quest_view["rewards"] = rewards
    return quest_view


def normalize_dynamic_quest_panel(
    raw_dynamic_quests: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Normalize all dynamic quests into the canonical quest-panel read model."""

    normalized_entries: list[tuple[str, dict[str, Any]]] = []
    for raw_quest_id, raw_quest in raw_dynamic_quests.items():
        quest_id = str(raw_quest_id).strip()
        if not quest_id:
            continue
        normalized_entries.append(
            (quest_id, normalize_dynamic_quest_view(quest_id, raw_quest))
        )
    normalized_entries.sort(key=lambda item: _quest_panel_sort_key(item[1]))
    return {quest_id: quest_view for quest_id, quest_view in normalized_entries}


def quest_can_report(raw_quest: Mapping[str, Any] | None) -> bool:
    """Return whether a quest is ready for receptionist turn-in.

    This remains a thin application-layer re-export so existing callers do not
    need to import the core runtime helper directly.
    """
    return runtime_quest_can_report(raw_quest)


def normalize_dynamic_quest_rewards(
    raw_quest: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the canonical reward mapping for one dynamic quest."""

    quest_map = raw_quest if isinstance(raw_quest, Mapping) else {}
    raw_rewards = quest_map.get("rewards", {})
    rewards = raw_rewards if isinstance(raw_rewards, Mapping) else {}

    normalized: dict[str, Any] = {}
    gold = _coerce_optional_non_negative_int(rewards.get("gold"))
    if gold is not None:
        normalized["gold"] = gold
    xp = _coerce_optional_non_negative_int(rewards.get("xp"))
    if xp is not None:
        normalized["xp"] = xp
    items = _coerce_reward_items(rewards.get("items"))
    if items:
        normalized["items"] = items
    return normalized


def _quest_ui_state(status: str, *, can_report: bool) -> str:
    normalized_status = status.strip().lower()
    if normalized_status == "active":
        return "active"
    if normalized_status == "completed" and can_report:
        return "ready_to_report"
    if normalized_status == "available":
        return "available"
    if normalized_status == "completed":
        return "completed"
    if normalized_status == "failed":
        return "failed"
    if normalized_status == "retired":
        return "retired"
    if normalized_status == "expired":
        return "expired"
    return "unknown"


def _quest_panel_sort_key(quest_view: Mapping[str, Any]) -> tuple[int, int, int, str]:
    status = str(quest_view.get("status", "")).strip().lower()
    can_report = bool(quest_view.get("can_report"))
    created_at_tick = _coerce_optional_non_negative_int(quest_view.get("created_at_tick"))
    if status == "active":
        rank = 0
    elif status == "completed" and can_report:
        rank = 1
    elif status == "available":
        rank = 2
    elif status == "completed":
        rank = 3
    elif status == "failed":
        rank = 4
    elif status == "retired":
        rank = 5
    elif status == "expired":
        rank = 6
    else:
        rank = 7
    missing_created_at = 1 if created_at_tick is None else 0
    created_at_key = 0 if created_at_tick is None else -created_at_tick
    quest_id = str(quest_view.get("quest_id", "")).strip()
    return (rank, missing_created_at, created_at_key, quest_id)


def _coerce_optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _coerce_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _coerce_reward_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for raw_item in value:
        if not isinstance(raw_item, Mapping):
            continue
        item_id = str(raw_item.get("item_id", "")).strip()
        if not item_id:
            continue
        count = _coerce_optional_non_negative_int(raw_item.get("count"))
        result.append({"item_id": item_id, "count": count or 1})
    return result


def _coerce_optional_non_negative_int(value: Any) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized >= 0 else None
