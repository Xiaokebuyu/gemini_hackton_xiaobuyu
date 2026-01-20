"""
Memory gateway tests.
"""
from datetime import datetime

import pytest

from app.mcp.memory_gateway import MemoryGateway
from app.models import Message, MessageRole


class FakeFirestore:
    async def get_all_mcp_topics(self, user_id, session_id):
        return [{"topic_id": "t1", "title": "Topic 1", "summary": "Summary 1"}]

    async def get_topic_threads(self, user_id, session_id, topic_id):
        return [{"thread_id": "th1", "title": "Thread 1", "summary": "Thread summary"}]

    async def get_latest_insight(self, user_id, session_id, topic_id, thread_id):
        return {"insight_id": "i1", "content": "Insight content", "embedding": [0.1, 0.2]}

    async def update_insight_embedding(self, user_id, session_id, topic_id, thread_id, insight_id, embedding):
        return None

    async def get_archived_messages_by_thread(self, user_id, session_id, thread_id):
        return [{"message_id": "m1", "role": "user", "content": "Old message"}]

    async def get_session_state(self, user_id, session_id):
        return {}

    async def update_session_state(self, user_id, session_id, state):
        return None

    async def get_recent_messages(self, user_id, session_id, limit=2000):
        return [
            Message(
                message_id="m1",
                role=MessageRole.USER,
                content="Hello",
                timestamp=datetime.now(),
                is_archived=False,
                token_count=1,
            ),
            Message(
                message_id="m2",
                role=MessageRole.ASSISTANT,
                content="Hi",
                timestamp=datetime.now(),
                is_archived=False,
                token_count=1,
            ),
        ]

    async def add_message(self, user_id, session_id, message, message_id=None):
        return message_id or "msg_auto"

    async def update_session_timestamp(self, user_id, session_id):
        return None

    async def get_message_by_id(self, user_id, session_id, message_id):
        return None


class FakeLLM:
    async def generate_json(self, prompt):
        return {
            "keywords": ["memory", "summary"],
            "include_raw": True,
            "max_threads": 1,
            "max_raw_messages": 2,
            "scope": "current_session",
        }

    async def generate_simple(self, prompt):
        return "Summary result"


class FakeEmbedding:
    async def embed_text(self, text):
        return [0.1, 0.2]


class TestGateway(MemoryGateway):
    async def _schedule_archive(self, user_id, session_id, stream):
        return None


@pytest.mark.asyncio
async def test_memory_request_basic():
    gateway = TestGateway(FakeFirestore(), FakeLLM(), FakeEmbedding())
    result = await gateway.memory_request(
        user_id="u1",
        session_id="s1",
        need="Find memory summary",
    )
    assert result["context"]["retrieved_memory_summary"] == "Summary result"
    assert result["insert_messages"]


@pytest.mark.asyncio
async def test_session_snapshot_basic():
    gateway = TestGateway(FakeFirestore(), FakeLLM(), FakeEmbedding())
    result = await gateway.session_snapshot(user_id="u1", session_id="s1")
    assert result["context"]["current_window_messages"]
    assert result["assembled_messages"][0]["role"] == "system"


@pytest.mark.asyncio
async def test_memory_commit_basic():
    gateway = TestGateway(FakeFirestore(), FakeLLM(), FakeEmbedding())
    result = await gateway.memory_commit(
        user_id="u1",
        session_id="s1",
        messages=[
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ],
    )
    assert len(result["stored_message_ids"]) == 2
