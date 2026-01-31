"""
Game session storage in Firestore.
"""
import uuid
from datetime import datetime
from typing import Optional

from google.cloud import firestore

from app.config import settings
from app.models.game import GameSessionState, SceneState, CombatContext


class GameSessionStore:
    """Firestore-backed session store."""

    def __init__(self, firestore_client: Optional[firestore.Client] = None) -> None:
        self.db = firestore_client or firestore.Client(database=settings.firestore_database)

    def _session_ref(self, world_id: str, session_id: str) -> firestore.DocumentReference:
        return self.db.collection("worlds").document(world_id).collection("sessions").document(session_id)

    async def create_session(
        self,
        world_id: str,
        session_id: Optional[str] = None,
        participants: Optional[list] = None,
    ) -> GameSessionState:
        session_id = session_id or f"sess_{uuid.uuid4().hex[:8]}"
        state = GameSessionState(
            session_id=session_id,
            world_id=world_id,
            participants=participants or [],
            updated_at=datetime.now(),
        )
        self._session_ref(world_id, session_id).set(state.model_dump())
        return state

    async def get_session(self, world_id: str, session_id: str) -> Optional[GameSessionState]:
        doc = self._session_ref(world_id, session_id).get()
        if not doc.exists:
            return None
        data = doc.to_dict() or {}
        return GameSessionState(**data)

    async def update_session(
        self,
        world_id: str,
        session_id: str,
        updates: dict,
    ) -> None:
        updates["updated_at"] = datetime.now()

        # Check if any keys contain dot notation (nested path updates)
        has_dot_notation = any("." in key for key in updates.keys())

        if has_dot_notation:
            # Use update() for dot notation support
            self._session_ref(world_id, session_id).update(updates)
        else:
            # Use set(merge=True) for simple updates
            self._session_ref(world_id, session_id).set(updates, merge=True)

    async def set_scene(
        self,
        world_id: str,
        session_id: str,
        scene: SceneState,
    ) -> None:
        await self.update_session(world_id, session_id, {"current_scene": scene.model_dump(), "status": "scene"})

    async def set_combat(
        self,
        world_id: str,
        session_id: str,
        combat_id: str,
        combat_context: CombatContext,
    ) -> None:
        await self.update_session(
            world_id,
            session_id,
            {
                "active_combat_id": combat_id,
                "combat_context": combat_context.model_dump(),
                "status": "combat",
            },
        )

    async def clear_combat(
        self,
        world_id: str,
        session_id: str,
    ) -> None:
        await self.update_session(world_id, session_id, {"active_combat_id": None, "status": "scene"})
