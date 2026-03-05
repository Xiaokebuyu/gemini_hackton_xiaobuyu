"""RoleStateProxy — restricts StateContainer access by agent role (N-8)."""

from __future__ import annotations

from typing import Any

from app.game_core.state import StateContainer

_FULL_ACCESS_ROLES = frozenset({"gm"})
_ROLE_ALLOWED_SLICES: dict[str, frozenset[str]] = {
    "npc": frozenset({"scene", "relations", "player", "time", "areas"}),
    "teammate": frozenset({"scene", "relations", "player", "time", "areas", "party"}),
}
_KNOWN_ROLES = _FULL_ACCESS_ROLES | frozenset(_ROLE_ALLOWED_SLICES)


class RoleStateProxy:
    """Read-only proxy for StateContainer enforcing role-based slice access.

    GM receives unrestricted access (full_access=True).
    NPC and Teammate are limited to their allowed slice names.
    Unknown roles are rejected eagerly instead of silently receiving GM access.
    has_slice() is always forwarded regardless of role (returns bool, no data).
    Any attribute assignment raises AttributeError (proxy is read-only).
    """

    __slots__ = ("_state", "_role", "_allowed", "_full_access")

    def __init__(self, state: StateContainer, role: str) -> None:
        if role not in _KNOWN_ROLES:
            raise ValueError(f"unknown role: {role}")
        object.__setattr__(self, "_state", state)
        object.__setattr__(self, "_role", role)
        allowed = _ROLE_ALLOWED_SLICES.get(role, frozenset())
        object.__setattr__(self, "_allowed", allowed)
        object.__setattr__(self, "_full_access", role in _FULL_ACCESS_ROLES)

    def has_slice(self, name: str) -> bool:
        """Always forwarded — returns bool without exposing data."""
        return self._state.has_slice(name)  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> Any:
        full_access: bool = object.__getattribute__(self, "_full_access")
        if full_access:
            return getattr(object.__getattribute__(self, "_state"), name)
        allowed: frozenset[str] = object.__getattribute__(self, "_allowed")
        if name in allowed:
            return getattr(object.__getattribute__(self, "_state"), name)
        role: str = object.__getattribute__(self, "_role")
        raise AttributeError(
            f"Role '{role}' may not access state slice '{name}'"
        )

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("RoleStateProxy is read-only")
