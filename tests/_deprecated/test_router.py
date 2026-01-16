"""
路由系统测试
"""
import pytest

pytest.skip("路由系统已弃用", allow_module_level=True)

from app.services._deprecated.router_service import RouterService
from app.services.firestore_service import FirestoreService
from app.models import TopicCreate


class TestRouterService:
    """路由服务测试"""
    
    @pytest.fixture
    def setup_services(self):
        """设置测试服务"""
        router = RouterService()
        firestore = FirestoreService()
        return router, firestore
    
    @pytest.mark.asyncio
    async def test_create_new_topic_when_no_existing_topics(self, setup_services):
        """测试：首次对话创建新主题"""
        router, firestore = setup_services
        user_id = "test_user_001"
        
        # 首次发送消息
        thread_id, is_new = await router.route_message(
            user_id=user_id,
            user_input="Python的列表推导式怎么用?",
            session_id="test_session_001"
        )
        
        assert is_new == True
        assert thread_id.startswith("thread_")
        
        # 验证主题已创建
        topic = await firestore.get_topic(user_id, thread_id)
        assert topic is not None
        assert topic.title is not None
    
    @pytest.mark.asyncio
    async def test_route_to_existing_topic(self, setup_services):
        """测试：继续相同话题路由到现有主题"""
        router, firestore = setup_services
        user_id = "test_user_002"
        session_id = "test_session_002"
        
        # 创建初始主题
        topic_create = TopicCreate(
            title="Python编程",
            current_artifact="# Python编程\n\n## 基础知识\n",
            summary_embedding=None,
            parent_thread_ids=[],
            child_thread_ids=[]
        )
        initial_thread_id = await firestore.create_topic(user_id, topic_create)
        
        # 发送相关消息
        thread_id, is_new = await router.route_message(
            user_id=user_id,
            user_input="Python的函数怎么定义?",
            session_id=session_id
        )
        
        # 应该路由到现有主题或创建新主题（取决于embedding相似度）
        assert thread_id is not None
        assert thread_id.startswith("thread_")
    
    @pytest.mark.asyncio
    async def test_embedding_similarity_calculation(self):
        """测试：余弦相似度计算"""
        from app.utils.embedding import cosine_similarity
        
        vec1 = [1.0, 0.0, 0.0]
        vec2 = [1.0, 0.0, 0.0]
        vec3 = [0.0, 1.0, 0.0]
        
        # 相同向量相似度为1
        sim1 = cosine_similarity(vec1, vec2)
        assert abs(sim1 - 1.0) < 0.001
        
        # 正交向量相似度为0
        sim2 = cosine_similarity(vec1, vec3)
        assert abs(sim2 - 0.0) < 0.001


def run_tests():
    """运行测试"""
    print("运行路由系统测试...")
    print("\n注意：这些测试需要实际的 Firebase 和 API 配置才能运行")
    print("建议使用 pytest 命令运行测试：")
    print("  cd backend")
    print("  pytest tests/test_router.py -v")


if __name__ == "__main__":
    run_tests()
