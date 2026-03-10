from __future__ import annotations

import pytest

from app.game_core.planning.directive_contracts import (
    SUPPORTED_PLANNER_DIRECTIVE_KINDS,
    normalize_planner_directive,
    validate_planner_directive,
)
from app.game_core.planning.models import CreateQuestPlan, DirectNpcPlan


@pytest.mark.parametrize(
    ("raw", "expected_kind"),
    [
        ({"kind": "create_quest", "payload": {"quest_id": "dq_new"}}, "create_quest"),
        ({"kind": "direct_npc", "payload": {"npc_id": "npc_guard", "directive": {"kind": "hint"}}}, "direct_npc"),
        ({"kind": "publish_bulletin", "payload": {"board_id": "board"}}, "publish_bulletin"),
        ({"kind": "escalate", "payload": {"delta": 1}}, "escalate"),
        ({"kind": "adjust_pacing", "payload": {"frozen": True}}, "adjust_pacing"),
        ({"kind": "retire_quest", "payload": {"quest_id": "dq_old"}}, "retire_quest"),
        ({"kind": "spawn_quest_npc", "payload": {"area_id": "forest"}}, "spawn_quest_npc"),
        ({"kind": "plant_environmental", "payload": {"area_id": "forest"}}, "plant_environmental"),
        ({"kind": "fill_area", "payload": {"area_id": "forest"}}, "fill_area"),
        ({"kind": "update_quest", "payload": {"quest_id": "dq_active"}}, "update_quest"),
        (
            {
                "kind": "design_reward",
                "payload": {
                    "linked_quest_id": "dq_active",
                    "item_id": "bandage",
                    "quantity": 2,
                },
            },
            "design_reward",
        ),
        ({"kind": "curate_shop", "payload": {"npc_id": "merchant"}}, "curate_shop"),
    ],
)
def test_validate_planner_directive_accepts_all_supported_happy_paths(
    raw: dict[str, object],
    expected_kind: str,
) -> None:
    result = validate_planner_directive(raw)

    assert result.ok is True
    assert result.kind == expected_kind
    assert result.reason_code is None
    assert result.payload_digest["kind"] == expected_kind


@pytest.mark.parametrize(
    ("raw", "reason_code"),
    [
        ({"kind": "create_quest", "payload": {}}, "missing_quest_id"),
        ({"kind": "direct_npc", "payload": {"npc_id": "npc_guard"}}, "missing_directive"),
        ({"kind": "publish_bulletin", "payload": {}}, "missing_board_id"),
        ({"kind": "escalate", "payload": {"delta": 4}}, "delta_out_of_range"),
        ({"kind": "adjust_pacing", "payload": {"frozen": "yes"}}, "invalid_frozen"),
        ({"kind": "retire_quest", "payload": {}}, "missing_quest_id"),
        ({"kind": "spawn_quest_npc", "payload": {}}, "missing_area_id"),
        ({"kind": "plant_environmental", "payload": {}}, "missing_area_id"),
        ({"kind": "fill_area", "payload": {}}, "missing_area_id"),
        ({"kind": "update_quest", "payload": {}}, "missing_quest_id"),
        (
            {"kind": "design_reward", "payload": {"item_id": "bandage", "quantity": 1}},
            "missing_linked_quest_id",
        ),
        ({"kind": "curate_shop", "payload": {}}, "missing_npc_id"),
    ],
)
def test_validate_planner_directive_rejects_contract_violations(
    raw: dict[str, object],
    reason_code: str,
) -> None:
    result = validate_planner_directive(raw)

    assert result.ok is False
    assert result.reason_code == reason_code


def test_validate_planner_directive_rejects_unknown_kinds() -> None:
    result = validate_planner_directive({"kind": "unknown_kind", "payload": {}})

    assert result.ok is False
    assert result.reason_code == "unsupported_kind"


def test_validate_planner_directive_honors_allowed_directives() -> None:
    result = validate_planner_directive(
        {"kind": "adjust_pacing", "payload": {"frozen": True}},
        allowed_directives={"create_quest"},
    )

    assert result.ok is False
    assert result.reason_code == "kind_not_allowed"


def test_normalize_planner_directive_supports_plan_dataclasses() -> None:
    assert normalize_planner_directive(CreateQuestPlan("dq_new", {"title": "Lead"})) == (
        "create_quest",
        {"quest_id": "dq_new", "title": "Lead"},
    )
    assert normalize_planner_directive(
        DirectNpcPlan("npc_guard", {"kind": "hint", "topic": "gate"})
    ) == (
        "direct_npc",
        {"npc_id": "npc_guard", "directive": {"kind": "hint", "topic": "gate"}},
    )


def test_supported_planner_directives_match_current_hook_surface() -> None:
    assert SUPPORTED_PLANNER_DIRECTIVE_KINDS == {
        "create_quest",
        "direct_npc",
        "publish_bulletin",
        "escalate",
        "adjust_pacing",
        "retire_quest",
        "spawn_quest_npc",
        "plant_environmental",
        "fill_area",
        "update_quest",
        "design_reward",
        "curate_shop",
    }


def test_payload_digest_keeps_only_key_ids_and_payload_keys() -> None:
    result = validate_planner_directive(
        {
            "kind": "publish_bulletin",
            "payload": {
                "board_id": "board",
                "area_id": "forest",
                "title": "Long bulletin",
                "content": "A much longer payload body that should not be persisted in trace.",
            },
        }
    )

    assert result.ok is True
    assert result.payload_digest == {
        "kind": "publish_bulletin",
        "payload_keys": ["area_id", "board_id", "content", "title"],
        "board_id": "board",
        "area_id": "forest",
    }


def test_validate_planner_directive_normalizes_design_reward_defaults() -> None:
    result = validate_planner_directive(
        {
            "kind": "design_reward",
            "payload": {
                "linked_quest_id": "dq_active",
                "item_id": "bandage",
            },
        }
    )

    assert result.ok is True
    assert result.payload == {
        "linked_quest_id": "dq_active",
        "item_id": "bandage",
        "reward_type": "item",
        "quantity": 1,
    }


def test_validate_planner_directive_accepts_update_quest_minimal_contract() -> None:
    result = validate_planner_directive(
        {
            "kind": "update_quest",
            "payload": {
                "quest_id": "dq_active",
                "current_step": "Go west",
                "next_steps": ["Cross the bridge"],
            },
        }
    )

    assert result.ok is True
    assert result.payload["quest_id"] == "dq_active"
    assert result.payload["current_step"] == "Go west"
    assert result.payload["next_steps"] == ["Cross the bridge"]


def test_validate_planner_directive_accepts_curate_shop_minimal_contract() -> None:
    result = validate_planner_directive(
        {
            "kind": "curate_shop",
            "payload": {
                "npc_id": "blacksmith",
                "add_items": [{"item_id": "cheap_shortsword", "count": 1}],
            },
        }
    )

    assert result.ok is True
    assert result.payload["npc_id"] == "blacksmith"
    assert result.payload["add_items"] == [{"item_id": "cheap_shortsword", "count": 1}]
