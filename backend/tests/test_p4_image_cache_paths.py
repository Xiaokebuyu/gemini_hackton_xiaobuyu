"""Tests for P4 Part 1: disk cache path helpers and prompt builders (pure functions, no Gemini calls)."""

from app.routers.images import (
    CACHE_DIR,
    _PERIOD_HINTS,
    _VALID_EMOTIONS,
    _build_portrait_emotion_prompt,
    _url_from_path,
    portrait_cache_path,
    scene_cache_path,
)


# ── Cache path helpers ────────────────────────────────────────────────────────


def test_scene_cache_path_no_location():
    path = scene_cache_path("world1", "tavern", None, "day")
    assert path == CACHE_DIR / "scenes" / "world1" / "tavern" / "_main" / "day.png"


def test_scene_cache_path_with_location():
    path = scene_cache_path("world1", "tavern", "bar", "night")
    assert path == CACHE_DIR / "scenes" / "world1" / "tavern" / "bar" / "night.png"


def test_scene_cache_path_all_periods():
    for period in ("dawn", "day", "dusk", "night"):
        path = scene_cache_path("w", "area", None, period)
        assert path.name == f"{period}.png"


def test_portrait_cache_path_base():
    path = portrait_cache_path("world1", "npc_alice", "base")
    assert path == CACHE_DIR / "portraits" / "world1" / "npc_alice" / "base.png"


def test_portrait_cache_path_emotion():
    path = portrait_cache_path("world1", "npc_alice", "happy")
    assert path == CACHE_DIR / "portraits" / "world1" / "npc_alice" / "happy.png"


# ── URL construction ──────────────────────────────────────────────────────────


def test_url_from_path_scene():
    path = scene_cache_path("world1", "tavern", None, "dawn")
    url = _url_from_path(path)
    assert url == "/static/images/scenes/world1/tavern/_main/dawn.png"


def test_url_from_path_portrait():
    path = portrait_cache_path("world1", "alice", "sad")
    url = _url_from_path(path)
    assert url == "/static/images/portraits/world1/alice/sad.png"


def test_url_always_uses_forward_slashes():
    """URL must use forward slashes regardless of OS path separator."""
    path = portrait_cache_path("w", "c", "happy")
    url = _url_from_path(path)
    assert "\\" not in url
    assert url.startswith("/static/images/")


# ── Period hints ──────────────────────────────────────────────────────────────


def test_period_hints_all_four_present():
    for period in ("dawn", "day", "dusk", "night"):
        assert period in _PERIOD_HINTS
        assert _PERIOD_HINTS[period]


def test_period_hints_all_unique():
    assert len(set(_PERIOD_HINTS.values())) == 4


# ── Emotion prompt ────────────────────────────────────────────────────────────


def test_portrait_emotion_prompt_contains_emotion():
    prompt = _build_portrait_emotion_prompt("angry")
    assert "angry" in prompt
    assert "expression" in prompt


def test_portrait_emotion_prompt_mentions_consistency():
    prompt = _build_portrait_emotion_prompt("sad")
    assert "same" in prompt.lower()


# ── Valid emotions set ────────────────────────────────────────────────────────


def test_valid_emotions_contains_expected():
    assert _VALID_EMOTIONS == {"neutral", "happy", "angry", "sad", "surprised"}


def test_unknown_emotion_not_in_valid_set():
    assert "unknown" not in _VALID_EMOTIONS
    assert "base" not in _VALID_EMOTIONS  # base is internal, not a valid request emotion
