"""
LLM 服务模块
"""
import json
from typing import List, Dict, Any, Optional
import google.generativeai as genai
from app.config import settings


class LLMService:
    """LLM 服务类"""
    
    def __init__(self):
        """初始化 Gemini API"""
        genai.configure(api_key=settings.gemini_api_key)
        
        # 配置模型
        self.flash_model = genai.GenerativeModel(settings.gemini_flash_model)
        self.main_model = genai.GenerativeModel(settings.gemini_main_model)
    
    async def route_decision(
        self,
        user_input: str,
        candidates: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        使用 Flash-Lite 做路由决策
        
        Args:
            user_input: 用户输入
            candidates: 候选主题列表，每个包含 {thread_id, title, similarity}
            
        Returns:
            Dict: 路由决策结果
                {
                    "action": "route_existing" | "create_new" | "create_cross",
                    "thread_id": str,      # action=route_existing时
                    "title": str,          # action=create_new时
                    "parent_ids": [str]    # action=create_cross时
                }
        """
        # 构建 prompt
        candidates_text = "\n".join([
            f"{i+1}. [{c['thread_id']}] {c['title']} (相似度: {c.get('similarity', 0):.2f})"
            for i, c in enumerate(candidates)
        ])
        
        prompt = f"""你是一个对话主题路由专家。用户发来了新消息，你需要判断应该如何处理。

用户消息: {user_input}

现有主题候选:
{candidates_text if candidates else "（无现有主题）"}

请分析用户消息的意图，并做出以下决策之一:

1. **route_existing**: 如果用户消息明确属于某个现有主题，返回该主题的 thread_id
2. **create_new**: 如果这是一个全新的话题，返回建议的主题标题（宽泛的大类，如"Python编程"、"Docker部署"）
3. **create_cross**: 如果用户消息涉及多个现有主题的交叉，返回父主题的 thread_id 列表和新主题标题

请以 JSON 格式返回决策，格式如下:

对于 route_existing:
{{
    "action": "route_existing",
    "thread_id": "thread_xxx",
    "reasoning": "简短说明为什么选择这个主题"
}}

对于 create_new:
{{
    "action": "create_new",
    "title": "主题标题",
    "reasoning": "简短说明为什么创建新主题"
}}

对于 create_cross:
{{
    "action": "create_cross",
    "parent_ids": ["thread_xxx", "thread_yyy"],
    "title": "交叉主题标题",
    "reasoning": "简短说明为什么创建交叉主题"
}}

只返回 JSON，不要其他内容。"""

        try:
            response = self.flash_model.generate_content(prompt)
            result_text = response.text.strip()
            
            # 移除可能的 markdown 代码块标记
            if result_text.startswith("```json"):
                result_text = result_text[7:]
            if result_text.startswith("```"):
                result_text = result_text[3:]
            if result_text.endswith("```"):
                result_text = result_text[:-3]
            
            result = json.loads(result_text.strip())
            return result
            
        except Exception as e:
            # 如果解析失败，返回默认决策
            print(f"路由决策失败: {str(e)}")
            if candidates:
                # 选择相似度最高的候选
                best_candidate = max(candidates, key=lambda x: x.get('similarity', 0))
                return {
                    "action": "route_existing",
                    "thread_id": best_candidate['thread_id'],
                    "reasoning": "默认选择最相似的主题"
                }
            else:
                # 创建新主题
                return {
                    "action": "create_new",
                    "title": "新对话",
                    "reasoning": "无现有主题，创建新主题"
                }
    
    async def find_relevant_sections(
        self,
        artifact: str,
        user_query: str
    ) -> List[str]:
        """
        在 Artifact 中找到与用户问题相关的章节
        
        Args:
            artifact: Artifact 内容
            user_query: 用户问题
            
        Returns:
            List[str]: 相关章节的标题列表
        """
        if not artifact or artifact.strip() == "":
            return []
        
        prompt = f"""分析以下知识文档，找出与用户问题最相关的章节。

知识文档:
{artifact}

用户问题: {user_query}

请返回相关章节的标题列表（Markdown 标题）。如果没有相关章节，返回空列表。

以 JSON 数组格式返回，例如: ["## 章节1", "### 子章节2"]

只返回 JSON 数组，不要其他内容。"""

        try:
            response = self.flash_model.generate_content(prompt)
            result_text = response.text.strip()
            
            # 移除可能的 markdown 代码块标记
            if result_text.startswith("```json"):
                result_text = result_text[7:]
            if result_text.startswith("```"):
                result_text = result_text[3:]
            if result_text.endswith("```"):
                result_text = result_text[:-3]
            
            sections = json.loads(result_text.strip())
            return sections if isinstance(sections, list) else []
            
        except Exception as e:
            print(f"章节查找失败: {str(e)}")
            return []
    
    async def check_should_update_artifact(
        self,
        current_artifact: str,
        conversation: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """
        判断是否需要更新 Artifact
        
        Args:
            current_artifact: 当前的 Artifact 内容
            conversation: 最近的对话，格式 [{"role": "user/assistant", "content": "..."}]
            
        Returns:
            Dict: {
                "should_update": bool,
                "reasoning": str
            }
        """
        # 构建对话历史
        conv_text = "\n".join([
            f"{msg['role'].upper()}: {msg['content']}"
            for msg in conversation[-4:]  # 只看最近4轮对话
        ])
        
        prompt = f"""分析以下对话，判断是否包含值得记录到知识文档的新信息。

当前知识文档:
{current_artifact if current_artifact else "（空文档）"}

最近对话:
{conv_text}

值得记录的信息包括:
1. 新的知识点、概念或技术
2. 重要的决策或选择
3. 具体的实现方案或代码片段
4. 问题的解决方法

不值得记录的信息:
1. 简单的问候或闲聊
2. 已经在文档中记录过的内容
3. 临时性的、不具有参考价值的内容

请以 JSON 格式返回:
{{
    "should_update": true/false,
    "reasoning": "简短说明原因"
}}

只返回 JSON，不要其他内容。"""

        try:
            response = self.flash_model.generate_content(prompt)
            result_text = response.text.strip()
            
            # 移除可能的 markdown 代码块标记
            if result_text.startswith("```json"):
                result_text = result_text[7:]
            if result_text.startswith("```"):
                result_text = result_text[3:]
            if result_text.endswith("```"):
                result_text = result_text[:-3]
            
            result = json.loads(result_text.strip())
            return result
            
        except Exception as e:
            print(f"Artifact 更新判断失败: {str(e)}")
            return {"should_update": False, "reasoning": "判断失败"}
    
    async def update_artifact(
        self,
        current_artifact: str,
        conversation: List[Dict[str, str]],
        message_ids: List[str]
    ) -> str:
        """
        更新 Artifact 内容
        
        Args:
            current_artifact: 当前的 Artifact 内容
            conversation: 对话历史
            message_ids: 对应的消息ID列表
            
        Returns:
            str: 更新后的 Artifact 内容
        """
        # 构建对话历史
        conv_text = "\n".join([
            f"{msg['role'].upper()}: {msg['content']}"
            for msg in conversation[-4:]
        ])
        
        # 构建消息ID索引
        msg_ids_str = ", ".join(message_ids[-2:])  # 最近2条消息
        
        prompt = f"""你是知识文档管理专家。根据对话内容更新知识文档。

当前文档:
{current_artifact if current_artifact else "# 新主题\n\n"}

最近对话:
{conv_text}

请更新文档，要求:
1. 保持 Markdown 格式
2. 在新增或修改的章节标题后添加索引注释: <!-- sources: {msg_ids_str} -->
3. 保持文档的层次结构清晰
4. 只添加有价值的信息，不要重复已有内容
5. 使用清晰的标题组织内容

返回完整的更新后文档。"""

        try:
            response = self.main_model.generate_content(prompt)
            updated_artifact = response.text.strip()
            
            # 移除可能的 markdown 代码块标记
            if updated_artifact.startswith("```markdown"):
                updated_artifact = updated_artifact[11:]
            if updated_artifact.startswith("```"):
                updated_artifact = updated_artifact[3:]
            if updated_artifact.endswith("```"):
                updated_artifact = updated_artifact[:-3]
            
            return updated_artifact.strip()
            
        except Exception as e:
            print(f"Artifact 更新失败: {str(e)}")
            return current_artifact
    
    async def generate_response(
        self,
        user_query: str,
        context: str,
        conversation_history: List[Dict[str, str]]
    ) -> str:
        """
        生成对用户问题的回复
        
        Args:
            user_query: 用户问题
            context: 上下文（包含 Artifact 和相关历史）
            conversation_history: 对话历史
            
        Returns:
            str: AI 回复
        """
        # 构建对话历史
        history_text = "\n".join([
            f"{msg['role'].upper()}: {msg['content']}"
            for msg in conversation_history[-6:]  # 最近6轮对话
        ])
        
        prompt = f"""你是一个有记忆的AI助手。你可以访问之前对话的知识积累。

相关知识背景:
{context}

最近对话历史:
{history_text}

用户问题: {user_query}

请基于上述背景和历史回答用户问题。如果知识背景中有相关信息，请引用它。保持回答简洁、准确。"""

        try:
            response = self.main_model.generate_content(prompt)
            return response.text.strip()
            
        except Exception as e:
            return f"抱歉，生成回复时出错: {str(e)}"
    
    async def generate_topic_summary(self, title: str, initial_message: str) -> str:
        """
        为新主题生成初始摘要
        
        Args:
            title: 主题标题
            initial_message: 初始消息
            
        Returns:
            str: 主题摘要文本（用于生成 embedding）
        """
        return f"{title}: {initial_message[:200]}"
