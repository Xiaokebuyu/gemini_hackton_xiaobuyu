"""Pure payload builders for the new-game opening sequence."""

from __future__ import annotations

from typing import Any

from app.game_core import ManagedSession
from app.scene_views import build_location_overview, build_scene_change

_OPENING_POSITIONS_1: tuple[str, ...] = ("center",)
_OPENING_POSITIONS_2: tuple[str, ...] = ("left", "right")
_OPENING_POSITIONS_3: tuple[str, ...] = ("center", "left", "right")


def build_opening_narration(session: ManagedSession) -> str:
    """Build a deterministic opening narration from the current scene snapshot."""

    overview = build_location_overview(session)
    scene = build_scene_change(session)
    location_name = str(scene.get("location_name", "")).strip() or "这里"

    present_npcs = [
        item for item in overview.get("present_npcs", [])
        if isinstance(item, dict)
    ]
    sub_locations = [
        item for item in overview.get("sub_locations", [])
        if isinstance(item, dict)
        and bool(item.get("available", False))
        and str(item.get("id", "")).strip() != str(overview.get("location_id") or "").strip()
    ]
    exits = [
        item for item in overview.get("exits", [])
        if isinstance(item, dict) and not bool(item.get("blocked", False))
    ]

    first_npc_name = (
        str(present_npcs[0].get("name", "")).strip()
        if present_npcs
        else ""
    )
    first_sub_location_name = (
        str(sub_locations[0].get("name", "")).strip()
        if sub_locations
        else ""
    )
    first_exit_name = (
        str(exits[0].get("name", "")).strip()
        if exits
        else ""
    )

    lines: list[str] = [f"你站在{location_name}，新的旅程就从这里开始。"]
    if first_npc_name:
        lines.append(f"{first_npc_name}已经注意到了你的到来，似乎正等着看看你会先做什么。")
    else:
        lines.append("四周的人声与脚步声交织在一起，像是在催促你尽快做出第一个决定。")

    if first_sub_location_name and first_exit_name:
        lines.append(
            f"你可以先去{first_sub_location_name}看看，或者直接动身前往{first_exit_name}。"
        )
    elif first_sub_location_name:
        lines.append(f"眼下最顺手的选择，是先去{first_sub_location_name}探探情况。")
    elif first_exit_name:
        lines.append(f"如果你愿意，现在就能立刻启程前往{first_exit_name}。")
    else:
        lines.append("无论怎么开局，第一步终究得由你自己迈出去。")

    return " ".join(line for line in lines if line)


def build_opening_comment(session: ManagedSession) -> dict[str, Any]:
    """Build one short GM-style aside for the opening flow."""

    seeded_quest = _first_opening_quest(session)
    if seeded_quest is not None:
        quest_title = str(seeded_quest.get("title", "")).strip() or "新的线索"
        return {
            "content": f"在你真正迈步之前，公会已经替你准备好了一条线索：「{quest_title}」。",
            "tone": "grim",
        }

    bulletin = _first_opening_bulletin(session)
    if bulletin is not None:
        title = str(bulletin.get("title", "")).strip() or "一张新布告"
        return {
            "content": f"布告板上新贴出的那张纸还没凉透：「{title}」。",
            "tone": "grim",
        }

    overview = build_location_overview(session)
    has_npcs = any(isinstance(item, dict) for item in overview.get("present_npcs", []))
    if has_npcs:
        content = "至少这次，你不用对着空气假装自己胸有成竹。"
    else:
        content = "没人替你开路，正好省得你以为今天会很轻松。"
    return {
        "content": content,
        "tone": "sarcastic",
    }


def _first_opening_quest(session: ManagedSession) -> dict[str, Any] | None:
    quests = session.runtime.state.quests.dynamic_quests
    for quest in quests.values():
        if not isinstance(quest, dict):
            continue
        status = str(quest.get("status", "")).strip().lower()
        if status in {"available", "accepted", "active", "in_progress"}:
            return dict(quest)
    return None


def _first_opening_bulletin(session: ManagedSession) -> dict[str, Any] | None:
    area_id = session.runtime.state.player.current_area
    if not area_id:
        return None
    if not session.runtime.state.has_slice("areas"):
        return None
    area_state = session.runtime.state.areas.areas.get(area_id)
    if area_state is None:
        return None
    for entries in area_state.board_bulletins.values():
        for bulletin in entries:
            if isinstance(bulletin, dict):
                return dict(bulletin)
    return None


