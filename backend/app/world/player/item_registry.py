"""Item registry - loads and queries items from structured data."""
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ITEMS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "goblin_slayer" / "structured" / "items.json"

# In-memory item lookup: item_id -> item dict
_ITEM_LOOKUP: Dict[str, Dict[str, Any]] = {}
_LOADED = False


def _flatten_items(raw: Any) -> List[Dict[str, Any]]:
    """Recursively flatten the nested items structure into a flat list."""
    result: List[Dict[str, Any]] = []
    if isinstance(raw, dict):
        item_id = raw.get("id", "")
        # Skip meta/placeholder entries
        if item_id and not item_id.startswith("---/") and raw.get("subtype") != "meta_rule":
            result.append(raw)
    elif isinstance(raw, list):
        for entry in raw:
            result.extend(_flatten_items(entry))
    return result


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
        raw_items = data.get("items", data) if isinstance(data, dict) else data
        flat = _flatten_items(raw_items)
        for item in flat:
            item_id = item.get("id", "")
            if item_id:
                _ITEM_LOOKUP[item_id] = item
        logger.info("Loaded %d items from %s", len(_ITEM_LOOKUP), _ITEMS_PATH)
    except Exception as exc:
        logger.error("Failed to load items: %s", exc)
    _LOADED = True


def get_item(item_id: str) -> Optional[Dict[str, Any]]:
    """Get item by ID."""
    _load()
    return _ITEM_LOOKUP.get(item_id)


def list_items(
    item_type: Optional[str] = None,
    rarity: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List items, optionally filtered by type and/or rarity."""
    _load()
    result = list(_ITEM_LOOKUP.values())
    if item_type:
        result = [i for i in result if i.get("type") == item_type]
    if rarity:
        result = [i for i in result if i.get("rarity") == rarity]
    return result


def search_items(query: str) -> List[Dict[str, Any]]:
    """Search items by name or description (case-insensitive)."""
    _load()
    q = query.lower()
    return [
        item for item in _ITEM_LOOKUP.values()
        if q in item.get("name", "").lower() or q in item.get("description", "").lower()
    ]
