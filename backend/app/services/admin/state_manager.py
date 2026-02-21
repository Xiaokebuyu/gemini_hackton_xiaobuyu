"""
State manager for admin layer.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

from app.models.state_delta import GameState, StateDelta


class StateManager:
    """In-memory state storage with delta tracking."""

    def __init__(self) -> None:
        self._states: Dict[str, GameState] = {}
        self._deltas: Dict[str, List[StateDelta]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def _key(self, world_id: str, session_id: str) -> str:
        return f"{world_id}:{session_id}"

    @staticmethod
    def _new_default_state(world_id: str, session_id: str) -> GameState:
        """Create a default state snapshot for a session."""
        return GameState(world_id=world_id, session_id=session_id)

    async def get_state(self, world_id: str, session_id: str) -> Optional[GameState]:
        return self._states.get(self._key(world_id, session_id))

    def get_state_sync(self, world_id: str, session_id: str) -> Optional[GameState]:
        """Synchronous state lookup (no lock, read-only snapshot)."""
        return self._states.get(self._key(world_id, session_id))

    async def set_state(self, world_id: str, session_id: str, state: GameState) -> GameState:
        """Replace current state snapshot."""
        key = self._key(world_id, session_id)
        async with self._lock:
            self._states[key] = state
            return state

    async def apply_delta(
        self,
        world_id: str,
        session_id: str,
        delta: StateDelta,
    ) -> GameState:
        logger.debug("应用状态变更: %s:%s, changes=%s", world_id, session_id, list((delta.changes or {}).keys()))
        state_key = self._key(world_id, session_id)
        async with self._lock:
            state = self._states.get(state_key)
            if state is None:
                # State not found; create a default one inline (under the same lock).
                state = self._new_default_state(world_id, session_id)
                self._states[state_key] = state
                logger.info("状态初始化完成: %s", state_key)

            updated = state.model_dump()
            changes = delta.changes or {}
            for field_key, value in changes.items():
                if field_key in updated:
                    updated[field_key] = value
                else:
                    updated.setdefault("metadata", {})[field_key] = value

            new_state = GameState(**updated)
            self._states[state_key] = new_state
            self._deltas[state_key].append(delta)
            return new_state

