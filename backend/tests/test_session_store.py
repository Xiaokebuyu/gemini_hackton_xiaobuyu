from __future__ import annotations

import asyncio
import copy

import app.game_core.adapters.session_store as session_store_module
from app.game_core.adapters import NullPersistencePort, SaveStore
from app.game_core.bootstrap import build_default_runtime
from app.game_core.state import StateChange, StateDelta
from app.game_core.state.slices import SceneEntry


def _runtime():
    return build_default_runtime("test_world", world_data={})


class SlowStalePersistencePort:
    def __init__(self) -> None:
        self._storage: dict[str, dict[str, object]] = {}
        self._in_flight = 0
        self.max_in_flight = 0

    async def load(self, key: str) -> dict[str, object]:
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        snapshot = copy.deepcopy(self._storage.get(key, {}))
        await asyncio.sleep(0)
        self._in_flight -= 1
        return snapshot

    async def save(self, key: str, payload: dict[str, object]) -> None:
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        await asyncio.sleep(0)
        self._storage[key] = copy.deepcopy(payload)
        self._in_flight -= 1


def test_null_persistence_port_supports_full_session_catalog() -> None:
    port = NullPersistencePort()

    asyncio.run(port.save("sess_b", {"value": 2}))
    asyncio.run(port.save("sess_a", {"value": 1}))

    assert asyncio.run(port.load("sess_a")) == {"value": 1}
    assert asyncio.run(port.list_keys()) == ["sess_a", "sess_b"]
    assert asyncio.run(port.delete("sess_a")) is True
    assert asyncio.run(port.delete("sess_a")) is False
    assert asyncio.run(port.list_keys()) == ["sess_b"]


def test_save_store_writes_full_snapshot_then_only_persistable_dirty_slices() -> None:
    port = NullPersistencePort()
    store = SaveStore(port)
    runtime = _runtime()

    initial = asyncio.run(store.save_runtime("sess_alpha", runtime))

    runtime.state.apply(
        StateDelta(
            changes=[StateChange("player", "set", "current_area", "forest")],
            reason="test",
        )
    )
    runtime.state.scene.add_entry(SceneEntry(source="gm", content="temp"))
    update = asyncio.run(store.save_runtime("sess_alpha", runtime))
    persisted = asyncio.run(port.load("sess_alpha"))

    assert initial.wrote_full_snapshot is True
    assert initial.wrote_to_port is True
    assert "scene" not in initial.persisted_slices
    assert "player" in initial.persisted_slices
    assert update.wrote_full_snapshot is False
    assert update.persisted_slices == ["player"]
    assert update.skipped_slices == ["scene"]
    assert persisted["state"]["player"]["current_area"] == "forest"
    assert "scene" not in persisted["state"]


def test_save_store_lists_session_meta_in_descending_last_played_order(monkeypatch) -> None:
    port = NullPersistencePort()
    store = SaveStore(port)
    runtime = _runtime()
    values = iter([10.0, 20.0])
    monkeypatch.setattr(session_store_module.time_module, "time", lambda: next(values))

    asyncio.run(store.save_runtime("sess_old", runtime))
    asyncio.run(store.save_runtime("sess_new", runtime))

    listed = asyncio.run(store.list_session_meta())

    assert [item["session_id"] for item in listed] == ["sess_new", "sess_old"]
    assert asyncio.run(store.delete_session("sess_new")) is True
    assert asyncio.run(store.delete_session("missing")) is False


def test_save_store_serializes_concurrent_writes_per_session() -> None:
    port = SlowStalePersistencePort()
    store = SaveStore(port)
    session_id = "sess_alpha"

    base_runtime = _runtime()
    asyncio.run(store.save_runtime(session_id, base_runtime))

    left_runtime = _runtime()
    left_runtime.state.apply(
        StateDelta(
            changes=[StateChange("player", "set", "current_area", "forest")],
            reason="left",
        )
    )

    right_runtime = _runtime()
    right_runtime.state.apply(
        StateDelta(
            changes=[StateChange("flags", "set", "flags.quest_started", True)],
            reason="right",
        )
    )

    async def _run() -> None:
        await asyncio.gather(
            store.save_runtime(session_id, left_runtime),
            store.save_runtime(session_id, right_runtime),
        )

    asyncio.run(_run())
    persisted = asyncio.run(port.load(session_id))

    assert port.max_in_flight == 1
    assert persisted["state"]["player"]["current_area"] == "forest"
    assert persisted["state"]["flags"]["flags"]["quest_started"] is True


def test_save_store_strictly_filters_sessions_without_matching_world_id() -> None:
    port = NullPersistencePort()
    store = SaveStore(port)

    asyncio.run(store.save_runtime("sess_current", _runtime()))
    asyncio.run(
        port.save(
            "sess_legacy",
            {
                "state": {},
                "meta": {"session_id": "sess_legacy", "saved_at": 5.0},
            },
        )
    )
    asyncio.run(
        store.save_runtime(
            "sess_other",
            build_default_runtime("other_world", world_data={}),
        )
    )

    listed = asyncio.run(store.list_session_meta(world_id="test_world"))

    assert [item["session_id"] for item in listed] == ["sess_current"]


def test_save_store_round_trip_preserves_story_facts() -> None:
    port = NullPersistencePort()
    store = SaveStore(port)
    runtime = _runtime()
    facts = [
        {"subject": "guild", "relation": "warns_about", "object": "raiders"},
        {"subject": "raiders", "relation": "target", "object": "western_farm"},
    ]

    runtime.state.narrative_plan.add_story_facts(facts)
    asyncio.run(store.save_runtime("sess_story_facts", runtime))
    restored = asyncio.run(store.load_runtime("test_world", "sess_story_facts"))

    assert restored is not None
    assert restored.state.narrative_plan.story_facts == facts
