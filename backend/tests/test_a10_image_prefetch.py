"""Tests for A10: dynamic sub-area background image prefetch integration."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Any
from unittest.mock import MagicMock, patch

from app.asset_resolver import AssetContext, AssetResolver, AssetResult
from app.game_core.orchestration.models import PipelineResult
from app.game_core.state import StateDelta
from app.game_core.state.delta import StateChange
from app.image_prefetch import (
    _sub_area_cache_key,
    get_cached_background_url,
    prefetch_sub_area_backgrounds,
)


# ── FakeAssetResolver ─────────────────────────────────────────────────────────


class FakeAssetResolver(AssetResolver):
    """AssetResolver subclass that stubs _generate() to avoid Gemini calls."""

    def __init__(self) -> None:
        # Skip super().__init__() to avoid Google SDK construction
        self._client = None
        self._cache: OrderedDict[str, AssetResult] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self.generate_calls: list[str] = []
        self.generate_results: dict[str, AssetResult] = {}

    async def _generate(self, ctx: AssetContext, *, is_portrait: bool = False) -> AssetResult:
        self.generate_calls.append(ctx.name)
        result = self.generate_results.get(
            ctx.name,
            AssetResult(image_base64="FAKEB64", mime_type="image/png", source="generated"),
        )
        return result


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_result_with_sub_area(sub_area: dict[str, Any]) -> PipelineResult:
    """Build a PipelineResult whose delta contains one temporary_sub_area add."""
    delta = StateDelta(
        changes=[
            StateChange(
                slice="areas",
                operation="add",
                path="frontier_town.temporary_sub_areas",
                value=sub_area,
            )
        ]
    )
    return PipelineResult(executed=True, delta=delta)


# ── prefetch_sub_area_backgrounds tests ───────────────────────────────────────


def test_prefetch_fires_task_for_new_sub_area():
    """When delta has a temporary_sub_areas change, prefetch schedules a task."""
    sub_area = {
        "id": "goblin_cave",
        "name": "Goblin Cave",
        "description": "A dark, dank cave filled with goblin stench.",
        "tags": ["cave", "hostile"],
    }
    result = _make_result_with_sub_area(sub_area)
    resolver = FakeAssetResolver()

    scheduled_tasks: list[str] = []

    async def _run() -> None:
        # We patch asyncio.create_task to capture without actually running tasks
        original_create_task = asyncio.create_task
        created: list[Any] = []

        def _capture_create_task(coro, *, name=None):
            created.append(name)
            return original_create_task(coro, name=name)

        with patch("app.image_prefetch.asyncio.create_task", side_effect=_capture_create_task):
            prefetch_sub_area_backgrounds(result, time_period="day", resolver=resolver)

        scheduled_tasks.extend(created)
        # drain all remaining tasks so the event loop is clean
        await asyncio.gather(*asyncio.all_tasks() - {asyncio.current_task()}, return_exceptions=True)

    asyncio.run(_run())

    assert any("goblin_cave" in (name or "") for name in scheduled_tasks), (
        f"Expected a task named with 'goblin_cave', got: {scheduled_tasks}"
    )


def test_prefetch_skips_sub_area_already_in_cache():
    """When cache already has the key, prefetch does NOT schedule a new task."""
    sub_area = {"id": "already_cached", "name": "Cached Area", "description": "already here", "tags": []}
    result = _make_result_with_sub_area(sub_area)
    resolver = FakeAssetResolver()

    # Pre-populate cache
    cache_key = _sub_area_cache_key("already_cached", "day")
    cached_result = AssetResult(image_base64="EXISTINGB64", mime_type="image/png", source="generated")
    resolver._cache_set(cache_key, cached_result)

    scheduled: list[Any] = []

    async def _run() -> None:
        def _capture(coro, *, name=None):
            scheduled.append(name)
            return asyncio.ensure_future(coro)

        with patch("app.image_prefetch.asyncio.create_task", side_effect=_capture):
            prefetch_sub_area_backgrounds(result, time_period="day", resolver=resolver)

        await asyncio.gather(*asyncio.all_tasks() - {asyncio.current_task()}, return_exceptions=True)

    asyncio.run(_run())
    assert len(scheduled) == 0, f"Expected no tasks scheduled for cached area, got: {scheduled}"


def test_prefetch_no_op_on_empty_delta():
    """When PipelineResult has no delta, prefetch does nothing."""
    result = PipelineResult(executed=True, delta=None)
    resolver = FakeAssetResolver()

    scheduled: list[Any] = []

    async def _run() -> None:
        def _capture(coro, *, name=None):
            scheduled.append(name)
            return asyncio.ensure_future(coro)

        with patch("app.image_prefetch.asyncio.create_task", side_effect=_capture):
            prefetch_sub_area_backgrounds(result, resolver=resolver)

    asyncio.run(_run())
    assert len(scheduled) == 0


def test_prefetch_no_op_on_non_area_changes():
    """Only 'areas' slice changes with 'temporary_sub_areas' trigger prefetch."""
    delta = StateDelta(
        changes=[
            StateChange(slice="quests", operation="set", path="some_quest.status", value="active"),
            StateChange(slice="player", operation="set", path="current_area", value="ruins"),
        ]
    )
    result = PipelineResult(executed=True, delta=delta)
    resolver = FakeAssetResolver()

    scheduled: list[Any] = []

    async def _run() -> None:
        def _capture(coro, *, name=None):
            scheduled.append(name)
            return asyncio.ensure_future(coro)

        with patch("app.image_prefetch.asyncio.create_task", side_effect=_capture):
            prefetch_sub_area_backgrounds(result, resolver=resolver)

    asyncio.run(_run())
    assert len(scheduled) == 0


def test_prefetch_sub_area_missing_id_is_skipped():
    """Sub-area dicts without an 'id' field should be silently ignored."""
    delta = StateDelta(
        changes=[
            StateChange(
                slice="areas",
                operation="add",
                path="frontier_town.temporary_sub_areas",
                value={"name": "No ID Area", "description": "oops"},  # no 'id'
            )
        ]
    )
    result = PipelineResult(executed=True, delta=delta)
    resolver = FakeAssetResolver()

    scheduled: list[Any] = []

    async def _run() -> None:
        def _capture(coro, *, name=None):
            scheduled.append(name)
            return asyncio.ensure_future(coro)

        with patch("app.image_prefetch.asyncio.create_task", side_effect=_capture):
            prefetch_sub_area_backgrounds(result, resolver=resolver)

    asyncio.run(_run())
    assert len(scheduled) == 0


# ── get_cached_background_url tests ──────────────────────────────────────────


def test_get_cached_background_url_hit():
    """Returns a data URL when the resolver has a cached entry."""
    resolver = FakeAssetResolver()
    cache_key = _sub_area_cache_key("goblin_cave", "day")
    resolver._cache_set(cache_key, AssetResult(image_base64="ABC123", mime_type="image/png", source="generated"))

    url = get_cached_background_url("goblin_cave", time_period="day", resolver=resolver)
    assert url is not None
    assert url == "data:image/png;base64,ABC123"


def test_get_cached_background_url_miss():
    """Returns None when the key is not in cache."""
    resolver = FakeAssetResolver()
    url = get_cached_background_url("no_such_area", time_period="day", resolver=resolver)
    assert url is None


def test_get_cached_background_url_fallback_not_returned():
    """If cached result has image_base64=None (fallback), returns None."""
    resolver = FakeAssetResolver()
    cache_key = _sub_area_cache_key("fallback_area", "day")
    resolver._cache_set(cache_key, AssetResult(image_base64=None, mime_type="image/png", source="fallback"))

    url = get_cached_background_url("fallback_area", time_period="day", resolver=resolver)
    assert url is None


# ── build_scene_change integration tests ─────────────────────────────────────


def _make_mock_session(
    area_id: str,
    location_id: str | None,
    temporary_sub_areas: list[dict],
    time_period: str = "day",
    has_maps_registry: bool = False,
) -> MagicMock:
    """Build a minimal ManagedSession mock for scene_change tests."""
    session = MagicMock()

    player = MagicMock()
    player.current_area = area_id
    player.current_location = location_id or ""
    session.runtime.state.player = player

    # area state
    area_state = MagicMock()
    area_state.temporary_sub_areas = temporary_sub_areas
    session.runtime.state.areas.areas = {area_id: area_state}

    # time slice
    session.runtime.state.has_slice.return_value = True
    time_slice = MagicMock()
    time_slice.period = time_period
    session.runtime.state.time = time_slice

    # maps registry
    session.runtime.world.has_registry.return_value = has_maps_registry
    if has_maps_registry:
        area_template = MagicMock()
        area_template.name = area_id
        area_template.sub_locations = {}  # no static sub-locations
        session.runtime.world.maps.get.return_value = area_template

    return session


def test_build_scene_change_background_url_for_dynamic_sub_location():
    """scene_change includes background_url when entering cached dynamic sub-location."""
    from app.scene_views import build_scene_change

    resolver = FakeAssetResolver()
    # Pre-populate cache for the sub-location
    cache_key = _sub_area_cache_key("temp_cave", "day")
    resolver._cache_set(
        cache_key,
        AssetResult(image_base64="IMAGEB64", mime_type="image/png", source="generated"),
    )

    sub_area = {"id": "temp_cave", "name": "Temporary Cave", "description": "A new cave"}
    session = _make_mock_session(
        area_id="frontier_town",
        location_id="temp_cave",
        temporary_sub_areas=[sub_area],
        time_period="day",
        has_maps_registry=True,
    )

    payload = build_scene_change(session, asset_resolver=resolver)

    assert "background_url" in payload, f"Expected background_url in payload, got: {list(payload.keys())}"
    assert payload["background_url"] == "data:image/png;base64,IMAGEB64"


def test_build_scene_change_no_background_url_on_cache_miss():
    """scene_change omits background_url when cache has no entry for sub-location."""
    from app.scene_views import build_scene_change

    resolver = FakeAssetResolver()  # empty cache

    sub_area = {"id": "temp_cave", "name": "Temporary Cave", "description": "A new cave"}
    session = _make_mock_session(
        area_id="frontier_town",
        location_id="temp_cave",
        temporary_sub_areas=[sub_area],
        time_period="day",
        has_maps_registry=True,
    )

    payload = build_scene_change(session, asset_resolver=resolver)

    # background_url must NOT be in the payload on a cache miss
    assert "background_url" not in payload


def test_build_scene_change_no_background_url_without_resolver():
    """scene_change omits background_url when no resolver is passed."""
    from app.scene_views import build_scene_change

    sub_area = {"id": "temp_cave", "name": "Temporary Cave", "description": "desc"}
    session = _make_mock_session(
        area_id="frontier_town",
        location_id="temp_cave",
        temporary_sub_areas=[sub_area],
        time_period="day",
        has_maps_registry=True,
    )

    payload = build_scene_change(session)  # no resolver

    assert "background_url" not in payload
