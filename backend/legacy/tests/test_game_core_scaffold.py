from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content import ContentRegistry, WorldInstance
from app.game_core.state import StateChange, StateContainer, StateDelta, StateSlice


class DemoRegistry(ContentRegistry):
    def __init__(self) -> None:
        super().__init__("demo")
        self._items: dict[str, dict[str, Any]] = {}

    def load(self, data: dict[str, Any]) -> None:
        self._items = {
            key: dict(value)
            for key, value in data.items()
            if isinstance(value, dict)
        }

    def get(self, content_id: str) -> Any | None:
        return self._items.get(content_id)

    def list_all(self) -> list[Any]:
        return list(self._items.values())


class DemoSlice(StateSlice):
    def __init__(self) -> None:
        super().__init__("demo")
        self._payload = {"count": 0, "flag": False}

    def restore(self, payload: Mapping[str, Any]) -> None:
        self._payload = dict(payload)
        self.clear_dirty()

    def serialize(self) -> dict[str, Any]:
        return dict(self._payload)

    def snapshot(self) -> dict[str, Any]:
        return dict(self._payload)

    def apply_state_change(self, change: StateChange) -> None:
        self._payload[change.path] = change.value
        self._dirty = True


def test_world_instance_registers_and_queries_registry() -> None:
    world = WorldInstance("bg3")
    registry = DemoRegistry()
    registry.load(
        {
            "a": {"id": "a", "tags": ["quest", "act1"]},
            "b": {"id": "b", "tags": ["npc"]},
        }
    )

    world.register(registry)

    assert world.get_registry("demo") is registry
    assert registry.query_by_tags(["quest"]) == [{"id": "a", "tags": ["quest", "act1"]}]
    assert registry.query_by_tags(["act1", "npc"], match_all=False) == [
        {"id": "a", "tags": ["quest", "act1"]},
        {"id": "b", "tags": ["npc"]},
    ]
    assert world.snapshot()["registries"]["demo"]["size"] == 2


def test_state_container_routes_slice_deltas_and_persists_dirty_only() -> None:
    container = StateContainer()
    slice_obj = DemoSlice()
    container.register(slice_obj)

    assert container.persist() == {}

    container.apply(
        StateDelta(
            changes=[
                StateChange(
                    slice="demo",
                    operation="set",
                    path="count",
                    value=3,
                ),
                StateChange(
                    slice="demo",
                    operation="set",
                    path="flag",
                    value=True,
                ),
            ],
            reason="unit-test",
        )
    )

    assert container.get_slice("demo").snapshot() == {"count": 3, "flag": True}
    assert container.persist() == {"demo": {"count": 3, "flag": True}}
