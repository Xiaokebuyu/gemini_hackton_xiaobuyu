"""
端到端集成测试
"""
import pytest
from app.services.firestore_service import FirestoreService
from app.services.router_service import RouterService
from app.services.artifact_service import ArtifactService
from app.services.context_builder import ContextBuilder
from app.services.llm_service import LLMService
from app.models import MessageCreate, MessageRole


class TestEndToEnd:
    """端到端测试"""
    
    @pytest.fixture
    def setup_all_services(self):
        """设置所有服务"""
        firestore = FirestoreService()
        router = RouterService()
        artifact = ArtifactService()
        context = ContextBuilder()
        llm = LLMService()
        
        return {
            'firestore': firestore,
            'router': router,
            'artifact': artifact,
            'context': context,
            'llm': llm
        }
    
    @pytest.mark.asyncio
    async def test_complete_conversation_flow(self, setup_all_services):
        """测试：完整对话流程"""
        services = setup_all_services
        user_id = "test_user_e2e_001"
        
        # 1. 创建会话
        session = await services['firestore'].create_session(user_id)
        session_id = session.session_id
        
        print(f"✓ 会话已创建: {session_id}")
        
        # 2. 第一条消息 - 应该创建新主题
        user_input_1 = "Python的列表推导式怎么用?"
        
        thread_id_1, is_new_1 = await services['router'].route_message(
            user_id=user_id,
            user_input=user_input_1,
            session_id=session_id
        )
        
        assert is_new_1 == True
        print(f"✓ 创建新主题: {thread_id_1}")
        
        # 保存消息
        msg1 = MessageCreate(
            role=MessageRole.USER,
            content=user_input_1,
            thread_id=thread_id_1
        )
        await services['firestore'].add_message(user_id, session_id, msg1)
        
        # 3. 第二条消息 - 应该路由到相同主题
        user_input_2 = "列表推导式可以嵌套吗?"
        
        thread_id_2, is_new_2 = await services['router'].route_message(
            user_id=user_id,
            user_input=user_input_2,
            session_id=session_id
        )
        
        print(f"✓ 路由结果: {thread_id_2} (新主题: {is_new_2})")
        
        # 4. 验证消息持久化
        messages = await services['firestore'].get_messages_by_session(
            user_id, session_id
        )
        
        assert len(messages) >= 1
        print(f"✓ 消息已保存: {len(messages)} 条")
        
        # 5. 测试上下文构建
        context_text = await services['context'].build_full_context(
            user_id=user_id,
            session_id=session_id,
            thread_id=thread_id_1,
            user_query=user_input_2
        )
        
        assert context_text is not None
        assert len(context_text) > 0
        print(f"✓ 上下文已构建: {len(context_text)} 字符")
        
        print("\n✓ 端到端测试通过!")
    
    @pytest.mark.asyncio
    async def test_artifact_update_flow(self, setup_all_services):
        """测试：Artifact 更新流程"""
        services = setup_all_services
        user_id = "test_user_artifact_001"
        
        # 创建会话和主题
        session = await services['firestore'].create_session(user_id)
        session_id = session.session_id
        
        # 创建主题
        thread_id, _ = await services['router'].route_message(
            user_id=user_id,
            user_input="教我Python",
            session_id=session_id
        )
        
        # 获取主题
        topic = await services['firestore'].get_topic(user_id, thread_id)
        initial_artifact = topic.current_artifact
        
        print(f"✓ 初始 Artifact 长度: {len(initial_artifact)}")
        
        # 构建对话历史
        conversation = [
            {"role": "user", "content": "Python的列表推导式怎么用?"},
            {"role": "assistant", "content": "列表推导式的基本语法是 [expr for x in iterable]"}
        ]
        
        # 判断是否需要更新
        should_update = await services['artifact'].should_update_artifact(
            initial_artifact,
            conversation
        )
        
        print(f"✓ 是否需要更新: {should_update}")
        
        if should_update:
            # 更新 Artifact
            updated_artifact = await services['artifact'].update_artifact(
                user_id=user_id,
                thread_id=thread_id,
                current_artifact=initial_artifact,
                conversation=conversation,
                message_ids=["msg_test_001", "msg_test_002"]
            )
            
            print(f"✓ Artifact 已更新: {len(updated_artifact)} 字符")
            assert len(updated_artifact) > len(initial_artifact)


class TestDataPersistence:
    """数据持久化测试"""
    
    @pytest.mark.asyncio
    async def test_message_storage_and_retrieval(self):
        """测试：消息存储和检索"""
        firestore = FirestoreService()
        user_id = "test_user_persist_001"
        
        # 创建会话
        session = await firestore.create_session(user_id)
        session_id = session.session_id
        
        # 保存多条消息
        messages_to_save = [
            MessageCreate(
                role=MessageRole.USER,
                content="你好",
                thread_id="thread_test_001"
            ),
            MessageCreate(
                role=MessageRole.ASSISTANT,
                content="你好！有什么可以帮助你的吗？",
                thread_id="thread_test_001"
            ),
            MessageCreate(
                role=MessageRole.USER,
                content="介绍一下Python",
                thread_id="thread_test_001"
            )
        ]
        
        for msg in messages_to_save:
            await firestore.add_message(user_id, session_id, msg)
        
        # 检索消息
        retrieved = await firestore.get_messages_by_session(user_id, session_id)
        
        assert len(retrieved) == len(messages_to_save)
        print(f"✓ 存储并检索 {len(retrieved)} 条消息")
        
        # 按主题检索
        thread_messages = await firestore.get_messages_by_thread(
            user_id, session_id, "thread_test_001"
        )
        
        assert len(thread_messages) == len(messages_to_save)
        print(f"✓ 按主题检索 {len(thread_messages)} 条消息")


def run_tests():
    """运行测试"""
    print("=" * 60)
    print("运行集成测试")
    print("=" * 60)
    print("\n这些测试需要：")
    print("1. 有效的 Firebase 配置")
    print("2. 有效的 Gemini API 密钥")
    print("3. 有效的 Cloudflare API 配置")
    print("\n建议使用 pytest 运行：")
    print("  cd backend")
    print("  pytest tests/test_integration.py -v -s")
    print("=" * 60)


if __name__ == "__main__":
    run_tests()
