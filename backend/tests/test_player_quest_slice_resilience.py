"""Phase 5: PlayerSlice + QuestSlice resilience tests.

Verifies that previously-raising paths now emit warnings and skip
instead of crashing (supporting planner-generated dirty data).
"""

import logging

from app.game_core.state.slices.player import PlayerSlice, ItemStack
from app.game_core.state.slices.quests import QuestSlice
from app.game_core.state.delta import StateChange


# ---------------------------------------------------------------------------
# PlayerSlice – inventory path
# ---------------------------------------------------------------------------


def test_inventory_non_list_skips_without_raise(caplog):
    """apply_state_change: inventory with non-list value should warn and skip."""
    player = PlayerSlice()
    player.inventory = [ItemStack(item_id="sword", count=1)]

    change = StateChange(slice="player", operation="set", path="inventory", value={"item_id": "bad"})
    with caplog.at_level(logging.WARNING, logger="app.game_core.state.slices.player"):
        player.apply_state_change(change)

    # inventory unchanged
    assert len(player.inventory) == 1
    assert player.inventory[0].item_id == "sword"
    # dirty flag not set
    assert not player.dirty
    # warning was emitted
    assert any("inventory change must be a list" in r.message for r in caplog.records)


def test_inventory_non_list_does_not_raise():
    """apply_state_change: inventory with non-list value must not raise."""
    player = PlayerSlice()
    change = StateChange(slice="player", operation="set", path="inventory", value="not_a_list")
    player.apply_state_change(change)  # should not raise


# ---------------------------------------------------------------------------
# PlayerSlice – _coerce_stack fallback
# ---------------------------------------------------------------------------


def test_coerce_stack_invalid_returns_empty_item_stack(caplog):
    """_coerce_stack: non-Mapping, non-ItemStack input → empty ItemStack fallback."""
    with caplog.at_level(logging.WARNING, logger="app.game_core.state.slices.player"):
        result = PlayerSlice._coerce_stack(12345)

    assert isinstance(result, ItemStack)
    assert result.item_id == ""
    assert result.count == 0
    assert result.tags == []
    assert any("invalid item stack" in r.message for r in caplog.records)


def test_coerce_stack_string_returns_empty_item_stack():
    """_coerce_stack: a plain string should not raise."""
    result = PlayerSlice._coerce_stack("bad_item")
    assert isinstance(result, ItemStack)
    assert result.item_id == ""


def test_coerce_stack_none_returns_empty_item_stack():
    """_coerce_stack: None input should not raise."""
    result = PlayerSlice._coerce_stack(None)
    assert isinstance(result, ItemStack)


def test_inventory_list_with_invalid_item_uses_fallback(caplog):
    """apply_state_change: inventory list containing a non-Mapping item uses empty fallback."""
    player = PlayerSlice()
    change = StateChange(
        slice="player",
        operation="set",
        path="inventory",
        value=[{"item_id": "potion", "count": 2}, "bad_entry", None],
    )
    with caplog.at_level(logging.WARNING, logger="app.game_core.state.slices.player"):
        player.apply_state_change(change)

    # 3 entries processed; bad ones become empty ItemStack
    assert len(player.inventory) == 3
    assert player.inventory[0].item_id == "potion"
    assert player.inventory[1].item_id == ""
    assert player.inventory[2].item_id == ""
    assert player.dirty


# ---------------------------------------------------------------------------
# QuestSlice – _apply_nested_dynamic_change resilience
# ---------------------------------------------------------------------------


def test_nested_dynamic_change_empty_path_skips(caplog):
    """_apply_nested_dynamic_change with empty path_parts should warn and skip."""
    quest = QuestSlice()
    container: dict = {}
    with caplog.at_level(logging.WARNING, logger="app.game_core.state.slices.quests"):
        QuestSlice._apply_nested_dynamic_change(
            container, [], value="x", remove=False
        )
    assert container == {}
    assert any("requires path parts" in r.message for r in caplog.records)


def test_nested_dynamic_change_invalid_list_index_skips(caplog):
    """_apply_nested_dynamic_change with non-integer index into list should warn and skip."""
    container = ["a", "b", "c"]
    with caplog.at_level(logging.WARNING, logger="app.game_core.state.slices.quests"):
        QuestSlice._apply_nested_dynamic_change(
            container, ["not_an_int"], value="x", remove=False
        )
    # container unchanged
    assert container == ["a", "b", "c"]
    assert any("invalid list index" in r.message for r in caplog.records)


def test_nested_dynamic_change_list_index_out_of_range_skips(caplog):
    """_apply_nested_dynamic_change with out-of-range index should warn and skip."""
    container = ["a", "b"]
    with caplog.at_level(logging.WARNING, logger="app.game_core.state.slices.quests"):
        QuestSlice._apply_nested_dynamic_change(
            container, ["99"], value="x", remove=False
        )
    assert container == ["a", "b"]
    assert any("out of range" in r.message for r in caplog.records)


def test_nested_dynamic_change_non_dict_container_skips(caplog):
    """_apply_nested_dynamic_change with non-dict/list container should warn and skip."""
    with caplog.at_level(logging.WARNING, logger="app.game_core.state.slices.quests"):
        QuestSlice._apply_nested_dynamic_change(
            "i_am_a_string", ["key"], value="x", remove=False
        )
    assert any("must be dict/list" in r.message for r in caplog.records)


def test_nested_dynamic_change_non_dict_container_does_not_raise():
    """_apply_nested_dynamic_change with bad container type must not raise."""
    QuestSlice._apply_nested_dynamic_change(42, ["key"], value="x", remove=False)


# ---------------------------------------------------------------------------
# QuestSlice – apply_state_change via the dynamic quest nested path
# ---------------------------------------------------------------------------


def test_quest_nested_path_bad_index_does_not_raise():
    """apply_state_change with a bad list index deep in a dynamic quest should not raise."""
    quest = QuestSlice()
    quest.add_dynamic_quest("q1", {"objectives": ["kill 5", "gather 3"]})

    change = StateChange(
        slice="quests",
        operation="set",
        path="dynamic_quests.q1.objectives.bad_index",
        value="new_obj",
    )
    quest.apply_state_change(change)  # should not raise


def test_quest_nested_path_out_of_range_does_not_raise():
    """apply_state_change with list index out of range should not raise."""
    quest = QuestSlice()
    quest.add_dynamic_quest("q1", {"objectives": ["kill 5"]})

    change = StateChange(
        slice="quests",
        operation="set",
        path="dynamic_quests.q1.objectives.99",
        value="new_obj",
    )
    quest.apply_state_change(change)  # should not raise


def test_quest_nested_path_valid_still_works():
    """Sanity: valid nested quest change still applies correctly."""
    quest = QuestSlice()
    quest.add_dynamic_quest("q1", {"objectives": ["kill 5", "gather 3"], "status": "active"})

    change = StateChange(
        slice="quests",
        operation="set",
        path="dynamic_quests.q1.status",
        value="completed",
    )
    quest.apply_state_change(change)

    assert quest.dynamic_quests["q1"]["status"] == "completed"
