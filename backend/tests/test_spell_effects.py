"""Tests for merge_status_effect_template and spell effect creation pipeline."""

from __future__ import annotations

from app.game_core.content.registries.skills import StatusEffectTemplate
from app.game_core.rules.handlers.spell_effects import merge_status_effect_template


def _make_template(
    *,
    id: str = "test_effect",
    disadvantage_on: list[str] | None = None,
    advantage_on_attacks_against: bool = False,
    prevents_action: bool = False,
    save_end_of_turn: str | None = None,
    save_dc: int | None = None,
    cure_conditions: list[str] | None = None,
    modifiers: dict | None = None,
) -> StatusEffectTemplate:
    return StatusEffectTemplate(
        id=id,
        disadvantage_on=disadvantage_on or [],
        advantage_on_attacks_against=advantage_on_attacks_against,
        prevents_action=prevents_action,
        save_end_of_turn=save_end_of_turn,
        save_dc=save_dc,
        cure_conditions=cure_conditions or [],
        modifiers=modifiers,
    )


class TestMergeStatusEffectTemplate:
    def test_merge_template_propagates_disadvantage_checks(self) -> None:
        template = _make_template(disadvantage_on=["attack", "ability_check"])
        effect_dict: dict = {}
        merge_status_effect_template(effect_dict, template)
        assert effect_dict["disadvantage_checks"] == ["attack", "ability_check"]

    def test_merge_template_propagates_advantage_on_attacks_against(self) -> None:
        template = _make_template(advantage_on_attacks_against=True)
        effect_dict: dict = {}
        merge_status_effect_template(effect_dict, template)
        assert effect_dict["advantage_on_attacks_against"] is True

    def test_merge_template_propagates_modifiers(self) -> None:
        template = _make_template(modifiers={"ac": 2})
        effect_dict: dict = {}
        merge_status_effect_template(effect_dict, template)
        assert effect_dict["modifiers"] == {"ac": 2}

    def test_merge_template_instance_value_takes_priority(self) -> None:
        """Effect instance already has modifiers — template value must NOT override."""
        template = _make_template(modifiers={"ac": 5})
        effect_dict: dict = {"modifiers": {"ac": 1}}
        merge_status_effect_template(effect_dict, template)
        assert effect_dict["modifiers"] == {"ac": 1}  # instance wins

    def test_merge_template_none_is_noop(self) -> None:
        effect_dict: dict = {"existing_key": "value"}
        merge_status_effect_template(effect_dict, None)
        assert effect_dict == {"existing_key": "value"}

    def test_merge_template_propagates_prevents_action(self) -> None:
        template = _make_template(prevents_action=True)
        effect_dict: dict = {}
        merge_status_effect_template(effect_dict, template)
        assert effect_dict["prevents_action"] is True

    def test_merge_template_propagates_save_end_of_turn(self) -> None:
        template = _make_template(save_end_of_turn="con", save_dc=14)
        effect_dict: dict = {}
        merge_status_effect_template(effect_dict, template)
        assert effect_dict["save_end_of_turn"] == "con"
        assert effect_dict["save_dc"] == 14

    def test_merge_template_propagates_cure_conditions(self) -> None:
        template = _make_template(cure_conditions=["long_rest"])
        effect_dict: dict = {}
        merge_status_effect_template(effect_dict, template)
        assert effect_dict["cure_conditions"] == ["long_rest"]

    def test_merge_template_empty_lists_do_not_propagate(self) -> None:
        """Empty list fields (disadvantage_on=[]) should not set any key."""
        template = _make_template(disadvantage_on=[])
        effect_dict: dict = {}
        merge_status_effect_template(effect_dict, template)
        assert "disadvantage_checks" not in effect_dict

    def test_merge_template_false_advantage_does_not_set_key(self) -> None:
        """advantage_on_attacks_against=False should not set the key."""
        template = _make_template(advantage_on_attacks_against=False)
        effect_dict: dict = {}
        merge_status_effect_template(effect_dict, template)
        assert "advantage_on_attacks_against" not in effect_dict
