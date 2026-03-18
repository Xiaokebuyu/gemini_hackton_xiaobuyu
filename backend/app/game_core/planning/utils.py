"""Shared utility functions for planner sub-systems."""
from __future__ import annotations

import json
from typing import Any, Mapping


def coerce_non_empty_string(value: Any) -> str | None:
    """Return *value* stripped if it is a non-empty string, else None."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def string_or_empty(value: Any) -> str:
    """Return *value* stripped if it is a non-empty string, else empty string."""
    return coerce_non_empty_string(value) or ""


def normalize_mapping(value: Any) -> dict[str, Any]:
    """Normalize a value to a dict. Accepts Mapping or JSON string."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return {}
    if isinstance(value, Mapping):
        return {str(key): val for key, val in value.items()}
    return {}
