"""Combat data repository for world-backed monsters/skills/items.

Primary source: Firestore (world scoped).
Fallback source: local structured data files.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)


def _safe_slug(value: str) -> str:
    text = (value or "").strip().lower()
    if not text:
        return ""
    out = []
    for ch in text:
        if ch.isalnum() or ch in ("_", "-"):
            out.append(ch)
        elif ch.isspace() or ch in ("/", "\\", ":", "|", "."):
            out.append("_")
    slug = "".join(out).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug


def _flatten_entries(raw: Any) -> List[Dict[str, Any]]:
    """Recursively flatten nested list/dict payloads into entity dicts."""
    result: List[Dict[str, Any]] = []

    if isinstance(raw, dict):
        # Keep dictionary if it looks like an entity entry.
        looks_like_entity = any(
            key in raw
            for key in (
                "id",
                "name",
                "type",
                "stats",
                "effect",
                "description",
                "properties",
            )
        )
        if looks_like_entity:
            result.append(raw)
            return result

        # Or descend into nested values.
        for value in raw.values():
            result.extend(_flatten_entries(value))
        return result

    if isinstance(raw, list):
        for entry in raw:
            result.extend(_flatten_entries(entry))

    return result


def _normalize_collection_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize common entity fields while preserving source payload."""
    normalized = dict(entry)

    entity_id = str(
        normalized.get("id")
        or normalized.get("enemy_id")
        or normalized.get("skill_id")
        or normalized.get("item_id")
        or normalized.get("name")
        or ""
    ).strip()
    if not entity_id:
        entity_id = _safe_slug(str(normalized.get("name", "")))

    normalized["id"] = entity_id
    normalized.setdefault("name", entity_id)
    normalized.setdefault("source", "world_data")

    return normalized


class CombatDataRepository:
    """Read world combat entities from Firestore, with local fallback."""

    def __init__(
        self,
        world_id: str,
        template_version: Optional[str] = None,
        firestore_client: Any = None,
    ) -> None:
        self.world_id = world_id
        self.template_version = template_version or "default"
        self._db = firestore_client
        self._cache: Dict[str, List[Dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_monsters(self) -> List[Dict[str, Any]]:
        return self._load_entities("monsters")

    def list_skills(self) -> List[Dict[str, Any]]:
        return self._load_entities("skills")

    def list_items(self) -> List[Dict[str, Any]]:
        return self._load_entities("items")

    def get_monster(self, enemy_id: str) -> Optional[Dict[str, Any]]:
        key = _safe_slug(enemy_id)
        for monster in self.list_monsters():
            if _safe_slug(str(monster.get("id", ""))) == key:
                return dict(monster)
            if _safe_slug(str(monster.get("name", ""))) == key:
                return dict(monster)
        return None

    def get_skill(self, skill_id: str) -> Optional[Dict[str, Any]]:
        key = _safe_slug(skill_id)
        for skill in self.list_skills():
            if _safe_slug(str(skill.get("id", ""))) == key:
                return dict(skill)
            if _safe_slug(str(skill.get("name", ""))) == key:
                return dict(skill)
        return None

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_entities(self, entity_type: str) -> List[Dict[str, Any]]:
        if entity_type in self._cache:
            return [dict(entry) for entry in self._cache[entity_type]]

        entries = self._load_from_firestore(entity_type)
        if not entries:
            entries = self._load_from_local_files(entity_type)

        normalized = [_normalize_collection_entry(entry) for entry in entries]
        self._cache[entity_type] = normalized
        return [dict(entry) for entry in normalized]

    def _get_firestore_client(self):
        if self._db is not None:
            return self._db

        try:
            from google.cloud import firestore

            self._db = firestore.Client(database=settings.firestore_database)
        except Exception as exc:  # pragma: no cover - environment dependent
            logger.debug("CombatDataRepository firestore init failed: %s", exc)
            self._db = None

        return self._db

    def _load_from_firestore(self, entity_type: str) -> List[Dict[str, Any]]:
        db = self._get_firestore_client()
        if db is None:
            return []

        world_ref = db.collection("worlds").document(self.world_id)
        candidates: List[Any] = []

        # Candidate 1: compact documents under worlds/{world_id}/combat_entities/{entity_type}
        try:
            doc = world_ref.collection("combat_entities").document(entity_type).get()
            if doc.exists:
                payload = doc.to_dict() or {}
                candidates.extend(_flatten_entries(payload))
        except Exception as exc:  # pragma: no cover - environment dependent
            logger.debug("Load combat_entities/%s failed: %s", entity_type, exc)

        # Candidate 2: collection worlds/{world_id}/{entity_type}
        try:
            for snap in world_ref.collection(entity_type).stream():
                payload = snap.to_dict() or {}
                payload.setdefault("id", snap.id)
                candidates.extend(_flatten_entries(payload))
        except Exception as exc:  # pragma: no cover - environment dependent
            logger.debug("Load collection %s failed: %s", entity_type, exc)

        return self._dedupe(candidates)

    def _load_from_local_files(self, entity_type: str) -> List[Dict[str, Any]]:
        for base_dir in self._candidate_local_dirs():
            path = base_dir / f"{entity_type}.json"
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                entries = _flatten_entries(payload)
                if entries:
                    logger.info(
                        "CombatDataRepository fallback load %s from %s",
                        entity_type,
                        path,
                    )
                    return self._dedupe(entries)
            except Exception as exc:
                logger.warning("Failed to load %s: %s", path, exc)

        return []

    def _candidate_local_dirs(self) -> Iterable[Path]:
        repo_root = Path(__file__).resolve().parents[2]
        data_root = repo_root / "data"

        world_candidates = [self.world_id]
        # Common local alias in this repo.
        if self.world_id != "goblin_slayer":
            world_candidates.append("goblin_slayer")

        for world in world_candidates:
            yield data_root / world / "structured_new"
            yield data_root / world / "structured"

    @staticmethod
    def _dedupe(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen: set[str] = set()
        deduped: List[Dict[str, Any]] = []

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_id = str(
                entry.get("id")
                or entry.get("enemy_id")
                or entry.get("skill_id")
                or entry.get("item_id")
                or entry.get("name")
                or ""
            ).strip()
            if not entry_id:
                continue

            key = _safe_slug(entry_id)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(dict(entry))

        return deduped
