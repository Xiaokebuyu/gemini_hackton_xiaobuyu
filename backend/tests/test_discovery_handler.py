"""Tests for DiscoveryHandler command types."""

from app.game_core.rules.handlers.discovery import DiscoveryHandler


class TestDiscoveryHandler:
    def test_command_types(self):
        h = DiscoveryHandler()
        assert "discover" in h.command_types
        assert "passive_scan" in h.command_types
