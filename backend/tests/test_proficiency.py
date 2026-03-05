"""Tests for proficiency check functions (P3-4: real implementations)."""

from __future__ import annotations

from types import SimpleNamespace

from app.game_core.rules.handlers.proficiency import (
    check_armor_proficiency,
    check_save_proficiency,
    check_weapon_proficiency,
)


def _class(weapon_prof=None, armor_prof=None, save_prof=None):
    return SimpleNamespace(
        weapon_proficiency=weapon_prof or [],
        armor_proficiency=armor_prof or [],
        save_proficiency=save_prof or [],
    )


def _weapon(prof=""):
    return SimpleNamespace(weapon_proficiency=prof)


def _armor(armor_type=""):
    return SimpleNamespace(armor_type=armor_type)


class TestCheckWeaponProficiency:
    def test_match_returns_true(self) -> None:
        cls = _class(weapon_prof=["simple", "martial"])
        assert check_weapon_proficiency(cls, _weapon("martial")) is True

    def test_no_match_returns_false(self) -> None:
        cls = _class(weapon_prof=["simple"])
        assert check_weapon_proficiency(cls, _weapon("martial")) is False

    def test_empty_weapon_prof_always_true(self) -> None:
        """weapon_proficiency='' means no requirement — always proficient."""
        cls = _class(weapon_prof=[])
        assert check_weapon_proficiency(cls, _weapon("")) is True

    def test_class_missing_prof_and_weapon_requires_it(self) -> None:
        cls = _class(weapon_prof=[])
        assert check_weapon_proficiency(cls, _weapon("martial")) is False


class TestCheckArmorProficiency:
    def test_match_returns_true(self) -> None:
        cls = _class(armor_prof=["light", "medium"])
        assert check_armor_proficiency(cls, _armor("light")) is True

    def test_no_match_returns_false(self) -> None:
        cls = _class(armor_prof=["light"])
        assert check_armor_proficiency(cls, _armor("heavy")) is False

    def test_empty_armor_type_always_true(self) -> None:
        """armor_type='' (e.g., not actual armor) — no restriction."""
        cls = _class(armor_prof=[])
        assert check_armor_proficiency(cls, _armor("")) is True


class TestCheckSaveProficiency:
    def test_match_returns_true(self) -> None:
        cls = _class(save_prof=["str", "con"])
        assert check_save_proficiency(cls, "con") is True

    def test_no_match_returns_false(self) -> None:
        cls = _class(save_prof=["str"])
        assert check_save_proficiency(cls, "dex") is False

    def test_empty_save_type_always_true(self) -> None:
        """Empty save_type means no check required."""
        cls = _class(save_prof=[])
        assert check_save_proficiency(cls, "") is True
