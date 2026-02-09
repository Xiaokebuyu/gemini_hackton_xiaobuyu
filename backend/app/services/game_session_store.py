"""
Game session storage in Firestore.
"""
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from google.cloud import firestore

from app.config import settings
from app.models.game import GameSessionState, SceneState, CombatContext


class GameSessionStore:
    """Firestore-backed session store."""

    def __init__(self, firestore_client: Optional[firestore.Client] = None) -> None:
        self.db = firestore_client or firestore.Client(database=settings.firestore_database)

    def _session_ref(self, world_id: str, session_id: str) -> firestore.DocumentReference:
        return self.db.collection("worlds").document(world_id).collection("sessions").document(session_id)

    def _session_exists(self, world_id: str, session_id: str) -> bool:
        doc = self._session_ref(world_id, session_id).get()
        return bool(doc.exists)

    async def create_session(
        self,
        world_id: str,
        session_id: Optional[str] = None,
        participants: Optional[list] = None,
    ) -> GameSessionState:
        if session_id:
            if self._session_exists(world_id, session_id):
                raise ValueError(
                    f"session_id '{session_id}' already exists; use resume endpoint or a new session_id"
                )
        else:
            for _ in range(8):
                candidate = f"sess_{uuid.uuid4().hex[:8]}"
                if not self._session_exists(world_id, candidate):
                    session_id = candidate
                    break
            if not session_id:
                raise RuntimeError("failed to allocate unique session_id")

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

    async def list_sessions(
        self,
        world_id: str,
        user_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[GameSessionState]:
        """
        列出世界内会话（按更新时间倒序）。

        说明：
        - Firestore 的 array_contains + order_by 往往需要复合索引。
        - 为了降低部署前置条件，这里先查询后在内存排序。
        """
        sessions_ref = self.db.collection("worlds").document(world_id).collection("sessions")
        query = sessions_ref
        if user_id:
            query = query.where("participants", "array_contains", user_id)

        docs = list(query.stream())
        sessions: List[GameSessionState] = []
        for doc in docs:
            data = doc.to_dict() or {}
            try:
                sessions.append(GameSessionState(**data))
            except Exception:
                continue

        def _sort_key(state: GameSessionState) -> datetime:
            updated = state.updated_at
            if isinstance(updated, datetime):
                if updated.tzinfo is None:
                    return updated.replace(tzinfo=timezone.utc)
                return updated
            return datetime.min.replace(tzinfo=timezone.utc)

        sessions.sort(key=_sort_key, reverse=True)
        safe_limit = max(1, min(int(limit), 100))
        return sessions[:safe_limit]

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
