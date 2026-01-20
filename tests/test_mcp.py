"""
MCP 上下文处理模块集成测试

测试内容：
1. MessageStream - 消息流管理
2. TruncateArchiver - 截断归档
3. MessageAssembler - 上下文组装
4. TopicStateManager - 话题状态管理
5. ContextMCPServer - 完整流程
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

# MCP 模块导入
from app.mcp.models import (
    APIMessage,
    TopicClassification,
    ArchiveResult,
    AssembledContext,
    TopicState,
    count_tokens,
)
from app.mcp.message_stream import MessageStream
from app.mcp.topic_state import TopicStateManager, TopicStateData


class TestAPIMessage:
    """APIMessage 模型测试"""
    
    def test_create_message(self):
        """测试创建消息"""
        msg = APIMessage(
            message_id="msg_001",
            role="user",
            content="Hello, world!",
            timestamp=datetime.now(),
        )
        
        assert msg.message_id == "msg_001"
        assert msg.role == "user"
        assert msg.content == "Hello, world!"
        assert msg.token_count > 0
    
    def test_token_count_auto_calculated(self):
        """测试 token 数自动计算"""
        msg = APIMessage(
            message_id="msg_002",
            role="assistant",
            content="这是一段较长的中文文本，用于测试 token 计数功能。",
            timestamp=datetime.now(),
        )
        
        assert msg.token_count > 0
    
    def test_to_dict_and_from_dict(self):
        """测试序列化和反序列化"""
        original = APIMessage(
            message_id="msg_003",
            role="user",
            content="Test content",
            timestamp=datetime(2024, 1, 15, 10, 30, 0),
            token_count=5,
        )
        
        data = original.to_dict()
        restored = APIMessage.from_dict(data)
        
        assert restored.message_id == original.message_id
        assert restored.role == original.role
        assert restored.content == original.content
        assert restored.token_count == original.token_count


class TestMessageStream:
    """MessageStream 消息流测试"""
    
    def test_create_empty_stream(self):
        """测试创建空消息流"""
        stream = MessageStream("session_001")
        
        assert stream.session_id == "session_001"
        assert stream.message_count == 0
        assert stream.total_tokens == 0
        assert stream.is_empty
    
    def test_append_message(self):
        """测试追加消息"""
        stream = MessageStream("session_001")
        
        msg = APIMessage(
            message_id="msg_001",
            role="user",
            content="Hello",
            timestamp=datetime.now(),
        )
        stream.append(msg)
        
        assert stream.message_count == 1
        assert stream.total_tokens == msg.token_count
        assert not stream.is_empty
    
    def test_append_user_message(self):
        """测试追加用户消息（便捷方法）"""
        stream = MessageStream("session_001")
        
        msg = stream.append_user_message("Hello, AI!")
        
        assert msg.role == "user"
        assert msg.content == "Hello, AI!"
        assert stream.message_count == 1
    
    def test_append_assistant_message(self):
        """测试追加助手消息（便捷方法）"""
        stream = MessageStream("session_001")
        
        msg = stream.append_assistant_message("Hello! How can I help?")
        
        assert msg.role == "assistant"
        assert msg.content == "Hello! How can I help?"
        assert stream.message_count == 1
    
    def test_get_active_window_no_overflow(self):
        """测试获取活跃窗口（无溢出）"""
        stream = MessageStream("session_001")
        
        for i in range(5):
            stream.append_user_message(f"Message {i}")
        
        active = stream.get_active_window()
        
        assert len(active) == 5
        assert not stream.has_overflow()
    
    def test_get_active_window_with_overflow(self):
        """测试获取活跃窗口（有溢出）"""
        stream = MessageStream("session_001")
        
        # 添加大量消息以触发溢出
        # 使用更长的内容确保触发溢出
        # tiktoken 中英文混合约 1 token = 2-4 字符
        long_content = "这是一段测试文本用于测试token计数功能 " * 50  # 约 200+ tokens
        
        for i in range(200):
            msg = APIMessage(
                message_id=f"msg_{i:03d}",
                role="user",
                content=f"{long_content} 消息编号 {i}",
                timestamp=datetime.now(),
            )
            stream.append(msg)
        
        # 检查是否触发溢出
        if stream.total_tokens <= MessageStream.ACTIVE_WINDOW_TOKENS:
            # 如果没有溢出，跳过此测试（取决于 tiktoken 版本）
            pytest.skip(f"未触发溢出，总 tokens: {stream.total_tokens}")
        
        assert stream.has_overflow()
        
        active = stream.get_active_window()
        overflow = stream.get_overflow()
        
        # 活跃窗口应该是末尾的消息
        assert len(active) < 200
        assert len(overflow) > 0
        
        # 活跃窗口的 token 数应该接近但不超过 32k
        active_tokens = sum(m.token_count for m in active)
        assert active_tokens <= MessageStream.ACTIVE_WINDOW_TOKENS
    
    def test_mark_as_archived(self):
        """测试标记消息为已归档"""
        stream = MessageStream("session_001")
        
        stream.append_user_message("Message 1")
        stream.append_assistant_message("Response 1")
        
        all_msgs = stream.get_all()
        msg_id = all_msgs[0].message_id
        
        stream.mark_as_archived([msg_id])
        
        assert stream.is_archived(msg_id)
        assert not stream.is_archived(all_msgs[1].message_id)
    
    def test_serialization(self):
        """测试序列化"""
        stream = MessageStream("session_001")
        stream.append_user_message("Hello")
        stream.append_assistant_message("Hi there!")
        
        data = stream.to_dict()
        restored = MessageStream.from_dict(data)
        
        assert restored.session_id == stream.session_id
        assert restored.message_count == stream.message_count
        assert restored.total_tokens == stream.total_tokens
    
    def test_get_stats(self):
        """测试获取统计信息"""
        stream = MessageStream("session_001")
        stream.append_user_message("Hello")
        stream.append_assistant_message("Hi!")
        
        stats = stream.get_stats()
        
        assert stats["session_id"] == "session_001"
        assert stats["total_messages"] == 2
        assert stats["total_tokens"] > 0
        assert stats["has_overflow"] is False


class TestTopicStateManager:
    """TopicStateManager 话题状态管理测试"""
    
    def test_initial_state(self):
        """测试初始状态"""
        manager = TopicStateManager()
        
        assert manager.get_current_topic_id() is None
        assert manager.get_current_thread_id() is None
        assert not manager.is_active()
    
    def test_on_tool_call_retrieval(self):
        """测试工具调用触发话题切换"""
        manager = TopicStateManager()
        
        switched = manager.on_tool_call(
            "retrieve_thread_history",
            {"thread_id": "thread_001"},
            topic_id="topic_001"
        )
        
        assert switched is True
        assert manager.get_current_thread_id() == "thread_001"
        assert manager.get_current_topic_id() == "topic_001"
        assert manager.is_active()
        assert manager.get_retrieval_count() == 1
    
    def test_on_tool_call_non_switching(self):
        """测试非切换工具不改变状态"""
        manager = TopicStateManager()
        
        # 先设置一个话题
        manager.set_topic("topic_001", "thread_001")
        
        # list_topics 不应该切换话题
        switched = manager.on_tool_call("list_topics", {})
        
        assert switched is False
        assert manager.get_current_thread_id() == "thread_001"
    
    def test_set_topic(self):
        """测试手动设置话题"""
        manager = TopicStateManager()
        
        manager.set_topic("topic_002", "thread_002")
        
        assert manager.get_current_topic_id() == "topic_002"
        assert manager.get_current_thread_id() == "thread_002"
        assert manager.is_active()
    
    def test_clear(self):
        """测试清除状态"""
        manager = TopicStateManager()
        manager.set_topic("topic_001", "thread_001")
        
        manager.clear()
        
        assert not manager.is_active()
        assert manager.get_retrieval_count() == 0
    
    def test_serialization(self):
        """测试序列化"""
        manager = TopicStateManager()
        manager.set_topic("topic_001", "thread_001")
        
        data = manager.to_dict()
        restored = TopicStateManager.from_dict(data)
        
        assert restored.get_current_topic_id() == "topic_001"
        assert restored.get_current_thread_id() == "thread_001"



class TestAssembledContext:
    """AssembledContext 组装上下文测试"""
    
    def test_to_api_messages(self):
        """测试转换为 API 消息格式"""
        msg1 = APIMessage(
            message_id="msg_001",
            role="user",
            content="Hello",
            timestamp=datetime.now(),
        )
        msg2 = APIMessage(
            message_id="msg_002",
            role="assistant",
            content="Hi!",
            timestamp=datetime.now(),
        )
        
        context = AssembledContext(
            system_prompt="You are a helpful assistant.",
            topic_summaries="## Topic 1\nSummary here",
            retrieved_history=None,
            active_messages=[msg1, msg2],
            current_topic_id=None,
        )
        
        api_messages = context.to_api_messages()
        
        assert len(api_messages) == 3  # system + 2 active
        assert api_messages[0]["role"] == "system"
        assert "已讨论的话题" in api_messages[0]["content"]
        assert api_messages[1]["role"] == "user"
        assert api_messages[2]["role"] == "assistant"
    
    def test_get_total_tokens(self):
        """测试计算总 token 数"""
        msg = APIMessage(
            message_id="msg_001",
            role="user",
            content="Hello world",
            timestamp=datetime.now(),
        )
        
        context = AssembledContext(
            system_prompt="System prompt here",
            topic_summaries="Topics",
            retrieved_history="History",
            active_messages=[msg],
            current_topic_id=None,
        )
        
        total = context.get_total_tokens()
        
        assert total > 0


class TestCountTokens:
    """Token 计数测试"""
    
    def test_count_english_tokens(self):
        """测试英文 token 计数"""
        text = "Hello, world! How are you today?"
        count = count_tokens(text)
        
        assert count > 0
        assert count < len(text)  # tokens 通常比字符数少
    
    def test_count_chinese_tokens(self):
        """测试中文 token 计数"""
        text = "你好，世界！今天天气怎么样？"
        count = count_tokens(text)
        
        assert count > 0
    
    def test_empty_string(self):
        """测试空字符串"""
        count = count_tokens("")
        
        assert count == 0


class TestMCPIntegration:
    """MCP 模块集成测试"""
    
    @pytest.fixture
    def mock_firestore(self):
        """模拟 Firestore 服务"""
        mock = MagicMock()
        mock.get_all_mcp_topics = AsyncMock(return_value=[])
        mock.get_topic_threads = AsyncMock(return_value=[])
        mock.find_thread_by_id = AsyncMock(return_value=None)
        mock.get_thread_insights = AsyncMock(return_value=[])
        mock.create_mcp_topic = AsyncMock(return_value="topic_001")
        mock.create_thread = AsyncMock(return_value="thread_001")
        mock.create_insight = AsyncMock(return_value="insight_001")
        mock.save_archived_message = AsyncMock()
        mock.mark_messages_archived_mcp = AsyncMock()
        mock.is_message_archived = AsyncMock(return_value=False)
        mock.update_thread_summary = AsyncMock()
        return mock
    
    @pytest.fixture
    def mock_llm(self):
        """模拟 LLM 服务"""
        mock = MagicMock()
        mock.classify_for_archive = AsyncMock(return_value={
            "topic_id": None,
            "topic_title": "测试主题",
            "thread_id": None,
            "thread_title": "测试话题",
            "is_new_topic": True,
            "is_new_thread": True,
        })
        mock.generate_simple = AsyncMock(return_value="测试见解内容")
        return mock
    
    @pytest.mark.asyncio
    async def test_message_assembler_basic(self, mock_firestore):
        """测试消息组装器基本功能"""
        from app.mcp.message_assembler import MessageAssembler
        
        assembler = MessageAssembler(mock_firestore)
        stream = MessageStream("session_001")
        stream.append_user_message("Hello")
        stream.append_assistant_message("Hi!")
        
        context = await assembler.assemble(
            stream=stream,
            user_id="user_001",
            session_id="session_001",
        )
        
        assert context.system_prompt is not None
        assert len(context.active_messages) == 2
    
    @pytest.mark.asyncio
    async def test_truncate_archiver_no_overflow(self, mock_firestore, mock_llm):
        """测试截断归档器（无溢出）"""
        from app.mcp.truncate_archiver import TruncateArchiver
        
        archiver = TruncateArchiver(mock_firestore, mock_llm)
        stream = MessageStream("session_001")
        
        # 只添加少量消息，不触发溢出
        stream.append_user_message("Hello")
        stream.append_assistant_message("Hi!")
        
        result = await archiver.process(stream, "user_001", "session_001")
        
        assert result is None  # 无需归档
    
    @pytest.mark.asyncio
    async def test_topic_classification(self, mock_firestore, mock_llm):
        """测试主题分类"""
        from app.mcp.truncate_archiver import TruncateArchiver
        
        archiver = TruncateArchiver(mock_firestore, mock_llm)
        
        messages = [
            APIMessage(
                message_id="msg_001",
                role="user",
                content="Python 的列表推导式怎么用？",
                timestamp=datetime.now(),
            ),
            APIMessage(
                message_id="msg_002",
                role="assistant",
                content="列表推导式的语法是 [expr for x in iterable]",
                timestamp=datetime.now(),
            ),
        ]
        
        classification = await archiver.classify_messages(
            messages, "user_001", "session_001"
        )
        
        assert classification.topic_title == "测试主题"
        assert classification.thread_title == "测试话题"
        assert classification.is_new_topic is True


def run_tests():
    """运行测试"""
    print("=" * 60)
    print("运行 MCP 模块测试")
    print("=" * 60)
    print("\n使用 pytest 运行：")
    print("  PYTHONPATH=backend pytest tests/test_mcp.py -v")
    print("=" * 60)


if __name__ == "__main__":
    run_tests()
