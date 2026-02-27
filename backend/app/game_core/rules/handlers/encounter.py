"""EncounterHandler skeleton."""

from app.game_core.rules.base import StaticCommandHandler


class EncounterHandler(StaticCommandHandler):
    COMMAND_TYPES = ("encounter_check", "generate_loot", "clear_hostile")
