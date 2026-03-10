"""Tests for combat_sse.py — v2 combat metadata → SSEEvent conversion."""

from __future__ import annotations

from app.game_core.orchestration.combat_sse import extract_combat_sse


class TestExtractCombatSSE:
    def test_non_combat_action_returns_empty(self) -> None:
        events = extract_combat_sse("move_area", {"sub_area_id": "x"})
        assert events == []

    def test_unknown_action_returns_empty(self) -> None:
        events = extract_combat_sse("attack", {"target": "goblin"})
        assert events == []

    def test_non_dict_metadata_returns_empty(self) -> None:
        events = extract_combat_sse("combat_attack", None)
        assert events == []

    def test_start_combat_sse(self) -> None:
        metadata = {
            "grid": {"width": 8, "height": 6, "terrain": ["GGGGGGGG"] * 6},
            "units": [{"unit_id": "player", "side": "ally"}],
            "turn_order": ["player", "goblin_1"],
            "current_unit_id": "player",
            "environment": {"time_of_day": "day", "weather": "clear"},
        }
        events = extract_combat_sse("start_combat", metadata)
        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == "combat_started"
        assert ev.payload["grid"] == metadata["grid"]
        assert ev.payload["units"] == metadata["units"]
        assert ev.payload["turn_order"] == metadata["turn_order"]
        assert ev.payload["current_unit_id"] == "player"
        assert ev.payload["environment"] == metadata["environment"]

    def test_combat_move_sse(self) -> None:
        metadata = {
            "unit_id": "player",
            "from": [0, 2],
            "to": [2, 2],
        }
        events = extract_combat_sse("combat_move", metadata)
        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == "unit_moved"
        assert ev.payload["unit_id"] == "player"
        assert ev.payload["from"] == [0, 2]
        assert ev.payload["to"] == [2, 2]

    def test_combat_attack_hit_sse(self) -> None:
        metadata = {
            "attacker_id": "player",
            "target_id": "goblin_1",
            "attack_name": "Longsword",
            "hit": True,
            "damage": 8,
            "target_hp": 0,
            "target_alive": True,
            "combat_cleared": False,
        }
        events = extract_combat_sse("combat_attack", metadata)
        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == "unit_attacked"
        assert ev.payload["attacker_id"] == "player"
        assert ev.payload["target_id"] == "goblin_1"
        assert ev.payload["hit"] is True
        assert ev.payload["damage"] == 8

    def test_combat_attack_kill_sse(self) -> None:
        """target_alive=False produces unit_attacked + unit_defeated."""
        metadata = {
            "attacker_id": "player",
            "target_id": "goblin_1",
            "attack_name": "Longsword",
            "hit": True,
            "damage": 7,
            "target_hp": 0,
            "target_alive": False,
            "combat_cleared": False,
        }
        events = extract_combat_sse("combat_attack", metadata)
        assert len(events) == 2
        assert events[0].event_type == "unit_attacked"
        assert events[1].event_type == "unit_defeated"
        assert events[1].payload["unit_id"] == "goblin_1"

    def test_combat_attack_clear_sse(self) -> None:
        """combat_cleared=True appends a combat_ended SSE."""
        metadata = {
            "attacker_id": "player",
            "target_id": "goblin_1",
            "attack_name": "Longsword",
            "hit": True,
            "damage": 7,
            "target_hp": 0,
            "target_alive": False,
            "combat_cleared": True,
            "xp_awarded": 25,
        }
        events = extract_combat_sse("combat_attack", metadata)
        types = [e.event_type for e in events]
        assert "combat_ended" in types
        ended = next(e for e in events if e.event_type == "combat_ended")
        assert ended.payload["result"] == "victory"
        assert ended.payload["xp_awarded"] == 25

    def test_combat_attack_no_target_id_no_events(self) -> None:
        """If target_id is absent (attack was blocked), no unit_attacked event."""
        metadata = {
            "attacker_id": "player",
            "hit": False,
            "damage": 0,
            "combat_cleared": False,
        }
        events = extract_combat_sse("combat_attack", metadata)
        assert events == []

    def test_combat_end_turn_sse(self) -> None:
        metadata = {
            "next_unit_id": "goblin_1",
            "combat_round": 2,
            "round_advanced": True,
        }
        events = extract_combat_sse("combat_end_turn", metadata)
        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == "turn_changed"
        assert ev.payload["unit_id"] == "goblin_1"
        assert ev.payload["combat_round"] == 2
        assert ev.payload["round_advanced"] is True

    def test_combat_npc_turn_attack_sse(self) -> None:
        """combat_npc_turn with attack sub-dict → unit_attacked SSE."""
        metadata = {
            "unit_id": "goblin_1",
            "decision_action": "attack",
            "attack": {
                "target_id": "player",
                "attack_name": "Bite",
                "hit": True,
                "damage": 3,
                "target_hp": 7,
                "target_alive": True,
            },
            "combat_cleared": False,
        }
        events = extract_combat_sse("combat_npc_turn", metadata)
        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == "unit_attacked"
        assert ev.payload["attacker_id"] == "goblin_1"
        assert ev.payload["target_id"] == "player"
        assert ev.payload["hit"] is True
        assert ev.payload["damage"] == 3

    def test_combat_npc_turn_no_attack_no_events(self) -> None:
        """NPC turn with hold/flee decision and no attack dict → no unit_attacked event."""
        metadata = {
            "unit_id": "goblin_1",
            "decision_action": "hold",
            "attack": None,
            "combat_cleared": False,
        }
        events = extract_combat_sse("combat_npc_turn", metadata)
        assert events == []

    def test_combat_npc_turn_combat_cleared_sse(self) -> None:
        """NPC kill that clears combat → unit_attacked + unit_defeated + combat_ended."""
        metadata = {
            "unit_id": "goblin_1",
            "decision_action": "attack",
            "attack": {
                "target_id": "player",
                "attack_name": "Bite",
                "hit": True,
                "damage": 10,
                "target_hp": 0,
                "target_alive": False,
            },
            "combat_cleared": True,
            "xp_awarded": 0,
        }
        events = extract_combat_sse("combat_npc_turn", metadata)
        types = [e.event_type for e in events]
        assert "unit_attacked" in types
        assert "unit_defeated" in types
        assert "combat_ended" in types

    def test_combat_defend_and_disengage_dash_return_empty(self) -> None:
        """combat_defend, combat_disengage, combat_dash are in the SSE type set
        but produce no events currently (no payload to surface)."""
        for action_type in ("combat_defend", "combat_disengage", "combat_dash"):
            events = extract_combat_sse(action_type, {"unit_id": "player", "status": "defending"})
            assert events == [], f"{action_type} should produce no SSE events"
