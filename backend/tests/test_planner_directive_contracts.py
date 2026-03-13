from __future__ import annotations

import pytest

from app.game_core.planning.directive_contracts import (
    SUPPORTED_PLANNER_DIRECTIVE_KINDS,
    expand_planner_directive,
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
        (
            {
                "kind": "plant_environmental",
                "payload": {"area_id": "forest", "description": "Fresh tracks in the mud."},
            },
            "plant_environmental",
        ),
        (
            {
                "kind": "plant_encounter",
                "payload": {
                    "area_id": "forest",
                    "sub_area_id": "forest_path",
                    "monster_ids": ["goblin"],
                },
            },
            "plant_encounter",
        ),
        (
            {
                "kind": "fill_area",
                "payload": {
                    "area_id": "forest",
                    "id": "forest_ruin",
                    "label": "Forest Ruin",
                    "description": "Broken stones under ivy.",
                },
            },
            "fill_area",
        ),
        (
            {
                "kind": "fill_location",
                "payload": {
                    "area_id": "forest",
                    "location_id": "camp",
                    "interactables": [{"id": "stash", "name": "Stash"}],
                },
            },
            "fill_location",
        ),
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
        (
            {"kind": "spawn_quest_npc", "payload": {"area_id": "forest", "room_id": "back_room"}},
            "room_id_requires_location_id",
        ),
        ({"kind": "plant_environmental", "payload": {}}, "missing_area_id"),
        ({"kind": "plant_encounter", "payload": {"area_id": "forest"}}, "missing_sub_area_id"),
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


def test_validate_planner_directive_rejects_blank_fill_area_payloads() -> None:
    result = validate_planner_directive({"kind": "fill_area", "payload": {"area_id": "forest"}})

    assert result.ok is False
    assert result.reason_code == "missing_sub_area_content"


def test_validate_planner_directive_rejects_blank_environmental_payloads() -> None:
    result = validate_planner_directive(
        {"kind": "plant_environmental", "payload": {"area_id": "forest"}}
    )

    assert result.ok is False
    assert result.reason_code == "missing_environmental_content"


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
        "plant_encounter",
        "fill_area",
        "fill_location",
        "update_quest",
        "design_reward",
        "curate_shop",
        "discover_room",
        "fill_room",
        "assign_capability",
        "revoke_capability",
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


def test_validate_planner_directive_accepts_legacy_create_quest_shape() -> None:
    result = validate_planner_directive(
        {
            "kind": "create_quest",
            "payload": {
                "id": "dq_legacy",
                "title": "Legacy Lead",
                "description": "Legacy summary field.",
            },
        }
    )

    assert result.ok is True
    assert result.payload["quest_id"] == "dq_legacy"
    assert result.payload["summary"] == "Legacy summary field."


def test_validate_planner_directive_normalizes_root_publish_bulletin_quest_id() -> None:
    result = validate_planner_directive(
        {
            "kind": "publish_bulletin",
            "payload": {
                "board_id": "board",
                "quest_id": "dq_legacy",
            },
        }
    )

    assert result.ok is True
    assert result.payload["metadata"] == {"quest_id": "dq_legacy"}


# --- Phase 0 tests: escalate delta type coercion ---


def test_validate_escalate_coerces_float_delta_to_int() -> None:
    result = validate_planner_directive({"kind": "escalate", "payload": {"delta": 1.0}})

    assert result.ok is True
    assert result.payload["delta"] == 1
    assert isinstance(result.payload["delta"], int)


def test_validate_escalate_coerces_string_delta_to_int() -> None:
    result = validate_planner_directive({"kind": "escalate", "payload": {"delta": "2"}})

    assert result.ok is True
    assert result.payload["delta"] == 2
    assert isinstance(result.payload["delta"], int)


def test_validate_escalate_rejects_none_delta() -> None:
    result = validate_planner_directive({"kind": "escalate", "payload": {"delta": None}})

    assert result.ok is False
    assert result.reason_code == "invalid_delta"


def test_validate_planner_directive_accepts_legacy_direct_npc_shape() -> None:
    result = validate_planner_directive(
        {
            "kind": "direct_npc",
            "payload": {
                "npc_id": "cow_girl",
                "behavior": "welcoming_with_relief",
                "topic": "new_bond_and_safety",
                "goal": "solidify_acquaintance_bond",
                "interactable": True,
            },
        }
    )

    assert result.ok is True
    assert result.payload["npc_id"] == "cow_girl"
    assert result.payload["directive"] == {
        "kind": "talk",
        "behavior": "welcoming_with_relief",
        "topic": "new_bond_and_safety",
        "goal": "solidify_acquaintance_bond",
        "interactable": True,
    }


def test_validate_planner_directive_normalizes_legacy_plant_encounter_location_id() -> None:
    result = validate_planner_directive(
        {
            "kind": "plant_encounter",
            "payload": {
                "area_id": "forest",
                "location_id": "forest_path",
                "monster_ids": ["goblin", "", None],
            },
        }
    )

    assert result.ok is True
    assert result.payload["sub_area_id"] == "forest_path"
    assert result.payload["monster_ids"] == ["goblin"]


def test_validate_planner_directive_flags_legacy_scene_encounter_shape() -> None:
    result = validate_planner_directive(
        {
            "kind": "plant_encounter",
            "payload": {
                "area_id": "forest",
                "location_id": "forest_path",
                "encounter_id": "scene_001",
                "participants": ["cow_girl"],
                "description": "A peaceful morning exchange.",
            },
        }
    )

    assert result.ok is False
    assert result.reason_code == "legacy_scene_encounter_not_supported"


def test_validate_planner_directive_flags_legacy_adjust_pacing_shape() -> None:
    result = validate_planner_directive(
        {
            "kind": "adjust_pacing",
            "payload": {"pacing_factor": 0.5, "reason": "slow down"},
        }
    )

    assert result.ok is False
    assert result.reason_code == "legacy_pacing_factor_not_supported"


def test_expand_planner_directive_splits_legacy_fill_area_locations() -> None:
    directives = expand_planner_directive(
        {
            "kind": "fill_area",
            "payload": {
                "area_id": "cow_girl_farm",
                "locations": [
                    {
                        "id": "farm_porch",
                        "name": "Farm Porch",
                        "description": "A warm place to sit.",
                        "traits": ["social", "warm"],
                    },
                    {
                        "id": "farm_barn",
                        "name": "Barn",
                        "description": "Hay and tools everywhere.",
                    },
                ],
            },
        }
    )

    assert directives == [
        {
            "kind": "fill_area",
            "payload": {
                "area_id": "cow_girl_farm",
                "id": "farm_porch",
                "label": "Farm Porch",
                "description": "A warm place to sit.",
                "tags": ["social", "warm"],
            },
        },
        {
            "kind": "fill_area",
            "payload": {
                "area_id": "cow_girl_farm",
                "id": "farm_barn",
                "label": "Barn",
                "description": "Hay and tools everywhere.",
            },
        },
    ]


def test_expand_planner_directive_splits_legacy_environmental_elements() -> None:
    directives = expand_planner_directive(
        {
            "kind": "plant_environmental",
            "payload": {
                "area_id": "cow_girl_farm",
                "elements": [
                    {
                        "id": "morning_mist",
                        "name": "Morning Mist",
                        "description": "Thin mist over the field.",
                        "persistence": 3,
                    }
                ],
            },
        }
    )

    assert directives == [
        {
            "kind": "plant_environmental",
            "payload": {
                "area_id": "cow_girl_farm",
                "clue_id": "morning_mist",
                "description": "Thin mist over the field.",
                "expiry_ticks": 3,
            },
        }
    ]


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


def test_validate_planner_directive_accepts_fill_location_clue_interactable() -> None:
    result = validate_planner_directive(
        {
            "kind": "fill_location",
            "payload": {
                "area_id": "frontier_town",
                "location_id": "adventurer_guild",
                "interactables": [
                    {
                        "id": "blood_trail_clue",
                        "name": "拖拽血迹",
                        "description": "半干的血迹一直拖向后门。",
                        "type": "inspect",
                        "tags": ["clue"],
                        "functional": {
                            "type": "investigate_clue",
                            "params": {
                                "clue_id": "blood_trail",
                                "options": [
                                    {"id": "examine", "label": "仔细检查"},
                                    {"id": "follow", "label": "顺着痕迹追过去"},
                                ],
                                "outcomes": {
                                    "examine": [],
                                    "follow": [
                                        {
                                            "type": "unlock_sub_location",
                                            "params": {
                                                "id": "north_alley_hideout",
                                                "label": "北巷暗门",
                                                "description": "血迹尽头的一扇侧门。",
                                            },
                                        }
                                    ],
                                },
                            },
                        },
                    }
                ],
            },
        }
    )

    assert result.ok is True
    interactable = result.payload["interactables"][0]
    assert interactable["functional"]["type"] == "investigate_clue"
    assert interactable["functional"]["params"]["clue_id"] == "blood_trail"


def test_validate_planner_directive_rejects_invalid_clue_interactable_shape() -> None:
    result = validate_planner_directive(
        {
            "kind": "fill_location",
            "payload": {
                "area_id": "frontier_town",
                "location_id": "adventurer_guild",
                "interactables": [
                    {
                        "id": "bad_clue",
                        "name": "坏线索",
                        "description": "没有选项。",
                        "type": "inspect",
                        "tags": ["clue"],
                        "functional": {
                            "type": "investigate_clue",
                            "params": {
                                "clue_id": "bad_clue",
                                "options": [{"id": "only", "label": "唯一选项"}],
                                "outcomes": {"only": []},
                            },
                        },
                    }
                ],
            },
        }
    )

    assert result.ok is False
    assert result.reason_code == "invalid_clue_interactable:invalid_option_count"
