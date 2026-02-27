"""RestHandler skeleton."""

from app.game_core.rules.base import StaticCommandHandler


class RestHandler(StaticCommandHandler):
    COMMAND_TYPES = ("rest_short", "rest_long", "night_watch", "set_camp")
