"""MapRegistry implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry


class MapRegistry(ContentRegistry):
    """Registry for area and map templates."""

    def __init__(self) -> None:
        super().__init__("maps")
        self._items: dict[str, dict[str, Any]] = {}

    def load(self, data: dict[str, Any]) -> None:
        self._items = self._coerce_mapping(data)

    def get(self, content_id: str) -> Any | None:
        item = self._items.get(content_id)
        return dict(item) if isinstance(item, dict) else item

    def list_all(self) -> list[Any]:
        return [dict(value) for value in self._items.values()]

    def validate(self) -> list[str]:
        issues: list[str] = []
        for item_id, item in self._items.items():
            if not item.get("id"):
                issues.append(f"map '{item_id}' missing id")
        return issues

    @staticmethod
    def _coerce_mapping(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        if isinstance(data, list):
            for raw in data:
                if isinstance(raw, Mapping):
                    item_id = str(raw.get("id", "")).strip()
                    if item_id:
                        result[item_id] = dict(raw)
            return result
        if isinstance(data, Mapping):
            for key, raw in data.items():
                if isinstance(raw, Mapping):
                    payload = dict(raw)
                    payload.setdefault("id", str(key))
                    result[str(key)] = payload
        return result
