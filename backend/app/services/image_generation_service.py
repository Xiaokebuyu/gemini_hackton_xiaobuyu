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
        prompt = self._build_image_prompt(scene_description=scene_description, style=style)
        if not prompt:
            return None

        logger.info("image generation starting: model=%s prompt=%.80s...", self.model, prompt)

        attempt_inputs = [
            {
                "name": "image_only",
                "prompt": prompt,
                "modalities": ["IMAGE"],
            },
            {
                "name": "text_image_fallback",
                "prompt": f"{prompt}\n\nReturn an image output.",
                "modalities": ["TEXT", "IMAGE"],
            },
        ]

        last_response: Optional[Any] = None
        for attempt in attempt_inputs:
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=[attempt["prompt"]],
                    config=types.GenerateContentConfig(
                        response_modalities=attempt["modalities"],
                        image_config=types.ImageConfig(
                            aspect_ratio="16:9",
                        ),
                    ),
                )
            except Exception as exc:
                logger.error(
                    "image generation API failed (attempt=%s): %s",
                    attempt["name"],
                    exc,
                    exc_info=True,
                )
                continue

            last_response = response
            payload = self._extract_image_payload(response, style=style, scene_description=scene_description)
            if payload is not None:
                return payload

            logger.warning(
                "image generation attempt returned no image parts: attempt=%s model=%s",
                attempt["name"],
                self.model,
            )

        logger.warning(
            "image generation returned no image parts after retries, candidates=%s",
            getattr(last_response, "candidates", "N/A"),
        )
        return None

    def _extract_image_payload(
        self,
        response: Any,
        *,
        style: str,
        scene_description: str,
    ) -> Optional[Dict[str, Any]]:
        """Parse image bytes from SDK response parts."""
        try:
            parts = getattr(response, "parts", None) or []
            if not parts:
                candidates = getattr(response, "candidates", None) or []
                if candidates:
                    content = getattr(candidates[0], "content", None)
                    parts = getattr(content, "parts", None) or []

            for part in parts:
                inline_data = getattr(part, "inline_data", None)
                if inline_data is None:
                    continue
                raw_bytes = getattr(inline_data, "data", None)
                if not raw_bytes:
                    continue
                b64 = base64.b64encode(raw_bytes).decode("utf-8")
                mime_type = getattr(inline_data, "mime_type", None) or "image/png"
                logger.info("image generated: mime=%s size=%d bytes", mime_type, len(raw_bytes))
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
        return None

    def _build_image_prompt(self, *, scene_description: str, style: str) -> str:
        """Build a robust visual prompt from tool input."""
        raw = str(scene_description or "").strip()
        if not raw:
            return ""
        normalized = " ".join(raw.split())
        if len(normalized) > 700:
            normalized = f"{normalized[:700]}..."

        style_hints = {
            "dark_fantasy": "dark fantasy concept art, cinematic composition, moody volumetric lighting",
            "anime": "anime illustration, clean linework, vibrant colors, dynamic framing",
            "watercolor": "watercolor painting, soft edges, textured paper feel, atmospheric color wash",
            "realistic": "photorealistic cinematic still, natural materials, physically plausible lighting",
        }
        style_hint = style_hints.get(style, style_hints["dark_fantasy"])

        return (
            "Generate one high-quality RPG scene image.\n"
            "Output image only. Do not render any words, captions, logos, UI overlays, or watermarks inside the image.\n"
            f"Visual style: {style_hint}.\n"
            f"Scene to depict: {normalized}\n"
            "Prioritize a clear subject, coherent depth, and readable environment details."
        )
