"""EconomyHandler skeleton."""

from app.game_core.rules.base import StaticCommandHandler


class EconomyHandler(StaticCommandHandler):
    COMMAND_TYPES = ("trade_buy", "trade_sell", "refresh_shop")
