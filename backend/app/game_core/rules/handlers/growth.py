"""GrowthHandler skeleton."""

from app.game_core.rules.base import StaticCommandHandler


class GrowthHandler(StaticCommandHandler):
    COMMAND_TYPES = (
        "add_xp",
        "level_up",
        "apply_asi",
        "choose_subclass",
        "create_character",
    )
