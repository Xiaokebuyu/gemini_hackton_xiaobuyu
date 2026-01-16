"""
端到端集成测试
"""
import pytest
from app.config import settings
from app.services.firestore_service import FirestoreService
from app.services.archive_service import ArchiveService
from app.services.context_builder import ContextBuilder
from app.services.context_loop import ContextLoop
from app.models import MessageCreate, MessageRole, TopicCreate


class TestEndToEnd:
    """端到端测试"""
    
    @pytest.fixture
    def setup_services(self):
        """设置所有服务"""
        firestore = FirestoreService()
        archive = ArchiveService()
        context_builder = ContextBuilder()
        context_loop = ContextLoop()
        
        return {
            "firestore": firestore,
            "archive": archive,
            "context_builder": context_builder,
            "context_loop": context_loop,
        }
    
    @pytest.mark.asyncio
    async def test_archive_and_context_flow(self, setup_services):
        """测试：归档 + 上下文构建"""
        services = setup_services
        user_id = "test_user_e2e_archive_001"
        
        session = await services["firestore"].create_session(user_id)
        session_id = session.session_id
        
        original_threshold = settings.archive_threshold
        original_window = settings.active_window_size
        settings.archive_threshold = 4
        settings.active_window_size = 2
        
        try:
            for i in range(4):
                role = MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT
                msg = MessageCreate(
                    role=role,
                    content=f"测试消息 {i}",
                    is_archived=False,
                )
                await services["firestore"].add_message(user_id, session_id, msg)
            
            should_archive = await services["archive"].check_should_archive(
                user_id, session_id
            )
            assert should_archive is True
            
            thread_id = await services["archive"].execute_archive(
                user_id, session_id
            )
            assert thread_id is not None
            
            messages = await services["firestore"].get_messages_by_session(
                user_id, session_id
            )
            archived_count = sum(1 for msg in messages if msg.is_archived)
            assert archived_count > 0
            
            context_text = await services["context_builder"].build(
                user_id, session_id
            )
            assert "知识文档" in context_text
            assert "最近对话" in context_text
        finally:
            settings.archive_threshold = original_threshold
            settings.active_window_size = original_window
    
    @pytest.mark.asyncio
    async def test_context_request_resolution(self, setup_services):
        """测试：上下文请求解析"""
        services = setup_services
        user_id = "test_user_context_001"
        
        session = await services["firestore"].create_session(user_id)
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
        
        msg1_id = await services["firestore"].add_message(user_id, session_id, msg1)
        msg2_id = await services["firestore"].add_message(user_id, session_id, msg2)
        
        artifact = (
            "# Python编程\n\n"
            f"## 列表推导式 <!-- sources: {msg1_id}, {msg2_id} -->\n"
            "基本语法说明\n"
        )
        
        thread_id = await services["firestore"].create_topic(
            user_id,
            session_id,
            TopicCreate(
                title="Python编程",
                summary="记录列表推导式",
                current_artifact=artifact,
            ),
        )
        assert thread_id is not None
        
        messages = await services["context_loop"].resolve_context_request(
            user_id, session_id, "列表推导式"
        )
        
        assert len(messages) == 2


class TestDataPersistence:
    """数据持久化测试"""
    
    @pytest.mark.asyncio
    async def test_message_storage_and_retrieval(self):
        """测试：消息存储和检索"""
        firestore = FirestoreService()
        user_id = "test_user_persist_001"
        
        session = await firestore.create_session(user_id)
        session_id = session.session_id
        
        messages_to_save = [
            MessageCreate(
                role=MessageRole.USER,
                content="你好",
                is_archived=False,
            ),
            MessageCreate(
                role=MessageRole.ASSISTANT,
                content="你好！有什么可以帮助你的吗？",
                is_archived=False,
            ),
            MessageCreate(
                role=MessageRole.USER,
                content="介绍一下Python",
                is_archived=False,
            ),
        ]
        
        message_ids = []
        for msg in messages_to_save:
            msg_id = await firestore.add_message(user_id, session_id, msg)
            message_ids.append(msg_id)
        
        retrieved = await firestore.get_messages_by_session(user_id, session_id)
        assert len(retrieved) >= len(messages_to_save)
        
        subset = await firestore.get_messages_by_ids(
            user_id, session_id, message_ids[:2]
        )
        assert len(subset) == 2


def run_tests():
    """运行测试"""
    print("=" * 60)
    print("运行集成测试")
    print("=" * 60)
    print("\n这些测试需要：")
    print("1. 有效的 Firebase 配置")
    print("2. 有效的 Gemini API 密钥")
    print("\n建议使用 pytest 运行：")
    print("  cd backend")
    print("  pytest tests/test_integration.py -v -s")
    print("=" * 60)


if __name__ == "__main__":
    run_tests()
