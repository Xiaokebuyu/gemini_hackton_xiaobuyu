"""goblin_slayer 数据文件加载与适配（T-2）。

将 data/goblin_slayer/structured_new/ 的 JSON 文件适配为
WorldInstance.load_all() 所期望的 world_data 格式。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# 哥布林杀手世界中的冒险者等级 → 近似 CR float
_CR_TIER_MAP: dict[str, float] = {
    "白瓷": 0.25,
    "黑曜石": 0.5,
    "白银": 1.0,
    "黄金": 3.0,
    "钢铁": 5.0,
    "白金": 8.0,
    "青铜": 12.0,
}


def _parse_price_sp(price: str) -> int | None:
    """解析 '30 sp' / '5 gp' / '10 cp' → 银币整数（1 gp=10 sp；cp<10 舍弃）。"""
    m = re.match(r"(\d+(?:\.\d+)?)\s*(gp|sp|cp)", price.strip(), re.IGNORECASE)
    if not m:
        return None
    amount, unit = float(m.group(1)), m.group(2).lower()
    if unit == "gp":
        return int(amount * 10)
    if unit == "sp":
        return int(amount)
    if unit == "cp":
        result = int(amount // 10)
        return result if result > 0 else None
    return None

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
            # I1: 解析 "30 sp" 等价格字符串 → base_price int（银币）
            price_raw = adapted.get("price")
            if isinstance(price_raw, str) and "base_price" not in adapted:
                parsed = _parse_price_sp(price_raw)
                if parsed is not None:
                    adapted["base_price"] = parsed
            result.append(adapted)
    return result


def _load_skills(base: Path) -> list[dict[str, Any]]:
    """递归展平嵌套列表，将各角色技能组并入顶层（S1）。

    skills.json 中有 12 个嵌套列表（每角色一组），内含 108 条技能；
    旧版 isinstance(s, dict) 过滤会将它们全部丢失。
    """
    result: list[dict[str, Any]] = []

    def _flatten(items: Any) -> None:
        for s in items:
            if isinstance(s, dict):
                result.append(s)
            elif isinstance(s, list):
                _flatten(s)

    _flatten(_read(base, "skills.json").get("skills", []))
    return result


def _load_monsters(base: Path) -> list[dict[str, Any]]:
    """适配 monsters.json → MonsterRegistry 格式（M1-M5）。

    原始格式与 Registry 期望的失配：
    - M1: stats 子对象（hp/ac/str…）需展平到顶层 + 构建 abilities Mapping
    - M2: "type" 字段需别名为 "creature_type"
    - M3: "challenge_rating"（中文等级）需映射为数值 cr
    - M4: attacks[].damage → attacks[].damage_dice 重命名
    - M5: 过滤 hp=0 的规则描述条目
    """
    raw_list = [
        m for m in _read(base, "monsters.json").get("monsters", [])
        if isinstance(m, dict)
    ]
    result: list[dict[str, Any]] = []
    for m in raw_list:
        stats = m.get("stats") if isinstance(m.get("stats"), dict) else {}
        # M5: 跳过 hp=0 的规则描述条目
        if stats.get("hp", 0) <= 0:
            continue
        adapted = dict(m)
        # M1: stats.hp / stats.ac → 顶层
        for k in ("hp", "ac"):
            if k in stats:
                adapted.setdefault(k, stats[k])
        # M1: 六维属性 → abilities Mapping
        abilities = {
            k: v
            for k in ("str", "dex", "con", "int", "wis", "cha")
            if (v := stats.get(k)) is not None
        }
        if abilities:
            adapted.setdefault("abilities", abilities)
        # M2: type → creature_type 别名
        adapted.setdefault("creature_type", adapted.get("type", ""))
        # M3: challenge_rating（中文）→ cr float
        if "cr" not in adapted:
            adapted["cr"] = _CR_TIER_MAP.get(
                str(adapted.get("challenge_rating", "")).strip()
            )
        # M4: attacks[].damage → attacks[].damage_dice 重命名
        raw_attacks = adapted.get("attacks", [])
        if isinstance(raw_attacks, list):
            fixed_attacks = []
            for atk in raw_attacks:
                if isinstance(atk, dict):
                    a = dict(atk)
                    if "damage_dice" not in a and "damage" in a:
                        a["damage_dice"] = a.pop("damage")
                    fixed_attacks.append(a)
            adapted["attacks"] = fixed_attacks
        result.append(adapted)
    return result


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
