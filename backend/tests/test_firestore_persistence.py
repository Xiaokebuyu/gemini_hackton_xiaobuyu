"""Tests for FirestorePersistencePort using mocked Firestore client."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_doc_snapshot(*, exists: bool, data: dict[str, Any] | None = None) -> MagicMock:
    """Create a mock Firestore document snapshot."""
    snap = MagicMock()
    snap.exists = exists
    snap.to_dict.return_value = data if exists else None
    snap.id = "test_session"
    return snap


def _make_doc_ref(snap: MagicMock) -> MagicMock:
    """Create a mock Firestore document reference."""
    ref = MagicMock()
    ref.get = AsyncMock(return_value=snap)
    ref.set = AsyncMock()
    ref.delete = AsyncMock()
    return ref


def _build_port(doc_ref: MagicMock | None = None, stream_docs: list[MagicMock] | None = None):
    """Build FirestorePersistencePort with mocked AsyncClient."""
    with patch("google.cloud.firestore_v1.AsyncClient") as mock_cls:
        mock_db = MagicMock()
        mock_cls.return_value = mock_db

        mock_collection = MagicMock()
        mock_db.collection.return_value = mock_collection

        if doc_ref is not None:
            mock_collection.document.return_value = doc_ref

        if stream_docs is not None:
            async def _stream():
                for doc in stream_docs:
                    yield doc
            mock_collection.select.return_value.stream.return_value = _stream()

        from app.game_core.adapters.firestore_persistence import FirestorePersistencePort
        port = FirestorePersistencePort(collection="test_sessions")
        return port, mock_collection


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


def test_load_returns_empty_when_not_exists() -> None:
    snap = _make_doc_snapshot(exists=False)
    ref = _make_doc_ref(snap)
    port, _ = _build_port(doc_ref=ref)

    result = asyncio.run(port.load("missing_session"))
    assert result == {}


def test_load_returns_payload() -> None:
    payload = {"state": {"player": {"level": 5}}, "meta": {"world_id": "w1"}}
    snap = _make_doc_snapshot(exists=True, data=payload)
    ref = _make_doc_ref(snap)
    port, _ = _build_port(doc_ref=ref)

    result = asyncio.run(port.load("sess_abc"))
    assert result == payload


def test_save_calls_set() -> None:
    snap = _make_doc_snapshot(exists=False)
    ref = _make_doc_ref(snap)
    port, col = _build_port(doc_ref=ref)

    payload = {"state": {"time": {"day": 3}}, "meta": {"session_id": "s1"}}
    asyncio.run(port.save("s1", payload))

    col.document.assert_called_with("s1")
    ref.set.assert_awaited_once_with(payload)


def test_list_keys_returns_sorted_ids() -> None:
    docs = []
    for sid in ["sess_c", "sess_a", "sess_b"]:
        d = MagicMock()
        d.id = sid
        docs.append(d)

    port, _ = _build_port(stream_docs=docs)

    result = asyncio.run(port.list_keys())
    assert result == ["sess_a", "sess_b", "sess_c"]


def test_delete_returns_false_when_not_exists() -> None:
    snap = _make_doc_snapshot(exists=False)
    ref = _make_doc_ref(snap)
    port, _ = _build_port(doc_ref=ref)

    result = asyncio.run(port.delete("missing"))
    assert result is False
    ref.delete.assert_not_awaited()


def test_delete_returns_true_and_deletes() -> None:
    snap = _make_doc_snapshot(exists=True, data={"state": {}})
    ref = _make_doc_ref(snap)
    port, _ = _build_port(doc_ref=ref)

    result = asyncio.run(port.delete("sess_abc"))
    assert result is True
    ref.delete.assert_awaited_once()
