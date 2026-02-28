"""Firestore implementation of PersistencePort + SessionCatalogPort."""

from __future__ import annotations

from typing import Any


class FirestorePersistencePort:
    """Persist session payloads as Firestore documents.

    Each session is a single document: collection/{session_id}.
    Document ID = session_id, payload = {"state": {...}, "meta": {...}}.
    """

    def __init__(self, collection: str = "sessions") -> None:
        from google.cloud.firestore_v1 import AsyncClient

        self._db = AsyncClient()
        self._collection = collection

    async def load(self, key: str) -> dict[str, Any]:
        doc = await self._db.collection(self._collection).document(key).get()
        if not doc.exists:
            return {}
        return doc.to_dict() or {}

    async def save(self, key: str, payload: dict[str, Any]) -> None:
        await self._db.collection(self._collection).document(key).set(payload)

    async def list_keys(self) -> list[str]:
        docs = self._db.collection(self._collection).select([]).stream()
        keys = [doc.id async for doc in docs]
        return sorted(keys)

    async def delete(self, key: str) -> bool:
        ref = self._db.collection(self._collection).document(key)
        doc = await ref.get()
        if not doc.exists:
            return False
        await ref.delete()
        return True
