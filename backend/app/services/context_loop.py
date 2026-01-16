"""
上下文循环 - 处理动态上下文重构
支持 Gemini 3 思考功能
"""
import re
from typing import List, Optional
from dataclasses import dataclass, field
from app.config import settings
from app.models import Message
from app.services.firestore_service import FirestoreService
from app.services.llm_service import LLMService, LLMResponse, ThinkingMetadata
from app.services.artifact_service import ArtifactService
from app.services.context_builder import ContextBuilder


@dataclass
class ContextLoopResult:
    """上下文循环结果"""
    response: str
    retry_count: int
    thinking: ThinkingMetadata = field(default_factory=ThinkingMetadata)


class ContextLoop:
    """上下文循环 - 支持思考功能"""
    
    def __init__(self):
        self.firestore = FirestoreService()
        self.llm = LLMService()
        self.artifact = ArtifactService()
        self.context_builder = ContextBuilder()
    
    async def run(
        self, 
        user_id: str, 
        session_id: str, 
        user_query: str,
        thinking_level: Optional[str] = None
    ) -> ContextLoopResult:
        """
        运行上下文循环
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            user_query: 用户问题
            thinking_level: 可选的思考层级覆盖
            
        Returns:
            ContextLoopResult: 包含响应、重试次数和思考元数据
        """
        loaded_messages: List[Message] = []
        retry_count = 0
        last_llm_response: Optional[LLMResponse] = None
        
        for _ in range(settings.max_context_retry):
            # 构建上下文
            context = await self.context_builder.build(
                user_id, session_id, loaded_messages
            )
            
            # 生成回复（带思考功能）
            llm_response = await self.llm.generate_response(
                context, 
                user_query,
                thinking_level=thinking_level
            )
            last_llm_response = llm_response
            
            # 检查是否需要加载上下文
            request_key = self.parse_context_request(llm_response.text)
            if not request_key:
                # 无需加载更多上下文，返回结果
                return ContextLoopResult(
                    response=llm_response.text,
                    retry_count=retry_count,
                    thinking=llm_response.thinking
                )
            
            # 根据关键词查找历史消息
            messages = await self.resolve_context_request(
                user_id, session_id, request_key
            )
            
            if not messages:
                # 找不到相关消息，移除标记后返回
                return ContextLoopResult(
                    response=self.strip_context_request(llm_response.text),
                    retry_count=retry_count,
                    thinking=llm_response.thinking
                )
            
            # 去重添加新消息
            existing_ids = {msg.message_id for msg in loaded_messages}
            new_messages = [msg for msg in messages if msg.message_id not in existing_ids]
            
            if not new_messages:
                # 没有新消息可加载
                return ContextLoopResult(
                    response=self.strip_context_request(llm_response.text),
                    retry_count=retry_count,
                    thinking=llm_response.thinking
                )
            
            loaded_messages.extend(new_messages)
            retry_count += 1
        
        # 达到最大重试次数
        final_response = ""
        final_thinking = ThinkingMetadata()
        
        if last_llm_response:
            final_response = self.strip_context_request(last_llm_response.text)
            final_thinking = last_llm_response.thinking
        
        return ContextLoopResult(
            response=final_response,
            retry_count=retry_count,
            thinking=final_thinking
        )
    
    def parse_context_request(self, response: str) -> Optional[str]:
        """解析 [NEED_CONTEXT: xxx] 标记"""
        match = re.search(settings.context_request_pattern, response)
        return match.group(1).strip() if match else None
    
    def strip_context_request(self, response: str) -> str:
        """移除回复中的 [NEED_CONTEXT: xxx] 标记"""
        return re.sub(settings.context_request_pattern, "", response).strip()
    
    async def resolve_context_request(
        self,
        user_id: str,
        session_id: str,
        keyword: str,
    ) -> List[Message]:
        """
        根据关键词查找历史消息
        """
        topics = await self.firestore.get_all_topics(user_id, session_id)
        
        for topic in topics:
            result = self.artifact.find_section_by_keyword(
                topic.current_artifact, keyword
            )
            if result:
                _, message_ids = result
                return await self.firestore.get_messages_by_ids(
                    user_id, session_id, message_ids
                )
        
        return []
