from app.game_core.planning import DynamicSubAreaManager
from app.game_core.state.slices import AreaSlice


def test_dynamic_sub_area_manager_uses_area_slice_as_runtime_source_of_truth() -> None:
    areas = AreaSlice()
    areas.restore({"areas": {"forest": {}}})
    manager = DynamicSubAreaManager(areas)

    created_temp = manager.create(
        "forest",
        {"id": "trail_event", "label": "trail event"},
    )
    created_timed = manager.create(
        "forest",
        {"id": "camp", "label": "camp", "expiry": 24},
    )
    created_perm = manager.create(
        "forest",
        {"id": "ruins", "label": "ruins", "expiry": -1, "tier": "permanent"},
    )

    created_temp["label"] = "changed"
    created_timed["label"] = "changed"
    created_perm["label"] = "changed"

    active = manager.list_active("forest")
    active[0]["label"] = "mutated"

    assert areas.list_temporary_sub_areas("forest")[0]["label"] == "trail event"
    assert areas.list_temporary_sub_areas("forest")[1]["label"] == "camp"
    assert areas.list_temporary_sub_areas("forest")[2]["label"] == "ruins"
    assert manager.get_cluster_status("forest") == {
        "permanent": 1,
        "timed": 1,
        "temporary": 1,
        "total": 3,
    }

    assert manager.expire("forest", "camp") is True
    assert manager.expire("forest", "missing") is False
    assert [item["id"] for item in manager.list_active("forest")] == [
        "trail_event",
        "ruins",
    ]
    assert manager.get_cluster_status("forest") == {
        "permanent": 1,
        "timed": 0,
        "temporary": 1,
        "total": 2,
    }


def test_dynamic_sub_area_manager_respects_cluster_capacity() -> None:
    areas = AreaSlice()
    areas.restore({"areas": {"forest": {}}})
    manager = DynamicSubAreaManager(areas)
    for idx in range(6):
        assert manager.create("forest", {"id": f"temp_{idx}"}) is not None

    assert manager.create("forest", {"id": "overflow"}) is None


def test_dynamic_sub_area_manager_respects_permanent_limit() -> None:
    areas = AreaSlice()
    areas.restore({"areas": {"forest": {}}})
    manager = DynamicSubAreaManager(areas)
    for idx in range(3):
        assert manager.create(
            "forest",
            {"id": f"perm_{idx}", "tier": "permanent"},
        ) is not None

    assert manager.create("forest", {"id": "perm_overflow", "tier": "permanent"}) is None
