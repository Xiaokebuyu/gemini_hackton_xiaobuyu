from __future__ import annotations

import pytest
from google.cloud.firestore_v1._helpers import DocumentExtractor

from app.models.player_character import CharacterClass, CharacterRace, PlayerCharacter
from app.services.character_store import CharacterStore


class _DocRef:
    def __init__(self) -> None:
        self.last_payload = None

    def set(self, payload, merge: bool = True) -> None:
        self.last_payload = payload
        # Raises ValueError if nested map keys are non-string / empty.
        DocumentExtractor(payload)


class _CharacterStoreStub(CharacterStore):
    def __init__(self, doc_ref: _DocRef) -> None:
        self._doc_ref = doc_ref

    def _session_ref(self, world_id: str, session_id: str):  # type: ignore[override]
        assert world_id
        assert session_id
        return self._doc_ref


@pytest.mark.asyncio
async def test_save_character_serializes_spell_slot_keys_for_firestore() -> None:
    doc_ref = _DocRef()
    store = _CharacterStoreStub(doc_ref)

    character = PlayerCharacter(
        name="Caster",
        race=CharacterRace.HUMAN,
        character_class=CharacterClass.MAGE,
        abilities={"str": 8, "dex": 14, "con": 12, "int": 15, "wis": 10, "cha": 13},
        max_hp=8,
        current_hp=8,
        ac=12,
        initiative_bonus=2,
        spell_slots={1: 2},
        spell_slots_used={1: 0},
    )

    await store.save_character("goblin_slayer", "sess_test", character)

    saved = doc_ref.last_payload["player_character"]
    assert saved["spell_slots"] == {"1": 2}
    assert saved["spell_slots_used"] == {"1": 0}
