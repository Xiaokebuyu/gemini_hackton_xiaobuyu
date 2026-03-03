"""Tests for DiscoveryHandler noop skeleton."""

from app.game_core.rules.handlers.discovery import DiscoveryHandler
from app.game_core.rules.models import Command


class TestDiscoveryHandler:
    def test_command_types(self):
        h = DiscoveryHandler()
        assert "discover" in h.command_types
        assert "passive_scan" in h.command_types

    def test_validate_ok(self):
        h = DiscoveryHandler()
        result = h.validate(Command(type="discover"), None, None)
        assert result.ok is True

    def test_compute_deferred(self):
        h = DiscoveryHandler()
        result = h.compute(Command(type="discover"), None, None)
        assert result.success is True
        assert result.metadata.get("status") == "deferred"
