"""
队友响应服务。

负责：
- 判断每个队友是否应该回复
- 生成队友回复（并发，每个队友独立）
- 通过 InstanceManager 维护每个队友的独立上下文窗口
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

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

if TYPE_CHECKING:
    from app.services.instance_manager import InstanceManager

logger = logging.getLogger(__name__)


class TeammateResponseService:
    """队友响应服务"""

    def __init__(
        self,
        llm_service: Optional[LLMService] = None,
        instance_manager: Optional["InstanceManager"] = None,
    ) -> None:
        self.llm_service = llm_service or LLMService()
        self.visibility_manager = TeammateVisibilityManager()
        self.instance_manager = instance_manager

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
    ) -> TeammateRoundResult:
        """
        完整的一轮队友响应流程（并发）。

        Args:
            party: 队伍信息
            player_input: 玩家输入
            gm_response: GM 叙述
            context: 上下文信息

        Returns:
            TeammateRoundResult: 本轮响应结果
        """
        start_time = time.time()
        responses: List[TeammateResponseResult] = []
        responding_count = 0

        # 1. 获取活跃队友
        active_members = party.get_active_members()
        if not active_members:
            return TeammateRoundResult(
                responses=[],
                total_latency_ms=0,
                responding_count=0,
            )

        world_id = context.get("world_id") or party.world_id

        # 2. 所有队友先接收本轮消息（即使后续不发言，也会更新独立上下文）
        preloaded_histories: Dict[str, Optional[str]] = {}
        if self.instance_manager is not None and world_id:
            # 2a. 注入上轮队友发言到各自 ContextWindow（排除自己）
            last_responses = context.get("last_teammate_responses", [])
            if last_responses:
                for member in active_members:
                    try:
                        instance = await self.instance_manager.get_or_create(
                            member.character_id, world_id
                        )
                        for resp in last_responses:
                            if resp.get("character_id") != member.character_id and resp.get("response"):
                                resp_name = resp.get("name", resp.get("character_id", "队友"))
                                instance.context_window.add_message(
                                    "system",
                                    f"[队友 {resp_name}] {resp['response']}"
                                )
                    except Exception as e:
                        logger.debug(
                            "[TeammateResponse] 跨轮队友发言注入失败 (%s): %s",
                            member.name, e,
                        )

            # 2b. 注入本轮公共信息（玩家输入 + GM 叙述）
            # 私密模式下跳过非目标队友的玩家输入注入
            is_private = context.get("is_private", False)
            private_target = context.get("private_target")

            async def _inject(member: PartyMember):
                inject_player = player_input
                if is_private and private_target and member.character_id != private_target:
                    inject_player = ""  # 非目标队友不注入玩家输入
                history_text = await self._inject_round_to_instance(
                    member=member,
                    world_id=world_id,
                    player_input=inject_player,
                    gm_response=gm_response,
                )
                return member.character_id, history_text

            inject_results = await asyncio.gather(
                *(_inject(member) for member in active_members),
                return_exceptions=True,
            )
            for member, item in zip(active_members, inject_results):
                if isinstance(item, Exception):
                    logger.error(
                        "[TeammateResponse] 轮次注入失败 (%s): %s",
                        member.name,
                        item,
                        exc_info=True,
                    )
                    continue
                char_id, history_text = item
                preloaded_histories[char_id] = history_text

        # 3. 每个队友独立决策是否回复
        decisions = await self.decide_responses(
            party=party,
            player_input=player_input,
            gm_response=gm_response,
            context=context,
        )
        decision_by_char = {d.character_id: d for d in decisions}

        # 4. 顺序生成队友响应，传递 previous_responses 实现当轮互相感知
        collected_responses: List[Dict[str, Any]] = []
        for member in active_members:
            decision = decision_by_char.get(member.character_id) or self._simple_decision(
                member=member,
                player_input=player_input,
                gm_response=gm_response,
                context=context,
            )

            if not decision.should_respond:
                responses.append(
                    TeammateResponseResult(
                        character_id=member.character_id,
                        name=member.name,
                        response=None,
                        reaction=decision.reason,
                        model_used="skip",
                    )
                )
                continue

            try:
                response_start = time.time()
                preloaded_history = preloaded_histories.get(member.character_id)
                result = await self._generate_single_response(
                    member=member,
                    player_input=player_input,
                    gm_response=gm_response,
                    context=context,
                    decision=decision,
                    previous_responses=collected_responses,
                    preloaded_history=preloaded_history,
                    inject_round=(self.instance_manager is not None and preloaded_history is None),
                )
                result.latency_ms = int((time.time() - response_start) * 1000)

                if result.response:
                    collected_responses.append({
                        "name": result.name,
                        "response": result.response,
                        "reaction": result.reaction,
                    })
                    responding_count += 1

                responses.append(result)
            except Exception as e:
                logger.error("[TeammateResponse] 顺序生成失败 (%s): %s", member.name, e, exc_info=True)
                responses.append(
                    TeammateResponseResult(
                        character_id=member.character_id,
                        name=member.name,
                        response=None,
                        reaction="",
                        model_used="error",
                    )
                )

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
        members_override: Optional[List[PartyMember]] = None,
    ) -> List[TeammateResponseDecision]:
        """决定每个队友是否回复"""
        members = members_override or party.get_active_members()
        tasks = [
            self._decide_single_response(
                member=member,
                player_input=player_input,
                gm_response=gm_response,
                context=context,
            )
            for member in members
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        decisions: List[TeammateResponseDecision] = []
        for member, result in zip(members, results):
            if isinstance(result, Exception):
                logger.error(
                    "[TeammateResponse] 决策失败 (%s): %s",
                    member.name,
                    result,
                    exc_info=True,
                )
                decisions.append(
                    self._simple_decision(
                        member=member,
                        player_input=player_input,
                        gm_response=gm_response,
                        context=context,
                    )
                )
                continue
            decisions.append(result)
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

        # 使用 filtered context 中的 player_said（支持私密模式）
        effective_player_input = filtered_context.get("player_said", player_input)
        # 私密模式下非目标队友没有 player_said，不应触发响应
        if context.get("is_private") and not filtered_context.get("player_said"):
            return TeammateResponseDecision(
                character_id=member.character_id,
                should_respond=False,
                reason="私密对话中，无法听到玩家",
                priority=0,
            )

        # 加载并填充提示词
        prompt_template = self._load_prompt(self.decision_prompt_path)
        if not prompt_template:
            # 无提示词，使用简单规则
            return self._simple_decision(member, effective_player_input, gm_response, context)

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
            player_said=effective_player_input,
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
            logger.warning("[TeammateResponse] LLM决策失败 (%s), fallback到简单规则: %s", member.name, e)

        # 决策失败，使用简单规则
        return self._simple_decision(member, player_input, gm_response, context)

    def _simple_decision(
        self,
        member: PartyMember,
        player_input: str,
        gm_response: str = "",
        context: Optional[Dict[str, Any]] = None,
    ) -> TeammateResponseDecision:
        """确定性规则决策（无随机数）。"""
        player_text = (player_input or "").lower()
        gm_text = (gm_response or "").lower()
        merged_text = f"{player_text}\n{gm_text}"

        # 玩家直接点名
        if member.name.lower() in player_text or member.character_id.lower() in player_text:
            return TeammateResponseDecision(
                character_id=member.character_id,
                should_respond=True,
                reason="玩家点名",
                priority=10,
            )

        # GM 叙述直接点名
        if member.name.lower() in gm_text or member.character_id.lower() in gm_text:
            return TeammateResponseDecision(
                character_id=member.character_id,
                should_respond=True,
                reason="GM点到我",
                priority=9,
            )

        # 职责相关关键词
        role_keywords: Dict[str, List[str]] = {
            "healer": ["治疗", "受伤", "恢复", "祈祷", "healing", "heal"],
            "mage": ["魔法", "法术", "咒", "mana", "spell"],
            "warrior": ["战斗", "冲锋", "守住", "attack", "frontline"],
            "rogue": ["潜行", "陷阱", "侦查", "stealth", "trap"],
            "scout": ["侦查", "追踪", "前方", "scout", "track"],
            "scholar": ["线索", "知识", "古文", "lore", "history"],
            "support": ["帮忙", "支援", "配合", "support"],
        }
        for keyword in role_keywords.get(member.role.value, []):
            if keyword in merged_text and member.response_tendency >= 0.5:
                return TeammateResponseDecision(
                    character_id=member.character_id,
                    should_respond=True,
                    reason="话题与职责相关",
                    priority=7,
                )

        # 玩家问句，更可能触发队友发言
        question_cues = ("?", "？", "吗", "如何", "怎么", "为什么", "是否", "要不要", "谁")
        if any(cue in (player_input or "") for cue in question_cues) and member.response_tendency >= 0.65:
            return TeammateResponseDecision(
                character_id=member.character_id,
                should_respond=True,
                reason="玩家在提问",
                priority=6,
            )

        # 紧急场景优先发言
        urgent_cues = ("危险", "战斗", "敌人", "救命", "快", "urgent", "help", "attack")
        if any(cue in merged_text for cue in urgent_cues):
            return TeammateResponseDecision(
                character_id=member.character_id,
                should_respond=True,
                reason="紧急情境",
                priority=8,
            )

        # 高健谈角色可主动补充
        if member.response_tendency >= 0.9:
            return TeammateResponseDecision(
                character_id=member.character_id,
                should_respond=True,
                reason="角色健谈",
                priority=4,
            )

        return TeammateResponseDecision(
            character_id=member.character_id,
            should_respond=False,
            reason="话题关联较低",
            priority=1,
        )

    # =========================================================================
    # 生成阶段
    # =========================================================================

    async def _inject_round_to_instance(
        self,
        member: PartyMember,
        world_id: str,
        player_input: str,
        gm_response: str,
    ) -> Optional[str]:
        """将当前轮公共信息写入队友实例的 context_window，返回最近对话历史文本。"""
        if not self.instance_manager:
            return None

        try:
            instance = await self.instance_manager.get_or_create(
                member.character_id, world_id
            )
            # 写入公共信息
            user_add = instance.context_window.add_message(
                "user", f"[玩家] {player_input}"
            )
            gm_add = instance.context_window.add_message(
                "system", f"[GM] {gm_response[:500]}"
            )
            if user_add.should_graphize or gm_add.should_graphize:
                await self.instance_manager.maybe_graphize_instance(
                    world_id=world_id,
                    npc_id=member.character_id,
                )
            # 提取最近对话历史
            recent = instance.context_window.get_recent_messages(count=10)
            if recent:
                lines = []
                for msg in recent:
                    lines.append(f"{msg.role}: {msg.content}")
                return "\n".join(lines)
        except Exception as e:
            logger.debug("[TeammateResponse] 实例注入失败 (%s): %s", member.name, e)
        return None

    async def _write_response_to_instance(
        self,
        member: PartyMember,
        world_id: str,
        response_text: str,
    ) -> None:
        """将队友回复写回其实例 context_window。"""
        if not self.instance_manager:
            return
        try:
            instance = self.instance_manager.get(world_id, member.character_id)
            if instance:
                add_result = instance.context_window.add_message(
                    "assistant", f"[{member.name}] {response_text}"
                )
                if add_result.should_graphize:
                    await self.instance_manager.maybe_graphize_instance(
                        world_id=world_id,
                        npc_id=member.character_id,
                    )
                instance.state.conversation_turn_count += 1
        except Exception as e:
            logger.debug("[TeammateResponse] 回写失败 (%s): %s", member.name, e)

    async def _generate_single_response(
        self,
        member: PartyMember,
        player_input: str,
        gm_response: str,
        context: Dict[str, Any],
        decision: TeammateResponseDecision,
        previous_responses: List[Dict[str, Any]],
        preloaded_history: Optional[str] = None,
        inject_round: bool = True,
    ) -> TeammateResponseResult:
        """生成单个队友的回复"""
        # 准备提示词
        prompt_template = self._load_prompt(self.response_prompt_path)
        if not prompt_template:
            logger.error("[TeammateResponse] 缺少提示词模板: %s", self.response_prompt_path)
            return TeammateResponseResult(
                character_id=member.character_id,
                name=member.name,
                response=None,
                reaction="",
                model_used="error",
            )

        location = context.get("location") or {}
        world_id = context.get("world_id") or ""

        # 注入当前轮到队友实例（如果有 InstanceManager）
        instance_history = preloaded_history
        if inject_round:
            instance_history = await self._inject_round_to_instance(
                member, world_id, player_input, gm_response,
            )

        # 格式化之前的回复
        prev_responses_text = ""
        if previous_responses:
            prev_responses_text = "\n".join(
                f"- {r['name']}: {r['response']} ({r['reaction']})"
                for r in previous_responses
            )
        else:
            prev_responses_text = "（还没有其他队友发言）"

        # 构建世界知识变量
        location_desc = location.get("atmosphere") or location.get("description") or ""
        available_dests = location.get("available_destinations", [])
        if isinstance(available_dests, list):
            available_dests_text = ", ".join(
                d.get("name", d) if isinstance(d, dict) else str(d)
                for d in available_dests
            ) or "未知"
        else:
            available_dests_text = str(available_dests) or "未知"
        sub_locs = location.get("available_sub_locations", location.get("sub_locations", []))
        if isinstance(sub_locs, list):
            sub_locs_text = ", ".join(
                s.get("name", s) if isinstance(s, dict) else str(s)
                for s in sub_locs
            ) or "无"
        else:
            sub_locs_text = str(sub_locs) or "无"

        # 使用 filtered context 的 player_said（支持私密模式）
        filtered_context = self.visibility_manager.filter_context_for_teammate(
            teammate=member,
            full_context={
                "player_input": player_input,
                "gm_response": gm_response,
                **context,
            },
        )
        effective_player_input = filtered_context.get("player_said", player_input)

        prompt = prompt_template.format(
            name=member.name,
            role=member.role.value,
            personality=member.personality,
            current_mood=member.current_mood,
            player_said=effective_player_input,
            gm_narration=gm_response[:300],
            location_name=location.get("location_name", "未知"),
            location_description=location_desc,
            available_destinations=available_dests_text,
            sub_locations=sub_locs_text,
            suggested_tone=decision.suggested_tone or "自然",
            previous_responses=prev_responses_text,
        )

        # 如果有实例历史，追加到 prompt 以提供上下文连续性
        if instance_history:
            prompt += f"\n\n## 你的近期记忆\n{instance_history}\n"

        # Admin Flash 为该队友编排的图谱上下文
        teammate_packages = context.get("teammate_context_packages") or {}
        teammate_package = teammate_packages.get(member.character_id)
        if isinstance(teammate_package, dict) and teammate_package:
            pkg_text = self._format_context_package(teammate_package)
            prompt += f"\n\n## 你的图谱上下文（Flash编排）\n{pkg_text}\n"

        teammate_memory_summaries = context.get("teammate_memory_summaries") or {}
        memory_summary = teammate_memory_summaries.get(member.character_id)
        if memory_summary:
            prompt += f"\n\n## 你的记忆摘要\n{memory_summary}\n"

        # 统一使用 Flash 模型，"low" thinking 足以保证队友回复质量
        model = settings.admin_flash_model
        thinking_level = "low"

        # 生成回复
        try:
            result = await self.llm_service.generate_simple(
                prompt,
                model_override=model,
                thinking_level=thinking_level,
            )
            parsed = self.llm_service.parse_json(result)
            if not parsed:
                logger.error("[TeammateResponse] JSON 解析失败 (%s): %s", member.name, result[:200])
                return TeammateResponseResult(
                    character_id=member.character_id,
                    name=member.name,
                    response=None,
                    reaction="",
                    model_used="error",
                    thinking_level=thinking_level,
                )

            # 更新队友情绪
            if parsed.get("updated_mood"):
                member.current_mood = parsed["updated_mood"]

            response_text = parsed.get("response")
            # 写回实例
            if response_text:
                await self._write_response_to_instance(
                    member, world_id, response_text
                )

            return TeammateResponseResult(
                character_id=member.character_id,
                name=member.name,
                response=response_text,
                reaction=parsed.get("reaction", ""),
                model_used=model,
                thinking_level=thinking_level,
            )
        except Exception as e:
            logger.warning("[TeammateResponse] 生成失败 (%s): %s", member.name, e)
            return TeammateResponseResult(
                character_id=member.character_id,
                name=member.name,
                response=None,
                reaction="",
                model_used="error",
                thinking_level=thinking_level,
            )

    # =========================================================================
    # 流式生成
    # =========================================================================

    async def process_round_stream(
        self,
        party: Party,
        player_input: str,
        gm_response: str,
        context: Dict[str, Any],
    ):
        """
        流式队友响应（异步生成器）。

        Yields:
            dict: teammate_start / teammate_chunk / teammate_end / teammate_skip
        """
        active_members = party.get_active_members()
        if not active_members:
            return

        world_id = context.get("world_id") or party.world_id
        is_private = context.get("is_private", False)
        private_target = context.get("private_target")

        # 消息注入（复用同步逻辑）
        preloaded_histories: Dict[str, Optional[str]] = {}
        if self.instance_manager is not None and world_id:
            last_responses = context.get("last_teammate_responses", [])
            if last_responses:
                for member in active_members:
                    try:
                        instance = await self.instance_manager.get_or_create(
                            member.character_id, world_id
                        )
                        for resp in last_responses:
                            if resp.get("character_id") != member.character_id and resp.get("response"):
                                resp_name = resp.get("name", resp.get("character_id", "队友"))
                                instance.context_window.add_message(
                                    "system",
                                    f"[队友 {resp_name}] {resp['response']}"
                                )
                    except Exception as e:
                        logger.debug("[TeammateResponse] 跨轮注入失败 (%s): %s", member.name, e)

            # 注入本轮信息（私密模式下跳过非目标队友）
            for member in active_members:
                if is_private and private_target and member.character_id != private_target:
                    continue
                try:
                    history_text = await self._inject_round_to_instance(
                        member=member,
                        world_id=world_id,
                        player_input=player_input,
                        gm_response=gm_response,
                    )
                    preloaded_histories[member.character_id] = history_text
                except Exception as e:
                    logger.error("[TeammateResponse] 轮次注入失败 (%s): %s", member.name, e)

        # 决策（私密模式仅目标参与决策）
        if is_private and private_target:
            members_for_decision = [m for m in active_members if m.character_id == private_target]
        else:
            members_for_decision = active_members

        decisions = await self.decide_responses(
            party=party,
            player_input=player_input,
            gm_response=gm_response,
            context=context,
            members_override=members_for_decision,
        )
        decision_by_char = {d.character_id: d for d in decisions}

        # 流式生成
        collected_responses: List[Dict[str, Any]] = []
        for member in active_members:
            # 私密模式下跳过非目标
            if is_private and private_target and member.character_id != private_target:
                yield {
                    "type": "teammate_skip",
                    "character_id": member.character_id,
                    "name": member.name,
                    "reason": "私密对话",
                }
                continue

            decision = decision_by_char.get(member.character_id) or self._simple_decision(
                member=member,
                player_input=player_input,
                gm_response=gm_response,
                context=context,
            )

            if not decision.should_respond:
                yield {
                    "type": "teammate_skip",
                    "character_id": member.character_id,
                    "name": member.name,
                    "reason": decision.reason or "无关话题",
                }
                continue

            yield {
                "type": "teammate_start",
                "character_id": member.character_id,
                "name": member.name,
            }

            # 流式生成单个队友响应
            full_text = ""
            reaction = ""
            try:
                preloaded_history = preloaded_histories.get(member.character_id)
                async for chunk in self._generate_single_response_stream(
                    member=member,
                    player_input=player_input,
                    gm_response=gm_response,
                    context=context,
                    decision=decision,
                    previous_responses=collected_responses,
                    preloaded_history=preloaded_history,
                    inject_round=(self.instance_manager is not None and preloaded_history is None),
                ):
                    if chunk["type"] == "answer":
                        full_text += chunk["text"]
                        yield {
                            "type": "teammate_chunk",
                            "character_id": member.character_id,
                            "text": chunk["text"],
                        }

                # 流式完成后，解析结构化数据（从完整文本提取 reaction/mood）
                parsed = self.llm_service.parse_json(full_text)
                if parsed:
                    response_text = parsed.get("response", full_text)
                    reaction = parsed.get("reaction", "")
                    if parsed.get("updated_mood"):
                        member.current_mood = parsed["updated_mood"]
                else:
                    response_text = full_text

                if response_text:
                    collected_responses.append({
                        "name": member.name,
                        "response": response_text,
                        "reaction": reaction,
                    })
                    await self._write_response_to_instance(member, world_id, response_text)

                yield {
                    "type": "teammate_end",
                    "character_id": member.character_id,
                    "name": member.name,
                    "response": response_text,
                    "reaction": reaction,
                }
            except Exception as e:
                logger.error("[TeammateResponse] 流式生成失败 (%s): %s", member.name, e, exc_info=True)
                yield {
                    "type": "teammate_end",
                    "character_id": member.character_id,
                    "name": member.name,
                    "response": None,
                    "reaction": "",
                }

    async def _generate_single_response_stream(
        self,
        member: PartyMember,
        player_input: str,
        gm_response: str,
        context: Dict[str, Any],
        decision: TeammateResponseDecision,
        previous_responses: List[Dict[str, Any]],
        preloaded_history: Optional[str] = None,
        inject_round: bool = True,
    ):
        """流式生成单个队友回复，yield {"type": "thought"/"answer", "text": str}。"""
        prompt_template = self._load_prompt(self.response_prompt_path)
        if not prompt_template:
            return

        location = context.get("location") or {}
        world_id = context.get("world_id") or ""

        instance_history = preloaded_history
        if inject_round:
            instance_history = await self._inject_round_to_instance(
                member, world_id, player_input, gm_response,
            )

        prev_responses_text = ""
        if previous_responses:
            prev_responses_text = "\n".join(
                f"- {r['name']}: {r['response']} ({r['reaction']})"
                for r in previous_responses
            )
        else:
            prev_responses_text = "（还没有其他队友发言）"

        location_desc = location.get("atmosphere") or location.get("description") or ""
        available_dests = location.get("available_destinations", [])
        if isinstance(available_dests, list):
            available_dests_text = ", ".join(
                d.get("name", d) if isinstance(d, dict) else str(d)
                for d in available_dests
            ) or "未知"
        else:
            available_dests_text = str(available_dests) or "未知"
        sub_locs = location.get("available_sub_locations", location.get("sub_locations", []))
        if isinstance(sub_locs, list):
            sub_locs_text = ", ".join(
                s.get("name", s) if isinstance(s, dict) else str(s)
                for s in sub_locs
            ) or "无"
        else:
            sub_locs_text = str(sub_locs) or "无"

        # 使用 filtered context 中的 player_said（支持私密模式）
        effective_player_input = player_input
        filtered_context = context.get("_filtered_context", {})
        if filtered_context.get("player_said_privately"):
            effective_player_input = filtered_context["player_said_privately"]
        elif filtered_context.get("player_said"):
            effective_player_input = filtered_context["player_said"]

        prompt = prompt_template.format(
            name=member.name,
            role=member.role.value,
            personality=member.personality,
            current_mood=member.current_mood,
            player_said=effective_player_input,
            gm_narration=gm_response[:300],
            location_name=location.get("location_name", "未知"),
            location_description=location_desc,
            available_destinations=available_dests_text,
            sub_locations=sub_locs_text,
            suggested_tone=decision.suggested_tone or "自然",
            previous_responses=prev_responses_text,
        )

        if instance_history:
            prompt += f"\n\n## 你的近期记忆\n{instance_history}\n"

        teammate_packages = context.get("teammate_context_packages") or {}
        teammate_package = teammate_packages.get(member.character_id)
        if isinstance(teammate_package, dict) and teammate_package:
            pkg_text = self._format_context_package(teammate_package)
            prompt += f"\n\n## 你的图谱上下文（Flash编排）\n{pkg_text}\n"

        teammate_memory_summaries = context.get("teammate_memory_summaries") or {}
        memory_summary = teammate_memory_summaries.get(member.character_id)
        if memory_summary:
            prompt += f"\n\n## 你的记忆摘要\n{memory_summary}\n"

        model = settings.admin_flash_model
        thinking_level = "low"

        async for chunk in self.llm_service.generate_simple_stream(
            prompt,
            model_override=model,
            thinking_level=thinking_level,
        ):
            yield chunk

    def _format_context_package(self, package: Dict[str, Any]) -> str:
        """将 Flash context_package 转成紧凑文本，避免提示词膨胀。"""
        if not package:
            return "无"
        keys = [
            "scene_summary",
            "relevant_npcs",
            "active_threads",
            "atmosphere_notes",
            "suggested_tone",
            "key_facts",
            "disposition_hints",
        ]
        compact = {k: package.get(k) for k in keys if package.get(k)}
        if not compact:
            compact = package
        return json.dumps(compact, ensure_ascii=False)

    def _get_member_name(self, party: Party, character_id: str) -> str:
        """获取队友名字"""
        member = party.get_member(character_id)
        return member.name if member else character_id
