"""
Memory gateway for MCP hot memory orchestration.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.legacy_mcp.models import APIMessage, count_tokens
from app.legacy_mcp.message_stream import MessageStream
from app.legacy_mcp.firestore_service import FirestoreService
from app.services.llm_service import LLMService
from app.legacy_mcp.embedding_service import EmbeddingService
from app.legacy_mcp.embedding import cosine_similarity


@dataclass
class MemoryRoute:
    keywords: List[str]
    include_raw: bool
    max_threads: int
    max_raw_messages: int
    scope: str


class SessionContextStore:
    """In-memory session cache with persistence fallback."""

    def __init__(self, firestore: FirestoreService):
        self.firestore = firestore
        self.streams: Dict[str, MessageStream] = {}
        self.insert_messages: Dict[str, List[Dict[str, str]]] = {}
        self.last_access: Dict[str, datetime] = {}
        self.locks: Dict[str, asyncio.Lock] = {}
        self.archive_locks: Dict[str, asyncio.Lock] = {}
        self.archive_pending: Dict[str, bool] = {}

    def _get_lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self.locks:
            self.locks[session_id] = asyncio.Lock()
        return self.locks[session_id]

    def _get_archive_lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self.archive_locks:
            self.archive_locks[session_id] = asyncio.Lock()
        return self.archive_locks[session_id]

    def _touch(self, session_id: str) -> None:
        self.last_access[session_id] = datetime.now()

    def _is_expired(self, session_id: str) -> bool:
        ttl = timedelta(seconds=settings.memory_session_ttl_seconds)
        last = self.last_access.get(session_id)
        if not last:
            return False
        return datetime.now() - last > ttl

    async def get_stream(
        self,
        user_id: str,
        session_id: str,
        window_tokens: int,
    ) -> MessageStream:
        if session_id in self.streams and not self._is_expired(session_id):
            stream = self.streams[session_id]
            stream.active_window_tokens = window_tokens
            self._touch(session_id)
            return stream

        messages = await self.firestore.get_recent_messages(
            user_id,
            session_id,
            limit=settings.memory_stream_load_limit,
        )
        stream = MessageStream(session_id, active_window_tokens=window_tokens)
        for msg in reversed(messages):
            token_count = msg.token_count or count_tokens(msg.content)
            api_msg = APIMessage(
                message_id=msg.message_id,
                role=msg.role.value,
                content=msg.content,
                timestamp=msg.timestamp,
                token_count=token_count,
            )
            stream.append(api_msg)
            if msg.is_archived:
                stream.mark_as_archived([msg.message_id])

        self.streams[session_id] = stream
        self._touch(session_id)
        return stream

    async def get_insert_messages(
        self, user_id: str, session_id: str
    ) -> List[Dict[str, str]]:
        if session_id in self.insert_messages and not self._is_expired(session_id):
            self._touch(session_id)
            return self.insert_messages[session_id]

        state = await self.firestore.get_session_state(user_id, session_id)
        insert_messages = state.get("insert_context_messages") or []
        self.insert_messages[session_id] = insert_messages
        self._touch(session_id)
        return insert_messages

    async def set_insert_messages(
        self,
        user_id: str,
        session_id: str,
        insert_messages: List[Dict[str, str]],
    ) -> None:
        self.insert_messages[session_id] = insert_messages
        self._touch(session_id)
        await self.firestore.update_session_state(
            user_id,
            session_id,
            {
                "insert_context_messages": insert_messages,
                "insert_context_updated_at": datetime.now().isoformat(),
            },
        )


class MemoryRouter:
    """Route natural language memory needs into retrieval hints."""

    def __init__(self, llm: LLMService):
        self.llm = llm

    async def route(self, need: str) -> MemoryRoute:
        prompt = f"""You are a memory router. Convert the request into JSON only.

Request:
{need}

