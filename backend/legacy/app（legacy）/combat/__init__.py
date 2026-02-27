"""Combat system package."""

from app.world.combat.combat_engine import CombatEngine


def run_combat_mcp_server(*args, **kwargs):
    """Lazy import to avoid hard dependency on mcp package at import time."""
    from .combat_mcp_server import run_combat_mcp_server as _run
    return _run(*args, **kwargs)


__all__ = ["CombatEngine", "run_combat_mcp_server"]
