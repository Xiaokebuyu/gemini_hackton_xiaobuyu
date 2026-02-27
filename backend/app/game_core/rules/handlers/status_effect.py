"""StatusEffectHandler skeleton."""

from app.game_core.rules.base import StaticCommandHandler


class StatusEffectHandler(StaticCommandHandler):
    COMMAND_TYPES = (
        "apply_effect",
        "remove_effect",
        "remove_effect_by_type",
        "tick_effects",
    )
