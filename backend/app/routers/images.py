"""Image generation routes — disk-cached, URL-first (P4 Part 1+2)."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.deps import _load_session_or_404

logger = logging.getLogger(__name__)

router = APIRouter()

_IMAGE_MODEL = "gemini-3.1-flash-image-preview"
_VALID_EMOTIONS = frozenset({"neutral", "happy", "angry", "sad", "surprised"})

# Disk cache root: backend/data/cache/images/
CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "cache" / "images"

# Limit concurrent Gemini image generation calls
_GENERATION_SEMAPHORE = asyncio.Semaphore(3)

_PERIOD_HINTS: dict[str, str] = {
    "dawn":  "Time of day: early dawn, soft orange-pink light on the horizon, misty atmosphere.",
    "day":   "Time of day: bright daytime, clear sunlight, vivid colors.",
    "dusk":  "Time of day: dusk, golden hour, warm amber light, long shadows.",
    "night": "Time of day: deep night, moonlight and torchlight, deep blues and dark shadows.",
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def disposition_to_emotion(approval: int, trust: int) -> str:
    """Map NPC disposition values to a portrait emotion key."""
    if approval > 50:
        return "happy"
    if approval < -30:
        return "angry"
    if trust < -20:
        return "sad"
    return "neutral"


# ── Models ────────────────────────────────────────────────────────────────────


class SceneImageRequest(BaseModel):
    area_id: str
    location_id: str | None = None
    room_id: str | None = None


class PortraitImageRequest(BaseModel):
    character_id: str
    emotion: str = "neutral"


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/api/game/{world_id}/sessions/{session_id}/images/scene")
async def generate_scene_image(
    world_id: str,
    session_id: str,
    request: SceneImageRequest,
) -> dict[str, Any]:
    """Return a scene background URL, generating and caching on first request."""
    session = await _load_session_or_404(world_id, session_id)
    area_id = request.area_id.strip()
    location_id = (request.location_id or "").strip() or None
    room_id = (request.room_id or "").strip() or None

    period = "day"
    if session.runtime.state.has_slice("time"):
        period = str(session.runtime.state.time.period)

    path = scene_cache_path(world_id, area_id, location_id, period, room_id=room_id)
    if path.exists():
        return _success_response(path, "cached")

    prompt = _build_scene_prompt(session, area_id=area_id, location_id=location_id, room_id=room_id, period=period)
    if prompt is None:
        return _fallback_response("unknown scene")

    ok = await _generate_and_save(prompt, path)
    return _success_response(path, "generated") if ok else _fallback_response("generation failed")


@router.post("/api/game/{world_id}/sessions/{session_id}/images/portrait")
async def generate_portrait(
    world_id: str,
    session_id: str,
    request: PortraitImageRequest,
) -> dict[str, Any]:
    """Return a portrait URL with emotion variant, generating and caching on first request."""
    session = await _load_session_or_404(world_id, session_id)
    character_id = request.character_id.strip()
    emotion = request.emotion if request.emotion in _VALID_EMOTIONS else "neutral"

    # Auto-derive emotion from RelationSlice when caller uses default "neutral"
    if emotion == "neutral" and session.runtime.state.has_slice("relations"):
        disp = session.runtime.state.relations.get_disposition(character_id)
        if isinstance(disp, dict):
            emotion = disposition_to_emotion(
                int(disp.get("approval", 0)), int(disp.get("trust", 0))
            )

    emotion_path = portrait_cache_path(world_id, character_id, emotion)
    if emotion_path.exists():
        return _success_response(emotion_path, "cached")

    base_prompt = _build_portrait_base_prompt(session, character_id=character_id)
    if base_prompt is None:
        return _fallback_response("unknown character")

    base_path = portrait_cache_path(world_id, character_id, "base")
    if not base_path.exists():
        ok = await _generate_and_save(base_prompt, base_path)
        if not ok:
            return _fallback_response("base portrait generation failed")

    emotion_prompt = _build_portrait_emotion_prompt(emotion)
    ok = await _generate_and_save_with_ref(emotion_prompt, base_path, emotion_path)
    return _success_response(emotion_path, "generated") if ok else _fallback_response("emotion generation failed")


# ── Cache path helpers ────────────────────────────────────────────────────────


def scene_cache_path(
    world_id: str,
    area_id: str,
    location_id: str | None,
    period: str,
    *,
    room_id: str | None = None,
) -> Path:
    loc = location_id or "_main"
    if room_id:
        return CACHE_DIR / "scenes" / world_id / area_id / loc / room_id / f"{period}.png"
    return CACHE_DIR / "scenes" / world_id / area_id / loc / f"{period}.png"


def portrait_cache_path(world_id: str, character_id: str, emotion: str) -> Path:
    return CACHE_DIR / "portraits" / world_id / character_id / f"{emotion}.png"


def _url_from_path(path: Path) -> str:
    return "/static/images/" + path.relative_to(CACHE_DIR).as_posix()


def _success_response(path: Path, source: str) -> dict[str, Any]:
    return {"image_url": _url_from_path(path), "source": source, "error": None}


def _fallback_response(error: str) -> dict[str, Any]:
    return {"image_url": None, "source": "fallback", "error": error}


# ── Prompt builders ───────────────────────────────────────────────────────────


def _build_scene_prompt(
    session: Any,
    *,
    area_id: str,
    location_id: str | None,
    room_id: str | None = None,
    period: str,
) -> str | None:
    if not area_id:
        return None

    area_name = area_id
    area_description = ""
    location_name = ""
    location_description = ""
    room_name = ""
    room_description = ""

    if session.runtime.world.has_registry("maps"):
        area_template = session.runtime.world.maps.get(area_id)
        if area_template is None:
            return None
        area_name = area_template.name or area_id
        area_description = area_template.description or ""
        if location_id is not None:
            sub_location = area_template.sub_locations.get(location_id)
            if sub_location is None:
                return None
            location_name = sub_location.name or location_id
            location_description = sub_location.description or ""
            if room_id is not None:
                room_template = sub_location.rooms.get(room_id)
                if room_template is not None:
                    room_name = room_template.name or room_id
                    room_description = room_template.description or ""

    location_line = ""
    if location_id is not None:
        location_line = f"Focus on the sub-location {location_name}. {location_description} "

    room_line = ""
    if room_id is not None and room_name:
        room_line = f"Specific room: {room_name}. {room_description} "

    period_hint = _PERIOD_HINTS.get(period, "")
    return (
        "Create a clean anime fantasy visual novel background. "
        "No text, no UI, no watermark overlay, no borders. "
        "Wide cinematic composition, highly readable foreground and midground, "
        "soft dramatic lighting, rich environment detail. "
        f"Area: {area_name}. "
        f"{area_description} "
        f"{location_line}"
        f"{room_line}"
        f"{period_hint} "
        "Style: polished anime illustration, detailed fantasy background, "
        "color-rich, immersive, suitable for a JRPG dialogue scene."
    )


def _build_portrait_base_prompt(session: Any, *, character_id: str) -> str | None:
    if not character_id:
        return None

    name = character_id
    appearance = ""
    character_class = ""
    tags: list[str] = []

    if session.runtime.world.has_registry("characters"):
        character = session.runtime.world.characters.get(character_id)
        if character is None:
            return None
        name = character.name or character_id
        appearance = character.appearance or ""
        character_class = character.character_class or character.class_id or ""
        tags = [str(tag).strip() for tag in character.tags if str(tag).strip()]

    tag_line = f"Tags: {', '.join(tags[:6])}. " if tags else ""
    class_line = f"Role: {character_class}. " if character_class else ""
    appearance_line = f"{appearance} " if appearance else ""
    return (
        "Create a polished anime-style vertical character portrait for a fantasy visual novel. "
        "Bust or half-body framing, expressive face, strong silhouette, clean background, "
        "no text, no UI, no speech bubble. "
        f"Character: {name}. "
        f"{class_line}"
        f"{tag_line}"
        f"{appearance_line}"
        "Style: refined anime illustration, detailed costume rendering, warm fantasy mood."
    )


def _build_portrait_emotion_prompt(emotion: str) -> str:
    return (
        f"Generate a portrait of this exact same character with a {emotion} expression. "
        "Keep the character's appearance, outfit, hairstyle, and art style exactly the same. "
        f"Only change the facial expression to convey: {emotion}. "
        "Maintain the same framing, background style, and illustration quality."
    )


# ── Generation helpers ────────────────────────────────────────────────────────


async def _generate_and_save(prompt: str, path: Path) -> bool:
    """Generate an image from a text prompt and save to disk. Returns True on success."""
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("image generation skipped: missing API key")
        return False

    try:
        from google import genai
        from google.genai import types
    except Exception:
        logger.exception("image sdk import failed")
        return False

    client = genai.Client(api_key=api_key)
    try:
        async with _GENERATION_SEMAPHORE:
            response = await client.aio.models.generate_content(
                model=_IMAGE_MODEL,
                contents=[prompt],
                config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
            )
        data, _ = _extract_inline_image(response)
        if data is None:
            logger.warning("model returned no image for %s", path.name)
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_bytes(data)
        tmp_path.rename(path)
        return True
    except Exception:
        logger.exception("image generation failed: %s", path.name)
        return False
    finally:
        await _close_client(client)


async def _generate_and_save_with_ref(prompt: str, ref_path: Path, out_path: Path) -> bool:
    """Generate an emotion variant using a reference image. Returns True on success."""
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return False

    try:
        from google import genai
        from google.genai import types
        from PIL import Image
    except Exception:
        logger.exception("image sdk or PIL import failed")
        return False

    # Validate reference image before use
    if not ref_path.exists() or ref_path.stat().st_size == 0:
        logger.warning("reference image missing or empty: %s", ref_path)
        return False
    try:
        base_img = Image.open(ref_path)
        base_img.load()
    except Exception:
        logger.exception("reference image corrupted: %s", ref_path)
        return False

    client = genai.Client(api_key=api_key)
    try:
        async with _GENERATION_SEMAPHORE:
            response = await client.aio.models.generate_content(
                model=_IMAGE_MODEL,
                contents=[prompt, base_img],
                config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
            )
        data, _ = _extract_inline_image(response)
        if data is None:
            logger.warning("model returned no image for %s", out_path.name)
            return False
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_suffix(".tmp")
        tmp_path.write_bytes(data)
        tmp_path.rename(out_path)
        return True
    except Exception:
        logger.exception("emotion variant generation failed: %s", out_path.name)
        return False
    finally:
        await _close_client(client)


async def _close_client(client: Any) -> None:
    aio_client = getattr(client, "aio", None)
    close_async = getattr(aio_client, "aclose", None)
    if callable(close_async):
        try:
            await close_async()
        except Exception:
            logger.debug("image aio client aclose failed", exc_info=True)
    close_sync = getattr(client, "close", None)
    if callable(close_sync):
        try:
            close_sync()
        except Exception:
            logger.debug("image client close failed", exc_info=True)


def _extract_inline_image(response: Any) -> tuple[bytes | None, str]:
    """Extract raw image bytes from a Gemini generate_content response."""
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
        mime_type = str(getattr(inline_data, "mime_type", "image/png") or "image/png")
        if isinstance(data, bytes) and data:
            return data, mime_type
        if isinstance(data, str) and data:
            return base64.b64decode(data), mime_type
    return None, "image/png"
