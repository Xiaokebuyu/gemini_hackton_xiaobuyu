"""Pure payload builders for session resume hydration."""

from __future__ import annotations

from typing import Any

from app.game_core import ManagedSession
from app.scene_views import build_location_overview, build_scene_change

_PERIOD_LABELS: dict[str, str] = {
    "dawn": "黎明",
    "day": "白昼",
    "dusk": "黄昏",
    "night": "深夜",
}


def build_resume_narration(session: ManagedSession) -> str:
    """Build deterministic resume narration from current runtime state."""

    player = session.runtime.state.player
    if not player.character_id.strip():
        return "你的旅程还停在启程之前。先完成角色创建，再真正踏出第一步。"

    overview = build_location_overview(session)
    scene = build_scene_change(session)
    location_name = str(scene.get("location_name", "")).strip() or "这里"

    day = None
    period = None
    if session.runtime.state.has_slice("time"):
        day = int(session.runtime.state.time.day)
        period = _PERIOD_LABELS.get(
            str(session.runtime.state.time.period).strip().lower(),
            str(session.runtime.state.time.period).strip() or None,
        )

    present_npcs = [
        item for item in overview.get("present_npcs", [])
        if isinstance(item, dict)
    ]
    first_npc_name = (
        str(present_npcs[0].get("name", "")).strip()
        if present_npcs
        else ""
    )

    quest_title = _first_active_quest_title(session)
    player_name = player.character_name.strip() or "冒险者"

    parts: list[str] = [f"{player_name}，你回到了{location_name}。"]
    if day is not None and period:
        parts.append(f"现在是第{day}天的{period}。")
    elif period:
        parts.append(f"眼下正是{period}时分。")

    if first_npc_name:
        parts.append(f"{first_npc_name}还在这里，等着看你接下来会怎么做。")
    else:
        parts.append("周围的空气没有替你停下来，决定仍得由你自己做。")

    if quest_title:
        parts.append(f"你手头最醒目的线索仍是「{quest_title}」。")

    return " ".join(part for part in parts if part)


def build_resume_location_visual(session: ManagedSession) -> dict[str, Any]:
    """Build lightweight visual restore hints for the frontend."""

    overview = build_location_overview(session)
    area_id = str(overview.get("area_id", "")).strip()
    location_id = str(overview.get("location_id") or "").strip() or None
    background_key = f"{area_id}/{location_id}" if area_id and location_id else area_id
    present_character_ids = [
        str(item.get("character_id", "")).strip()
        for item in overview.get("present_npcs", [])
        if isinstance(item, dict) and str(item.get("character_id", "")).strip()
    ]
    return {
        "area_id": area_id,
        "location_id": location_id,
        "background_key": background_key,
        "present_character_ids": present_character_ids,
    }


def _first_active_quest_title(session: ManagedSession) -> str:
    quests = session.runtime.state.quests.dynamic_quests
    for quest in quests.values():
        if not isinstance(quest, dict):
            continue
        status = str(quest.get("status", "")).strip().lower()
        if status not in {"available", "accepted", "active", "in_progress"}:
            continue
        title = str(quest.get("title", "")).strip()
        if title:
            return title
    return ""
