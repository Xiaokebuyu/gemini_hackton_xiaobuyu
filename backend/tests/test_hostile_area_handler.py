"""Tests for HostileAreaHandler noop skeleton."""

from app.game_core.rules.handlers.hostile_area import HostileAreaHandler
from app.game_core.rules.models import Command


class TestHostileAreaHandler:
    def test_command_types(self):
        h = HostileAreaHandler()
        assert "enter_hostile" in h.command_types

    def test_validate_ok(self):
        h = HostileAreaHandler()
        result = h.validate(Command(type="enter_hostile"), None, None)
        assert result.ok is True

    def test_compute_deferred(self):
        h = HostileAreaHandler()
        result = h.compute(Command(type="enter_hostile"), None, None)
        assert result.success is True
        assert result.metadata.get("status") == "deferred"
