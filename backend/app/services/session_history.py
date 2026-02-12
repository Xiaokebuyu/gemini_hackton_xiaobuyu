"""
Session History Service.

Accumulates conversation messages per game session and triggers
graphization when the context window fills up.

Responsibilities:
1. Record player input + GM response each round
2. Provide recent conversation history for context continuity
3. Trigger MemoryGraphizer when token threshold is reached
4. Remove graphized messages to free context space
5. Persist messages to Firestore for cross-restart recovery
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from google.cloud import firestore as firestore_lib

from app.models.context_window import WindowMessage
from app.services.context_window import ContextWindow

if TYPE_CHECKING:
    from app.services.graph_store import GraphStore
    from app.services.memory_graphizer import MemoryGraphizer

logger = logging.getLogger(__name__)


def _firestore_messages_path(world_id: str, session_id: str) -> str:
    """Firestore collection path for session messages."""
    return f"worlds/{world_id}/sessions/{session_id}/messages"


def _normalize_message_for_api(
    role: str,
    content: str,
    metadata: Optional[Dict[str, Any]],
    timestamp: Any,
) -> Dict[str, Any]:
    """Normalize raw history message for API/front-end consumption."""
    meta = metadata or {}
    source = meta.get("source")
    is_teammate = source == "teammate"

    if hasattr(timestamp, "isoformat"):
        ts_str = timestamp.isoformat()
    else:
        ts_str = str(timestamp) if timestamp else None

    is_npc_dialogue = source == "npc_dialogue"

    if is_teammate:
        speaker = meta.get("name") or meta.get("character_id") or "队友"
        message_type = "teammate"
        normalized_role = "assistant"
    elif is_npc_dialogue:
        speaker = meta.get("name") or meta.get("character_id") or "NPC"
        message_type = "npc"
        normalized_role = "assistant"
    elif role == "user":
        speaker = "玩家"
        message_type = "player"
        normalized_role = role
    elif role == "assistant":
        speaker = "GM"
        message_type = "gm"
        normalized_role = role
    else:
        speaker = "系统"
        message_type = "system"
        normalized_role = role

    return {
        # Preserve original role for backward compatibility with existing front-end parsers.
        "role": role,
        "original_role": role,
        "normalized_role": normalized_role,
        "content": content or "",
        "timestamp": ts_str,
        "metadata": meta,
        "message_type": message_type,
        "speaker": speaker,
    }


class SessionHistory:
    """
    Per-session conversation accumulator with graphization trigger.

    Each session has its own ContextWindow. When the window fills to 90%,
    old messages are graphized into the player's character graph and then
    removed from the window.
    """

    def __init__(
        self,
        world_id: str,
        session_id: str,
        max_tokens: int = 1_000_000,
        graphize_threshold: float = 0.9,
        keep_recent_tokens: int = 100_000,
        firestore_db: Any = None,
    ) -> None:
        self.world_id = world_id
        self.session_id = session_id
        self._firestore_db = firestore_db
        self._window = ContextWindow(
            npc_id="player",
            world_id=world_id,
            max_tokens=max_tokens,
            graphize_threshold=graphize_threshold,
            keep_recent_tokens=keep_recent_tokens,
        )
        self._graphize_in_progress = False
        self._total_graphize_runs = 0

    def record_round(
        self,
        player_input: str,
        gm_response: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Record a single game round (player input + GM response).

        Returns a dict with token stats and whether graphization is needed.
        Supports visibility marker: metadata may contain "visibility": "private"
        and "private_target" for private conversations.
        """
        user_result = self._window.add_message(
            role="user",
            content=player_input,
            metadata=metadata or {},
        )

        assistant_result = self._window.add_message(
            role="assistant",
            content=gm_response,
            metadata={"source": "gm_narration", **(metadata or {})},
        )

        # Fire-and-forget Firestore persistence
        if self._firestore_db:
            try:
                now = datetime.now(timezone.utc)
                asyncio.get_event_loop().run_in_executor(
                    None,
                    self._persist_messages_sync,
                    [
                        {"role": "user", "content": player_input, "timestamp": now, "metadata": metadata or {}},
                        {
                            "role": "assistant",
                            "content": gm_response,
                            "timestamp": now,
                            "metadata": {"source": "gm_narration", **(metadata or {})},
                        },
                    ],
                )
            except Exception as exc:
                logger.debug("[SessionHistory] Firestore persist failed: %s", exc)

        return {
            "round_tokens": user_result.token_count + assistant_result.token_count,
            "total_tokens": assistant_result.current_tokens,
            "usage_ratio": assistant_result.usage_ratio,
            "should_graphize": assistant_result.should_graphize,
            "message_count": self._window.message_count,
        }

    def record_npc_response(
        self,
        character_id: str,
        name: str,
        dialogue: str,
    ) -> None:
        """Record an NPC dialogue response as a system message."""
        self._window.add_message(
            role="system",
            content=f"[NPC:{name}] {dialogue}",
            metadata={"source": "npc_dialogue", "character_id": character_id, "name": name},
        )

        # Fire-and-forget Firestore persistence
        if self._firestore_db:
            try:
                now = datetime.now(timezone.utc)
                asyncio.get_event_loop().run_in_executor(
                    None,
                    self._persist_messages_sync,
                    [
                        {
                            "role": "system",
                            "content": f"[NPC:{name}] {dialogue}",
                            "timestamp": now,
                            "metadata": {"source": "npc_dialogue", "character_id": character_id, "name": name},
                        },
                    ],
                )
            except Exception as exc:
                logger.debug("[SessionHistory] Firestore persist failed: %s", exc)

    def record_teammate_response(
        self,
        character_id: str,
        name: str,
        response: str,
    ) -> None:
        """Record a teammate response as a system message."""
        self._window.add_message(
            role="system",
            content=f"[{name}] {response}",
            metadata={"source": "teammate", "character_id": character_id, "name": name},
        )

        # Fire-and-forget Firestore persistence
        if self._firestore_db:
            try:
                now = datetime.now(timezone.utc)
                asyncio.get_event_loop().run_in_executor(
                    None,
                    self._persist_messages_sync,
                    [
                        {
                            "role": "system",
                            "content": f"[{name}] {response}",
                            "timestamp": now,
                            "metadata": {"source": "teammate", "character_id": character_id, "name": name},
                        },
                    ],
                )
            except Exception as exc:
                logger.debug("[SessionHistory] Firestore persist failed: %s", exc)

    def _persist_messages_sync(self, messages: List[Dict[str, Any]]) -> None:
        """Synchronously write messages to Firestore (runs in executor)."""
        col_path = _firestore_messages_path(self.world_id, self.session_id)
        col_ref = self._firestore_db.collection(col_path)
        for msg in messages:
            col_ref.add(msg)

    def get_recent_history(self, max_tokens: int = 4000) -> str:
        """
        Get recent conversation history formatted for context injection.

        Returns a condensed text representation of recent messages,
        staying within max_tokens budget.
        """
        messages = self._window.messages
        if not messages:
            return ""

        lines = []
        accumulated_tokens = 0

        for msg in reversed(messages):
            if accumulated_tokens + msg.token_count > max_tokens:
                break

            if msg.role == "user":
                lines.insert(0, f"玩家: {msg.content}")
            elif msg.role == "assistant":
                lines.insert(0, f"GM: {msg.content}")
            elif msg.role == "system" and msg.metadata.get("source") in ("teammate", "npc_dialogue"):
                lines.insert(0, msg.content)

            accumulated_tokens += msg.token_count

        return "\n".join(lines)

    def get_last_teammate_responses(self) -> List[Dict[str, str]]:
        """
        Get teammate responses from the most recent round.

        Scans messages backwards from the end, collecting teammate responses
        until hitting a non-teammate message (player input or GM response),
        which marks the boundary of the current/last round.

        Returns:
            List of dicts with character_id, name, response.
        """
        results: List[Dict[str, str]] = []
        for msg in reversed(self._window.messages):
            meta = msg.metadata or {}
            if meta.get("source") == "teammate":
                name = meta.get("name", "")
                # 剥离 [name] 前缀，提取纯文本 response（避免跨轮注入时双重前缀）
                content = msg.content
                prefix = f"[{name}] " if name else ""
                if prefix and content.startswith(prefix):
                    content = content[len(prefix):]
                results.append({
                    "character_id": meta.get("character_id", ""),
                    "name": name,
                    "response": content,
                })
            elif msg.role in ("user", "assistant"):
                # Hit player/GM message — we've gone past the latest round
                break
        results.reverse()
        return results

    def get_recent_messages(self, count: int = 10) -> List[Dict[str, str]]:
        """Get recent messages as list of dicts for API context."""
        messages = self._window.get_recent_messages(count)
        return [
            {"role": msg.role, "content": msg.content}
            for msg in messages
        ]

    def get_recent_messages_for_api(self, count: int = 50) -> List[Dict[str, Any]]:
        """Get recent messages with normalized fields for front-end display."""
        messages = self._window.get_recent_messages(count)
        return [
            _normalize_message_for_api(
                role=msg.role,
                content=msg.content,
                metadata=msg.metadata,
                timestamp=msg.timestamp,
            )
            for msg in messages
        ]

    async def maybe_graphize(
        self,
        graphizer: MemoryGraphizer,
        graph_store: Optional[GraphStore] = None,
        game_day: int = 1,
        current_scene: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Check if graphization is needed and run it if so.

        This is designed to be called after each round. It checks the
        ContextWindow threshold and, if triggered, runs the MemoryGraphizer
        to convert old messages into graph nodes.

        Returns graphization result dict, or None if not triggered.
        """
        if self._graphize_in_progress:
            logger.info("[SessionHistory] Graphization already in progress, skipping")
            return None

        trigger = self._window.check_graphize_trigger()
        if not trigger.should_graphize:
            return None

        logger.info(
            "[SessionHistory] Graphization triggered: %s (urgency=%.2f)",
            trigger.reason,
            trigger.urgency,
        )

        self._graphize_in_progress = True
        try:
            request = self._window.get_graphize_request(
                current_scene=current_scene,
                game_day=game_day,
            )

            if not request.messages:
                logger.info("[SessionHistory] No messages to graphize")
                return None

            result = await graphizer.graphize(request)

            if result.success:
                message_ids = [m.id for m in request.messages]
                self._window.mark_messages_graphized(message_ids)
                remove_result = self._window.remove_graphized_messages()
                self._total_graphize_runs += 1

                logger.info(
                    "[SessionHistory] Graphization complete: "
                    "%d nodes added, %d edges added, %d messages removed, "
                    "%d tokens freed",
                    result.nodes_added,
                    result.edges_added,
                    remove_result.removed_count,
                    remove_result.tokens_freed,
                )

                return {
                    "success": True,
                    "nodes_added": result.nodes_added,
                    "edges_added": result.edges_added,
                    "messages_removed": remove_result.removed_count,
                    "tokens_freed": remove_result.tokens_freed,
                    "current_tokens": remove_result.current_tokens,
                    "usage_ratio": remove_result.usage_ratio,
                    "graphize_run": self._total_graphize_runs,
                }
            else:
                logger.error(
                    "[SessionHistory] Graphization failed: %s", result.error
                )
                raise RuntimeError(f"Graphization failed: {result.error}")

        finally:
            self._graphize_in_progress = False

    @property
    def stats(self) -> Dict[str, Any]:
        """Get session history statistics."""
        return {
            "world_id": self.world_id,
            "session_id": self.session_id,
            "message_count": self._window.message_count,
            "current_tokens": self._window.current_tokens,
            "usage_ratio": self._window.usage_ratio,
            "total_graphize_runs": self._total_graphize_runs,
            "graphize_in_progress": self._graphize_in_progress,
        }


class SessionHistoryManager:
    """
    Manages SessionHistory instances across sessions.

    Singleton pattern — one manager per coordinator.
    """

    def __init__(
        self,
        firestore_db: Any = None,
        max_tokens: int = 1_000_000,
        graphize_threshold: float = 0.9,
        keep_recent_tokens: int = 100_000,
    ) -> None:
        self._histories: Dict[str, SessionHistory] = {}
        self._firestore_db = firestore_db
        self._max_tokens = max_tokens
        self._graphize_threshold = graphize_threshold
        self._keep_recent_tokens = keep_recent_tokens

    def get_or_create(
        self,
        world_id: str,
        session_id: str,
    ) -> SessionHistory:
        """Get existing or create new SessionHistory for a session."""
        key = f"{world_id}:{session_id}"
        if key not in self._histories:
            history = SessionHistory(
                world_id=world_id,
                session_id=session_id,
                max_tokens=self._max_tokens,
                graphize_threshold=self._graphize_threshold,
                keep_recent_tokens=self._keep_recent_tokens,
                firestore_db=self._firestore_db,
            )
            # Try to restore from Firestore on first access
            if self._firestore_db:
                try:
                    self._restore_from_firestore_sync(history)
                except Exception as exc:
                    logger.debug("[SessionHistoryManager] Firestore restore failed: %s", exc)
            self._histories[key] = history
        return self._histories[key]

    def _restore_from_firestore_sync(self, history: SessionHistory) -> None:
        """Restore messages from Firestore into the ContextWindow."""
        col_path = _firestore_messages_path(history.world_id, history.session_id)
        col_ref = self._firestore_db.collection(col_path)
        docs = col_ref.order_by("timestamp").stream()

        count = 0
        for doc in docs:
            data = doc.to_dict()
            if not data:
                continue
            role = data.get("role", "system")
            content = data.get("content", "")
            metadata = data.get("metadata") or {}
            if content:
                history._window.add_message(
                    role=role,
                    content=content,
                    metadata=metadata,
                )
                count += 1

        if count > 0:
            logger.info(
                "[SessionHistoryManager] Restored %d messages from Firestore for %s:%s",
                count, history.world_id, history.session_id,
            )

    async def load_history_from_firestore(
        self,
        world_id: str,
        session_id: str,
        limit: int = 50,
        firestore_db: Any = None,
    ) -> List[Dict[str, Any]]:
        """Load message history from Firestore for API response."""
        db = firestore_db or self._firestore_db
        if not db:
            return []

        col_path = _firestore_messages_path(world_id, session_id)
        col_ref = db.collection(col_path)

        # Get recent messages ordered by timestamp descending, then reverse
        query = col_ref.order_by("timestamp", direction=firestore_lib.Query.DESCENDING).limit(limit)

        messages = []
        for doc in query.stream():
            data = doc.to_dict()
            if not data:
                continue
            messages.append(
                _normalize_message_for_api(
                    role=data.get("role", "system"),
                    content=data.get("content", ""),
                    metadata=data.get("metadata") or {},
                    timestamp=data.get("timestamp"),
                )
            )

        messages.reverse()  # oldest first
        return messages

    def get(self, world_id: str, session_id: str) -> Optional[SessionHistory]:
        """Get SessionHistory if it exists, None otherwise."""
        key = f"{world_id}:{session_id}"
        return self._histories.get(key)

    def remove(self, world_id: str, session_id: str) -> None:
        """Remove a session's history (e.g., on session end)."""
        key = f"{world_id}:{session_id}"
        self._histories.pop(key, None)

    @property
    def active_count(self) -> int:
        """Number of active session histories."""
        return len(self._histories)
