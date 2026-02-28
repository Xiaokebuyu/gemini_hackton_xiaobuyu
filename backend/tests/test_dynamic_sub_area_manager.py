from app.game_core.planning import DynamicSubAreaManager
from app.game_core.state.slices import AreaSlice


def test_dynamic_sub_area_manager_uses_area_slice_as_runtime_source_of_truth() -> None:
    areas = AreaSlice()
    manager = DynamicSubAreaManager(areas)

    created_temp = manager.create(
        "forest",
        {"id": "trail_event", "expiry": 3, "kind": "ambush"},
    )
    created_timed = manager.create(
        "forest",
        {"id": "camp", "expiry": 24, "kind": "camp"},
    )
    created_perm = manager.create(
        "forest",
        {"id": "ruins", "expiry": -1, "kind": "ruins"},
    )

    created_temp["kind"] = "changed"
    created_timed["kind"] = "changed"
    created_perm["kind"] = "changed"

    active = manager.list_active("forest")
    active[0]["kind"] = "mutated"

    assert areas.list_temporary_sub_areas("forest")[0]["kind"] == "ambush"
    assert areas.list_temporary_sub_areas("forest")[1]["kind"] == "camp"
    assert areas.list_temporary_sub_areas("forest")[2]["kind"] == "ruins"
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