Return JSON:
{{
  "keywords": ["keyword1", "keyword2"],
  "include_raw": true,
  "max_threads": {settings.memory_max_threads},
  "max_raw_messages": {settings.memory_max_raw_messages},
  "scope": "current_session"
}}"""
        result = await self.llm.generate_json(prompt)
        if isinstance(result, dict):
            keywords = _normalize_keywords(result.get("keywords"))
            return MemoryRoute(
                keywords=keywords or _fallback_keywords(need),
                include_raw=bool(result.get("include_raw", True)),
                max_threads=int(result.get("max_threads", settings.memory_max_threads)),
                max_raw_messages=int(result.get("max_raw_messages", settings.memory_max_raw_messages)),
                scope=str(result.get("scope", "current_session")),
            )

        return MemoryRoute(
            keywords=_fallback_keywords(need),
            include_raw=True,
            max_threads=settings.memory_max_threads,
            max_raw_messages=settings.memory_max_raw_messages,
            scope="current_session",
        )


class MemoryRetriever:
    """Retrieve relevant memory using embeddings and lexical fallback."""

    def __init__(
        self,
        firestore: FirestoreService,
        llm: LLMService,
        embedding: EmbeddingService,
    ):
        self.firestore = firestore
        self.llm = llm
        self.embedding = embedding

    async def retrieve(
        self,
        user_id: str,
        session_id: str,
        route: MemoryRoute,
    ) -> Dict[str, Any]:
        topics = await self.firestore.get_all_mcp_topics(user_id, session_id)
        thread_candidates: List[Dict[str, Any]] = []

        query_embedding = await self.embedding.embed_text(" ".join(route.keywords) or "")

        for topic in topics:
            topic_id = topic.get("topic_id", "")
            topic_title = topic.get("title", "")
            threads = await self.firestore.get_topic_threads(user_id, session_id, topic_id)
            for thread in threads:
                thread_id = thread.get("thread_id", "")
                thread_title = thread.get("title", "")
                thread_summary = thread.get("summary", "")
                latest_insight = await self.firestore.get_latest_insight(
                    user_id, session_id, topic_id, thread_id
                )
                insight_content = latest_insight.get("content", "") if latest_insight else ""
                embedding = latest_insight.get("embedding") if latest_insight else None

                insight_id = latest_insight.get("insight_id", "") if latest_insight else ""
                if not embedding and insight_content:
                    embedding = await self.embedding.embed_text(insight_content)
                    if embedding and insight_id:
                        await self.firestore.update_insight_embedding(
                            user_id,
                            session_id,
                            topic_id,
                            thread_id,
                            insight_id,
                            embedding,
                        )

                score = self._score_thread(
                    route.keywords,
                    thread_title,
                    thread_summary,
                    insight_content,
                    query_embedding,
                    embedding,
                )

                thread_candidates.append({
                    "topic_id": topic_id,
                    "topic_title": topic_title,
                    "thread_id": thread_id,
                    "thread_title": thread_title,
                    "thread_summary": thread_summary,
                    "latest_insight": latest_insight or {},
                    "score": score,
                })

        thread_candidates.sort(key=lambda item: item["score"], reverse=True)
        selected_threads = thread_candidates[: route.max_threads]

        raw_messages = []
        if route.include_raw:
            raw_messages = await self._load_raw_messages(
                user_id, session_id, selected_threads, route.max_raw_messages
            )

        summary = await self._summarize_threads(route.keywords, selected_threads)

        return {
            "matched_threads": [t["thread_id"] for t in selected_threads],
            "thread_scores": {t["thread_id"]: t["score"] for t in selected_threads},
            "summary": summary,
            "raw_messages": raw_messages,
            "threads": selected_threads,
        }

    def _score_thread(
        self,
        keywords: List[str],
        thread_title: str,
        thread_summary: str,
        insight_content: str,
        query_embedding: List[float],
        insight_embedding: Optional[List[float]],
    ) -> float:
        lexical = _lexical_score(keywords, " ".join([thread_title, thread_summary, insight_content]))
        if query_embedding and insight_embedding:
            try:
                similarity = cosine_similarity(query_embedding, insight_embedding)
            except Exception:
                similarity = 0.0
            return similarity + (lexical * 0.1)
        return lexical

    async def _summarize_threads(
        self,
        keywords: List[str],
        threads: List[Dict[str, Any]],
    ) -> str:
        if not threads:
            return "No matching memory found."

        parts = []
        for thread in threads:
            insight = thread.get("latest_insight", {})
            parts.append(
                f"Topic: {thread.get('topic_title', '')}\n"
                f"Thread: {thread.get('thread_title', '')}\n"
                f"Summary: {thread.get('thread_summary', '')}\n"
                f"Insight: {insight.get('content', '')}"
            )
        prompt = f"""Summarize the following memory for the user request.
