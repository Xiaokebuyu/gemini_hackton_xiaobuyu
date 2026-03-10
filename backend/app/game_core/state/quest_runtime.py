"""Core-safe runtime quest projections.

This module exposes only runtime facts derived from ``QuestSlice.dynamic_quests``.
It intentionally does not add presentation concerns such as badges,
localized labels, sorting, or panel-specific fields.

Contract:
- ``requires_report`` and ``reported`` are persisted quest-state fields.
- ``can_report`` is always a derived runtime fact.
- Top-level quest fields win over ``metadata`` when both are present.
"""

from __future__ import annotations

from typing import Any, Mapping


def normalize_runtime_dynamic_quest(
    quest_id: str,
    raw_quest: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project one raw dynamic quest into stable runtime facts.

    The returned mapping is intentionally small and runtime-oriented:
    - ``requires_report`` / ``reported`` mirror persisted quest-state flags
    - ``can_report`` is derived as
      ``status == "completed" and requires_report and not reported``

    If both top-level quest fields and ``metadata`` contain report flags,
    the top-level fields take precedence. ``metadata`` is only a fallback for
    older or partial quest payloads still restored into memory.
    """

    quest_map = raw_quest if isinstance(raw_quest, Mapping) else {}
    metadata = quest_map.get("metadata", {})
    metadata_map = metadata if isinstance(metadata, Mapping) else {}

    status = str(quest_map.get("status", "")).strip()
    requires_report = _coerce_bool(
        quest_map.get("requires_report", metadata_map.get("requires_report"))
    )
    reported = _coerce_bool(quest_map.get("reported", metadata_map.get("reported")))
    can_report = status == "completed" and requires_report and not reported

    return {
        "quest_id": str(quest_map.get("quest_id", quest_id)).strip() or quest_id,
        "status": status,
        "requires_report": requires_report,
        "reported": reported,
        "can_report": can_report,
    }


def quest_can_report(raw_quest: Mapping[str, Any] | None) -> bool:
    """Return whether a quest is ready for receptionist turn-in.

    This helper is safe for core/runtime callers because it only depends on
    persisted quest-state flags plus the quest status.
    """

    return bool(
        normalize_runtime_dynamic_quest(
            str((raw_quest or {}).get("quest_id", "")),
            raw_quest,
        ).get("can_report")
    )


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False
