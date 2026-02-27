"""Static content layer for the new game-core kernel."""

from app.game_core.content.base import ContentRegistry
from app.game_core.content.world import WorldInstance

__all__ = [
    "ContentRegistry",
    "WorldInstance",
]
