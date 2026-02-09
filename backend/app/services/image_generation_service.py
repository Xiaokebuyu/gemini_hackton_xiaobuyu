"""
Image generation service backed by Gemini image model.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Dict, Optional

from google import genai
from google.genai import types

from app.config import settings

logger = logging.getLogger(__name__)


class ImageGenerationService:
    """Generate scene images for GM responses."""

    def __init__(self) -> None:
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model = settings.image_generation_model

    async def generate(
        self,
        scene_description: str,
        style: str = "dark_fantasy",
    ) -> Optional[Dict[str, Any]]:
        """Generate an image and return base64 payload."""
        if not settings.image_generation_enabled:
            return None
        prompt = f"[{style}] {scene_description}".strip()
        if not prompt:
            return None

        logger.info("image generation starting: model=%s prompt=%.80s...", self.model, prompt)
        try:
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=[prompt],
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(
                        aspect_ratio="16:9",
                    ),
                ),
            )
        except Exception as exc:
            logger.error("image generation API failed: %s", exc, exc_info=True)
            return None

        # Official recommended parsing: iterate response.parts
        try:
            for part in response.parts:
                if part.inline_data is not None:
                    b64 = base64.b64encode(part.inline_data.data).decode("utf-8")
                    mime_type = getattr(part.inline_data, "mime_type", None) or "image/png"
                    logger.info("image generated: mime=%s size=%d bytes", mime_type, len(part.inline_data.data))
                    return {
                        "base64": b64,
                        "mime_type": mime_type,
                        "model": self.model,
                        "style": style,
                        "prompt": scene_description,
                    }
        except Exception as exc:
            logger.error("image parsing failed: %s", exc, exc_info=True)
            return None

        logger.warning(
            "image generation returned no image parts, candidates=%s",
            getattr(response, "candidates", "N/A"),
        )
        return None
