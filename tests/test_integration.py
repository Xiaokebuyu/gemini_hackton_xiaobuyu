"""
端到端集成测试 - 基于 MCP 架构
"""
import pytest
from app.config import settings
from app.services.firestore_service import FirestoreService
from app.services.llm_service import LLMService
from app.models import MessageCreate, MessageRole, TopicCreate
from app.mcp import (
    MessageStream,
    MessageAssembler,
    TruncateArchiver,
    TopicStateManager,
    get_mcp_server,
)


class TestMCPEndToEnd:
    """MCP 端到端测试"""
    
    @pytest.fixture
    def setup_services(self):
        """设置所有服务"""
        firestore = FirestoreService()
        llm = LLMService()
        
        return {
            "firestore": firestore,
            "llm": llm,
        }
    
    @pytest.mark.asyncio
    async def test_message_stream_basic(self, setup_services):
        """测试：消息流基本功能"""
        stream = MessageStream("test_session_001")
        
        # 添加消息
        stream.append_user_message("你好")
        stream.append_assistant_message("你好！有什么可以帮助你的吗？")
        
        assert stream.message_count == 2
        assert stream.total_tokens > 0
        
        # 获取活跃窗口
        active = stream.get_active_window()
        assert len(active) == 2
    
    @pytest.mark.asyncio
    async def test_topic_state_management(self, setup_services):
        """测试：话题状态管理"""
        state_manager = TopicStateManager()
        
        # 初始状态
        assert not state_manager.is_active()
        
        # 模拟工具调用
        switched = state_manager.on_tool_call(
            "retrieve_thread_history",
            {"thread_id": "thread_001"},
            topic_id="topic_001"
        )
        
        assert switched is True
        assert state_manager.is_active()
        assert state_manager.get_current_thread_id() == "thread_001"
    
    @pytest.mark.asyncio
    async def test_firestore_thread_operations(self, setup_services):
        """测试：Firestore 话题操作"""
        firestore = setup_services["firestore"]
        user_id = "test_user_mcp_001"
        
        # 创建会话
        session = await firestore.create_session(user_id)
        session_id = session.session_id
        
        # 创建主题
        topic_id = await firestore.create_mcp_topic(
            user_id=user_id,
            session_id=session_id,
            topic_id="topic_test_001",
            title="测试主题",
            summary="这是一个测试主题"
        )
        assert topic_id == "topic_test_001"
        
        # 创建话题
        thread_id = await firestore.create_thread(
            user_id=user_id,
            session_id=session_id,
            topic_id=topic_id,
            thread_id="thread_test_001",
            title="测试话题",
            summary="这是一个测试话题"
        )
        assert thread_id == "thread_test_001"
        
        # 创建见解
        insight_id = await firestore.create_insight(
            user_id=user_id,
            session_id=session_id,
            topic_id=topic_id,
            thread_id=thread_id,
            insight_id="insight_test_001",
            version=1,
            content="这是第一个见解内容",
            source_message_ids=["msg_001", "msg_002"],
            evolution_note=""
        )
        assert insight_id == "insight_test_001"
        
        # 验证数据
        topics = await firestore.get_all_mcp_topics(user_id, session_id)
        assert len(topics) >= 1
        
        threads = await firestore.get_topic_threads(user_id, session_id, topic_id)
        assert len(threads) >= 1
        
        insights = await firestore.get_thread_insights(
            user_id, session_id, topic_id, thread_id
        )
        assert len(insights) >= 1


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


class TestMCPServer:
    """MCP Server 测试"""
    
    @pytest.mark.asyncio
    async def test_get_mcp_server_singleton(self):
        """测试：MCP Server 单例"""
        server1 = get_mcp_server()
        server2 = get_mcp_server()
        
        assert server1 is server2
    
    @pytest.mark.asyncio
    async def test_mcp_server_session_management(self):
        """测试：MCP Server 会话管理"""
        server = get_mcp_server()
        
        # 获取会话信息（不存在的会话）
        info = server.get_session_info("nonexistent_session")
        assert info["has_stream"] is False
        
        # 创建消息流
        stream = server._get_or_create_stream("test_session_002")
        assert stream is not None
        
        # 再次获取会话信息
        info = server.get_session_info("test_session_002")
        assert info["has_stream"] is True
        
        # 清除会话
        server.clear_session("test_session_002")
        info = server.get_session_info("test_session_002")
        assert info["has_stream"] is False


def run_tests():
    """运行测试"""
    print("=" * 60)
    print("运行 MCP 集成测试")
    print("=" * 60)
    print("\n这些测试需要：")
    print("1. 有效的 Firebase 配置")
    print("2. 有效的 Gemini API 密钥")
    print("\n建议使用 pytest 运行：")
    print("  cd /home/xiaokebuyu/workplace/gemini-hackton")
    print("  PYTHONPATH=backend pytest tests/test_integration.py -v -s")
    print("=" * 60)


if __name__ == "__main__":
    run_tests()