def build_opening_character_enters(session: ManagedSession) -> list[dict[str, Any]]:
    """Build ordered character_enter payloads from the current overview."""

    overview = build_location_overview(session)
    present_npcs = [
        item for item in overview.get("present_npcs", [])
        if isinstance(item, dict)
    ][:3]
    if not present_npcs:
        return []

    if len(present_npcs) == 1:
        positions = _OPENING_POSITIONS_1
    elif len(present_npcs) == 2:
        positions = _OPENING_POSITIONS_2
    else:
        positions = _OPENING_POSITIONS_3

    state = session.runtime.state
    events: list[dict[str, Any]] = []
    for index, npc in enumerate(present_npcs):
        npc_id = str(npc.get("character_id", "")).strip()
        emotion = "neutral"
        if npc_id and state.has_slice("relations"):
            disp = state.relations.get_disposition(npc_id) or {}
            if isinstance(disp, dict):
                approval = int(disp.get("approval", 0))
                trust = int(disp.get("trust", 0))
                if approval > 50:
                    emotion = "happy"
                elif approval < -30:
                    emotion = "angry"
                elif trust < -20:
                    emotion = "sad"
        events.append(
            {
                "character_id": npc_id,
                "position": positions[index],
                "emotion": emotion,
                "animation": "fade_in",
            }
        )
    return [item for item in events if item["character_id"]]


def build_opening_status_snapshot(session: ManagedSession) -> dict[str, Any]:
    """Build a HUD-oriented status snapshot for the opening flow."""

    player = session.runtime.state.player
    day = 1
    slot = 0
    period = "dawn"
    if session.runtime.state.has_slice("time"):
        day = int(session.runtime.state.time.day)
        slot = int(session.runtime.state.time.slot)
        period = str(session.runtime.state.time.period)
    return {
        "kind": "hud",
        "hp": int(player.hp),
        "max_hp": int(player.max_hp),
        "gold": int(player.gold),
        "day": day,
        "slot": slot,
        "period": period,
    }


def build_opening_dialogue_options(session: ManagedSession) -> list[dict[str, Any]]:
    """Build executable opening options from current scene data."""

    overview = build_location_overview(session)
    current_location_id = str(overview.get("location_id") or "").strip()

    options: list[dict[str, Any]] = []

    present_npcs = [
        item for item in overview.get("present_npcs", [])
        if isinstance(item, dict)
    ]
    if present_npcs:
        npc = present_npcs[0]
        npc_id = str(npc.get("character_id", "")).strip()
        npc_name = str(npc.get("name", "")).strip() or npc_id
        if npc_id:
            label = f"和{npc_name}说话"
            options.append(
                {
                    "id": f"opening-talk-{npc_id}",
                    "text": label,
                    "label": label,
                    "icon": "💬",
                    "dispatch": {
                        "kind": "interact",
                        "payload": {
                            "intent": "talk",
                            "target_kind": "npc",
                            "target_id": npc_id,
                        },
                    },
                }
            )

    sub_locations = [
        item for item in overview.get("sub_locations", [])
        if isinstance(item, dict)
        and bool(item.get("available", False))
        and str(item.get("id", "")).strip()
        and str(item.get("id", "")).strip() != current_location_id
    ]
    if sub_locations:
        loc = sub_locations[0]
        loc_id = str(loc.get("id", "")).strip()
        loc_name = str(loc.get("name", "")).strip() or loc_id
        if loc_id:
            label = f"进入{loc_name}"
            options.append(
                {
                    "id": f"opening-enter-{loc_id}",
                    "text": label,
                    "label": label,
                    "icon": "🚪",
                    "dispatch": {
                        "kind": "navigate",
                        "payload": {
                            "action": "enter_sub_location",
                            "location_id": loc_id,
                        },
                    },
                }
            )

    exits = [
        item for item in overview.get("exits", [])
        if isinstance(item, dict)
        and not bool(item.get("blocked", False))
        and str(item.get("target_area_id", "")).strip()
    ]
    if exits:
        exit_item = exits[0]
        target_area_id = str(exit_item.get("target_area_id", "")).strip()
        exit_name = str(exit_item.get("name", "")).strip() or target_area_id
        if target_area_id:
            label = f"前往{exit_name}"
            options.append(
                {
                    "id": f"opening-move-{target_area_id}",
                    "text": label,
                    "label": label,
                    "icon": "🚶",
                    "dispatch": {
                        "kind": "navigate",
                        "payload": {
                            "action": "move_area",
                            "area_id": target_area_id,
                        },
                    },
                }
            )

    return options
