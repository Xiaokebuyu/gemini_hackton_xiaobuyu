"""
工具调用服务

整合 Gemini API 与工具执行，实现完整的工具调用循环
"""
from typing import List, Dict, Any, Optional, AsyncGenerator
from dataclasses import dataclass, field
from google import genai
from google.genai import types

from app.config import settings
from app.tools.definitions import TOOL_DECLARATIONS, get_tools_config, ToolName
from app.tools.executor import ToolExecutor, ToolResult


@dataclass
class ToolCall:
    """单次工具调用记录"""
    name: str
    args: Dict[str, Any]
    result: Optional[ToolResult] = None


@dataclass
class ThinkingInfo:
    """思考信息"""
    enabled: bool = False
    level: str = "medium"
    summary: str = ""
    token_count: int = 0


@dataclass
class ToolServiceResult:
    """工具服务最终结果"""
    response: str
    tool_calls: List[ToolCall] = field(default_factory=list)
    thinking: ThinkingInfo = field(default_factory=ThinkingInfo)
    total_rounds: int = 0


class ToolService:
    """
    工具调用服务
    
    实现完整的工具调用循环：
    1. 发送用户请求到模型
    2. 检测模型返回的工具调用
    3. 执行工具，将结果返回给模型
    4. 重复直到模型返回最终文本响应
    """
    
    def __init__(self, executor: ToolExecutor = None):
        """
        初始化服务
        
        Args:
            executor: 工具执行器，默认使用内置演示执行器
        """
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model = settings.gemini_main_model
        self.executor = executor or ToolExecutor()
        self.max_rounds = 10  # 最大工具调用轮次
    
    def _get_thinking_config(self, level: str = None) -> types.ThinkingConfig:
        """构建思考配置"""
        if not settings.thinking_enabled:
            return types.ThinkingConfig(
                thinking_level="lowest",
                include_thoughts=False
            )
        return types.ThinkingConfig(
            thinking_level=level or settings.thinking_level,
            include_thoughts=settings.include_thoughts
        )
    
    def _build_config(
        self,
        enabled_tools: List[ToolName] = None,
        mode: str = "AUTO",
        thinking_level: str = None
    ) -> types.GenerateContentConfig:
        """构建完整的生成配置"""
        # 工具配置
        if enabled_tools is None:
            declarations = TOOL_DECLARATIONS
        else:
            enabled_names = {t.value for t in enabled_tools}
            declarations = [d for d in TOOL_DECLARATIONS if d["name"] in enabled_names]
        
        tools = types.Tool(function_declarations=declarations)
        tool_config = types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode=mode)
        )
        
        # 思考配置
        thinking_config = self._get_thinking_config(thinking_level)
        
        return types.GenerateContentConfig(
            tools=[tools],
            tool_config=tool_config,
            thinking_config=thinking_config
        )
    
    def _extract_function_calls(self, response) -> List[Dict[str, Any]]:
        """从响应中提取函数调用"""
        calls = []
        if not hasattr(response, 'candidates') or not response.candidates:
            return calls
        
        for part in response.candidates[0].content.parts:
            if hasattr(part, 'function_call') and part.function_call:
                fc = part.function_call
                calls.append({
                    "name": fc.name,
                    "args": dict(fc.args) if fc.args else {}
                })
        
        return calls
    
    def _extract_text_and_thinking(self, response) -> tuple:
        """从响应中提取文本和思考摘要"""
        text = ""
        thinking_summary = ""
        
        if not hasattr(response, 'candidates') or not response.candidates:
            return text, thinking_summary
        
        for part in response.candidates[0].content.parts:
            if not hasattr(part, 'text') or not part.text:
                continue
            if hasattr(part, 'thought') and part.thought:
                thinking_summary += part.text
            else:
                text += part.text
        
        return text.strip(), thinking_summary.strip()
    
    def _get_thinking_token_count(self, response) -> int:
        """获取思考 token 数量"""
        if hasattr(response, 'usage_metadata'):
            usage = response.usage_metadata
            if hasattr(usage, 'thoughts_token_count'):
                return usage.thoughts_token_count or 0
        return 0
    
    async def run(
        self,
        user_message: str,
        system_prompt: str = None,
        context: str = None,
        enabled_tools: List[ToolName] = None,
        tool_mode: str = "AUTO",
        thinking_level: str = None
    ) -> ToolServiceResult:
        """
        运行工具调用循环
        
        Args:
            user_message: 用户消息
            system_prompt: 系统提示词
            context: 额外上下文
            enabled_tools: 启用的工具列表
            tool_mode: 工具调用模式 (AUTO/ANY/NONE)
            thinking_level: 思考层级
            
        Returns:
            ToolServiceResult: 包含响应、工具调用记录和思考信息
        """
        # 构建初始消息
        contents = []
        
        # 构建提示
        prompt_parts = []
        if system_prompt:
            prompt_parts.append(system_prompt)
        if context:
            prompt_parts.append(f"## 上下文\n\n{context}")
        prompt_parts.append(f"## 用户问题\n\n{user_message}")
        
        full_prompt = "\n\n---\n\n".join(prompt_parts)
        contents.append(types.Content(
            role="user",
            parts=[types.Part(text=full_prompt)]
        ))
        
        # 构建配置
        config = self._build_config(enabled_tools, tool_mode, thinking_level)
        
        # 工具调用记录
        all_tool_calls: List[ToolCall] = []
        final_thinking = ThinkingInfo(
            enabled=settings.thinking_enabled,
            level=thinking_level or settings.thinking_level
        )
        
        # 循环处理
        for round_num in range(self.max_rounds):
            # 调用模型
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config
            )
            
            # 提取思考信息（只在第一轮记录）
            if round_num == 0:
                _, thinking_summary = self._extract_text_and_thinking(response)
                final_thinking.summary = thinking_summary
                final_thinking.token_count = self._get_thinking_token_count(response)
            
            # 检查是否有函数调用
            function_calls = self._extract_function_calls(response)
            
            if not function_calls:
                # 无函数调用，提取最终响应
                text, _ = self._extract_text_and_thinking(response)
                return ToolServiceResult(
                    response=text,
                    tool_calls=all_tool_calls,
                    thinking=final_thinking,
                    total_rounds=round_num + 1
                )
            
            # 执行所有函数调用
            function_responses = []
            for fc in function_calls:
                tool_call = ToolCall(name=fc["name"], args=fc["args"])
                
                # 执行工具
                result = await self.executor.execute(fc["name"], fc["args"])
                tool_call.result = result
                all_tool_calls.append(tool_call)
                
                # 构建函数响应
                function_responses.append(
                    types.Part.from_function_response(
                        name=fc["name"],
                        response={"result": result.data if result.success else {"error": result.error}}
                    )
                )
            
            # 将模型响应和函数结果加入对话历史
            contents.append(response.candidates[0].content)
            contents.append(types.Content(
                role="user",
                parts=function_responses
            ))
        
        # 达到最大轮次
        return ToolServiceResult(
            response="达到最大工具调用轮次限制",
            tool_calls=all_tool_calls,
            thinking=final_thinking,
            total_rounds=self.max_rounds
        )
    
    async def run_stream(
        self,
        user_message: str,
        system_prompt: str = None,
        context: str = None,
        enabled_tools: List[ToolName] = None,
        thinking_level: str = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        流式运行工具调用循环
        
        Yields:
            dict: 包含 type ('thought'/'answer'/'tool_call'/'tool_result') 和相关数据
        """
        # 构建初始消息
        prompt_parts = []
        if system_prompt:
            prompt_parts.append(system_prompt)
        if context:
            prompt_parts.append(f"## 上下文\n\n{context}")
        prompt_parts.append(f"## 用户问题\n\n{user_message}")
        
        full_prompt = "\n\n---\n\n".join(prompt_parts)
        contents = [types.Content(
            role="user",
            parts=[types.Part(text=full_prompt)]
        )]
        
        # 构建配置
        config = self._build_config(enabled_tools, "AUTO", thinking_level)
        
        for round_num in range(self.max_rounds):
            # 流式调用
            current_text = ""
            current_thoughts = ""
            function_calls = []
            
            for chunk in self.client.models.generate_content_stream(
                model=self.model,
                contents=contents,
                config=config
            ):
                if not hasattr(chunk, 'candidates') or not chunk.candidates:
                    continue
                
                for part in chunk.candidates[0].content.parts:
                    # 检查函数调用
                    if hasattr(part, 'function_call') and part.function_call:
                        fc = part.function_call
                        function_calls.append({
                            "name": fc.name,
                            "args": dict(fc.args) if fc.args else {}
                        })
                        yield {
                            "type": "tool_call",
                            "name": fc.name,
                            "args": dict(fc.args) if fc.args else {}
                        }
                    
                    # 检查文本
                    if hasattr(part, 'text') and part.text:
                        if hasattr(part, 'thought') and part.thought:
                            current_thoughts += part.text
                            yield {"type": "thought", "text": part.text}
                        else:
                            current_text += part.text
                            yield {"type": "answer", "text": part.text}
            
            # 如果没有函数调用，结束
            if not function_calls:
                yield {"type": "done", "total_rounds": round_num + 1}
                return
            
            # 执行函数并继续
            # 需要重新获取完整响应来构建历史（流式API限制）
            full_response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config
            )
            
            function_responses = []
            for fc in function_calls:
                result = await self.executor.execute(fc["name"], fc["args"])
                yield {
                    "type": "tool_result",
                    "name": fc["name"],
                    "success": result.success,
                    "data": result.data if result.success else result.error
                }
                
                function_responses.append(
                    types.Part.from_function_response(
                        name=fc["name"],
                        response={"result": result.data if result.success else {"error": result.error}}
                    )
                )
            
            contents.append(full_response.candidates[0].content)
            contents.append(types.Content(
                role="user",
                parts=function_responses
            ))
        
        yield {"type": "error", "message": "达到最大工具调用轮次限制"}
