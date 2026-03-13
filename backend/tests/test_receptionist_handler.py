"""Tests for receptionist-mediated quest acceptance."""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.content.registries import CharacterRegistry
from app.game_core.orchestration.defaults import DEFAULT_ACTION_COMMAND_TYPES
from app.game_core.rules import Command
from app.game_core.rules.handlers import ReceptionistHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, PlayerSlice, QuestSlice


def _make_world() -> WorldInstance:
    world = WorldInstance("test_world")
    characters = CharacterRegistry()
    characters.load(
        {
            "receptionist": {
                "id": "receptionist",
                "name": "Guild Receptionist",
                "current_area": "guild_hall",
                "current_location": "counter",
                "tags": ["receptionist", "guild_staff"],
            },
            "merchant": {
                "id": "merchant",
                "name": "Guild Merchant",
                "current_area": "guild_hall",
                "current_location": "counter",
                "tags": ["merchant"],
            },
        }
    )
    world.register(characters)
    return world


def _make_state(*, board_entries: list[dict[str, str]] | None = None) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore({"current_area": "guild_hall", "current_location": "counter"})
    state.register(player)

    areas = AreaSlice()
    areas.restore(
        {
            "areas": {
                "guild_hall": {
                    "board_bulletins": {
                        "board": board_entries
                        if board_entries is not None
                        else [
                            {"board_id": "board", "quest_id": "dq_report_in", "title": "New Lead Posted"},
                        ]
                    }
                }
            }
        }
    )
    state.register(areas)

    quests = QuestSlice()
    quests.restore(
        {
            "dynamic_quests": {
                "dq_report_in": {
                    "status": "available",
                    "title": "Lead: Report In",
                }
            }
        }
    )
    state.register(quests)
    return state


def test_receptionist_handler_registers_expected_command_type() -> None:
    handler = ReceptionistHandler()
    assert handler.COMMAND_TYPES == (
        "receptionist_accept_quest",
        "receptionist_report_quest",
    )


def test_default_action_map_registers_accept_quest_command() -> None:
    mapping = {action: command for action, command in DEFAULT_ACTION_COMMAND_TYPES}
    assert mapping["accept_quest"] == "receptionist_accept_quest"
    assert mapping["report_quest"] == "receptionist_report_quest"


def test_receptionist_accept_quest_success_marks_dynamic_quest_active() -> None:
    state = _make_state()
    world = _make_world()
    result = ReceptionistHandler().compute(
        Command(
            type="receptionist_accept_quest",
            params={
                "npc_id": "receptionist",
                "quest_id": "dq_report_in",
            },
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)
    assert state.quests.dynamic_quests["dq_report_in"]["status"] == "active"
    assert result.metadata["board_id"] == "board"


def test_receptionist_accept_quest_rejects_non_receptionist_npc() -> None:
    state = _make_state()
    world = _make_world()
    result = ReceptionistHandler().compute(
        Command(
            type="receptionist_accept_quest",
            params={
                "npc_id": "merchant",
                "quest_id": "dq_report_in",
            },
        ),
        state,
        world,
    )

    assert result.executed is False
    assert result.errors == ["npc is not a receptionist"]


def test_receptionist_accept_quest_accepts_available_quest_not_on_board() -> None:
    """B-6: receptionist can accept a quest that is not on the board but is 'available'."""
    state = _make_state(board_entries=[])
    world = _make_world()
    result = ReceptionistHandler().compute(
        Command(
            type="receptionist_accept_quest",
            params={
                "npc_id": "receptionist",
                "quest_id": "dq_report_in",
            },
        ),
        state,
        world,
    )

    # Quest is 'available' in dynamic_quests → accepted even without board entry
    assert result.executed is True


def test_receptionist_accept_quest_rejects_non_available_quest_not_on_board() -> None:
    """B-6: quest not on board and not 'available' should still be rejected."""
    state = _make_state(board_entries=[])
    # Set quest to active (not available) so it can't be accepted again
    state.quests.dynamic_quests["dq_report_in"]["status"] = "active"
    world = _make_world()
    result = ReceptionistHandler().compute(
        Command(
            type="receptionist_accept_quest",
            params={
                "npc_id": "receptionist",
                "quest_id": "dq_report_in",
            },
        ),
        state,
        world,
    )

    assert result.executed is False


def test_receptionist_report_quest_success_marks_quest_reported() -> None:
    state = _make_state()
    state.quests.dynamic_quests["dq_report_in"].update(
        {"status": "completed", "requires_report": True}
    )
    world = _make_world()
    result = ReceptionistHandler().compute(
        Command(
            type="receptionist_report_quest",
            params={
                "npc_id": "receptionist",
                "quest_id": "dq_report_in",
            },
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)
    assert state.quests.dynamic_quests["dq_report_in"]["status"] == "completed"
    assert state.quests.dynamic_quests["dq_report_in"]["reported"] is True


def test_receptionist_report_quest_rejects_non_receptionist_npc() -> None:
    state = _make_state()
    state.quests.dynamic_quests["dq_report_in"].update(
        {"status": "completed", "requires_report": True}
    )
    world = _make_world()
    result = ReceptionistHandler().compute(
        Command(
            type="receptionist_report_quest",
            params={
                "npc_id": "merchant",
                "quest_id": "dq_report_in",
            },
        ),
        state,
        world,
    )

    assert result.executed is False
    assert result.errors == ["npc is not a receptionist"]


def test_receptionist_report_quest_rejects_non_completed_quest() -> None:
    state = _make_state()
    state.quests.dynamic_quests["dq_report_in"].update({"status": "active", "requires_report": True})
    world = _make_world()
    result = ReceptionistHandler().compute(
        Command(
            type="receptionist_report_quest",
            params={
                "npc_id": "receptionist",
                "quest_id": "dq_report_in",
            },
        ),
        state,
        world,
    )

    assert result.executed is False
    assert result.errors == ["quest is not ready to report"]


def test_receptionist_report_quest_rejects_non_reportable_quest() -> None:
    state = _make_state()
    state.quests.dynamic_quests["dq_report_in"].update({"status": "completed"})
    world = _make_world()
    result = ReceptionistHandler().compute(
        Command(
            type="receptionist_report_quest",
            params={
                "npc_id": "receptionist",
                "quest_id": "dq_report_in",
            },
        ),
        state,
        world,
    )

    assert result.executed is False
    assert result.errors == ["quest does not require reporting"]


def test_receptionist_report_quest_rejects_already_reported() -> None:
    state = _make_state()
    state.quests.dynamic_quests["dq_report_in"].update(
        {"status": "completed", "requires_report": True, "reported": True}
    )
    world = _make_world()
    result = ReceptionistHandler().compute(
        Command(
            type="receptionist_report_quest",
            params={
                "npc_id": "receptionist",
                "quest_id": "dq_report_in",
            },
        ),
        state,
        world,
    )

    assert result.executed is False
    assert result.errors == ["quest already reported"]
