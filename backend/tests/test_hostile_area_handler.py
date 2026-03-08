"""Tests for HostileAreaHandler — enter_hostile stealth check."""

from app.game_core.rules.handlers.hostile_area import HostileAreaHandler
from app.game_core.rules.models import Command
from app.game_core.state import StateContainer
from app.game_core.state.slices.player import PlayerSlice
from app.game_core.state.slices.area import AreaSlice


def _make_state(dex: int = 10) -> StateContainer:
    """Build a minimal StateContainer with player + areas slices."""
    state = StateContainer()

    player = PlayerSlice()
    player.restore({
        "character_id": "hero",
        "current_area": "forest",
        "current_location": "hostile_1",
        "stats": {"str": 10, "dex": dex, "con": 10, "int": 10, "wis": 10, "cha": 10},
    })
    state.register(player)

    areas = AreaSlice()
    areas.restore({
        "areas": {
            "forest": {
                "danger_level": 0.5,
                "npc_locations": {},
                "hostile_tracking": {
                    "hostile_1": {
                        "sub_area_id": "hostile_1",
                        "area_id": "forest",
                        "status": "spotted",
                        "threat_level": "moderate",
                        "blocking": False,
                        "cleared": False,
                        "combat_active": False,
                    },
                },
            }
        }
    })
    state.register(areas)
    return state


class TestHostileAreaHandler:
    def test_command_types(self):
        h = HostileAreaHandler()
        assert "enter_hostile" in h.command_types

    def test_validate_requires_sub_area_id(self):
        h = HostileAreaHandler()
        state = _make_state()
        result = h.validate(Command(type="enter_hostile", params={}), state, None)
        assert result.ok is False
        assert "sub_area_id" in result.reason

    def test_validate_ok_with_sub_area_id(self):
        h = HostileAreaHandler()
        state = _make_state()
        result = h.validate(
            Command(type="enter_hostile", params={"sub_area_id": "hostile_1"}),
            state, None,
        )
        assert result.ok is True

    def test_validate_requires_player_slice(self):
        h = HostileAreaHandler()
        state = StateContainer()
        result = h.validate(
            Command(type="enter_hostile", params={"sub_area_id": "hostile_1"}),
            state, None,
        )
        assert result.ok is False

    def test_compute_returns_success_with_roll(self):
        h = HostileAreaHandler()
        state = _make_state(dex=14)  # DEX mod = +2
        result = h.compute(
            Command(type="enter_hostile", params={"sub_area_id": "hostile_1"}),
            state, None,
        )
        assert result.executed is True
        assert len(result.rolls) == 1
        roll = result.rolls[0]
        assert roll.purpose == "stealth"
        meta = result.metadata
        assert meta["sub_area_id"] == "hostile_1"
        assert "passed" in meta
        assert meta["outcome"]["category"] == "binary_action"
        assert meta["outcome"]["passed"] is meta["passed"]
        assert "roll" in meta
        assert "dc" in meta
        assert "modifier" in meta
        assert meta["dc"] == 12  # moderate threat default dc
        assert meta["modifier"] >= 2  # includes at least the DEX 14 modifier

    def test_compute_total_equals_roll_plus_modifier(self):
        h = HostileAreaHandler()
        state = _make_state(dex=16)  # +3
        result = h.compute(
            Command(type="enter_hostile", params={"sub_area_id": "hostile_1"}),
            state, None,
        )
        meta = result.metadata
        assert result.rolls[0].total == meta["roll"] + meta["modifier"]
        assert meta["modifier"] >= 3  # includes at least the DEX 16 modifier

    def test_compute_success_has_options(self):
        """If stealth passed, options include surprise_attack."""
        h = HostileAreaHandler()
        state = _make_state(dex=10)
        result = h.compute(
            Command(type="enter_hostile", params={"sub_area_id": "hostile_1"}),
            state, None,
        )
        meta = result.metadata
        if meta["passed"]:
            actions = {opt["action"] for opt in meta.get("options", [])}
            assert "surprise_attack" in actions
        else:
            assert "surprise_state" in meta
            assert meta["surprise_state"] in ("none", "enemy_surprise")

    def test_compute_fail_has_surprise_state(self):
        """If stealth failed, surprise_state is set."""
        h = HostileAreaHandler()
        # DEX 1 = -5 modifier, virtually guaranteed to fail
        state = _make_state(dex=1)
        result = h.compute(
            Command(type="enter_hostile", params={"sub_area_id": "hostile_1"}),
            state, None,
        )
        meta = result.metadata
        if not meta["passed"]:
            assert meta["surprise_state"] in ("none", "enemy_surprise")
