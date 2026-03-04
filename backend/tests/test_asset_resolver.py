"""Tests for AssetResolver: LRU cache, prompt builder, and generation logic."""

import asyncio

from app.asset_resolver import (
    CACHE_SIZE,
    AssetContext,
    AssetPromptBuilder,
    AssetResolver,
    AssetResult,
)


# ── FakeAssetResolver ─────────────────────────────────────────────────────────


class FakeAssetResolver(AssetResolver):
    """Subclass that overrides _generate() to avoid real Gemini calls."""

    def __init__(self) -> None:
        # Skip super().__init__() to avoid genai.Client() construction.
        from collections import OrderedDict
        self._client = None
        self._cache: OrderedDict[str, AssetResult] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self.generate_calls: list[str] = []

    async def _generate(self, ctx: AssetContext, *, is_portrait: bool = False) -> AssetResult:
        self.generate_calls.append(ctx.name)
        return AssetResult(image_base64="FAKEB64", mime_type="image/png", source="generated")


# ── Prompt builder tests ───────────────────────────────────────────────────────


def test_scene_prompt_keywords():
    ctx = AssetContext(name="Guild Hall", description="A busy adventurer guild", tags=["indoor"])
    prompt = AssetPromptBuilder.scene(ctx)
    assert "dark fantasy RPG" in prompt
    assert "no characters" in prompt
    assert "background art" in prompt
    assert "Guild Hall" in prompt


def test_portrait_prompt_keywords():
    ctx = AssetContext(
        name="Goblin Slayer",
        description="Silent, battle-scarred warrior",
        tags=["warrior", "armored"],
        role="fighter",
    )
    prompt = AssetPromptBuilder.portrait(ctx)
    assert "dark fantasy RPG" in prompt
    assert "character portrait" in prompt
    assert "upper body" in prompt
    assert "Goblin Slayer" in prompt
    assert "fighter" in prompt


def test_scene_prompt_time_period():
    ctx = AssetContext(
        name="Wilderness Road", description="open road at night", tags=[], time_period="night"
    )
    prompt = AssetPromptBuilder.scene(ctx)
    assert "moonlight" in prompt or "night" in prompt


def test_portrait_prompt_no_role():
    ctx = AssetContext(name="Merchant", description="Cheerful shopkeeper", tags=["npc"])
    prompt = AssetPromptBuilder.portrait(ctx)
    assert "character portrait" in prompt
    # No role injected — prompt should still be valid
    assert "Merchant" in prompt


# ── Cache hit tests ────────────────────────────────────────────────────────────


def test_cache_hit_second_call():
    """Resolve same key twice → _generate called only once, second source='cache'."""
    resolver = FakeAssetResolver()
    ctx = AssetContext(name="Guild Hall", description="desc", tags=[])

    async def run():
        r1 = await resolver.resolve("loc/guild_hall/default.png", ctx)
        r2 = await resolver.resolve("loc/guild_hall/default.png", ctx)
        return r1, r2

    r1, r2 = asyncio.run(run())
    assert r1.source == "generated"
    assert r2.source == "cache"
    assert len(resolver.generate_calls) == 1


def test_different_keys_generate_separately():
    resolver = FakeAssetResolver()

    async def run():
        ctx_a = AssetContext(name="Area A", description="", tags=[])
        ctx_b = AssetContext(name="Area B", description="", tags=[])
        await resolver.resolve("loc/a/default.png", ctx_a)
        await resolver.resolve("loc/b/default.png", ctx_b)

    asyncio.run(run())
    assert len(resolver.generate_calls) == 2


# ── LRU eviction test ─────────────────────────────────────────────────────────


def test_lru_eviction():
    """Writing CACHE_SIZE+1 entries evicts the oldest key."""
    resolver = FakeAssetResolver()

    async def run():
        for i in range(CACHE_SIZE + 1):
            ctx = AssetContext(name=f"Area {i}", description="", tags=[])
            await resolver.resolve(f"loc/area_{i}/default.png", ctx)

    asyncio.run(run())
    # The first key should have been evicted
    assert resolver._cache_get("loc/area_0/default.png") is None
    # The last key should still be cached
    assert resolver._cache_get(f"loc/area_{CACHE_SIZE}/default.png") is not None


# ── Invalidate test ───────────────────────────────────────────────────────────


def test_invalidate_forces_regeneration():
    resolver = FakeAssetResolver()
    ctx = AssetContext(name="Test Area", description="", tags=[])
    key = "loc/test/default.png"

    async def run():
        await resolver.resolve(key, ctx)
        resolver.invalidate(key)
        await resolver.resolve(key, ctx)

    asyncio.run(run())
    assert len(resolver.generate_calls) == 2


# ── Fallback not cached test ──────────────────────────────────────────────────


def test_fallback_not_cached():
    """When _generate returns a fallback (no image_base64), it must NOT be cached."""

    class FallbackResolver(FakeAssetResolver):
        async def _generate(self, ctx, *, is_portrait=False) -> AssetResult:
            self.generate_calls.append(ctx.name)
            return AssetResult(image_base64=None, mime_type="image/png", source="fallback")

    resolver = FallbackResolver()
    ctx = AssetContext(name="Ghost Town", description="", tags=[])
    key = "loc/ghost/default.png"

    async def run():
        r1 = await resolver.resolve(key, ctx)
        r2 = await resolver.resolve(key, ctx)
        return r1, r2

    r1, r2 = asyncio.run(run())
    assert r1.source == "fallback"
    assert r2.source == "fallback"
    # _generate should be called twice since fallback isn't cached
    assert len(resolver.generate_calls) == 2
