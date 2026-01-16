"""
LLM 服务模块 - 支持 Gemini 3 思考功能
"""
import json
import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from google import genai
from google.genai import types
from app.config import settings


@dataclass
class ThinkingMetadata:
    """思考元数据"""
    thinking_enabled: bool = False
    thinking_level: str = "medium"
    thoughts_summary: str = ""
    thoughts_token_count: int = 0
    output_token_count: int = 0
    total_token_count: int = 0


@dataclass
class LLMResponse:
    """LLM 响应结构"""
    text: str
    thinking: ThinkingMetadata = field(default_factory=ThinkingMetadata)
    raw_response: Any = None


class LLMService:
    """LLM 服务类 - 支持 Gemini 3 思考功能"""
    
    def __init__(self):
        """初始化 Gemini API"""
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.flash_model = settings.gemini_flash_model
        self.main_model = settings.gemini_main_model
    
    def _get_thinking_config(self, override_level: Optional[str] = None) -> types.ThinkingConfig:
        """构建 Gemini 3 思考配置"""
        if not settings.thinking_enabled:
            return types.ThinkingConfig(
                thinking_level="lowest",
                include_thoughts=False
            )
        
        level = override_level or settings.thinking_level
        return types.ThinkingConfig(
            thinking_level=level,
            include_thoughts=settings.include_thoughts
        )
    
    def _strip_code_block(self, text: str) -> str:
        """移除代码块标记"""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
            cleaned = cleaned.rstrip("`").strip()
        return cleaned
    
    def _parse_json(self, text: str) -> Optional[Dict[str, Any]]:
        """解析 JSON"""
        cleaned = self._strip_code_block(text)
        try:
            return json.loads(cleaned)
        except Exception:
            return None
    
    def _extract_response(self, response, level: str = None) -> LLMResponse:
        """从响应中提取文本和思考摘要"""
        answer_text = ""
        thoughts_summary = ""
        
        if hasattr(response, 'candidates') and response.candidates:
            for part in response.candidates[0].content.parts:
                if not hasattr(part, 'text') or not part.text:
                    continue
                
                if hasattr(part, 'thought') and part.thought:
                    thoughts_summary += part.text
                else:
                    answer_text += part.text
        
        thinking_meta = ThinkingMetadata(
            thinking_enabled=settings.thinking_enabled,
            thinking_level=level or settings.thinking_level,
            thoughts_summary=thoughts_summary.strip(),
        )
        
        if hasattr(response, 'usage_metadata'):
            usage = response.usage_metadata
            if hasattr(usage, 'thoughts_token_count'):
                thinking_meta.thoughts_token_count = usage.thoughts_token_count or 0
            if hasattr(usage, 'candidates_token_count'):
                thinking_meta.output_token_count = usage.candidates_token_count or 0
            if hasattr(usage, 'total_token_count'):
                thinking_meta.total_token_count = usage.total_token_count or 0
        
        return LLMResponse(
            text=answer_text.strip(),
            thinking=thinking_meta,
            raw_response=response
        )
    
    async def generate_response(
        self, 
        context: str, 
        user_query: str,
        thinking_level: Optional[str] = None
    ) -> LLMResponse:
        """
        生成回复（支持 Gemini 3 思考功能）
        
        Args:
            context: 上下文内容
            user_query: 用户问题
            thinking_level: 思考层级 (lowest/low/medium/high)
            
        Returns:
            LLMResponse: 包含回复文本和思考元数据
        """
        prompt = f"""{context}

---

用户问题: {user_query}

请基于上下文回答问题，保持简洁准确。"""
        
        try:
            thinking_config = self._get_thinking_config(thinking_level)
            
            response = self.client.models.generate_content(
                model=self.main_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    thinking_config=thinking_config
                )
            )
            
            return self._extract_response(response, thinking_level)
            
        except Exception as e:
            error_msg = f"抱歉，生成回复时出错: {str(e)}"
            return LLMResponse(
                text=error_msg,
                thinking=ThinkingMetadata(
                    thinking_enabled=settings.thinking_enabled,
                    thinking_level=thinking_level or settings.thinking_level
                )
            )
    
    async def generate_response_stream(
        self, 
        context: str, 
        user_query: str,
        thinking_level: Optional[str] = None
    ):
        """
        流式生成回复（支持思考功能）
        
        Yields:
            dict: 包含 type ('thought' 或 'answer') 和 text 的字典
        """
        prompt = f"""{context}

---

用户问题: {user_query}

请基于上下文回答问题，保持简洁准确。"""
        
        try:
            thinking_config = self._get_thinking_config(thinking_level)
            
            for chunk in self.client.models.generate_content_stream(
                model=self.main_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    thinking_config=thinking_config
                )
            ):
                if not hasattr(chunk, 'candidates') or not chunk.candidates:
                    continue
                    
                for part in chunk.candidates[0].content.parts:
                    if not hasattr(part, 'text') or not part.text:
                        continue
                    
                    if hasattr(part, 'thought') and part.thought:
                        yield {"type": "thought", "text": part.text}
                    else:
                        yield {"type": "answer", "text": part.text}
                        
        except Exception as e:
            yield {"type": "error", "text": f"生成出错: {str(e)}"}
    
    async def analyze_messages_for_archive(
        self, 
        messages: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """分析消息块，生成主题和 Artifact"""
        messages_text = "\n".join(
            [
                f"{msg['role'].upper()}({msg['message_id']}): {msg['content']}"
                for msg in messages
            ]
        )
        
        prompt = f"""你是归档助手，需要把一段对话整理成主题摘要和知识文档。

对话内容:
{messages_text}

请输出 JSON，格式:
{{
  "title": "主题标题（宽泛大类）",
  "summary": "简短摘要（用于合并判断）",
  "artifact": "Markdown 文档"
}}

Artifact 要求:
1. 使用 Markdown 标题组织内容
2. 在新增或更新的章节标题后添加索引注释: <!-- sources: msg_xxx, msg_yyy -->
3. 只引用本次对话中出现的 message_id
4. 内容简洁、可用于后续检索

只返回 JSON，不要其他内容。"""
        
        try:
            thinking_config = types.ThinkingConfig(
                thinking_level="low",
                include_thoughts=False
            )
            
            response = self.client.models.generate_content(
                model=self.flash_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    thinking_config=thinking_config
                )
            )
            
            text = ""
            if hasattr(response, 'candidates') and response.candidates:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'text') and part.text:
                        if not (hasattr(part, 'thought') and part.thought):
                            text += part.text
            
            result = self._parse_json(text)
            if result:
                return result
                
        except Exception as e:
            print(f"归档分析失败: {str(e)}")
        
        return {
            "title": "未分类主题",
            "summary": "归档分析失败，暂未生成摘要",
            "artifact": "# 未分类主题\n\n",
        }
    
    async def should_merge_topics(
        self,
        existing_title: str,
        existing_summary: str,
        new_title: str,
        new_summary: str,
    ) -> bool:
        """判断两个 Topic 是否应该合并"""
        prompt = f"""判断两个主题是否应该合并为同一主题。

现有主题:
标题: {existing_title}
摘要: {existing_summary}

新主题:
标题: {new_title}
摘要: {new_summary}

请以 JSON 返回:
{{"should_merge": true/false, "reasoning": "简短说明"}}

只返回 JSON，不要其他内容。"""
        
        try:
            thinking_config = types.ThinkingConfig(
                thinking_level="lowest",
                include_thoughts=False
            )
            
            response = self.client.models.generate_content(
                model=self.flash_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    thinking_config=thinking_config
                )
            )
            
            text = ""
            if hasattr(response, 'candidates') and response.candidates:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'text') and part.text:
                        if not (hasattr(part, 'thought') and part.thought):
                            text += part.text
            
            result = self._parse_json(text)
            if isinstance(result, dict):
                return bool(result.get("should_merge", False))
                
        except Exception as e:
            print(f"主题合并判断失败: {str(e)}")
        
        return False
    
    async def merge_artifacts(
        self, 
        existing_artifact: str, 
        new_artifact: str
    ) -> str:
        """合并两个 Artifact"""
        prompt = f"""你是知识文档合并助手，请把两份 Artifact 合并为一份。

现有 Artifact:
{existing_artifact}

新增 Artifact:
{new_artifact}

要求:
1. 保持 Markdown 结构清晰
2. 合并同类章节，避免重复
3. 保留所有 <!-- sources: --> 索引

返回合并后的完整 Markdown 文档。"""
        
        try:
            thinking_config = types.ThinkingConfig(
                thinking_level="low",
                include_thoughts=False
            )
            
            response = self.client.models.generate_content(
                model=self.main_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    thinking_config=thinking_config
                )
            )
            
            text = ""
            if hasattr(response, 'candidates') and response.candidates:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'text') and part.text:
                        if not (hasattr(part, 'thought') and part.thought):
                            text += part.text
            
            return self._strip_code_block(text)
            
        except Exception as e:
            print(f"Artifact 合并失败: {str(e)}")
            return existing_artifact
