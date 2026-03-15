from __future__ import annotations

import asyncio

from app.game_core.adapters.inbound import CommandAliasInputPort, FastAPIInputPort


def _parse(text: str) -> dict[str, object]:
    return asyncio.run(CommandAliasInputPort().process_text(text))


def _parse_action(payload: dict[str, object]) -> dict[str, object]:
    return asyncio.run(
        FastAPIInputPort().process_action(
            {
                "channel": "interaction",
                "payload": payload,
            }
        )
    )


def test_move_aliases_parse_into_move_area() -> None:
    assert _parse("go frontier") == {
        "status": "parsed",
        "normalized_text": "go frontier",
        "action_type": "move_area",
        "params": {"area_id": "frontier"},
    }
    assert _parse("move training_grounds") == {
        "status": "parsed",
        "normalized_text": "move training_grounds",
        "action_type": "move_area",
        "params": {"area_id": "training_grounds"},
    }
    assert _parse("travel frontier") == {
        "status": "parsed",
        "normalized_text": "travel frontier",
        "action_type": "move_area",
        "params": {"area_id": "frontier"},
    }


def test_enter_and_leave_aliases_parse_into_navigation_actions() -> None:
    assert _parse("enter yard") == {
        "status": "parsed",
        "normalized_text": "enter yard",
        "action_type": "enter_sub_location",
        "params": {"location_id": "yard"},
    }
    assert _parse("leave") == {
        "status": "parsed",
        "normalized_text": "leave",
        "action_type": "leave_sub_location",
        "params": {},
    }
    assert _parse("exit") == {
        "status": "parsed",
        "normalized_text": "exit",
        "action_type": "leave_sub_location",
        "params": {},
    }


def test_text_alias_rejections_stay_stable() -> None:
    assert _parse("   ") == {
        "status": "rejected",
        "normalized_text": "",
        "code": "empty_input",
        "message": "text input must be non-empty",
    }
    assert _parse("go") == {
        "status": "rejected",
        "normalized_text": "go",
        "code": "missing_argument",
        "message": "area_id is required for go/move/travel",
    }
    assert _parse("enter") == {
        "status": "rejected",
        "normalized_text": "enter",
        "code": "missing_argument",
        "message": "location_id is required for enter",
    }
    assert _parse("leave now") == {
        "status": "rejected",
        "normalized_text": "leave now",
        "code": "unexpected_argument",
        "message": "leave/exit does not accept additional arguments",
    }
    assert _parse("inventory") == {
        "status": "rejected",
        "normalized_text": "inventory",
        "code": "unsupported_input",
        "message": "input is not a supported command alias",
    }


def test_fastapi_input_port_normalizes_greet_without_needing_session() -> None:
    assert _parse_action({"npc_id": "merchant", "intent": "greet"}) == {
        "status": "resolved",
        "target_kind": "npc",
        "target_id": "merchant",
        "intent": "greet",
        "item_id": None,
        "quest_id": None,
        "count": 1,
        "execution": {
            "kind": "pipeline_action",
            "action_type": "add_knowledge",
            "params": {
                "npc_id": "merchant",
                "impression": "Shared a brief greeting.",
            },
            "post_snapshot": "talk",
        },
    }


def test_fastapi_input_port_normalizes_talk_and_rejects_invalid_npc_intent() -> None:
    assert _parse_action({"npc_id": "merchant", "intent": "talk"}) == {
        "status": "resolved",
        "target_kind": "npc",
        "target_id": "merchant",
        "intent": "talk",
        "item_id": None,
        "quest_id": None,
        "count": 1,
        "execution": {
            "kind": "snapshot",
            "snapshot_type": "talk",
        },
    }
    assert _parse_action({"npc_id": "merchant", "intent": "inspect"}) == {
        "status": "rejected",
        "target_kind": "npc",
        "target_id": "merchant",
        "intent": "inspect",
        "item_id": None,
        "quest_id": None,
        "count": 1,
        "code": "invalid_intent",
        "message": (
            "npc intent must be browse, buy, sell, buy_service, talk, greet, "
            "accept_quest, report_quest, "
            "inspect_item, ask_quest, ask_progress, ask_location, "
            "ask_requirements, or ask_reward"
        ),
    }


