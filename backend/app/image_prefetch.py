"""Dynamic sub-area background image prefetch integration (A10).

Wires the AssetResolver singleton into the tick pipeline so that when a
Planner directive creates a new temporary_sub_area, a background image is
generated non-blockingly in the background.  When the player later enters
that sub-location, build_scene_change() can attach the cached image URL.

Public surface:
- get_asset_resolver() → AssetResolver singleton (lazy-initialized)
- prefetch_sub_area_backgrounds(result) → fire-and-forget generate tasks
- get_cached_background_url(sub_area_id, time_period) → URL str | None
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from app.asset_resolver import AssetContext, AssetResolver, AssetResult

if TYPE_CHECKING:
    from app.game_core.orchestration.models import PipelineResult

logger = logging.getLogger(__name__)

# ── Singleton ─────────────────────────────────────────────────────────────────

_resolver: AssetResolver | None = None


def get_asset_resolver() -> AssetResolver:
    """Return the module-level singleton AssetResolver (lazy-initialized)."""
    global _resolver
    if _resolver is None:
        _resolver = AssetResolver()
    return _resolver


# ── Cache key helpers ─────────────────────────────────────────────────────────


def _sub_area_cache_key(sub_area_id: str, time_period: str = "day") -> str:
    """Build the canonical cache key for a temporary sub-area background."""
    return f"temp/{sub_area_id}/background/{time_period}"


# ── Prefetch on pipeline result ───────────────────────────────────────────────


def prefetch_sub_area_backgrounds(
    result: "PipelineResult",
    time_period: str = "day",
    resolver: AssetResolver | None = None,
) -> None:
    """Scan PipelineResult.delta for newly created temporary_sub_areas and
    fire non-blocking background-generation tasks for each one.

    This function is intentionally synchronous — it spawns asyncio tasks and
    returns immediately.  It must only be called from within a running event
    loop (i.e. inside an async route handler).

    Args:
        result: Completed PipelineResult from TickCoordinator.process().
        time_period: Current in-game time period (e.g. "day", "night").
        resolver: AssetResolver to use; defaults to the module singleton.
    """
    if result.delta is None:
        return

    resolver = resolver or get_asset_resolver()

    for change in result.delta.changes:
        if change.slice != "areas":
            continue
        # Path pattern: "{area_id}.temporary_sub_areas" (operation "add")
        if "temporary_sub_areas" not in change.path:
            continue
        sub_area = change.value
        if not isinstance(sub_area, dict):
            continue
        sub_area_id = str(sub_area.get("id") or "").strip()
        if not sub_area_id:
            continue
        description = str(sub_area.get("description") or "").strip()
        name = str(sub_area.get("name") or sub_area.get("label") or sub_area_id).strip()
        tags = _extract_tags(sub_area)

        cache_key = _sub_area_cache_key(sub_area_id, time_period)

        # Skip if already cached (synchronous check — no I/O)
        if resolver._cache_get(cache_key) is not None:
            logger.debug("image_prefetch: cache hit for %s, skipping", cache_key)
            continue

        ctx = AssetContext(
            name=name,
            description=description,
            tags=tags,
            time_period=time_period,
        )

        logger.debug("image_prefetch: scheduling background generation for %s", sub_area_id)
        asyncio.create_task(
            _prefetch_one(resolver, cache_key, ctx),
            name=f"img_prefetch_{sub_area_id}",
        )


async def _prefetch_one(resolver: AssetResolver, key: str, ctx: AssetContext) -> None:
    """Coroutine that generates and caches one background asset."""
    try:
        await resolver.resolve(key, ctx)
        logger.debug("image_prefetch: completed generation for key=%s", key)
    except Exception:
        logger.exception("image_prefetch: generation failed for key=%s", key)


def _extract_tags(sub_area: dict[str, Any]) -> list[str]:
    """Extract a list of string tags from a sub-area dict."""
    raw = sub_area.get("tags") or []
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(t).strip() for t in raw if str(t).strip()]


# ── Cache lookup for scene_change ─────────────────────────────────────────────


def get_cached_background_url(
    sub_area_id: str,
    time_period: str = "day",
    resolver: AssetResolver | None = None,
) -> str | None:
    """Return a data URL for a cached sub-area background, or None on miss.

    This is a synchronous cache-only lookup — it never triggers generation.
    Safe to call from build_scene_change() (a sync function).

    Returns:
        "data:image/png;base64,<b64>" string if cached, None otherwise.
    """
    resolver = resolver or get_asset_resolver()
    cache_key = _sub_area_cache_key(sub_area_id, time_period)
    result: AssetResult | None = resolver._cache_get(cache_key)
    if result is None or result.image_base64 is None:
        return None
    return f"data:{result.mime_type};base64,{result.image_base64}"
