"""
Party persistence service (Firestore).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from google.cloud import firestore

from app.config import settings
from app.models.party import Party, PartyMember, TeammateRole, TeammateModelConfig


class PartyStore:
    """Firestore-backed party store."""

    def __init__(self, firestore_client: Optional[firestore.Client] = None) -> None:
        self.db = firestore_client or firestore.Client(database=settings.firestore_database)

    def _party_ref(self, world_id: str, session_id: str) -> firestore.DocumentReference:
        return (
            self.db.collection("worlds")
            .document(world_id)
            .collection("parties")
            .document(session_id)
        )

    def _members_ref(self, world_id: str, session_id: str) -> firestore.CollectionReference:
        return self._party_ref(world_id, session_id).collection("members")

    @staticmethod
    def _parse_datetime(value) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except Exception:
                pass
        return datetime.utcnow()

    async def create_party(
        self,
        world_id: str,
        session_id: str,
        party_id: str,
        leader_id: str,
    ) -> None:
        now = datetime.utcnow()
        self._party_ref(world_id, session_id).set(
            {
                "party_id": party_id,
                "world_id": world_id,
                "session_id": session_id,
                "leader_id": leader_id,
                "formed_at": now,
                "max_size": 4,
                "auto_follow": True,
                "share_events": True,
                "current_location": None,
                "current_sub_location": None,
                "updated_at": now,
            }
        )

    async def get_party(self, world_id: str, session_id: str) -> Optional[Party]:
        doc = self._party_ref(world_id, session_id).get()
        if not doc.exists:
            return None
        data = doc.to_dict() or {}

        members = []
        for member_doc in self._members_ref(world_id, session_id).stream():
            member_data = member_doc.to_dict() or {}
            member_data["character_id"] = member_doc.id
            role_value = member_data.get("role", "support")
            try:
                member_data["role"] = TeammateRole(role_value)
            except ValueError:
                member_data["role"] = TeammateRole.SUPPORT

            member_data["joined_at"] = self._parse_datetime(member_data.get("joined_at"))
            if member_data.get("model_config_override"):
                member_data["model_config_override"] = TeammateModelConfig(
                    **member_data["model_config_override"]
                )
            members.append(PartyMember(**member_data))

        party_data = {k: v for k, v in data.items() if k != "updated_at"}
        party_data["formed_at"] = self._parse_datetime(data.get("formed_at"))
        party_data["members"] = members
        return Party(**party_data)

    async def add_member(self, world_id: str, session_id: str, member: PartyMember) -> None:
        payload = {
            "name": member.name,
            "role": member.role.value,
            "personality": member.personality,
            "response_tendency": member.response_tendency,
            "joined_at": member.joined_at,
            "is_active": member.is_active,
            "current_mood": member.current_mood,
            "graph_ref": member.graph_ref,
        }
        if member.model_config_override:
            payload["model_config_override"] = member.model_config_override.model_dump()

        self._members_ref(world_id, session_id).document(member.character_id).set(payload)

    async def remove_member(self, world_id: str, session_id: str, character_id: str) -> None:
        self._members_ref(world_id, session_id).document(character_id).delete()

    async def update_member_status(
        self,
        world_id: str,
        session_id: str,
        character_id: str,
        is_active: bool,
    ) -> None:
        self._members_ref(world_id, session_id).document(character_id).set(
            {"is_active": is_active},
            merge=True,
        )

    async def update_party_location(
        self,
        world_id: str,
        session_id: str,
        location: str,
        sub_location: Optional[str] = None,
    ) -> None:
        self._party_ref(world_id, session_id).set(
            {
                "current_location": location,
                "current_sub_location": sub_location,
                "updated_at": datetime.utcnow(),
            },
            merge=True,
        )

    async def delete_party(self, world_id: str, session_id: str) -> None:
        for doc in self._members_ref(world_id, session_id).stream():
            doc.reference.delete()
        self._party_ref(world_id, session_id).delete()
