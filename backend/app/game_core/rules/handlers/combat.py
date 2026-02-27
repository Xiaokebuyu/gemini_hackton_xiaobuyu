"""CombatHandler skeleton."""

from app.game_core.rules.base import StaticCommandHandler


class CombatHandler(StaticCommandHandler):
    COMMAND_TYPES = (
        "attack",
        "defend",
        "disengage",
        "dash",
        "shove",
        "flee",
        "use_combat_item",
        "offhand_attack",
        "start_combat",
    )
