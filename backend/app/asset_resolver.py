"""AssetResolver — in-memory LRU cache + prompt builder for AI-generated assets (P4 Part 3).

Implements the interface expected by tests/test_asset_resolver.py:
- CACHE_SIZE: int
- AssetContext: dataclass (name, description, tags, time_period, role)
- AssetResult: dataclass (image_base64, mime_type, source)
- AssetPromptBuilder: static scene() / portrait() methods
- AssetResolver: resolve() / invalidate() / _cache_get() / _generate()
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

CACHE_SIZE = 128


# ── Data models ───────────────────────────────────────────────────────────────


@dataclass
class AssetContext:
    name: str
    description: str
    tags: list[str]
    time_period: str = ""
    role: str = ""


@dataclass
class AssetResult:
    image_base64: str | None
    mime_type: str
    source: str  # "generated" | "cache" | "fallback"


# ── Prompt builder ────────────────────────────────────────────────────────────

_NIGHT_PERIODS = frozenset({"night"})
_TIME_DESCS: dict[str, str] = {
    "dawn":  "Time: early dawn, soft orange-pink light fading from night, moonlight receding.",
    "day":   "Time: bright daytime, clear sunlight.",
    "dusk":  "Time: dusk, golden hour, warm amber light.",
    "night": "Time: deep night, moonlight and stars, dark shadows.",
}


class AssetPromptBuilder:
    @staticmethod
    def scene(ctx: AssetContext) -> str:
        """Build a scene background prompt.

        Required keywords: "dark fantasy RPG", "no characters", "background art", ctx.name.
        If time_period=="night": must contain "moonlight" or "night".
        """
        tag_line = f" Key features: {', '.join(ctx.tags)}." if ctx.tags else ""
        time_line = ""
        if ctx.time_period:
            time_line = " " + _TIME_DESCS.get(
                ctx.time_period,
                f"Time: {ctx.time_period}, night atmosphere.",
            )
        return (
            f"Create a dark fantasy RPG background art for a visual novel. "
            f"no characters, no text, no UI elements. "
            f"Location: {ctx.name}. {ctx.description}{tag_line}{time_line} "
            f"Style: detailed painterly illustration, atmospheric mood, cinematic composition."
        )

    @staticmethod
    def portrait(ctx: AssetContext) -> str:
        """Build a character portrait prompt.

        Required keywords: "dark fantasy RPG", "character portrait", "upper body", ctx.name.
        If ctx.role: must appear in prompt.
        """
        role_line = f" Role: {ctx.role}." if ctx.role else ""
        tag_line = f" Traits: {', '.join(ctx.tags[:6])}." if ctx.tags else ""
        return (
            f"Create a dark fantasy RPG character portrait, upper body framing, "
            f"for a visual novel. "
            f"Character: {ctx.name}. {ctx.description}{role_line}{tag_line} "
            f"Style: detailed anime illustration, clean background, no text, no UI."
        )


# ── Resolver ──────────────────────────────────────────────────────────────────


class AssetResolver:
    """In-memory LRU cache layer over AI image generation.

    FakeAssetResolver in tests overrides _generate() and skips __init__(),
    so __init__ must only set _client, _cache, and _locks.
    """

    def __init__(self) -> None:
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        self._client: Any = None
        if api_key:
            try:
                from google import genai
                self._client = genai.Client(api_key=api_key)
            except Exception:
                logger.warning("AssetResolver: google-genai unavailable, generation disabled")
        self._cache: OrderedDict[str, AssetResult] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}

    # ── Cache primitives ──────────────────────────────────────────────────────

    def _cache_get(self, key: str) -> AssetResult | None:
        """Return cached result (LRU: moves to end on hit), or None."""
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def _cache_set(self, key: str, result: AssetResult) -> None:
        """Insert result into cache; evict oldest entry if over CACHE_SIZE."""
        self._cache[key] = result
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)

    def invalidate(self, key: str) -> None:
        """Remove a key from cache, forcing regeneration on next resolve()."""
        self._cache.pop(key, None)

    # ── Public API ────────────────────────────────────────────────────────────

    async def resolve(self, key: str, ctx: AssetContext) -> AssetResult:
        """Resolve an asset: cache hit → return immediately; miss → generate and cache."""
        cached = self._cache_get(key)
        if cached is not None:
            return AssetResult(
                image_base64=cached.image_base64,
                mime_type=cached.mime_type,
                source="cache",
            )

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            # Double-check after acquiring lock (another coroutine may have populated cache)
            cached = self._cache_get(key)
            if cached is not None:
                return AssetResult(
                    image_base64=cached.image_base64,
                    mime_type=cached.mime_type,
                    source="cache",
                )
            result = await self._generate(ctx)
            if result.image_base64 is not None:
                self._cache_set(key, result)
            return result

    async def _generate(self, ctx: AssetContext, *, is_portrait: bool = False) -> AssetResult:
        """Generate an asset via Gemini API. Override in tests via subclass."""
        if self._client is None:
            return AssetResult(image_base64=None, mime_type="image/png", source="fallback")

        prompt = (
            AssetPromptBuilder.portrait(ctx) if is_portrait
            else AssetPromptBuilder.scene(ctx)
        )
        try:
            from google.genai import types
            response = await self._client.aio.models.generate_content(
                model="gemini-3.1-flash-image-preview",
                contents=[prompt],
                config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
            )
            image_b64 = _extract_b64(response)
            if image_b64 is None:
                return AssetResult(image_base64=None, mime_type="image/png", source="fallback")
            return AssetResult(image_base64=image_b64, mime_type="image/png", source="generated")
        except Exception:
            logger.exception("AssetResolver._generate failed for %s", ctx.name)
            return AssetResult(image_base64=None, mime_type="image/png", source="fallback")


# ── Internal helpers ──────────────────────────────────────────────────────────


def _extract_b64(response: Any) -> str | None:
    """Extract base64 image string from a Gemini response, or None."""
    import base64

    parts: list[Any] = []
    response_parts = getattr(response, "parts", None)
    if isinstance(response_parts, list):
        parts.extend(response_parts)
    else:
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            candidate_parts = getattr(content, "parts", None)
            if isinstance(candidate_parts, list):
                parts.extend(candidate_parts)

    for part in parts:
        inline_data = getattr(part, "inline_data", None)
        if inline_data is None:
            continue
        data = getattr(inline_data, "data", None)
        if isinstance(data, bytes) and data:
            return base64.b64encode(data).decode("ascii")
        if isinstance(data, str) and data:
            return data
    return None
