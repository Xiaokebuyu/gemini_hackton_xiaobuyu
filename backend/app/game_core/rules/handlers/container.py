"""ContainerHandler skeleton."""

from app.game_core.rules.base import StaticCommandHandler


class ContainerHandler(StaticCommandHandler):
    COMMAND_TYPES = (
        "open_container",
        "disarm_trap",
        "take_from_container",
        "take_all",
    )
