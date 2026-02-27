"""NavigationHandler skeleton."""

from app.game_core.rules.base import StaticCommandHandler


class NavigationHandler(StaticCommandHandler):
    COMMAND_TYPES = ("move_area", "enter_sub_location", "leave_sub_location")
