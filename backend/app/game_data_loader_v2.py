"""V2 世界数据加载器 — 从 data/goblin_slayer/v2/ 读取 LLM 生成的数据。

运行 v2_data_pipeline.py 后，使用此加载器向引擎提供数据。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_V2_DIR = Path(__file__).resolve().parent.parent / "data" / "goblin_slayer" / "v2"


def _load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_v2_world_data() -> dict[str, Any]:
    """加载 v2 世界数据，返回 WorldInstance.load_all() 所需的 world-data mapping。"""
    return {
        "tags":       _load_json(_V2_DIR / "tags.json"),
        "factions":   _load_json(_V2_DIR / "factions.json"),
        "lore":       _load_json(_V2_DIR / "lore.json"),
        "classes":    _load_json(_V2_DIR / "classes.json"),
        "characters": _load_json(_V2_DIR / "characters.json"),
        "items":      _load_json(_V2_DIR / "items.json"),
        "skills":     _load_json(_V2_DIR / "skills.json"),
        "monsters":   _load_json(_V2_DIR / "monsters.json"),
        "maps":       _load_json(_V2_DIR / "maps.json"),
        "quests":     _load_json(_V2_DIR / "quests.json"),
        "battle_maps": _load_json(_V2_DIR / "battle_maps.json"),
    }
