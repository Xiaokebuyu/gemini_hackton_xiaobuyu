"""Shared utility functions for planner sub-systems."""
from __future__ import annotations

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
    """Return a copy of *value* if it is a Mapping, else an empty dict."""
    if isinstance(value, Mapping):
        return {str(key): val for key, val in value.items()}
    return {}
