"""ItemRegistry — 世界物品定义的只读数据源。

物品定义从 items.json 加载，解析为 Item Pydantic 模型（BG3/D&D 5e Schema）。
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.models.item import Item

logger = logging.getLogger(__name__)

_ITEMS_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "data" / "goblin_slayer" / "structured_new" / "items.json"
)

# In-memory lookup: item_id -> Item
_ITEM_LOOKUP: Dict[str, Item] = {}
_LOADED = False

# ── 价格解析 ──────────────────────────────────────────

_CURRENCY_TO_CP = {
    "cp": 1,
    "sp": 10,
    "gp": 100,
    "pp": 1000,
}

_PRICE_RE = re.compile(r"^(\d+)\+?\s*(cp|sp|gp|pp)$", re.IGNORECASE)


def parse_price(price_str: Optional[str]) -> Optional[int]:
    """解析价格字符串为铜币整数。

    "25 gp" → 2500, "10 sp" → 100, "500+ gp" → 50000
    "varies" / None / 无法解析 → None
    """
    if not price_str:
        return None
    s = price_str.strip()
    if s == "0":
        return 0
    m = _PRICE_RE.match(s)
    if not m:
        return None
    amount = int(m.group(1))
    unit = m.group(2).lower()
    return amount * _CURRENCY_TO_CP[unit]


# ── 加载 ──────────────────────────────────────────────

def _load() -> None:
    global _LOADED
    if _LOADED:
        return
    if not _ITEMS_PATH.exists():
        logger.warning("Items file not found: %s", _ITEMS_PATH)
        _LOADED = True
        return
    try:
        with open(_ITEMS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw_items = data.get("items", []) if isinstance(data, dict) else data
        if not isinstance(raw_items, list):
            raw_items = [raw_items]
        for raw in raw_items:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            try:
                item = Item.model_validate(raw)
                _ITEM_LOOKUP[item.id] = item
            except Exception as exc:
                logger.warning("Failed to parse item %s: %s", raw.get("id", "?"), exc)
        logger.info("Loaded %d items from %s", len(_ITEM_LOOKUP), _ITEMS_PATH)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load items: %s", exc)
    _LOADED = True


def reload() -> None:
    """强制重新加载（测试/热更新用）。"""
    global _LOADED
    _ITEM_LOOKUP.clear()
    _LOADED = False
    _load()


# ── 查询接口 ──────────────────────────────────────────

def get_item(item_id: str) -> Optional[Item]:
    """按 ID 精确查询。"""
    _load()
    return _ITEM_LOOKUP.get(item_id)


def list_items(
    item_type: Optional[str] = None,
    subtype: Optional[str] = None,
    rarity: Optional[str] = None,
) -> List[Item]:
    """列出物品，可按 type / subtype / rarity 过滤。"""
    _load()
    result = list(_ITEM_LOOKUP.values())
    if item_type:
        result = [i for i in result if i.type.value == item_type]
    if subtype:
        result = [i for i in result if i.subtype == subtype]
    if rarity:
        result = [i for i in result if i.rarity.value == rarity]
    return result


def search_items(query: str) -> List[Item]:
    """按名称或描述模糊搜索（不区分大小写）。"""
    _load()
    q = query.lower()
    return [
        item for item in _ITEM_LOOKUP.values()
        if q in item.name.lower() or q in item.description.lower()
    ]