Keywords: {', '.join(keywords)}

Memory:
{'\n\n'.join(parts)}

Return a concise summary."""
        result = await self.llm.generate_simple(prompt)
        return result.strip() if result else "\n".join(parts)

    async def _load_raw_messages(
        self,
        user_id: str,
        session_id: str,
        threads: List[Dict[str, Any]],
        limit: int,
    ) -> List[Dict[str, Any]]:
        raw_messages: List[Dict[str, Any]] = []
        for thread in threads:
            thread_id = thread.get("thread_id", "")
            if not thread_id:
                continue
            archived = await self.firestore.get_archived_messages_by_thread(
                user_id, session_id, thread_id
            )
            for msg in archived:
                raw_messages.append(msg)
                if len(raw_messages) >= limit:
                    return raw_messages
        return raw_messages


class MemoryGateway:
    """Public entry for memory_request/session_snapshot/memory_commit."""

    def __init__(
        self,
        firestore: FirestoreService,
        llm: LLMService,
        embedding: EmbeddingService,
    ):
        self.firestore = firestore
        self.llm = llm
        self.embedding = embedding
        self.store = SessionContextStore(firestore)
        self.router = MemoryRouter(llm)
        self.retriever = MemoryRetriever(firestore, llm, embedding)

    async def session_snapshot(
        self,
        user_id: str,
        session_id: str,
        window_tokens: Optional[int] = None,
        insert_budget_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        window_tokens = window_tokens or settings.memory_window_tokens
        insert_budget_tokens = insert_budget_tokens or settings.memory_insert_budget_tokens

        lock = self.store._get_lock(session_id)
        async with lock:
            stream = await self.store.get_stream(user_id, session_id, window_tokens)
            insert_messages = await self.store.get_insert_messages(user_id, session_id)

        current_window = [
            {
                "message_id": msg.message_id,
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
            }
            for msg in stream.get_active_window()
        ]
        window_messages = [{"role": msg["role"], "content": msg["content"]} for msg in current_window]

        topic_summaries = await _build_topic_summaries(self.firestore, user_id, session_id)

        insert_messages_trimmed, insert_tokens = _trim_insert_messages(
            insert_messages, insert_budget_tokens
        )

        assembled_messages = [
            {"role": "system", "content": _build_system_prompt()},
            *insert_messages_trimmed,
            *window_messages,
        ]

        return {
            "session_id": session_id,
            "context": {
                "system_message": {"role": "system", "content": _build_system_prompt()},
                "current_window_messages": current_window,
                "current_session_topic_summaries": topic_summaries,
                "retrieved_memory_summary": "",
                "retrieved_raw_messages": [],
                "other_sessions_topic_summaries": {"status": "todo", "data": []},
            },
            "insert_messages": insert_messages_trimmed,
            "assembled_messages": assembled_messages,
            "trace": {
                "window_tokens": window_tokens,
                "insert_budget_tokens": insert_budget_tokens,
                "insert_tokens": insert_tokens,
                "window_message_count": len(current_window),
            },
        }

    async def memory_request(
        self,
        user_id: str,
        session_id: str,
        need: str,
        user_message: Optional[str] = None,
        window_tokens: Optional[int] = None,
        insert_budget_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        window_tokens = window_tokens or settings.memory_window_tokens
        insert_budget_tokens = insert_budget_tokens or settings.memory_insert_budget_tokens

        route = await self.router.route(need)
        retrieval = await self.retriever.retrieve(user_id, session_id, route)
        topic_summaries = await _build_topic_summaries(self.firestore, user_id, session_id)

        insert_messages = _build_insert_messages(
            topic_summaries,
            retrieval.get("summary", ""),
            retrieval.get("raw_messages", []),
            insert_budget_tokens,
        )

        lock = self.store._get_lock(session_id)
        async with lock:
            await self.store.set_insert_messages(user_id, session_id, insert_messages)
            stream = await self.store.get_stream(user_id, session_id, window_tokens)
        await self._schedule_archive(user_id, session_id, stream)

        assembled_messages = [
            {"role": "system", "content": _build_system_prompt()},
            *insert_messages,
        ]

        return {
            "session_id": session_id,
            "context": {
                "system_message": {"role": "system", "content": _build_system_prompt()},
                "user_message": {"role": "user", "content": user_message} if user_message else None,
                "current_session_topic_summaries": topic_summaries,
                "retrieved_memory_summary": retrieval.get("summary", ""),
                "retrieved_raw_messages": retrieval.get("raw_messages", []),
                "other_sessions_topic_summaries": {"status": "todo", "data": []},
            },
            "insert_messages": insert_messages,
            "assembled_messages": assembled_messages,
            "trace": {
                "route": {
                    "keywords": route.keywords,
                    "include_raw": route.include_raw,
                    "max_threads": route.max_threads,
                    "max_raw_messages": route.max_raw_messages,
                    "scope": route.scope,
                },
                "matched_threads": retrieval.get("matched_threads", []),
                "thread_scores": retrieval.get("thread_scores", {}),
                "window_tokens": window_tokens,
                "insert_budget_tokens": insert_budget_tokens,
            },
        }

    async def memory_commit(
        self,
        user_id: str,
        session_id: str,
        messages: List[Dict[str, Any]],
        window_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        window_tokens = window_tokens or settings.memory_window_tokens
        lock = self.store._get_lock(session_id)
        async with lock:
            stream = await self.store.get_stream(user_id, session_id, window_tokens)
            stored_ids = []
            for msg in messages:
                role = msg.get("role")
                content = msg.get("content", "")
                if not role or not content:
                    continue
                message_id = msg.get("message_id") or _generate_message_id()
                if stream.get_message_by_id(message_id):
                    continue
                existing = await self.firestore.get_message_by_id(
                    user_id, session_id, message_id
                )
                if existing:
                    continue
                token_count = count_tokens(content)
                api_msg = APIMessage(
                    message_id=message_id,
                    role=role,
                    content=content,
                    timestamp=datetime.now(),
                    token_count=token_count,
                )
                stream.append(api_msg)
                stored_ids.append(api_msg.message_id)
                await self.firestore.add_message(
                    user_id,
                    session_id,
                    _to_message_create(api_msg),
                    message_id=api_msg.message_id,
                )
            await self.firestore.update_session_timestamp(user_id, session_id)

        await self._schedule_archive(user_id, session_id, stream)

        return {
            "session_id": session_id,
            "stored_message_ids": stored_ids,
            "stream_stats": stream.get_stats(),
        }

    async def _schedule_archive(
        self,
        user_id: str,
        session_id: str,
        stream: MessageStream,
    ) -> None:
        archive_lock = self.store._get_archive_lock(session_id)
        if archive_lock.locked():
            self.store.archive_pending[session_id] = True
            return

        async def _run():
            async with archive_lock:
                while True:
                    self.store.archive_pending[session_id] = False
                    try:
                        await _archive_stream(self.firestore, self.llm, self.embedding, stream, user_id, session_id)
                    except Exception:
                        pass
                    if not self.store.archive_pending.get(session_id):
                        break

        asyncio.create_task(_run())


async def _archive_stream(
    firestore: FirestoreService,
    llm: LLMService,
    embedding: EmbeddingService,
    stream: MessageStream,
    user_id: str,
    session_id: str,
) -> None:
    from app.legacy_mcp.truncate_archiver import TruncateArchiver

    archiver = TruncateArchiver(firestore, llm, embedding)
    await archiver.process(stream, user_id, session_id)


def _build_system_prompt() -> str:
    return (
        "You are the main assistant. Use memory sections as supplemental context. "
        "If memory conflicts with recent messages, prioritize the recent messages."
    )


async def _build_topic_summaries(
    firestore: FirestoreService,
    user_id: str,
    session_id: str,
) -> str:
    topics = await firestore.get_all_mcp_topics(user_id, session_id)
    if not topics:
        return ""

    summaries = []
    for topic in topics:
        topic_id = topic.get("topic_id")
        topic_title = topic.get("title", "Untitled")
        topic_summary = topic.get("summary", "")
        threads = await firestore.get_topic_threads(user_id, session_id, topic_id) if topic_id else []
        thread_list = ", ".join([
            f"{t.get('title', 'Untitled')} (ID: {t.get('thread_id', '')})"
            for t in threads
        ])
        if thread_list:
            summaries.append(
                f"### {topic_title}\n"
                f"Threads: {thread_list}\n"
                f"Summary: {topic_summary or 'None'}"
            )
        else:
            summaries.append(
                f"### {topic_title}\n"
                f"Summary: {topic_summary or 'None'}"
            )
    return "\n\n".join(summaries)


def _build_insert_messages(
    topic_summaries: str,
    memory_summary: str,
    raw_messages: List[Dict[str, Any]],
    budget_tokens: int,
) -> List[Dict[str, str]]:
    sections: List[Tuple[str, str]] = []
    if topic_summaries:
        sections.append(("Current Session Topics", topic_summaries))
    if memory_summary:
        sections.append(("Retrieved Memory Summary", memory_summary))
    if raw_messages:
        formatted = []
        for msg in raw_messages:
            formatted.append(
                f"[{msg.get('message_id', '')}] {msg.get('role', '')}: {msg.get('content', '')}"
            )
        sections.append(("Retrieved Raw Messages", "\n".join(formatted)))

    messages: List[Dict[str, str]] = []
    used = 0
    for title, content in sections:
        section_text = f"## {title}\n{content}"
        section_tokens = count_tokens(section_text)
        if used + section_tokens > budget_tokens:
            available = max(budget_tokens - used - count_tokens(f"## {title}\n"), 0)
            if available <= 0:
                break
            content = _truncate_to_tokens(content, available)
            section_text = f"## {title}\n{content}"
            section_tokens = count_tokens(section_text)
        messages.append({"role": "system", "content": section_text})
        used += section_tokens
        if used >= budget_tokens:
            break
    return messages


def _trim_insert_messages(
    insert_messages: List[Dict[str, str]],
    budget_tokens: int,
) -> Tuple[List[Dict[str, str]], int]:
    trimmed: List[Dict[str, str]] = []
    used = 0
    for msg in insert_messages:
        content = msg.get("content", "")
        tokens = count_tokens(content)
        if used + tokens > budget_tokens:
            available = max(budget_tokens - used, 0)
            if available <= 0:
                break
            content = _truncate_to_tokens(content, available)
            tokens = count_tokens(content)
        trimmed.append({"role": msg.get("role", "system"), "content": content})
        used += tokens
        if used >= budget_tokens:
            break
    return trimmed, used


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    if max_tokens <= 0:
        return ""
    current_tokens = count_tokens(text)
    if current_tokens <= max_tokens:
        return text
    ratio = max_tokens / max(current_tokens, 1)
    cutoff = max(int(len(text) * ratio), 1)
    return text[:cutoff].rstrip() + "..."


def _fallback_keywords(text: str) -> List[str]:
    words = re.split(r"\W+", text)
    return [w for w in words if len(w) > 1][:6]


def _normalize_keywords(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def _lexical_score(keywords: List[str], text: str) -> float:
    if not keywords or not text:
        return 0.0
    lower = text.lower()
    hits = sum(1 for kw in keywords if kw.lower() in lower)
    return hits / max(len(keywords), 1)


def _generate_message_id() -> str:
    import uuid
    return f"msg_{uuid.uuid4().hex[:12]}"


def _to_message_create(api_msg: APIMessage):
    from app.models import MessageCreate, MessageRole

    if api_msg.role == "user":
        role = MessageRole.USER
    elif api_msg.role == "assistant":
        role = MessageRole.ASSISTANT
    else:
        role = MessageRole.SYSTEM
    return MessageCreate(
        role=role,
        content=api_msg.content,
        is_archived=False,
        token_count=api_msg.token_count,
    )
