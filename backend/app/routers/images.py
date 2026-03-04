"""Image generation routes."""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.deps import _load_session_or_404


logger = logging.getLogger(__name__)

router = APIRouter()

_IMAGE_MODEL = "gemini-3.1-flash-image-preview"


class SceneImageRequest(BaseModel):
    area_id: str
    location_id: str | None = None


class PortraitImageRequest(BaseModel):
    character_id: str


@router.post("/api/game/{world_id}/sessions/{session_id}/images/scene")
async def generate_scene_image(
    world_id: str,
    session_id: str,
    request: SceneImageRequest,
) -> dict[str, Any]:
    """Generate one scene background image."""
    session = await _load_session_or_404(world_id, session_id)
    prompt = _build_scene_prompt(
        session,
        area_id=request.area_id.strip(),
        location_id=(request.location_id or "").strip() or None,
    )
    if prompt is None:
        return _fallback_response("unknown scene")
    return await _generate_image_response(prompt)


@router.post("/api/game/{world_id}/sessions/{session_id}/images/portrait")
async def generate_portrait(
    world_id: str,
    session_id: str,
    request: PortraitImageRequest,
) -> dict[str, Any]:
    """Generate one character portrait."""
    session = await _load_session_or_404(world_id, session_id)
    prompt = _build_portrait_prompt(session, character_id=request.character_id.strip())
    if prompt is None:
        return _fallback_response("unknown character")
    return await _generate_image_response(prompt)


def _build_scene_prompt(
    session: Any,
    *,
    area_id: str,
    location_id: str | None,
) -> str | None:
    if not area_id:
        return None

    area_name = area_id
    area_description = ""
    location_name = ""
    location_description = ""

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

    location_line = ""
    if location_id is not None:
        location_line = (
            f"Focus on the sub-location {location_name}. "
            f"{location_description} "
        )

    return (
        "Create a clean anime fantasy visual novel background. "
        "No text, no UI, no watermark overlay, no borders. "
        "Wide cinematic composition, highly readable foreground and midground, "
        "soft dramatic lighting, rich environment detail. "
        f"Area: {area_name}. "
        f"{area_description} "
        f"{location_line}"
        "Style: polished anime illustration, detailed fantasy background, "
        "color-rich, immersive, suitable for a JRPG dialogue scene."
    )


def _build_portrait_prompt(
    session: Any,
    *,
    character_id: str,
) -> str | None:
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


async def _generate_image_response(prompt: str) -> dict[str, Any]:
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return _fallback_response("missing image api key")

    try:
        from google import genai
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.exception("image sdk import failed")
        return _fallback_response(str(exc))

    client = genai.Client(api_key=api_key)
    try:
        response = await client.aio.models.generate_content(
            model=_IMAGE_MODEL,
            contents=[prompt],
        )
        image_data, mime_type = _extract_inline_image(response)
        if image_data is None:
            return _fallback_response("model returned no image")
        return {
            "image_base64": image_data,
            "mime_type": mime_type,
            "source": "generated",
            "error": None,
        }
    except Exception as exc:  # pragma: no cover - network / sdk dependent
        logger.exception("image generation failed")
        return _fallback_response(str(exc))
    finally:
        aio_client = getattr(client, "aio", None)
        close_async = getattr(aio_client, "aclose", None)
        if callable(close_async):
            try:
                await close_async()
            except Exception:
                logger.debug("image aio client close failed", exc_info=True)
        close_sync = getattr(client, "close", None)
        if callable(close_sync):
            try:
                close_sync()
            except Exception:
                logger.debug("image client close failed", exc_info=True)


def _extract_inline_image(response: Any) -> tuple[str | None, str]:
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
        if isinstance(data, bytes):
            return base64.b64encode(data).decode("ascii"), mime_type
        if isinstance(data, str) and data:
            return data, mime_type
    return None, "image/png"


def _fallback_response(error: str) -> dict[str, Any]:
    return {
        "image_base64": None,
        "mime_type": "image/png",
        "source": "fallback",
        "error": error,
    }
