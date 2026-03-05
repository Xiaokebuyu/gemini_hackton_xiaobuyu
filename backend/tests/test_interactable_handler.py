"""Tests for InteractableHandler command types."""

from app.game_core.rules.handlers.interactable import InteractableHandler


class TestInteractableHandler:
    def test_command_types(self):
        h = InteractableHandler()
        assert "interact_object_v2" in h.command_types
