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
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Tuple

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


# ── B1: 辅助函数（AgenticExecutor 迁移） ──


def _is_combat_active(session: Any) -> bool:
    """检查当前是否在战斗中。"""
    gs = getattr(session, "game_state", None)
    return bool(gs and getattr(gs, "combat_id", None))


def _make_combat_action_tool(
    flash_cpu: Any, session: Any, character_id: str,
) -> Any:
    """创建队友专属的战斗行动工具（从 TeammateAgenticToolRegistry 提取）。"""
    from typing import Callable

    async def choose_battle_action(action_id: str) -> Dict[str, Any]:
        """Select your combat action. action_id: the action to perform."""
        if not action_id:
            return {"success": False, "error": "missing action_id"}
        gs = getattr(session, "game_state", None)
        combat_id = getattr(gs, "combat_id", None) if gs else None
        if not combat_id:
            return {"success": False, "error": "no active combat"}
        payload = await flash_cpu.call_combat_tool(
            "execute_action_for_actor",
            {"combat_id": combat_id, "actor_id": character_id, "action_id": action_id},
        )
        if isinstance(payload, dict) and payload.get("error"):
            payload = await flash_cpu.call_combat_tool(
                "execute_action",
                {"combat_id": combat_id, "action_id": action_id},
            )
        if not isinstance(payload, dict):
            payload = {"success": False, "error": "invalid combat response"}
        payload["actor_id"] = character_id
        payload["combat_id"] = combat_id
        payload["success"] = not bool(payload.get("error"))
        return payload

    choose_battle_action.__annotations__ = {"action_id": str, "return": Dict[str, Any]}
    return choose_battle_action


