"""
上下文循环测试
"""
import pytest
from app.services.context_loop import ContextLoop
from app.services.firestore_service import FirestoreService
from app.models import MessageCreate, MessageRole, TopicCreate


class TestContextLoop:
    """上下文循环测试"""
    
    def test_parse_and_strip_context_request(self):
        """测试：解析与移除 NEED_CONTEXT 标记"""
        loop = ContextLoop()
        response = "这里是回复 [NEED_CONTEXT: 列表推导式] 请补充"
        
        keyword = loop.parse_context_request(response)
        assert keyword == "列表推导式"
        
        stripped = loop.strip_context_request(response)
        assert "[NEED_CONTEXT" not in stripped
    
    @pytest.mark.asyncio
    async def test_resolve_context_request(self):
        """测试：根据关键词加载历史消息"""
        firestore = FirestoreService()
        loop = ContextLoop()
        user_id = "test_user_context_002"
        
        session = await firestore.create_session(user_id)
        session_id = session.session_id
        
        msg1 = MessageCreate(
            role=MessageRole.USER,
            content="列表推导式怎么写？",
            is_archived=False,
        )
        msg2 = MessageCreate(
            role=MessageRole.ASSISTANT,
            content="使用 [expr for x in iterable]",
            is_archived=False,
        )
        
        msg1_id = await firestore.add_message(user_id, session_id, msg1)
        msg2_id = await firestore.add_message(user_id, session_id, msg2)
        
        artifact = (
            "# Python编程\n\n"
            f"## 列表推导式 <!-- sources: {msg1_id}, {msg2_id} -->\n"
            "基本语法说明\n"
        )
        
        await firestore.create_topic(
            user_id,
            session_id,
            TopicCreate(
                title="Python编程",
                summary="记录列表推导式",
                current_artifact=artifact,
            ),
        )
        
        messages = await loop.resolve_context_request(
            user_id, session_id, "列表推导式"
        )
        
        assert len(messages) == 2
