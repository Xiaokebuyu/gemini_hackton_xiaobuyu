"""
LLM 服务模块 - 支持 Gemini 3 思考功能
"""
import asyncio
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


@dataclass
class AgenticForcedCallRound:
    """Forced tool-calling first round payload."""
    function_calls: List[Dict[str, Any]] = field(default_factory=list)
    response_content: Any = None
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

    def parse_json(self, text: str) -> Optional[Dict[str, Any]]:
        """公开 JSON 解析"""
        return self._parse_json(text)

    async def generate_json(self, prompt: str) -> Optional[Dict[str, Any]]:
        """生成 JSON 响应并解析"""
        try:
            result = await self.generate_simple(prompt)
            return self._parse_json(result)
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

        if not answer_text and hasattr(response, "text") and response.text:
            answer_text = response.text
        
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

    def _extract_function_calls(self, response: Any) -> List[Dict[str, Any]]:
        """Extract function-call suggestions from Gemini response."""
        calls: List[Dict[str, Any]] = []
        seen: set[str] = set()

        raw_calls = getattr(response, "function_calls", None) or []
        for fn in raw_calls:
            name = str(getattr(fn, "name", "") or "").strip()
            if not name:
                continue
            args_obj = getattr(fn, "args", None)
            args: Dict[str, Any] = dict(args_obj) if isinstance(args_obj, dict) else {}
            call_key = f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
            if call_key in seen:
                continue
            seen.add(call_key)
            calls.append({"name": name, "args": args})

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return calls
        parts = getattr(getattr(candidates[0], "content", None), "parts", None) or []
        for part in parts:
            fn = getattr(part, "function_call", None)
            if not fn:
                continue
            name = str(getattr(fn, "name", "") or "").strip()
            if not name:
                continue
            args_obj = getattr(fn, "args", None)
            args: Dict[str, Any] = dict(args_obj) if isinstance(args_obj, dict) else {}
            call_key = f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
            if call_key in seen:
                continue
            seen.add(call_key)
            calls.append({"name": name, "args": args})
        return calls
    
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

    async def agentic_generate(
        self,
        *,
        user_prompt: str,
        system_instruction: str,
        tools: List[Any],
        model_override: Optional[str] = None,
        thinking_level: Optional[str] = None,
        max_remote_calls: Optional[int] = None,
        cached_content: Optional[str] = None,
    ) -> LLMResponse:
        """Run a single agentic session with automatic function calling.

        IMPORTANT: Uses mode="AUTO" (default) so the model can freely mix
        tool calls and text responses within the SDK's automatic loop.
        mode="ANY" is incompatible with automatic_function_calling because
        the model is forced to always return function calls and can never
        produce the final text response, causing an infinite tool loop.
        Post-hoc enforcement (admin_coordinator.evaluate_agentic_tool_usage)
        handles the "must call required tools" constraint instead.
        """
        import logging
        _logger = logging.getLogger(__name__)

        thinking_config = self._get_thinking_config(thinking_level)
        # mode="AUTO" lets the model decide when to call tools vs output text.
        # Do NOT use mode="ANY" here — it creates an infinite loop with auto-FC.
        model = model_override or settings.admin_agentic_model or self.main_model

        try:
            response = await self.client.aio.models.generate_content(
                model=model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    thinking_config=thinking_config,
                    tools=tools,
                    cached_content=cached_content,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(
                        disable=False,
                        maximum_remote_calls=max_remote_calls or settings.admin_agentic_max_remote_calls,
                    ),
                ),
            )

            # --- AFC diagnostic: log any tool errors silently swallowed by SDK ---
            afc_history = getattr(response, "automatic_function_calling_history", None)
            if afc_history:
                for content in afc_history:
                    if not hasattr(content, "parts") or not content.parts:
                        continue
                    for part in content.parts:
                        fn_resp = getattr(part, "function_response", None)
                        if fn_resp is None:
                            continue
                        resp_data = getattr(fn_resp, "response", None) or {}
                        name = getattr(fn_resp, "name", "unknown")
                        if isinstance(resp_data, dict) and "error" in resp_data:
                            _logger.warning(
                                "[agentic_generate] AFC tool error: tool=%s error=%s",
                                name, str(resp_data["error"])[:300],
                            )

            return self._extract_response(response, thinking_level)
        except Exception as e:
            _logger.error("agentic_generate failed (model=%s): %s", model, e, exc_info=True)
            raise

    async def agentic_force_tool_calls(
        self,
        *,
        user_prompt: str,
        system_instruction: str,
        tools: List[Any],
        allowed_function_names: List[str],
        model_override: Optional[str] = None,
        thinking_level: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Backward-compatible wrapper for forced function calls (mode=ANY)."""
        round_payload = await self.agentic_force_tool_calls_round(
            user_prompt=user_prompt,
            system_instruction=system_instruction,
            tools=tools,
            allowed_function_names=allowed_function_names,
            model_override=model_override,
            thinking_level=thinking_level,
        )
        return round_payload.function_calls

    async def agentic_force_tool_calls_round(
        self,
        *,
        user_prompt: str,
        system_instruction: str,
        tools: List[Any],
        allowed_function_names: List[str],
        model_override: Optional[str] = None,
        thinking_level: Optional[str] = None,
    ) -> AgenticForcedCallRound:
        """Force one model round to output function calls and keep the response content."""
        thinking_config = self._get_thinking_config(thinking_level)
        model = model_override or settings.admin_agentic_model or self.main_model
        allow_list = [str(name).strip() for name in (allowed_function_names or []) if str(name).strip()]
        allow_list = list(dict.fromkeys(allow_list))
        tool_config = types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(
                mode="ANY",
                allowed_function_names=allow_list or None,
            )
        )

        response = await self.client.aio.models.generate_content(
            model=model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                thinking_config=thinking_config,
                tools=tools,
                tool_config=tool_config,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        )
        candidates = getattr(response, "candidates", None) or []
        response_content = getattr(candidates[0], "content", None) if candidates else None
        return AgenticForcedCallRound(
            function_calls=self._extract_function_calls(response),
            response_content=response_content,
            raw_response=response,
        )

    async def agentic_finalize_with_function_responses(
        self,
        *,
        user_prompt: str,
        system_instruction: str,
        forced_response_content: Any,
        function_responses: List[Dict[str, Any]],
        model_override: Optional[str] = None,
        thinking_level: Optional[str] = None,
    ) -> LLMResponse:
        """Finalize a forced tool round by feeding function responses back to model."""
        if forced_response_content is None:
            raise ValueError("forced_response_content is required for finalize round")
        if not function_responses:
            raise ValueError("function_responses is required for finalize round")

        thinking_config = self._get_thinking_config(thinking_level)
        model = model_override or settings.admin_agentic_model or self.main_model

        response_parts: List[Any] = []
        for item in function_responses:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            payload = item.get("response")
            if not isinstance(payload, dict):
                payload = {"result": payload}
            response_parts.append(
                types.Part.from_function_response(
                    name=name,
                    response=payload,
                )
            )
        if not response_parts:
            raise ValueError("no valid function responses to finalize")

        contents = [
            types.Content(role="user", parts=[types.Part(text=user_prompt)]),
            forced_response_content,
            types.Content(role="user", parts=response_parts),
        ]
        response = await self.client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                thinking_config=thinking_config,
            ),
        )
        return self._extract_response(response, thinking_level)
    
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
    
    # ==================== MCP 扩展方法 ====================

    async def generate_simple_stream(
        self,
        prompt: str,
        model_override: Optional[str] = None,
        thinking_level: Optional[str] = None,
    ):
        """
        流式文本生成（异步生成器）。

        与 generate_simple() 对标，但逐 chunk 返回。

        Yields:
            dict: {"type": "thought"/"answer", "text": str}
        """
        try:
            model = model_override or self.flash_model
            config_kwargs: dict = {}
            if thinking_level:
                config_kwargs["thinking_config"] = self._get_thinking_config(thinking_level)

            async for chunk in await self.client.aio.models.generate_content_stream(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(**config_kwargs) if config_kwargs else None,
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
            yield {"type": "error", "text": f"流式生成出错: {str(e)}"}

    async def generate_simple(
        self,
        prompt: str,
        model_override: Optional[str] = None,
        thinking_level: Optional[str] = None,
        timeout: float = 30.0,
    ) -> str:
        """
        简单文本生成（使用 Gemini 3 Flash）

        Args:
            prompt: 提示文本
            model_override: 可选模型覆盖
            thinking_level: 思考层级 (lowest/low/medium/high)
            timeout: 单次调用超时秒数

        Returns:
            生成的文本
        """
        try:
            model = model_override or self.flash_model
            config_kwargs: dict = {}
            if thinking_level:
                config_kwargs["thinking_config"] = self._get_thinking_config(thinking_level)
            response = await asyncio.wait_for(
                self.client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(**config_kwargs) if config_kwargs else None,
                ),
                timeout=timeout,
            )

            text = ""
            if hasattr(response, 'candidates') and response.candidates:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'text') and part.text:
                        # 跳过思考部分，只取实际回答
                        if not (hasattr(part, 'thought') and part.thought):
                            text += part.text

            return text.strip()

        except asyncio.TimeoutError:
            raise Exception(f"LLM 调用超时({timeout}s)")
        except Exception as e:
            raise Exception(f"文本生成失败: {str(e)}")
    
    async def classify_for_archive(self, prompt: str) -> Dict[str, Any]:
        """
        MCP 归档分类
        
        使用 LLM 对消息进行主题-话题分类
        
        Args:
            prompt: 分类提示（包含现有主题列表和待分类消息）
            
        Returns:
            分类结果字典，包含：
            - topic_id: 主题ID（可能为None表示新主题）
            - topic_title: 主题标题
            - thread_id: 话题ID（可能为None表示新话题）
            - thread_title: 话题标题
            - is_new_topic: 是否新主题
            - is_new_thread: 是否新话题
        """
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
            
            # 解析失败，返回默认值
            return self._default_classification_result()
            
        except Exception as e:
            print(f"MCP 分类失败: {str(e)}")
            return self._default_classification_result()
    
    def _default_classification_result(self) -> Dict[str, Any]:
        """返回默认分类结果"""
        return {
            "topic_id": None,
            "topic_title": "未分类",
            "thread_id": None,
            "thread_title": "通用讨论",
            "is_new_topic": True,
            "is_new_thread": True
        }
    
    async def extract_insight(self, messages_text: str) -> str:
        """
        从消息中提取见解
        
        Args:
            messages_text: 格式化的消息文本
            
        Returns:
            提取的见解内容（Markdown格式）
        """
        prompt = f"""请从以下对话中提取关键见解和要点：

{messages_text}

请总结：
1. 讨论的主要内容
2. 达成的结论或理解
3. 关键的知识点
4. 用户的疑问或关注点

请用简洁的 Markdown 格式输出，不超过 500 字。"""
        
        try:
            return await self.generate_simple(prompt)
        except Exception as e:
            print(f"见解提取失败: {str(e)}")
            return "见解提取失败"
    
    async def generate_evolution_note(
        self, 
        previous_content: str, 
        new_content: str
    ) -> str:
        """
        生成见解演变说明
        
        对比两个版本的理解，说明发生了什么变化
        
        Args:
            previous_content: 上一版本的见解内容
            new_content: 新版本的见解内容
            
        Returns:
            演变说明文本
        """
        prompt = f"""请比较以下两个版本的理解，简要说明发生了什么变化：

## 之前的理解
{previous_content}

## 现在的理解
{new_content}

请用一两句话说明用户理解的演变（如：深化了对XX的认识、纠正了之前的误解、扩展了XX方面的知识等）："""
        
        try:
            result = await self.generate_simple(prompt)
            return result.strip()[:200]  # 限制长度
        except Exception as e:
            print(f"演变说明生成失败: {str(e)}")
            return "理解有所更新"
    
    async def generate_thread_summary(self, insights_text: str) -> str:
        """
        生成话题总结
        
        基于所有见解版本生成话题总结
        
        Args:
            insights_text: 所有见解版本的文本
            
        Returns:
            话题总结（简短）
        """
        prompt = f"""请基于以下见解版本，生成一个简短的话题总结（50字以内）：

{insights_text}

总结："""
        
        try:
            result = await self.generate_simple(prompt)
            return result.strip()[:100]  # 限制长度
        except Exception as e:
            print(f"话题总结生成失败: {str(e)}")
            return "话题讨论"
    
    async def generate_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict[str, Any]],
        thinking_level: Optional[str] = None
    ) -> LLMResponse:
        """
        带工具调用的生成（用于 MCP）
        
        Args:
            messages: API 格式的消息列表
            tools: 工具定义列表
            thinking_level: 思考层级
            
        Returns:
            LLM 响应（可能包含工具调用）
        """
        try:
            thinking_config = self._get_thinking_config(thinking_level)
            
            # 构建内容
            contents = []
            for msg in messages:
                contents.append(types.Content(
                    role=msg["role"] if msg["role"] != "assistant" else "model",
                    parts=[types.Part(text=msg["content"])]
                ))
            
            # 构建工具配置
            tool_config = None
            if tools:
                function_declarations = []
                for tool in tools:
                    function_declarations.append(
                        types.FunctionDeclaration(
                            name=tool["name"],
                            description=tool.get("description", ""),
                            parameters=tool.get("parameters", {})
                        )
                    )
                tool_config = types.Tool(function_declarations=function_declarations)
            
            response = self.client.models.generate_content(
                model=self.main_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    thinking_config=thinking_config,
                    tools=[tool_config] if tool_config else None
                )
            )
            
            return self._extract_response(response, thinking_level)
            
        except Exception as e:
            error_msg = f"工具调用生成失败: {str(e)}"
            return LLMResponse(
                text=error_msg,
                thinking=ThinkingMetadata(
                    thinking_enabled=settings.thinking_enabled,
                    thinking_level=thinking_level or settings.thinking_level
                )
            )
