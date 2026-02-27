"""SpellHandler skeleton."""

from app.game_core.rules.base import StaticCommandHandler


class SpellHandler(StaticCommandHandler):
    COMMAND_TYPES = ("cast_spell", "prepare_spells", "break_concentration")
