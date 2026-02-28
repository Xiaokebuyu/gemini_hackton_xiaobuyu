from __future__ import annotations

import asyncio

import app.game_core.adapters.session_store as session_store_module
from app.game_core.adapters import NullPersistencePort, SaveStore
from app.game_core.bootstrap import build_default_runtime
from app.game_core.state import StateChange, StateDelta
from app.game_core.state.slices import SceneEntry


def _runtime():
    return build_default_runtime("test_world", world_data={})


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
