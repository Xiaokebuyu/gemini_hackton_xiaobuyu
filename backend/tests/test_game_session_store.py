import pytest

from app.services.game_session_store import GameSessionStore


class _FakeSnapshot:
    def __init__(self, payload):
        self._payload = payload
        self.exists = payload is not None

    def to_dict(self):
        return self._payload or {}


class _FakeDocRef:
    def __init__(self):
        self.payload = None

    def set(self, payload, merge=False):
        if merge and isinstance(self.payload, dict):
            merged = dict(self.payload)
            merged.update(payload or {})
            self.payload = merged
        else:
            self.payload = dict(payload or {})

    def get(self):
        return _FakeSnapshot(self.payload)

    def update(self, updates):
        merged = dict(self.payload or {})
        merged.update(updates or {})
        self.payload = merged


class _DummySessionStore(GameSessionStore):
    def __init__(self):
        self._refs = {}

    def _session_ref(self, world_id: str, session_id: str):  # type: ignore[override]
        key = (world_id, session_id)
        if key not in self._refs:
            self._refs[key] = _FakeDocRef()
        return self._refs[key]


@pytest.mark.asyncio
async def test_create_session_rejects_duplicate_session_id():
    store = _DummySessionStore()

    first = await store.create_session(
        world_id="world_1",
        session_id="sess_fixed",
        participants=["player_1"],
    )
    assert first.session_id == "sess_fixed"

    with pytest.raises(ValueError, match="already exists"):
        await store.create_session(
            world_id="world_1",
            session_id="sess_fixed",
            participants=["player_1"],
        )

    # Different world can reuse same session_id.
    second = await store.create_session(
        world_id="world_2",
        session_id="sess_fixed",
        participants=["player_2"],
    )
    assert second.session_id == "sess_fixed"