def test_fastapi_input_port_rejects_board_kind_for_action_stream() -> None:
    assert _parse_action(
        {
            "target_kind": "board",
            "target_id": "board",
            "intent": "accept",
        }
    ) == {
        "status": "rejected",
        "target_kind": "board",
        "target_id": "board",
        "intent": "accept",
        "item_id": None,
        "quest_id": None,
        "count": 1,
        "code": "invalid_target_kind",
        "message": "target_kind must be npc",
    }


def test_fastapi_input_port_normalizes_all_npc_quest_snapshots() -> None:
    expected_snapshot_types = {
        "ask_quest": "quest_brief",
        "ask_progress": "quest_progress",
        "ask_location": "quest_location",
        "ask_requirements": "quest_requirements",
        "ask_reward": "quest_reward",
    }
    for intent, snapshot_type in expected_snapshot_types.items():
        missing = _parse_action({"npc_id": "merchant", "intent": intent})
        assert missing == {
            "status": "rejected",
            "target_kind": "npc",
            "target_id": "merchant",
            "intent": intent,
            "item_id": None,
            "quest_id": None,
            "count": 1,
            "code": "missing_quest",
            "message": f"quest_id is required for npc {intent}",
        }
        resolved = _parse_action(
            {
                "npc_id": "merchant",
                "intent": intent,
                "quest_id": "dq_report_in",
            }
        )
        assert resolved == {
            "status": "resolved",
            "target_kind": "npc",
            "target_id": "merchant",
            "intent": intent,
            "item_id": None,
            "quest_id": "dq_report_in",
            "count": 1,
            "execution": {
                "kind": "snapshot",
                "snapshot_type": snapshot_type,
                "quest_id": "dq_report_in",
            },
        }


def test_fastapi_input_port_normalizes_accept_quest() -> None:
    missing = _parse_action({"npc_id": "receptionist", "intent": "accept_quest"})
    assert missing == {
        "status": "rejected",
        "target_kind": "npc",
        "target_id": "receptionist",
        "intent": "accept_quest",
        "item_id": None,
        "quest_id": None,
        "count": 1,
        "code": "missing_quest",
        "message": "quest_id is required for npc accept_quest",
    }

    resolved = _parse_action(
        {
            "npc_id": "receptionist",
            "intent": "accept_quest",
            "quest_id": "dq_report_in",
        }
    )
    assert resolved == {
        "status": "resolved",
        "target_kind": "npc",
        "target_id": "receptionist",
        "intent": "accept_quest",
        "item_id": None,
        "quest_id": "dq_report_in",
        "count": 1,
        "execution": {
            "kind": "pipeline_action",
            "action_type": "accept_quest",
            "params": {
                "npc_id": "receptionist",
                "quest_id": "dq_report_in",
            },
        },
    }


def test_fastapi_input_port_normalizes_report_quest() -> None:
    missing = _parse_action({"npc_id": "receptionist", "intent": "report_quest"})
    assert missing == {
        "status": "rejected",
        "target_kind": "npc",
        "target_id": "receptionist",
        "intent": "report_quest",
        "item_id": None,
        "quest_id": None,
        "count": 1,
        "code": "missing_quest",
        "message": "quest_id is required for npc report_quest",
    }

    resolved = _parse_action(
        {
            "npc_id": "receptionist",
            "intent": "report_quest",
            "quest_id": "dq_report_in",
        }
    )
    assert resolved == {
        "status": "resolved",
        "target_kind": "npc",
        "target_id": "receptionist",
        "intent": "report_quest",
        "item_id": None,
        "quest_id": "dq_report_in",
        "count": 1,
        "execution": {
            "kind": "pipeline_action",
            "action_type": "report_quest",
            "params": {
                "npc_id": "receptionist",
                "quest_id": "dq_report_in",
            },
        },
    }


def test_fastapi_input_port_still_rejects_basic_shape_errors() -> None:
    assert _parse_action({"intent": "greet"}) == {
        "status": "rejected",
        "target_kind": None,
        "target_id": None,
        "intent": "greet",
        "item_id": None,
        "quest_id": None,
        "count": 1,
        "code": "missing_target",
        "message": "target_kind and target_id are required",
    }
    assert _parse_action(
        {
            "target_kind": "campfire",
            "target_id": "camp",
            "intent": "browse",
        }
    ) == {
        "status": "rejected",
        "target_kind": "campfire",
        "target_id": "camp",
        "intent": "browse",
        "item_id": None,
        "quest_id": None,
        "count": 1,
        "code": "invalid_target_kind",
        "message": "target_kind must be npc",
    }
