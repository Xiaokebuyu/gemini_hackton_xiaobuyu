"""
State manager for admin layer.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Dict, List, Optional

from app.models.state_delta import GameState, StateDelta


class StateManager:
    """In-memory state storage with delta tracking."""

    def __init__(self) -> None:
        self._states: Dict[str, GameState] = {}
        self._deltas: Dict[str, List[StateDelta]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def _key(self, world_id: str, session_id: str) -> str:
        return f"{world_id}:{session_id}"

    async def get_state(self, world_id: str, session_id: str) -> Optional[GameState]:
        return self._states.get(self._key(world_id, session_id))

    async def init_state(
        self,
        world_id: str,
        session_id: str,
        initial_state: Optional[GameState] = None,
    ) -> GameState:
        key = self._key(world_id, session_id)
        async with self._lock:
            if key in self._states:
                return self._states[key]
            if initial_state is None:
                initial_state = GameState(world_id=world_id, session_id=session_id)
            else:
                initial_state.world_id = world_id
                initial_state.session_id = session_id
            self._states[key] = initial_state
            return initial_state

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
        async with self._lock:
            state = self._states.get(self._key(world_id, session_id))
            if state is None:
                state = await self.init_state(world_id, session_id)

            updated = state.model_dump()
            changes = delta.changes or {}
            for key, value in changes.items():
                if key in updated:
                    updated[key] = value
                else:
                    updated.setdefault("metadata", {})[key] = value

            new_state = GameState(**updated)
            self._states[self._key(world_id, session_id)] = new_state
            self._deltas[self._key(world_id, session_id)].append(delta)
            return new_state

    async def list_deltas(self, world_id: str, session_id: str) -> List[StateDelta]:
        return list(self._deltas.get(self._key(world_id, session_id), []))
