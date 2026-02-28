"""StructuredAction to Command dispatcher."""

from __future__ import annotations

from typing import Callable

from app.game_core.orchestration.models import StructuredAction
from app.game_core.rules.models import Command


class ActionDispatcher:
    """Map UI-level actions into rules-layer commands."""

    def __init__(self) -> None:
        self._registry: dict[str, str] = {}
        self._transforms: dict[str, Callable[[StructuredAction], Command]] = {}

    def register(
        self,
        action_type: str,
        command_type: str | None = None,
        transform: Callable[[StructuredAction], Command] | None = None,
    ) -> None:
        if transform is not None:
            self._transforms[action_type] = transform
        if command_type is not None:
            self._registry[action_type] = command_type

    def has_action(self, action_type: str) -> bool:
        """Whether an action type is already registered."""
        return (
            action_type in self._registry
            or action_type in self._transforms
        )

    def register_if_missing(
        self,
        action_type: str,
        command_type: str | None = None,
        transform: Callable[[StructuredAction], Command] | None = None,
    ) -> bool:
        """Register one action mapping without overriding an existing one."""
        if self.has_action(action_type):
            return False
        if command_type is None and transform is None:
            raise ValueError(
                "register_if_missing requires command_type or transform"
            )
        self.register(
            action_type,
            command_type=command_type,
            transform=transform,
        )
        return True

    def dispatch(self, action: StructuredAction) -> Command | None:
        transform = self._transforms.get(action.action_type)
        if transform is not None:
            return transform(action)

        command_type = self._registry.get(action.action_type)
        if command_type is None:
            return None
        return Command(
            type=command_type,
            params=dict(action.params),
            source=action.source,
            context=dict(action.context) if action.context else None,
        )
