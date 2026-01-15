"""
Artifact 管理服务
"""
import re
from typing import List, Dict, Any, Optional
from app.services.firestore_service import FirestoreService
from app.services.llm_service import LLMService


class ArtifactService:
    """Artifact 管理服务类"""
    
    def __init__(self):
        """初始化服务"""
        self.firestore = FirestoreService()
        self.llm = LLMService()
    
    def parse_artifact_sources(self, artifact: str) -> Dict[str, List[str]]:
        """
        解析 Artifact 中的源消息索引
        
        Args:
            artifact: Artifact 内容
            
        Returns:
            Dict[str, List[str]]: {章节标题: [消息ID列表]}
            
        Example:
            输入: "## 列表推导式 <!-- sources: msg_001, msg_002 -->"
            返回: {"## 列表推导式": ["msg_001", "msg_002"]}
        """
        sources_map = {}
        
        # 正则匹配: 标题行 + sources 注释
        # 匹配格式: (#+) 标题内容 <!-- sources: msg_id1, msg_id2 -->
        pattern = r'(#{1,6}\s+[^\n]+?)\s*<!--\s*sources:\s*([^>]+?)\s*-->'
        
        matches = re.finditer(pattern, artifact)
        
        for match in matches:
            title = match.group(1).strip()  # 标题（包含 #）
            sources_str = match.group(2).strip()  # 消息ID列表
            
            # 解析消息ID
            message_ids = [
                msg_id.strip() 
                for msg_id in sources_str.split(',')
                if msg_id.strip()
            ]
            
            if message_ids:
                sources_map[title] = message_ids
        
        return sources_map
    
    def extract_section_content(
        self,
        artifact: str,
        section_title: str
    ) -> Optional[str]:
        """
        提取指定章节的内容
        
        Args:
            artifact: Artifact 完整内容
            section_title: 章节标题（如 "## 列表推导式"）
            
        Returns:
            Optional[str]: 章节内容，如果不存在返回 None
        """
        if not artifact or not section_title:
            return None
        
        lines = artifact.split('\n')
        
        # 提取标题级别
        title_match = re.match(r'^(#{1,6})\s+(.+)', section_title.strip())
        if not title_match:
            return None
        
        target_level = len(title_match.group(1))
        target_title = title_match.group(2).strip()
        
        # 查找章节起始位置
        start_idx = None
        for i, line in enumerate(lines):
            line_match = re.match(r'^(#{1,6})\s+(.+)', line.strip())
            if line_match:
                level = len(line_match.group(1))
                title = line_match.group(2).strip()
                
                # 移除可能的注释
                title = re.sub(r'\s*<!--.*?-->\s*$', '', title).strip()
                
                if level == target_level and title == target_title:
                    start_idx = i
                    break
        
        if start_idx is None:
            return None
        
        # 查找章节结束位置（下一个同级或更高级标题）
        end_idx = len(lines)
        for i in range(start_idx + 1, len(lines)):
            line_match = re.match(r'^(#{1,6})\s+', lines[i].strip())
            if line_match:
                level = len(line_match.group(1))
                if level <= target_level:
                    end_idx = i
                    break
        
        # 提取内容
        section_lines = lines[start_idx:end_idx]
        return '\n'.join(section_lines)
    
    async def find_relevant_sections(
        self,
        artifact: str,
        user_query: str
    ) -> List[str]:
        """
        找到与用户问题相关的章节
        
        Args:
            artifact: Artifact 内容
            user_query: 用户问题
            
        Returns:
            List[str]: 相关章节的标题列表
        """
        return await self.llm.find_relevant_sections(artifact, user_query)
    
    async def load_section_messages(
        self,
        user_id: str,
        session_id: str,
        artifact: str,
        section_titles: List[str]
    ) -> List[Any]:
        """
        加载指定章节关联的历史消息
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            artifact: Artifact 内容
            section_titles: 章节标题列表
            
        Returns:
            List[Message]: 消息列表
        """
        # 解析所有章节的源索引
        sources_map = self.parse_artifact_sources(artifact)
        
        # 收集所有相关的消息ID
        message_ids = set()
        for title in section_titles:
            if title in sources_map:
                message_ids.update(sources_map[title])
        
        # 加载消息
        messages = []
        for msg_id in message_ids:
            msg = await self.firestore.get_message_by_id(user_id, session_id, msg_id)
            if msg:
                messages.append(msg)
        
        # 按时间排序
        messages.sort(key=lambda m: m.timestamp)
        
        return messages
    
    async def should_update_artifact(
        self,
        current_artifact: str,
        conversation: List[Dict[str, str]]
    ) -> bool:
        """
        判断是否需要更新 Artifact
        
        Args:
            current_artifact: 当前 Artifact 内容
            conversation: 最近的对话历史
            
        Returns:
            bool: 是否需要更新
        """
        result = await self.llm.check_should_update_artifact(
            current_artifact,
            conversation
        )
        
        should_update = result.get('should_update', False)
        reasoning = result.get('reasoning', '')
        
        print(f"Artifact 更新判断: {should_update} - {reasoning}")
        
        return should_update
    
    async def update_artifact(
        self,
        user_id: str,
        thread_id: str,
        current_artifact: str,
        conversation: List[Dict[str, str]],
        message_ids: List[str]
    ) -> str:
        """
        更新 Artifact 内容
        
        Args:
            user_id: 用户ID
            thread_id: 主题ID
            current_artifact: 当前 Artifact
            conversation: 对话历史
            message_ids: 相关消息ID
            
        Returns:
            str: 更新后的 Artifact
        """
        # 使用 LLM 更新 Artifact
        updated_artifact = await self.llm.update_artifact(
            current_artifact,
            conversation,
            message_ids
        )
        
        # 保存到 Firestore
        await self.firestore.update_artifact(user_id, thread_id, updated_artifact)
        
        # 保存版本历史
        await self.firestore.save_artifact_version(
            user_id,
            thread_id,
            updated_artifact,
            message_ids
        )
        
        print(f"Artifact 已更新: {thread_id}")
        
        return updated_artifact
    
    async def build_context_from_artifact(
        self,
        user_id: str,
        session_id: str,
        thread_id: str,
        user_query: str
    ) -> str:
        """
        从 Artifact 构建上下文
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            thread_id: 主题ID
            user_query: 用户问题
            
        Returns:
            str: 构建的上下文文本
        """
        # 获取主题
        topic = await self.firestore.get_topic(user_id, thread_id)
        if not topic or not topic.current_artifact:
            return ""
        
        artifact = topic.current_artifact
        
        # 找到相关章节
        relevant_sections = await self.find_relevant_sections(artifact, user_query)
        
        if not relevant_sections:
            # 如果没有找到相关章节，返回整个 Artifact
            return f"## 相关知识\n\n{artifact}\n\n"
        
        # 构建上下文：Artifact 骨架 + 相关章节的详细内容
        context = f"## 主题: {topic.title}\n\n"
        context += f"### Artifact 概览\n\n{artifact}\n\n"
        
        # 加载相关章节的源消息
        section_messages = await self.load_section_messages(
            user_id,
            session_id,
            artifact,
            relevant_sections
        )
        
        if section_messages:
            context += "### 相关历史对话\n\n"
            for msg in section_messages:
                context += f"**{msg.role.upper()}**: {msg.content}\n\n"
        
        return context
