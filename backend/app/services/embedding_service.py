"""
Embedding service abstraction.
"""
from typing import List, Optional
from google import genai

from app.config import settings
from app.utils.embedding import get_cloudflare_embedding


class EmbeddingService:
    """Embedding service wrapper for multiple providers."""

    def __init__(self):
        self.provider = settings.embedding_provider
        self.model = settings.gemini_embedding_model
        self.client = None
        if self.provider == "gemini":
            self.client = genai.Client(api_key=settings.gemini_api_key)

    async def embed_text(self, text: str) -> List[float]:
        """Embed a single text."""
        if not text:
            return []
        if self.provider == "cloudflare":
            return await get_cloudflare_embedding(text)
        return self._embed_gemini(text)

    def _embed_gemini(self, text: str) -> List[float]:
        """Embed text via Gemini models."""
        try:
            response = self.client.models.embed_content(
                model=self.model,
                contents=text,
            )
        except Exception:
            return []

        embedding = None
        if hasattr(response, "embedding"):
            embedding = response.embedding
        elif hasattr(response, "embeddings"):
            embeddings = response.embeddings or []
            embedding = embeddings[0] if embeddings else None
        elif isinstance(response, dict):
            embedding = response.get("embedding") or response.get("embeddings")

        return _extract_embedding_values(embedding)


def _extract_embedding_values(embedding: Optional[object]) -> List[float]:
    if embedding is None:
        return []
    if hasattr(embedding, "values"):
        return list(embedding.values)
    if isinstance(embedding, dict):
        values = embedding.get("values")
        if isinstance(values, list):
            return values
        if "embedding" in embedding:
            return _extract_embedding_values(embedding["embedding"])
    if isinstance(embedding, list):
        return embedding
    return []
