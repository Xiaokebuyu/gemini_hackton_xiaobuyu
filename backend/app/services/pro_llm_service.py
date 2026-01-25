"""
Pro LLM Service - Pro的LLM对话能力

使用Gemini 3的函数调用功能，让Pro能在对话中按需调用Flash获取记忆。

对话流程：
1. 用户发送消息
2. Pro分析是否需要记忆
3. 如需要，调用recall_memory工具
4. Flash返回记忆
5. Pro生成最终回复
"""
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from app.config import settings
from app.models.flash import NaturalRecallRequest
from app.models.pro import CharacterProfile, ChatMessage, SceneContext
from app.services.flash_service import FlashService


# 定义recall_memory函数声明
RECALL_MEMORY_DECLARATION = {
    "name": "recall_memory",
    "description": "当需要回忆过去的事情、人物、地点或经历时调用此函数。当用户问到你可能知道但需要仔细回想的事情时使用。",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "要回忆的内容描述，例如：'那个帮我修炉子的冒险者'、'昨天发生的事'、'我认识的猎人'"
            }
        },
        "required": ["query"]
    }
}


class ProLLMService:
    """Pro的LLM对话服务"""

    def __init__(
        self,
        flash_service: Optional[FlashService] = None,
    ) -> None:
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model = settings.gemini_main_model
        self.flash_service = flash_service or FlashService()

    def _build_system_prompt(
        self,
        profile: CharacterProfile,
        state: Dict[str, Any],
        scene: Optional[SceneContext],
        injected_memory: Optional[str] = None,
    ) -> str:
        """构建系统提示"""
        parts = []

        # 角色基础信息
        parts.append(f"# 你是 {profile.name}")
        parts.append("")

        parts.append("## 基本信息")
        if profile.occupation:
            parts.append(f"- 职业: {profile.occupation}")
        if profile.age:
            parts.append(f"- 年龄: {profile.age}")
        parts.append("")

        # 性格
        if profile.personality:
            parts.append("## 性格特点")
            parts.append(profile.personality)
            parts.append("")

        # 说话风格
        if profile.speech_pattern:
            parts.append("## 说话风格")
            parts.append(profile.speech_pattern)
            if profile.example_dialogue:
                parts.append(f'例句: "{profile.example_dialogue}"')
            parts.append("")

        # 当前状态
        if state:
            parts.append("## 当前状态")
            if state.get("mood"):
                parts.append(f"- 情绪: {state['mood']}")
            if state.get("goals"):
                parts.append(f"- 目标: {', '.join(state['goals'])}")
            if state.get("location"):
                parts.append(f"- 位置: {state['location']}")
            parts.append("")

        # 注入的记忆
        if injected_memory:
            parts.append("## 你知道的重要事情（刚刚回忆起来的）")
            parts.append(injected_memory)
            parts.append("")

        # 场景信息
        if scene:
            parts.append("## 当前场景")
            parts.append(scene.description)
            if scene.location:
                parts.append(f"地点: {scene.location}")
            if scene.present_characters:
                parts.append(f"在场的人: {', '.join(scene.present_characters)}")
            if scene.environment:
                parts.append(f"环境: {scene.environment}")
            parts.append("")

        # 行为准则
        parts.append("## 互动规则")
        parts.append("1. 始终保持角色扮演，不要跳出角色")
        parts.append("2. 你只知道你记忆中有的事情")
        parts.append("3. 如果被问到不知道的事，就说不知道或表示需要想想")
        parts.append("4. 你的回答应该反映你的性格和情绪")
        parts.append("5. 如果需要回忆更多细节，可以调用 recall_memory 工具")
        parts.append("6. 回复要简洁自然，像真人对话")

        return "\n".join(parts)

    def _build_contents(
        self,
        system_prompt: str,
        conversation_history: List[ChatMessage],
        user_message: str,
    ) -> List[types.Content]:
        """构建对话内容"""
        contents = []

        # 系统提示作为第一条user消息
        contents.append(types.Content(
            role="user",
            parts=[types.Part(text=f"[系统设定]\n{system_prompt}\n\n请以这个角色身份与我对话。")]
        ))
        contents.append(types.Content(
            role="model",
            parts=[types.Part(text="好的，我现在是这个角色。请开始对话。")]
        ))

        # 添加对话历史
        for msg in conversation_history:
            role = "user" if msg.role == "user" else "model"
            contents.append(types.Content(
                role=role,
                parts=[types.Part(text=msg.content)]
            ))

        # 添加当前用户消息
        contents.append(types.Content(
            role="user",
            parts=[types.Part(text=user_message)]
        ))

        return contents

    async def chat(
        self,
        world_id: str,
        character_id: str,
        user_message: str,
        profile: CharacterProfile,
        state: Dict[str, Any],
        scene: Optional[SceneContext] = None,
        conversation_history: Optional[List[ChatMessage]] = None,
        injected_memory: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        与角色对话

        Args:
            world_id: 世界ID
            character_id: 角色ID
            user_message: 用户消息
            profile: 角色资料
            state: 角色状态
            scene: 场景上下文
            conversation_history: 对话历史
            injected_memory: 预注入的记忆（场景加载时）

        Returns:
            {
                "response": "角色的回复",
                "tool_called": bool,
                "recalled_memory": "如果调用了工具，返回的记忆",
                "thinking": "思考过程（如果有）"
            }
        """
        conversation_history = conversation_history or []

        # 构建系统提示
        system_prompt = self._build_system_prompt(
            profile=profile,
            state=state,
            scene=scene,
            injected_memory=injected_memory,
        )

        # 构建对话内容
        contents = self._build_contents(
            system_prompt=system_prompt,
            conversation_history=conversation_history,
            user_message=user_message,
        )

        # 配置工具
        tools = types.Tool(function_declarations=[RECALL_MEMORY_DECLARATION])
        config = types.GenerateContentConfig(
            tools=[tools],
            # 让模型自动决定是否调用工具
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode='AUTO')
            ),
        )

        # 第一次调用模型
        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )

        # 检查是否有函数调用
        tool_called = False
        recalled_memory = None
        thinking = None

        if response.candidates and response.candidates[0].content.parts:
            first_part = response.candidates[0].content.parts[0]

            # 检查是否是函数调用
            if hasattr(first_part, 'function_call') and first_part.function_call:
                function_call = first_part.function_call
                tool_called = True

                if function_call.name == "recall_memory":
                    query = function_call.args.get("query", "")

                    # 调用Flash获取记忆
                    recall_result = await self.flash_service.recall_memory_natural(
                        world_id=world_id,
                        character_id=character_id,
                        request=NaturalRecallRequest(
                            query=query,
                            translate=True,
                        )
                    )

                    recalled_memory = recall_result.translated_memory or "（没有找到相关记忆）"

                    # 构建函数响应
                    function_response_part = types.Part.from_function_response(
                        name=function_call.name,
                        response={"memory": recalled_memory},
                    )

                    # 将函数调用和响应添加到对话中
                    contents.append(response.candidates[0].content)
                    contents.append(types.Content(
                        role="user",
                        parts=[function_response_part]
                    ))

                    # 再次调用模型获取最终回复
                    final_response = self.client.models.generate_content(
                        model=self.model,
                        contents=contents,
                        config=config,
                    )

                    # 提取最终回复
                    final_text = self._extract_text(final_response)

                    return {
                        "response": final_text,
                        "tool_called": True,
                        "recalled_memory": recalled_memory,
                        "recall_query": query,
                        "thinking": thinking,
                    }

        # 没有函数调用，直接返回回复
        response_text = self._extract_text(response)

        return {
            "response": response_text,
            "tool_called": False,
            "recalled_memory": None,
            "thinking": thinking,
        }

    def _extract_text(self, response) -> str:
        """从响应中提取文本"""
        text = ""
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if hasattr(part, 'text') and part.text:
                    # 跳过思考部分
                    if not (hasattr(part, 'thought') and part.thought):
                        text += part.text
        return text.strip()

    async def chat_simple(
        self,
        world_id: str,
        character_id: str,
        user_message: str,
        profile: CharacterProfile,
        state: Dict[str, Any],
        scene: Optional[SceneContext] = None,
        conversation_history: Optional[List[ChatMessage]] = None,
    ) -> str:
        """
        简化的对话接口，只返回回复文本

        适用于不需要详细信息的场景
        """
        result = await self.chat(
            world_id=world_id,
            character_id=character_id,
            user_message=user_message,
            profile=profile,
            state=state,
            scene=scene,
            conversation_history=conversation_history,
        )
        return result["response"]
