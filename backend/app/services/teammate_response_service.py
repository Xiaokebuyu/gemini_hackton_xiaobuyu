"""
队友响应服务。

负责：
- 判断每个队友是否应该回复
- 生成队友回复（顺序生成，后者可见前者）
- 分层模型选择（平常用 Flash，显式对话用 Pro）
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.models.party import (
    Party,
    PartyMember,
    TeammateModelConfig,
    TeammateResponseDecision,
    TeammateResponseResult,
    TeammateRoundResult,
)
from app.services.llm_service import LLMService
from app.services.teammate_visibility_manager import TeammateVisibilityManager


class TeammateResponseService:
    """队友响应服务"""

    def __init__(
        self,
        llm_service: Optional[LLMService] = None,
    ) -> None:
        self.llm_service = llm_service or LLMService()
        self.visibility_manager = TeammateVisibilityManager()

        # 加载提示词模板
        self.decision_prompt_path = Path("app/prompts/teammate_decision.md")
        self.response_prompt_path = Path("app/prompts/teammate_response.md")

        # 默认模型配置
        self.default_model_config = TeammateModelConfig()

    def _load_prompt(self, path: Path) -> str:
        """加载提示词模板"""
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    # =========================================================================
    # 主流程
    # =========================================================================

    async def process_round(
        self,
        party: Party,
        player_input: str,
        gm_response: str,
        context: Dict[str, Any],
        use_pro_model: bool = False,
    ) -> TeammateRoundResult:
        """
        完整的一轮队友响应流程。

        Args:
            party: 队伍信息
            player_input: 玩家输入
            gm_response: GM 叙述
            context: 上下文信息
            use_pro_model: 是否使用 Pro 模型（显式对话时）

        Returns:
            TeammateRoundResult: 本轮响应结果
        """
        start_time = time.time()
        responses = []
        responding_count = 0

        # 1. 获取活跃队友
        active_members = party.get_active_members()
        if not active_members:
            return TeammateRoundResult(
                responses=[],
                total_latency_ms=0,
                responding_count=0,
            )

        # 2. 决策：每个队友是否回复
        decisions = await self.decide_responses(
            party=party,
            player_input=player_input,
            gm_response=gm_response,
            context=context,
        )

        # 3. 按优先级排序
        decisions.sort(key=lambda d: d.priority, reverse=True)

        # 4. 顺序生成回复（后者可见前者）
        previous_responses: List[Dict[str, Any]] = []
        for decision in decisions:
            if not decision.should_respond:
                # 不回复，但仍记录
                responses.append(TeammateResponseResult(
                    character_id=decision.character_id,
                    name=self._get_member_name(party, decision.character_id),
                    response=None,
                    reaction="",
                ))
                continue

            # 获取队友信息
            member = party.get_member(decision.character_id)
            if not member:
                continue

            # 生成回复
            response_start = time.time()
            result = await self._generate_single_response(
                member=member,
                player_input=player_input,
                gm_response=gm_response,
                context=context,
                decision=decision,
                previous_responses=previous_responses,
                use_pro_model=use_pro_model,
            )
            response_latency = int((time.time() - response_start) * 1000)

            result.latency_ms = response_latency
            responses.append(result)

            if result.response:
                responding_count += 1
                previous_responses.append({
                    "name": result.name,
                    "response": result.response,
                    "reaction": result.reaction,
                })

        total_latency = int((time.time() - start_time) * 1000)
        return TeammateRoundResult(
            responses=responses,
            total_latency_ms=total_latency,
            responding_count=responding_count,
        )

    # =========================================================================
    # 决策阶段
    # =========================================================================

    async def decide_responses(
        self,
        party: Party,
        player_input: str,
        gm_response: str,
        context: Dict[str, Any],
    ) -> List[TeammateResponseDecision]:
        """决定每个队友是否回复"""
        decisions = []

        for member in party.get_active_members():
            decision = await self._decide_single_response(
                member=member,
                player_input=player_input,
                gm_response=gm_response,
                context=context,
            )
            decisions.append(decision)

        return decisions

    async def _decide_single_response(
        self,
        member: PartyMember,
        player_input: str,
        gm_response: str,
        context: Dict[str, Any],
    ) -> TeammateResponseDecision:
        """决定单个队友是否回复"""
        # 准备上下文
        filtered_context = self.visibility_manager.filter_context_for_teammate(
            teammate=member,
            full_context={
                "player_input": player_input,
                "gm_response": gm_response,
                **context,
            },
        )

        # 加载并填充提示词
        prompt_template = self._load_prompt(self.decision_prompt_path)
        if not prompt_template:
            # 无提示词，使用简单规则
            return self._simple_decision(member, player_input)

        location = context.get("location") or {}
        present_characters = ", ".join(
            location.get("npcs_present", [])
        )

        prompt = prompt_template.format(
            name=member.name,
            role=member.role.value,
            personality=member.personality,
            current_mood=member.current_mood,
            response_tendency=member.response_tendency,
            player_said=player_input,
            gm_narration=gm_response[:200],  # 限制长度
            location_name=location.get("location_name", "未知"),
            present_characters=present_characters or "无",
        )

        # 调用 LLM 决策
        try:
            result = await self.llm_service.generate_simple(
                prompt,
                model_override=settings.gemini_flash_model,
            )
            parsed = self.llm_service.parse_json(result)
            if parsed:
                return TeammateResponseDecision(
                    character_id=member.character_id,
                    should_respond=parsed.get("should_respond", False),
                    reason=parsed.get("reason", ""),
                    priority=parsed.get("priority", 5),
                    suggested_tone=parsed.get("suggested_tone"),
                )
        except Exception as e:
            print(f"[TeammateResponse] 决策失败 ({member.name}): {e}")

        # 决策失败，使用简单规则
        return self._simple_decision(member, player_input)

    def _simple_decision(
        self,
        member: PartyMember,
        player_input: str,
    ) -> TeammateResponseDecision:
        """简单规则决策（无 LLM）"""
        # 如果玩家提到队友名字，应该回复
        if member.name.lower() in player_input.lower():
            return TeammateResponseDecision(
                character_id=member.character_id,
                should_respond=True,
                reason="玩家提到了我",
                priority=10,
            )

        # 根据回复倾向决定
        import random
        should_respond = random.random() < member.response_tendency * 0.5

        return TeammateResponseDecision(
            character_id=member.character_id,
            should_respond=should_respond,
            reason="根据性格随机决定",
            priority=5,
        )

    # =========================================================================
    # 生成阶段
    # =========================================================================

    async def _generate_single_response(
        self,
        member: PartyMember,
        player_input: str,
        gm_response: str,
        context: Dict[str, Any],
        decision: TeammateResponseDecision,
        previous_responses: List[Dict[str, Any]],
        use_pro_model: bool = False,
    ) -> TeammateResponseResult:
        """生成单个队友的回复"""
        # 准备提示词
        prompt_template = self._load_prompt(self.response_prompt_path)
        if not prompt_template:
            return self._generate_fallback_response(member, decision)

        location = context.get("location") or {}

        # 格式化之前的回复
        prev_responses_text = ""
        if previous_responses:
            prev_responses_text = "\n".join(
                f"- {r['name']}: {r['response']} ({r['reaction']})"
                for r in previous_responses
            )
        else:
            prev_responses_text = "（还没有其他队友发言）"

        prompt = prompt_template.format(
            name=member.name,
            role=member.role.value,
            personality=member.personality,
            current_mood=member.current_mood,
            player_said=player_input,
            gm_narration=gm_response[:300],
            location_name=location.get("location_name", "未知"),
            suggested_tone=decision.suggested_tone or "自然",
            previous_responses=prev_responses_text,
        )

        # 选择模型
        model_config = member.model_config_override or self.default_model_config
        if use_pro_model:
            model = model_config.dialogue_model
            thinking_level = model_config.dialogue_thinking
        else:
            model = model_config.casual_model
            thinking_level = model_config.casual_thinking

        # 生成回复
        try:
            result = await self.llm_service.generate_simple(
                prompt,
                model_override=model,
            )
            parsed = self.llm_service.parse_json(result)
            if parsed:
                # 更新队友情绪
                if parsed.get("updated_mood"):
                    member.current_mood = parsed["updated_mood"]

                return TeammateResponseResult(
                    character_id=member.character_id,
                    name=member.name,
                    response=parsed.get("response"),
                    reaction=parsed.get("reaction", ""),
                    model_used=model,
                    thinking_level=thinking_level,
                )
        except Exception as e:
            print(f"[TeammateResponse] 生成失败 ({member.name}): {e}")

        return self._generate_fallback_response(member, decision)

    def _generate_fallback_response(
        self,
        member: PartyMember,
        decision: TeammateResponseDecision,
    ) -> TeammateResponseResult:
        """生成降级回复"""
        # 简单的预设回复
        fallback_responses = {
            "warrior": ("……", "沉默地点了点头"),
            "healer": ("小心点。", "关切地看着"),
            "mage": ("有意思。", "若有所思"),
            "rogue": ("了解。", "警觉地环顾四周"),
            "support": ("好的。", "认真地听着"),
            "scout": ("我去探探路。", "向前张望"),
            "scholar": ("让我想想……", "陷入沉思"),
        }

        response, reaction = fallback_responses.get(
            member.role.value,
            ("……", "默默听着"),
        )

        return TeammateResponseResult(
            character_id=member.character_id,
            name=member.name,
            response=response,
            reaction=reaction,
            model_used="fallback",
        )

    def _get_member_name(self, party: Party, character_id: str) -> str:
        """获取队友名字"""
        member = party.get_member(character_id)
        return member.name if member else character_id
