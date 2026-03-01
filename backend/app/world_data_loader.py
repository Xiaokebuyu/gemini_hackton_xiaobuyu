"""goblin_slayer 数据文件加载与适配（T-2）。

将 data/goblin_slayer/structured_new/ 的 JSON 文件适配为
WorldInstance.load_all() 所期望的 world_data 格式。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data" / "goblin_slayer" / "structured_new"


def load_goblin_slayer_world_data(
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """读取 structured_new JSON，返回 WorldInstance.load_all() 格式的 world_data。"""
    base = data_dir or _DEFAULT_DATA_DIR
    return {
        "characters": _load_characters(base),
        "items": _load_items(base),
        "skills": _load_skills(base),
        "monsters": _load_monsters(base),
        "maps": _load_maps(base),
        "quests": _load_quests(base),
    }


def _read(base: Path, name: str) -> Any:
    with open(base / name, encoding="utf-8") as f:
        return json.load(f)


def _load_characters(base: Path) -> list[dict[str, Any]]:
    """重命名 default_map → area_id，default_sub_location → location_id。"""
    items = _read(base, "characters.json").get("characters", [])
    result: list[dict[str, Any]] = []
    for ch in items:
        if not isinstance(ch, dict):
            continue
        adapted = dict(ch)
        if "default_map" in adapted:
            adapted.setdefault("area_id", adapted.pop("default_map"))
        if "default_sub_location" in adapted:
            adapted.setdefault("location_id", adapted.pop("default_sub_location"))
        result.append(adapted)
    return result


def _load_items(base: Path) -> list[dict[str, Any]]:
    """展平嵌套列表，过滤掉字符串表头行。"""
    nested = _read(base, "items.json").get("items", [])
    return [
        item
        for batch in nested
        if isinstance(batch, list)
        for item in batch
        if isinstance(item, dict)
    ]


def _load_skills(base: Path) -> list[dict[str, Any]]:
    return [s for s in _read(base, "skills.json").get("skills", []) if isinstance(s, dict)]


def _load_monsters(base: Path) -> list[dict[str, Any]]:
    return [m for m in _read(base, "monsters.json").get("monsters", []) if isinstance(m, dict)]


def _load_maps(base: Path) -> list[dict[str, Any]]:
    """将 connections 对象中的 target_map_id 提取为字符串。"""
    items = _read(base, "maps.json").get("maps", [])
    result: list[dict[str, Any]] = []
    for area in items:
        if not isinstance(area, dict):
            continue
        adapted = dict(area)
        raw_conn = adapted.get("connections")
        if isinstance(raw_conn, list):
            adapted["connections"] = [
                entry["target_map_id"]
                if isinstance(entry, dict)
                else str(entry)
                for entry in raw_conn
                if (isinstance(entry, dict) and entry.get("target_map_id"))
                or isinstance(entry, str)
            ]
        result.append(adapted)
    return result


def _load_quests(base: Path) -> dict[str, Any]:
    """chapters_v2 → {chapters: [...], milestones: {...}}。"""
    chapters_raw = _read(base, "chapters_v2.json")
    if not isinstance(chapters_raw, list):
        return {}
    chapters: list[dict[str, Any]] = []
    milestones: dict[str, dict[str, Any]] = {}
    for ch in chapters_raw:
        if not isinstance(ch, dict) or not ch.get("id"):
            continue
        ch_id = str(ch["id"])
        chapters.append({
            "id": ch_id,
            "title": ch.get("name", ""),
            "description": ch.get("description", ""),
        })
        for ev in ch.get("events", []):
            if not isinstance(ev, dict) or not ev.get("id"):
                continue
            ev_id = str(ev["id"])
            milestones[ev_id] = {
                "id": ev_id,
                "title": ev.get("name", ""),
                "description": ev.get("description", ""),
                "chapter_id": ch_id,
            }
    return {"chapters": chapters, "milestones": milestones}
