"""CrimeHandler skeleton."""

from app.game_core.rules.base import StaticCommandHandler


class CrimeHandler(StaticCommandHandler):
    COMMAND_TYPES = ("steal", "lockpick")
