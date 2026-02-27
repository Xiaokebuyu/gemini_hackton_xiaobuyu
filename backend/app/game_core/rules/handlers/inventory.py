"""InventoryHandler skeleton."""

from app.game_core.rules.base import StaticCommandHandler


class InventoryHandler(StaticCommandHandler):
    COMMAND_TYPES = ("pick_up", "drop", "equip", "unequip", "use_item")
