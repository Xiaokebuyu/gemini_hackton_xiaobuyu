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
    """展平嵌套列表，将 properties 子对象展平到顶层字段。"""
    nested = _read(base, "items.json").get("items", [])
    result: list[dict[str, Any]] = []
    for batch in nested:
        if not isinstance(batch, list):
            continue
        for item in batch:
            if not isinstance(item, dict):
                continue
            adapted = dict(item)
            props = adapted.pop("properties", None)
            if isinstance(props, dict):
                for k, v in props.items():
                    if k not in adapted:  # 顶层字段优先，properties 作补充
                        adapted[k] = v
            result.append(adapted)
    return result


def _load_skills(base: Path) -> list[dict[str, Any]]:
    return [s for s in _read(base, "skills.json").get("skills", []) if isinstance(s, dict)]


def _load_monsters(base: Path) -> list[dict[str, Any]]:
    return [m for m in _read(base, "monsters.json").get("monsters", []) if isinstance(m, dict)]


def _load_maps(base: Path) -> list[dict[str, Any]]:
    """直接透传地图数据，connection 对象由 MapRegistry 负责解析。"""
    items = _read(base, "maps.json").get("maps", [])
    return [area for area in items if isinstance(area, dict)]


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
