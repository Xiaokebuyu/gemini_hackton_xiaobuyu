"""WorldStateHandler skeleton."""

from app.game_core.rules.base import StaticCommandHandler


class WorldStateHandler(StaticCommandHandler):
    COMMAND_TYPES = (
        "set_flag",
        "modify_disposition",
        "modify_approval",
        "advance_quest",
        "schedule_event",
        "create_rumor",
        "modify_location",
        "add_knowledge",
        "modify_completion",
        "adjust_danger",
    )
