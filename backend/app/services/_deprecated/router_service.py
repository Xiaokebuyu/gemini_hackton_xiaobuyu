"""
路由服务模块 - 实现两阶段路由策略
"""
from typing import List, Dict, Any, Optional, Tuple
from app.services.firestore_service import FirestoreService
from app.services.llm_service import LLMService
from app.utils.embedding import get_cloudflare_embedding, cosine_similarity
from app.config import settings
from app.models import TopicCreate


class RouterService:
    """路由服务类"""
    
    def __init__(self):
        """初始化服务"""
        self.firestore = FirestoreService()
        self.llm = LLMService()
    
    async def route_message(
        self,
        user_id: str,
        user_input: str,
        session_id: str
    ) -> Tuple[str, bool]:
        """
        路由用户消息到合适的主题
        
        Args:
            user_id: 用户ID
            user_input: 用户输入
            session_id: 会话ID
            
        Returns:
            Tuple[str, bool]: (thread_id, is_new_topic)
        """
        # 1. 获取所有现有主题
        topics = await self.firestore.get_all_topics(user_id)
        
        if not topics:
            # 没有现有主题，直接创建新主题
            thread_id = await self._create_new_topic(user_id, user_input)
            return thread_id, True
        
        # 2. 生成用户输入的 embedding
        try:
            user_embedding = await get_cloudflare_embedding(user_input)
        except Exception as e:
            print(f"生成 embedding 失败: {str(e)}")
            # 如果 embedding 失败，使用 LLM 直接判断
            return await self._llm_only_route(user_id, user_input, topics)
        
        # 3. 第一阶段：Embedding 粗筛
        candidates = await self._embedding_filter(user_embedding, topics)
        
        if not candidates:
            # 没有找到相似的主题，创建新主题
            thread_id = await self._create_new_topic(user_id, user_input)
            return thread_id, True
        
        # 4. 第二阶段：判断是否需要 LLM 精判
        best_candidate = candidates[0]
        
        if best_candidate['similarity'] >= settings.embedding_threshold:
            # 相似度足够高，直接路由
            print(f"直接路由到: {best_candidate['title']} (相似度: {best_candidate['similarity']:.3f})")
            return best_candidate['thread_id'], False
        
        # 5. 相似度不够高，使用 LLM 精判
        decision = await self.llm.route_decision(user_input, candidates)
        
        return await self._execute_routing_decision(user_id, user_input, decision)

    async def _embedding_filter(
        self,
        user_embedding: List[float],
        topics: List[Any]
    ) -> List[Dict[str, Any]]:
        """
        使用 Embedding 进行粗筛
        
        Args:
            user_embedding: 用户输入的 embedding
            topics: 所有主题列表
            
        Returns:
            List[Dict]: 排序后的候选主题列表
        """
        candidates = []
        
        for topic in topics:
            if not topic.summary_embedding:
                continue
            
            # 计算相似度
            similarity = cosine_similarity(user_embedding, topic.summary_embedding)
            
            candidates.append({
                'thread_id': topic.thread_id,
                'title': topic.title,
                'similarity': similarity,
                'artifact': topic.current_artifact
            })
        
        # 按相似度降序排序
        candidates.sort(key=lambda x: x['similarity'], reverse=True)
        
        # 返回 Top-K 候选
        return candidates[:settings.max_candidate_topics]
    
    async def _llm_only_route(
        self,
        user_id: str,
        user_input: str,
        topics: List[Any]
    ) -> Tuple[str, bool]:
        """
        仅使用 LLM 进行路由（当 embedding 失败时）
        
        Args:
            user_id: 用户ID
            user_input: 用户输入
            topics: 主题列表
            
        Returns:
            Tuple[str, bool]: (thread_id, is_new_topic)
        """
        candidates = [
            {
                'thread_id': topic.thread_id,
                'title': topic.title,
                'similarity': 0.0
            }
            for topic in topics[:settings.max_candidate_topics]
        ]
        
        decision = await self.llm.route_decision(user_input, candidates)
        return await self._execute_routing_decision(user_id, user_input, decision)
    
    async def _execute_routing_decision(
        self,
        user_id: str,
        user_input: str,
        decision: Dict[str, Any]
    ) -> Tuple[str, bool]:
        """
        执行路由决策
        
        Args:
            user_id: 用户ID
            user_input: 用户输入
            decision: LLM 的路由决策
            
        Returns:
            Tuple[str, bool]: (thread_id, is_new_topic)
        """
        action = decision.get('action')
        
        if action == 'route_existing':
            # 路由到现有主题
            thread_id = decision.get('thread_id')
            print(f"路由决策: {action} -> {thread_id}")
            return thread_id, False
        
        elif action == 'create_new':
            # 创建新主题
            title = decision.get('title', '新对话')
            print(f"路由决策: {action} -> {title}")
            thread_id = await self._create_new_topic(user_id, user_input, title)
            return thread_id, True
        
        elif action == 'create_cross':
            # 创建交叉主题
            title = decision.get('title', '交叉主题')
            parent_ids = decision.get('parent_ids', [])
            print(f"路由决策: {action} -> {title} (父主题: {parent_ids})")
            thread_id = await self._create_cross_topic(user_id, user_input, title, parent_ids)
            return thread_id, True
        
        else:
            # 未知决策，创建新主题
            print(f"未知路由决策: {action}，创建新主题")
            thread_id = await self._create_new_topic(user_id, user_input)
            return thread_id, True
    
    async def _create_new_topic(
        self,
        user_id: str,
        user_input: str,
        title: Optional[str] = None
    ) -> str:
        """
        创建新主题
        
        Args:
            user_id: 用户ID
            user_input: 用户输入
            title: 主题标题
            
        Returns:
            str: 新主题的 thread_id
        """
        if not title:
            title = "新对话"
        
        # 生成主题摘要的 embedding
        summary_text = await self.llm.generate_topic_summary(title, user_input)
        
        try:
            summary_embedding = await get_cloudflare_embedding(summary_text)
        except Exception as e:
            print(f"生成主题 embedding 失败: {str(e)}")
            summary_embedding = None
        
        # 创建主题
        topic = TopicCreate(
            title=title,
            current_artifact=f"# {title}\n\n",
            summary_embedding=summary_embedding,
            parent_thread_ids=[],
            child_thread_ids=[]
        )
        
        thread_id = await self.firestore.create_topic(user_id, topic)
        print(f"创建新主题: {title} ({thread_id})")
        
        return thread_id
    
    async def _create_cross_topic(
        self,
        user_id: str,
        user_input: str,
        title: str,
        parent_ids: List[str]
    ) -> str:
        """
        创建交叉主题
        
        Args:
            user_id: 用户ID
            user_input: 用户输入
            title: 交叉主题标题
            parent_ids: 父主题ID列表
            
        Returns:
            str: 新主题的 thread_id
        """
        # 获取父主题的信息
        parent_titles = []
        parent_artifacts = []
        
        for parent_id in parent_ids:
            parent_topic = await self.firestore.get_topic(user_id, parent_id)
            if parent_topic:
                parent_titles.append(parent_topic.title)
                parent_artifacts.append(parent_topic.current_artifact)
        
        # 生成初始 Artifact（聚合父主题的相关内容）
        initial_artifact = f"# {title}\n\n"
        initial_artifact += f"_此主题涉及: {', '.join(parent_titles)}_\n\n"
        
        # 生成 embedding
        summary_text = f"{title}: {user_input}"
        
        try:
            summary_embedding = await get_cloudflare_embedding(summary_text)
        except Exception as e:
            print(f"生成交叉主题 embedding 失败: {str(e)}")
            summary_embedding = None
        
        # 创建交叉主题
        topic = TopicCreate(
            title=title,
            current_artifact=initial_artifact,
            summary_embedding=summary_embedding,
            parent_thread_ids=parent_ids,
            child_thread_ids=[]
        )
        
        thread_id = await self.firestore.create_topic(user_id, topic)
        
        # 更新父主题的 child_thread_ids
        for parent_id in parent_ids:
            parent_topic = await self.firestore.get_topic(user_id, parent_id)
            if parent_topic:
                # 这里需要在 FirestoreService 中添加更新 child_thread_ids 的方法
                # 暂时跳过，后续可以优化
                pass
        
        print(f"创建交叉主题: {title} ({thread_id}), 父主题: {parent_ids}")
        
        return thread_id
