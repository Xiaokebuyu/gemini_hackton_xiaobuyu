"""
Character persistence service (Firestore).

Stores player character data within session metadata:
  worlds/{world_id}/sessions/{session_id}  -> field "player_character"

Creation config cached from:
  worlds/{world_id}/character_creation/config
  or falls back to local JSON at data/goblin_slayer/structured/character_creation.json
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from google.cloud import firestore

from app.config import settings
from app.models.player_character import PlayerCharacter

logger = logging.getLogger(__name__)

# In-memory cache for creation config (keyed by world_id)
_config_cache: Dict[str, Dict[str, Any]] = {}

# Path to the bundled fallback config
_LOCAL_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "goblin_slayer" / "structured" / "character_creation.json"


class CharacterStore:
    """Firestore-backed player character store."""

    def __init__(self, firestore_client: Optional[firestore.Client] = None) -> None:
        self.db = firestore_client or firestore.Client(database=settings.firestore_database)

    def _session_ref(self, world_id: str, session_id: str) -> firestore.DocumentReference:
        return (
            self.db.collection("worlds")
            .document(world_id)
            .collection("sessions")
            .document(session_id)
        )

    async def save_character(
        self,
        world_id: str,
        session_id: str,
        character: PlayerCharacter,
    ) -> None:
        """Write player character into session metadata."""
        self._session_ref(world_id, session_id).set(
            # Firestore field paths require non-empty string components.
            # Use JSON mode so Dict[int, ...] fields (e.g. spell slots) become string-keyed maps.
            {"player_character": character.model_dump(mode="json")},
            merge=True,
        )
        logger.info("Saved player character '%s' for session %s/%s", character.name, world_id, session_id)

    async def get_character(
        self,
        world_id: str,
        session_id: str,
    ) -> Optional[PlayerCharacter]:
        """Read player character from session metadata."""
        doc = self._session_ref(world_id, session_id).get()
        if not doc.exists:
            return None
        data = doc.to_dict() or {}
        pc_data = data.get("player_character")
        if not pc_data:
            return None
        try:
            return PlayerCharacter(**pc_data)
        except Exception:
            logger.warning("Failed to parse player character for %s/%s", world_id, session_id, exc_info=True)
            return None

    async def get_creation_config(self, world_id: str) -> Dict[str, Any]:
        """
        Get character creation config.

        Lookup order:
          1. In-memory cache
          2. Firestore: worlds/{world_id}/character_creation/config
          3. Local JSON fallback
        """
        if world_id in _config_cache:
            return _config_cache[world_id]

        # Try Firestore
        try:
            doc_ref = (
                self.db.collection("worlds")
                .document(world_id)
                .collection("character_creation")
                .document("config")
            )
            doc = doc_ref.get()
            if doc.exists:
                config = doc.to_dict() or {}
                if config:
                    _config_cache[world_id] = config
                    logger.info("Loaded character creation config from Firestore for world %s", world_id)
                    return config
        except Exception:
            logger.debug("Firestore config not available for world %s, using local fallback", world_id)

        # Fallback to local JSON
        config = self._load_local_config()
        _config_cache[world_id] = config
        return config

    @staticmethod
    def _load_local_config() -> Dict[str, Any]:
        """Load bundled character_creation.json."""
        with open(_LOCAL_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
