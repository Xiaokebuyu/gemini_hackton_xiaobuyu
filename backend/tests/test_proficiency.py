"""Tests for proficiency utility functions (always-True stubs)."""

from app.game_core.rules.handlers.proficiency import (
    check_armor_proficiency,
    check_save_proficiency,
    check_weapon_proficiency,
)


def test_check_weapon_proficiency_always_true():
    assert check_weapon_proficiency(None, None) is True


def test_check_armor_proficiency_always_true():
    assert check_armor_proficiency(None, None) is True


def test_check_save_proficiency_always_true():
    assert check_save_proficiency(None, "dexterity") is True
