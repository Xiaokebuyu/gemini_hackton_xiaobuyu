"""Tests for InteractableHandler noop skeleton."""

from app.game_core.rules.handlers.interactable import InteractableHandler
from app.game_core.rules.models import Command


class TestInteractableHandler:
    def test_command_types(self):
        h = InteractableHandler()
        assert "interact_object_v2" in h.command_types

    def test_validate_ok(self):
        h = InteractableHandler()
        result = h.validate(Command(type="interact_object_v2"), None, None)
        assert result.ok is True

    def test_compute_deferred(self):
        h = InteractableHandler()
        result = h.compute(Command(type="interact_object_v2"), None, None)
        assert result.success is True
        assert result.metadata.get("status") == "deferred"
