"""SkillCheckHandler skeleton."""

from app.game_core.rules.base import StaticCommandHandler


class SkillCheckHandler(StaticCommandHandler):
    COMMAND_TYPES = ("skill_check", "saving_throw", "contest")