class TeammateResponseService:
    """队友响应服务"""

    def __init__(
        self,
        llm_service: Optional[LLMService] = None,
        instance_manager: Optional["InstanceManager"] = None,
        flash_cpu: Optional[Any] = None,
        graph_store: Optional[Any] = None,
        recall_orchestrator: Optional[Any] = None,
    ) -> None:
        self.llm_service = llm_service or LLMService()
        self.visibility_manager = TeammateVisibilityManager()
        self.instance_manager = instance_manager
        self.flash_cpu = flash_cpu
        self.graph_store = graph_store
        self.recall_orchestrator = recall_orchestrator

        # 加载提示词模板
        self.decision_prompt_path = Path("app/prompts/teammate_decision.md")
        self.response_prompt_path = Path("app/prompts/teammate_response.md")
        self.agentic_system_prompt_path = Path("app/prompts/teammate_agentic_system.md")

        # 默认模型配置
        self.default_model_config = TeammateModelConfig()

    def _load_prompt(self, path: Path) -> str:
        """加载提示词模板"""
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def _resolve_round_basics(
        self,
        party: Party,
        gm_response: str,
        context: Dict[str, Any],
    ) -> Tuple[List[PartyMember], str, str, bool, Optional[str]]:
        """统一提取回合公共参数。"""
        active_members = party.get_active_members()
        world_id = context.get("world_id") or party.world_id
        gm_narration_full = str(context.get("gm_narration_full") or gm_response or "")
        is_private = bool(context.get("is_private", False))
        private_target = context.get("private_target")
        return active_members, world_id, gm_narration_full, is_private, private_target

    def _get_location_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """兼容 location/location_context 两种键。"""
        location = context.get("location")
        if not location:
            location = context.get("location_context")
        return location or {}

    def _build_filtered_context(
        self,
        member: PartyMember,
        player_input: str,
        gm_response: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self.visibility_manager.filter_context_for_teammate(
            teammate=member,
            full_context={
                "player_input": player_input,
                "gm_response": gm_response,
                **context,
            },
        )

    @staticmethod
    def _get_effective_player_input(
        filtered_context: Dict[str, Any],
        player_input: str,
    ) -> str:
        if filtered_context.get("player_said_privately"):
            return str(filtered_context["player_said_privately"])
        if filtered_context.get("player_said"):
            return str(filtered_context["player_said"])
        return player_input

    @staticmethod
    def _format_previous_responses(previous_responses: List[Dict[str, Any]]) -> str:
        if previous_responses:
            return "\n".join(
                f"- {r['name']}: {r['response']} ({r['reaction']})"
                for r in previous_responses
            )
        return "（还没有其他队友发言）"

    @staticmethod
    def _format_location_values(location: Dict[str, Any]) -> Tuple[str, str, str]:
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
        return location_desc, available_dests_text, sub_locs_text

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 72) -> List[str]:
        payload = str(text or "")
        if not payload:
            return []
        return [payload[i:i + chunk_size] for i in range(0, len(payload), chunk_size)]

    async def _inject_last_teammate_responses(
        self,
        *,
        active_members: List[PartyMember],
        world_id: str,
        context: Dict[str, Any],
    ) -> None:
        if self.instance_manager is None or not world_id:
            return
        last_responses = context.get("last_teammate_responses", [])
        if not last_responses:
            return
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
                            f"[队友 {resp_name}] {resp['response']}",
                        )
            except Exception as exc:
                logger.debug(
                    "[TeammateResponse] 跨轮队友发言注入失败 (%s): %s",
                    member.name,
                    exc,
                )

    async def _inject_round_histories(
        self,
        *,
        active_members: List[PartyMember],
        world_id: str,
        player_input: str,
        gm_narration_full: str,
        is_private: bool,
        private_target: Optional[str],
        parallel: bool,
        skip_non_target_in_private: bool,
    ) -> Dict[str, Optional[str]]:
        preloaded_histories: Dict[str, Optional[str]] = {}
        if self.instance_manager is None or not world_id:
            return preloaded_histories

        async def _inject_single(member: PartyMember):
            inject_player = player_input
            if is_private and private_target and member.character_id != private_target:
                if skip_non_target_in_private:
                    return member.character_id, None
                inject_player = ""
            history_text = await self._inject_round_to_instance(
                member=member,
                world_id=world_id,
                player_input=inject_player,
                gm_response=gm_narration_full,
            )
            return member.character_id, history_text

        if parallel:
            inject_results = await asyncio.gather(
                *(_inject_single(member) for member in active_members),
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
            return preloaded_histories

        for member in active_members:
            try:
                char_id, history_text = await _inject_single(member)
                preloaded_histories[char_id] = history_text
            except Exception as exc:
                logger.error(
                    "[TeammateResponse] 轮次注入失败 (%s): %s",
                    member.name,
                    exc,
                )
        return preloaded_histories

    @staticmethod
    def _members_for_decision(
        active_members: List[PartyMember],
        is_private: bool,
        private_target: Optional[str],
        private_only: bool,
    ) -> List[PartyMember]:
        if private_only and is_private and private_target:
            return [m for m in active_members if m.character_id == private_target]
        return active_members

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

        active_members, world_id, gm_narration_full, is_private, private_target = (
            self._resolve_round_basics(
                party=party,
                gm_response=gm_response,
                context=context,
            )
        )
        if not active_members:
            return TeammateRoundResult(
                responses=[],
                total_latency_ms=0,
                responding_count=0,
            )

        # 所有队友先接收本轮消息（即使后续不发言，也会更新独立上下文）
        await self._inject_last_teammate_responses(
            active_members=active_members,
            world_id=world_id,
            context=context,
        )
        preloaded_histories = await self._inject_round_histories(
            active_members=active_members,
            world_id=world_id,
            player_input=player_input,
            gm_narration_full=gm_narration_full,
            is_private=is_private,
            private_target=private_target,
            parallel=True,
            skip_non_target_in_private=False,
        )

        # 3. 每个队友独立决策是否回复
        members_for_decision = self._members_for_decision(
            active_members=active_members,
            is_private=is_private,
            private_target=private_target,
            private_only=False,
        )
        decisions = await self.decide_responses(
            party=party,
            player_input=player_input,
            gm_response=gm_response,
            context=context,
            members_override=members_for_decision,
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
                    gm_response=gm_narration_full,
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
        filtered_context = self._build_filtered_context(
            member=member,
            player_input=player_input,
            gm_response=gm_response,
            context=context,
        )

        # 使用 filtered context 中的 player_said（支持私密模式）
        effective_player_input = self._get_effective_player_input(
            filtered_context=filtered_context,
            player_input=player_input,
        )
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

        location = self._get_location_context(context)
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

        bus_summary = context.get("scene_bus_summary")
        if bus_summary:
            prompt += f"\n\n## 本轮场景事件\n{bus_summary}\n"

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
            if keyword in merged_text and member.response_tendency >= 0.3:
                return TeammateResponseDecision(
                    character_id=member.character_id,
                    should_respond=True,
                    reason="话题与职责相关",
                    priority=7,
                )

        # 玩家问句，更可能触发队友发言
        question_cues = ("?", "？", "吗", "如何", "怎么", "为什么", "是否", "要不要", "谁")
        if any(cue in (player_input or "") for cue in question_cues) and member.response_tendency >= 0.4:
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

        # 健谈角色可主动补充
        if member.response_tendency >= 0.7:
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
        result, _ = await self._generate_single_response_core(
            member=member,
            player_input=player_input,
            gm_response=gm_response,
            context=context,
            decision=decision,
            previous_responses=previous_responses,
            preloaded_history=preloaded_history,
            inject_round=inject_round,
        )
        return result

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
        active_members, world_id, gm_narration_full, is_private, private_target = (
            self._resolve_round_basics(
                party=party,
                gm_response=gm_response,
                context=context,
            )
        )
        if not active_members:
            return

        # 消息注入（复用同步逻辑）
        await self._inject_last_teammate_responses(
            active_members=active_members,
            world_id=world_id,
            context=context,
        )
        preloaded_histories = await self._inject_round_histories(
            active_members=active_members,
            world_id=world_id,
            player_input=player_input,
            gm_narration_full=gm_narration_full,
            is_private=is_private,
            private_target=private_target,
            parallel=False,
            skip_non_target_in_private=True,
        )

        # 决策（私密模式仅目标参与决策）
        members_for_decision = self._members_for_decision(
            active_members=active_members,
            is_private=is_private,
            private_target=private_target,
            private_only=True,
        )

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

            try:
                preloaded_history = preloaded_histories.get(member.character_id)
                result, tool_events = await self._generate_single_response_core(
                    member=member,
                    player_input=player_input,
                    gm_response=gm_narration_full,
                    context=context,
                    decision=decision,
                    previous_responses=collected_responses,
                    preloaded_history=preloaded_history,
                    inject_round=(self.instance_manager is not None and preloaded_history is None),
                )
                for tool_event in tool_events:
                    yield tool_event

                if result.response:
                    for piece in self._chunk_text(result.response):
                        yield {
                            "type": "teammate_chunk",
                            "character_id": member.character_id,
                            "text": piece,
                        }
                    collected_responses.append({
                        "name": result.name,
                        "response": result.response,
                        "reaction": result.reaction,
                    })

                yield {
                    "type": "teammate_end",
                    "character_id": member.character_id,
                    "name": member.name,
                    "response": result.response,
                    "reaction": result.reaction,
                }
            except Exception as exc:
                logger.error(
                    "[TeammateResponse] 流式生成失败 (%s): %s",
                    member.name,
                    exc,
                    exc_info=True,
                )
                yield {
                    "type": "teammate_end",
                    "character_id": member.character_id,
                    "name": member.name,
                    "response": None,
                    "reaction": "",
                }


    def _get_agentic_system_prompt(self) -> str:
        prompt = self._load_prompt(self.agentic_system_prompt_path)
        if prompt:
            return prompt
        return (
            "你是RPG队友Agent。\n"
            "你可以调用工具来表达自主决策（例如回忆、战斗行动、好感度变化），"
            "但你不能操控世界主流程。\n"
            "最终必须输出严格JSON：\n"
            "{\"response\": string|null, \"reaction\": string, \"updated_mood\": string}\n"
            "不要输出JSON之外的文本。"
        )

    def _build_response_prompt(
        self,
        *,
        member: PartyMember,
        player_input: str,
        gm_response: str,
        context: Dict[str, Any],
        decision: TeammateResponseDecision,
        previous_responses: List[Dict[str, Any]],
        instance_history: Optional[str],
    ) -> str:
        prompt_template = self._load_prompt(self.response_prompt_path)
        if not prompt_template:
            return ""

        location = self._get_location_context(context)
        location_desc, available_dests_text, sub_locs_text = self._format_location_values(location)
        prev_responses_text = self._format_previous_responses(previous_responses)
        filtered_context = self._build_filtered_context(
            member=member,
            player_input=player_input,
            gm_response=gm_response,
            context=context,
        )
        effective_player_input = self._get_effective_player_input(
            filtered_context=filtered_context,
            player_input=player_input,
        )

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

        bus_summary = context.get("scene_bus_summary")
        if bus_summary:
            prompt += f"\n\n## 本轮场景事件\n{bus_summary}\n"
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
        return prompt

    async def _run_simple_generation_payload(
        self,
        *,
        prompt: str,
        model: str,
        thinking_level: str,
    ) -> Optional[Dict[str, Any]]:
        result = await self.llm_service.generate_simple(
            prompt,
            model_override=model,
            thinking_level=thinking_level,
        )
        return self.llm_service.parse_json(result)

    async def _run_agentic_generation_payload(
        self,
        *,
        member: PartyMember,
        context: Dict[str, Any],
        prompt: str,
        model: str,
        thinking_level: str,
    ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        session = context.get("_runtime_session")
        if session is None:
            return None, []

        from app.world.agentic_executor import AgenticExecutor
        from app.world.immersive_tools import AgenticContext

        event_queue: asyncio.Queue = asyncio.Queue()

        ctx = AgenticContext(
            session=session,
            agent_id=member.character_id,
            role="teammate",
            scene_bus=getattr(session, "scene_bus", None),
            world_id=getattr(session, "world_id", ""),
            chapter_id=getattr(session, "chapter_id", ""),
            area_id=getattr(session, "area_id", ""),
            location_id=getattr(session, "sub_location", ""),
            recall_orchestrator=self.recall_orchestrator,
            graph_store=self.graph_store,
        )

        # 战斗时注入 extra_tool
        extra_tools: List[Any] = []
        if self.flash_cpu and _is_combat_active(session):
            extra_tools.append(
                _make_combat_action_tool(self.flash_cpu, session, member.character_id)
            )

        executor = AgenticExecutor(self.llm_service)
        result = await executor.run(
            ctx=ctx,
            system_prompt=self._get_agentic_system_prompt(),
            user_prompt=prompt,
            extra_tools=extra_tools or None,
            event_queue=event_queue,
            model_override=model,
            thinking_level=thinking_level,
        )

        # 收集工具事件
        tool_events: List[Dict[str, Any]] = []
        while not event_queue.empty():
            evt = event_queue.get_nowait()
            evt["character_id"] = member.character_id
            evt["type"] = "teammate_tool_call"
            tool_events.append(evt)

        parsed = self.llm_service.parse_json(result.narration)
        return parsed, tool_events

    async def _generate_response_payload(
        self,
        *,
        member: PartyMember,
        context: Dict[str, Any],
        prompt: str,
        model: str,
        thinking_level: str,
    ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        tool_events: List[Dict[str, Any]] = []
        parsed: Optional[Dict[str, Any]] = None

        try:
            parsed, tool_events = await self._run_agentic_generation_payload(
                member=member,
                context=context,
                prompt=prompt,
                model=model,
                thinking_level=thinking_level,
            )
        except Exception as exc:
            logger.warning(
                "[TeammateResponse] agentic生成失败 (%s), fallback到simple: %s",
                member.name,
                exc,
            )

        if parsed:
            return parsed, tool_events

        try:
            parsed = await self._run_simple_generation_payload(
                prompt=prompt,
                model=model,
                thinking_level=thinking_level,
            )
            return parsed, tool_events
        except Exception as exc:
            logger.warning(
                "[TeammateResponse] simple生成失败 (%s): %s",
                member.name,
                exc,
            )
            return None, tool_events

    async def _generate_single_response_core(
        self,
        *,
        member: PartyMember,
        player_input: str,
        gm_response: str,
        context: Dict[str, Any],
        decision: TeammateResponseDecision,
        previous_responses: List[Dict[str, Any]],
        preloaded_history: Optional[str] = None,
        inject_round: bool = True,
    ) -> Tuple[TeammateResponseResult, List[Dict[str, Any]]]:
        world_id = context.get("world_id") or ""
        instance_history = preloaded_history
        if inject_round:
            instance_history = await self._inject_round_to_instance(
                member,
                world_id,
                player_input,
                gm_response,
            )

        prompt = self._build_response_prompt(
            member=member,
            player_input=player_input,
            gm_response=gm_response,
            context=context,
            decision=decision,
            previous_responses=previous_responses,
            instance_history=instance_history,
        )
        if not prompt:
            logger.error("[TeammateResponse] 缺少提示词模板: %s", self.response_prompt_path)
            return (
                TeammateResponseResult(
                    character_id=member.character_id,
                    name=member.name,
                    response=None,
                    reaction="",
                    model_used="error",
                ),
                [],
            )

        model = settings.admin_flash_model
        thinking_level = "low"
        parsed, tool_events = await self._generate_response_payload(
            member=member,
            context=context,
            prompt=prompt,
            model=model,
            thinking_level=thinking_level,
        )
        if not parsed:
            return (
                TeammateResponseResult(
                    character_id=member.character_id,
                    name=member.name,
                    response=None,
                    reaction="",
                    model_used="error",
                    thinking_level=thinking_level,
                ),
                tool_events,
            )

        if parsed.get("updated_mood"):
            member.current_mood = parsed["updated_mood"]

        response_text = parsed.get("response")
        if response_text:
            await self._write_response_to_instance(member, world_id, response_text)

        return (
            TeammateResponseResult(
                character_id=member.character_id,
                name=member.name,
                response=response_text,
                reaction=parsed.get("reaction", ""),
                model_used=model,
                thinking_level=thinking_level,
            ),
            tool_events,
        )

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

