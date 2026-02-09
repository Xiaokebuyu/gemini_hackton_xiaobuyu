"""
Flash CPU service - rules/execution/state management.
"""
from __future__ import annotations

import json
import logging
import uuid
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.models.admin_protocol import (
    AnalysisPlan,
    FlashOperation,
    FlashRequest,
    FlashResponse,
    IntentType,
    ParsedIntent,
)
from app.models.state_delta import StateDelta
from app.services.admin.state_manager import StateManager
from app.services.flash_service import FlashService
from app.services.admin.event_service import AdminEventService
from app.services.admin.world_runtime import AdminWorldRuntime
from app.services.game_session_store import GameSessionStore
from app.services.narrative_service import NarrativeService
from app.services.passerby_service import PasserbyService
from app.services.llm_service import LLMService
from app.services.mcp_client_pool import MCPClientPool, MCPServiceUnavailableError

logger = logging.getLogger(__name__)


class FlashCPUService:
    """Flash CPU service."""

    def __init__(
        self,
        state_manager: Optional[StateManager] = None,
        event_service: Optional[AdminEventService] = None,
        flash_service: Optional[FlashService] = None,
        world_runtime: Optional[AdminWorldRuntime] = None,
        session_store: Optional[GameSessionStore] = None,
        narrative_service: Optional[NarrativeService] = None,
        passerby_service: Optional[PasserbyService] = None,
        llm_service: Optional[LLMService] = None,
        analysis_prompt_path: Optional[Path] = None,
        instance_manager: Optional[Any] = None,
        party_service: Optional[Any] = None,
        character_service: Optional[Any] = None,
        character_store: Optional[Any] = None,
    ) -> None:
        self.state_manager = state_manager or StateManager()
        self.event_service = event_service or AdminEventService()
        self.flash_service = flash_service or FlashService()
        self.world_runtime = world_runtime
        self.session_store = session_store or GameSessionStore()
        self.narrative_service = narrative_service
        self.passerby_service = passerby_service or PasserbyService()
        self.llm_service = llm_service or LLMService()
        self.analysis_prompt_path = analysis_prompt_path or Path("app/prompts/flash_analysis.md")
        self.instance_manager = instance_manager
        self.party_service = party_service
        self.character_service = character_service
        self.character_store = character_store

    def _load_analysis_prompt(self) -> str:
        if self.analysis_prompt_path.exists():
            return self.analysis_prompt_path.read_text(encoding="utf-8")
        return (
            "你是游戏系统的分析引擎。一次性完成意图解析、操作规划与记忆召回建议，并返回严格 JSON。"
        )

    def _default_plan(self, player_input: str) -> AnalysisPlan:
        intent = ParsedIntent(
            intent_type=IntentType.ROLEPLAY,
            confidence=0.5,
            raw_input=player_input,
            interpretation="无法解析，作为角色扮演处理",
        )
        return AnalysisPlan(intent=intent)

    def _handle_system_command(self, player_input: str) -> AnalysisPlan:
        cmd_parts = player_input[1:].split(maxsplit=1)
        cmd = cmd_parts[0].lower() if cmd_parts else ""
        arg = cmd_parts[1] if len(cmd_parts) > 1 else ""

        if cmd in ("go", "navigate") and arg:
            intent = ParsedIntent(
                intent_type=IntentType.NAVIGATION,
                confidence=1.0,
                target=arg,
                action=cmd,
                parameters={"destination": arg},
                raw_input=player_input,
                interpretation="系统指令导航",
            )
            return AnalysisPlan(
                intent=intent,
                operations=[
                    FlashRequest(
                        operation=FlashOperation.NAVIGATE,
                        parameters={"destination": arg},
                    )
                ],
                reasoning="system_command",
            )

        if cmd == "talk" and arg:
            intent = ParsedIntent(
                intent_type=IntentType.NPC_INTERACTION,
                confidence=1.0,
                target=arg,
                action=cmd,
                parameters={"npc_id": arg},
                raw_input=player_input,
                interpretation="系统指令对话",
            )
            return AnalysisPlan(
                intent=intent,
                operations=[
                    FlashRequest(
                        operation=FlashOperation.NPC_DIALOGUE,
                        parameters={"npc_id": arg, "message": "你好"},
                    )
                ],
                reasoning="system_command",
            )

        if cmd == "wait":
            minutes = 30
            if arg.isdigit():
                minutes = int(arg)
            intent = ParsedIntent(
                intent_type=IntentType.WAIT,
                confidence=1.0,
                action=cmd,
                parameters={"minutes": minutes},
                raw_input=player_input,
                interpretation="系统指令等待",
            )
            return AnalysisPlan(
                intent=intent,
                operations=[
                    FlashRequest(
                        operation=FlashOperation.UPDATE_TIME,
                        parameters={"minutes": minutes},
                    )
                ],
                reasoning="system_command",
            )

        intent = ParsedIntent(
            intent_type=IntentType.SYSTEM_COMMAND,
            confidence=1.0,
            target=cmd,
            action=cmd,
            parameters={"command": cmd, "argument": arg},
            raw_input=player_input,
            interpretation="系统命令",
        )
        return AnalysisPlan(intent=intent, reasoning="system_command")

    def _parse_analysis_result(
        self,
        parsed: Dict[str, Any],
        player_input: str,
        context: Dict[str, Any],
    ) -> AnalysisPlan:
        intent_type_str = str(parsed.get("intent_type", "roleplay")).lower()
        intent_aliases = {
            "enter_sublocation": "enter_sub_location",
            "leave_sublocation": "leave_sub_location",
            "npc_interaction": "npc_interaction",
            "team_interaction": "team_interaction",
            "start_combat": "start_combat",
        }
        intent_type_str = intent_aliases.get(intent_type_str, intent_type_str)
        try:
            intent_type = IntentType(intent_type_str)
        except ValueError:
            intent_type = IntentType.ROLEPLAY

        intent = ParsedIntent(
            intent_type=intent_type,
            confidence=float(parsed.get("confidence", 0.8)),
            target=parsed.get("target"),
            action=parsed.get("action"),
            parameters=parsed.get("parameters", {}),
            raw_input=player_input,
            interpretation=parsed.get("interpretation"),
            player_emotion=parsed.get("player_emotion"),
        )

        operations: List[FlashRequest] = []
        for req_data in parsed.get("operations", []):
            if not isinstance(req_data, dict):
                continue
            op_raw = str(req_data.get("operation", "")).strip()
            if not op_raw:
                continue
            op_key = op_raw.lower()
            op_aliases = {
                "enter_sub_location": "enter_sublocation",
                "leave_sub_location": "leave_sublocation",
                "enter_sublocation": "enter_sublocation",
                "start_combat": "start_combat",
                "npc_dialogue": "npc_dialogue",
                "spawn_passerby": "spawn_passerby",
                "trigger_narrative_event": "trigger_narrative_event",
                "update_time": "update_time",
                "navigate": "navigate",
                "get_progress": "get_progress",
                "get_status": "get_status",
                "add_teammate": "add_teammate",
                "remove_teammate": "remove_teammate",
                "disband_party": "disband_party",
                "heal_player": "heal_player",
                "damage_player": "damage_player",
                "add_xp": "add_xp",
                "add_item": "add_item",
                "remove_item": "remove_item",
                "ability_check": "ability_check",
            }
            op_key = op_aliases.get(op_key, op_key)
            operation = None
            try:
                operation = FlashOperation(op_key)
            except ValueError:
                operation = None
            if not operation:
                continue
            operations.append(
                FlashRequest(
                    operation=operation,
                    parameters=req_data.get("parameters", {}),
                    priority=req_data.get("priority", "normal"),
                )
            )

        memory_seeds = [str(s) for s in parsed.get("memory_seeds", []) if s]
        reasoning = parsed.get("reasoning") or ""
        context_package = parsed.get("context_package")
        if context_package and not isinstance(context_package, dict):
            context_package = None

        story_progression = parsed.get("story_progression")
        if not isinstance(story_progression, dict) and isinstance(context_package, dict):
            nested_story_progression = context_package.get("story_progression")
            if isinstance(nested_story_progression, dict):
                story_progression = nested_story_progression

        if isinstance(story_progression, dict):
            story_events_raw = story_progression.get("story_events", [])
            story_events: List[str] = []
            if isinstance(story_events_raw, list):
                for event_id in story_events_raw:
                    event_text = ""
                    if isinstance(event_id, str):
                        event_text = event_id.strip()
                    elif isinstance(event_id, (int, float)) and not isinstance(event_id, bool):
                        event_text = str(event_id).strip()
                    if event_text:
                        story_events.append(event_text)
            elif isinstance(story_events_raw, str):
                story_events = [
                    token.strip()
                    for token in story_events_raw.replace("，", ",").replace("、", ",").split(",")
                    if token.strip()
                ]

            normalized_story_progression: Dict[str, Any] = {"story_events": story_events}
            progress_note = story_progression.get("progress_note")
            if isinstance(progress_note, str) and progress_note.strip():
                normalized_story_progression["progress_note"] = progress_note.strip()

            # v2: 提取 condition_evaluations（缺失降级为 []）
            raw_evals = story_progression.get("condition_evaluations", [])
            condition_evaluations: List[Dict[str, Any]] = []
            if isinstance(raw_evals, list):
                for eval_item in raw_evals:
                    if isinstance(eval_item, dict) and "id" in eval_item:
                        condition_evaluations.append({
                            "id": str(eval_item["id"]),
                            "result": bool(eval_item.get("result", False)),
                            "reasoning": str(eval_item.get("reasoning", "")),
                        })
            if condition_evaluations:
                normalized_story_progression["condition_evaluations"] = condition_evaluations

            story_progression = normalized_story_progression
        else:
            story_progression = None

        return AnalysisPlan(
            intent=intent,
            operations=operations,
            memory_seeds=memory_seeds,
            reasoning=reasoning,
            context_package=context_package,
            story_progression=story_progression,
        )

    async def analyze_and_plan(
        self,
        player_input: str,
        context: Dict[str, Any],
    ) -> AnalysisPlan:
        if player_input.strip().startswith("/"):
            return self._handle_system_command(player_input)

        prompt = self._load_analysis_prompt()
        location = context.get("location") or {}
        time_info = context.get("time") or {}
        teammates = context.get("teammates") or []
        available_destinations = context.get("available_destinations") or []
        sub_locations = context.get("sub_locations") or location.get("sub_locations") or []

        chapter_info = context.get("chapter_info") or {}
        chapter_obj = chapter_info.get("chapter") or {}
        chapter_goals = chapter_info.get("goals", [])
        chapter_events = chapter_info.get("required_events", [])

        # v2 StoryDirector 注入
        story_directives_list = context.get("story_directives") or []
        story_directives_text = "\n".join(story_directives_list) if story_directives_list else "无"

        pending_conditions = context.get("pending_flash_conditions") or []
        if pending_conditions:
            pending_lines = []
            for pc in pending_conditions:
                if isinstance(pc, dict):
                    pending_lines.append(f"- [{pc.get('id', '?')}] {pc.get('prompt', '?')}")
            pending_flash_text = "\n".join(pending_lines) if pending_lines else "无"
        else:
            pending_flash_text = "无"

        filled_prompt = prompt.format(
            player_character=context.get("player_character_summary", "无玩家角色"),
            location_name=location.get("location_name", "未知地点"),
            available_destinations=", ".join(
                d.get("name", d.get("id", str(d)))
                if isinstance(d, dict) else str(d)
                for d in available_destinations
            ) or "无",
            sub_locations=", ".join(
                f"{s.get('name', s.get('id', str(s)))}"
                if isinstance(s, dict) else str(s)
                for s in sub_locations
            ) or "无",
            npcs_present=", ".join(location.get("npcs_present", [])) or "无",
            teammates=", ".join(
                t.get("name", t) if isinstance(t, dict) else str(t)
                for t in teammates
            ) or "无",
            time=time_info.get("formatted") or time_info.get("formatted_time") or "未知",
            current_state=context.get("state", "exploring"),
            active_npc=context.get("active_npc") or "无",
            player_input=player_input,
            conversation_history=context.get("conversation_history", "无"),
            character_roster=context.get("character_roster", "无"),
            chapter_name=self._sanitize_tavern_text(chapter_obj.get("name", "未知")),
            chapter_goals="、".join(self._sanitize_tavern_text(g) for g in chapter_goals) if chapter_goals else "无",
            chapter_description=self._build_enriched_chapter_description(chapter_obj, chapter_info),
            chapter_events="、".join(chapter_events) if chapter_events else "无",
            story_directives=story_directives_text,
            pending_flash_conditions=pending_flash_text,
        )

        try:
            result = await self.llm_service.generate_simple(
                filled_prompt,
                model_override=settings.admin_flash_model,
                thinking_level=settings.admin_flash_thinking_level,
            )
            parsed = self.llm_service.parse_json(result)
            if parsed:
                return self._parse_analysis_result(parsed, player_input, context)
        except Exception as exc:
            logger.error("analyze_and_plan 失败: %s", exc, exc_info=True)

        return self._default_plan(player_input)

    def _load_curation_prompt(self) -> str:
        curation_path = Path("app/prompts/flash_context_curation.md")
        if curation_path.exists():
            return curation_path.read_text(encoding="utf-8")
        return "你是上下文编排引擎。从图谱数据中挑选与当前场景相关的信息，返回严格 JSON。"

    def _load_gm_narration_prompt(self) -> str:
        prompt_path = Path("app/prompts/flash_gm_narration.md")
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")
        return (
            "你是GM。基于场景、近期对话、记忆和本轮执行结果生成2-4句沉浸式中文叙述。"
            "避免复读，不要输出JSON。"
        )

    def _format_subgraph_for_prompt(self, memory_result) -> tuple:
        """将扩散激活结果格式化为提示词文本。返回 (nodes_text, edges_text)。"""
        activated = getattr(memory_result, "activated_nodes", {}) or {}
        subgraph = getattr(memory_result, "subgraph", None)

        if not activated or not subgraph:
            return "无激活节点", "无关系"

        node_lookup = {}
        if getattr(subgraph, "nodes", None):
            node_lookup = {node.id: node for node in subgraph.nodes}

        # 格式化节点（按激活分数排序，最多20个）
        scored = sorted(activated.items(), key=lambda x: x[1], reverse=True)
        node_lines = []
        for node_id, score in scored[:20]:
            node = node_lookup.get(node_id)
            if not node:
                continue
            props = getattr(node, "properties", {}) or {}
            if props.get("placeholder"):
                continue
            # 收集关键属性
            detail_parts = []
            rich_keys = [
                "background", "role", "personality", "occupation",
                "summary", "description", "status", "atmosphere",
                "danger_level", "resident_npcs", "participants",
                "emotion", "objectives", "consequences", "resolved",
                "sub_type", "location",
            ]
            for key in rich_keys:
                val = props.get(key)
                if val:
                    val_str = str(val)
                    if len(val_str) > 120:
                        val_str = val_str[:120] + "..."
                    detail_parts.append(f"{key}: {val_str}")
            game_day = props.get("game_day")
            if game_day is not None:
                detail_parts.append(f"game_day: {game_day}")

            details = "; ".join(detail_parts[:6]) if detail_parts else ""
            details_block = f" | {details}" if details else ""
            node_lines.append(
                f"- [{node.type}] {node.name} (id={node.id}, score={score:.2f}){details_block}"
            )

        # 格式化边（仅涉及激活节点的边，最多15条）
        edge_lines = []
        activated_ids = set(activated.keys())
        if getattr(subgraph, "edges", None):
            for edge in subgraph.edges:
                src = getattr(edge, "source", None)
                tgt = getattr(edge, "target", None)
                if src not in activated_ids and tgt not in activated_ids:
                    continue
                src_name = node_lookup[src].name if src in node_lookup else src
                tgt_name = node_lookup[tgt].name if tgt in node_lookup else tgt
                relation = getattr(edge, "relation", "related")
                edge_props = getattr(edge, "properties", {}) or {}
                prop_parts = []
                for ek in ["evidence_text", "travel_time", "approval", "trust", "fear", "romance"]:
                    ev = edge_props.get(ek)
                    if ev is not None:
                        prop_parts.append(f"{ek}: {ev}")
                prop_str = f" ({', '.join(prop_parts)})" if prop_parts else ""
                edge_lines.append(f"- {src_name} --[{relation}]--> {tgt_name}{prop_str}")
                if len(edge_lines) >= 15:
                    break

        nodes_text = "\n".join(node_lines) if node_lines else "无激活节点"
        edges_text = "\n".join(edge_lines) if edge_lines else "无关系"
        return nodes_text, edges_text

    async def curate_context(
        self,
        player_input: str,
        intent: "ParsedIntent",
        memory_result: Any,
        flash_results: List[Any],
        context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """第二轮 Flash：基于扩散激活结果编排 context_package。"""
        prompt_template = self._load_curation_prompt()

        location = context.get("location") or {}
        time_info = context.get("time") or {}

        # 格式化扩散激活结果
        nodes_text, edges_text = self._format_subgraph_for_prompt(memory_result)

        # 格式化操作执行结果
        exec_lines = []
        for r in (flash_results or []):
            op = getattr(r, "operation", None)
            op_str = op.value if hasattr(op, "value") else str(op)
            success = getattr(r, "success", False)
            error = getattr(r, "error", None)
            if success:
                detail = ""
                result_dict = getattr(r, "result", {})
                if isinstance(result_dict, dict):
                    detail = (
                        result_dict.get("summary")
                        or result_dict.get("narration")
                        or result_dict.get("description")
                        or ""
                    )
                exec_lines.append(f"- [{op_str}] 成功: {detail or '执行成功'}")
            else:
                exec_lines.append(f"- [{op_str}] 失败: {error or '未知错误'}")
        execution_summary = "\n".join(exec_lines) if exec_lines else "无操作执行"

        chapter_info = context.get("chapter_info") or {}
        chapter_obj = chapter_info.get("chapter") or {}
        chapter_goals = chapter_info.get("goals", [])

        filled_prompt = prompt_template.format(
            intent_type=intent.intent_type.value if hasattr(intent.intent_type, "value") else str(intent.intent_type),
            intent_target=intent.target or "无",
            intent_interpretation=intent.interpretation or "无",
            player_input=player_input,
            location_name=location.get("location_name", "未知地点"),
            time=time_info.get("formatted") or time_info.get("formatted_time") or "未知",
            current_state=context.get("state", "exploring"),
            execution_summary=execution_summary,
            activated_nodes=nodes_text,
            activated_edges=edges_text,
            chapter_name=chapter_obj.get("name", "未知"),
            chapter_goals="、".join(chapter_goals) if chapter_goals else "无",
        )

        result = await self.llm_service.generate_simple(
            filled_prompt,
            model_override=settings.admin_flash_model,
            thinking_level="low",
        )
        parsed = self.llm_service.parse_json(result)
        if not parsed:
            logger.warning("curate_context: JSON 解析失败, raw=%s", result[:200])
            return None
        # 支持顶层 context_package 或直接返回 context_package 内容
        cp = parsed.get("context_package", parsed)
        if isinstance(cp, dict):
            return cp
        logger.warning("curate_context: 返回值不是 dict, type=%s", type(cp))
        return None

    async def generate_gm_narration(
        self,
        player_input: str,
        execution_summary: str,
        context: Dict[str, Any],
    ) -> str:
        """使用 Flash 模型直接生成 GM 叙述。"""
        location = context.get("location") or {}
        time_info = context.get("time") or {}
        teammates = context.get("teammates") or []
        conversation_history = context.get("conversation_history") or "无"
        world_background = context.get("world_background") or "无"
        memory_summary = context.get("memory_summary") or "无"
        context_package = context.get("context_package") or {}

        teammate_lines = []
        for teammate in teammates:
            if not isinstance(teammate, dict):
                continue
            teammate_lines.append(
                f"- {teammate.get('name', '?')}({teammate.get('role', '?')}, 情绪:{teammate.get('current_mood', '未知')})"
            )
        teammates_text = "\n".join(teammate_lines) if teammate_lines else "无"

        # v2 StoryDirector 叙述指令
        story_directives_list = context.get("story_narrative_directives") or context.get("story_directives") or []
        story_directives_text = "\n".join(story_directives_list) if story_directives_list else "无"

        prompt_template = self._load_gm_narration_prompt()
        prompt = prompt_template.format(
            player_character=context.get("player_character_summary", "无玩家角色"),
            location_name=location.get("location_name", "未知地点"),
            location_atmosphere=location.get("atmosphere", ""),
            time=time_info.get("formatted") or time_info.get("formatted_time") or "未知",
            current_state=context.get("state", "exploring"),
            active_npc=context.get("active_npc") or "无",
            world_background=world_background,
            chapter_guidance=self._build_chapter_guidance(context),
            story_directives=story_directives_text,
            teammates=teammates_text,
            conversation_history=conversation_history,
            memory_summary=memory_summary,
            context_package=json.dumps(context_package, ensure_ascii=False) if context_package else "无",
            execution_summary=execution_summary or "无",
            player_input=player_input,
        )

        result = await self.llm_service.generate_simple(
            prompt,
            model_override=settings.admin_flash_model,
            thinking_level=settings.admin_flash_thinking_level,
        )
        narration = (result or "").strip()
        if not narration:
            logger.warning("Flash GM narration returned empty, using fallback")
            return "……"
        return narration

    async def generate_gm_narration_stream(
        self,
        player_input: str,
        execution_summary: str,
        context: Dict[str, Any],
    ):
        """流式生成 GM 叙述，yield {"type": "thought"/"answer", "text": str}。"""
        location = context.get("location") or {}
        time_info = context.get("time") or {}
        teammates = context.get("teammates") or []
        conversation_history = context.get("conversation_history") or "无"
        world_background = context.get("world_background") or "无"
        memory_summary = context.get("memory_summary") or "无"
        context_package = context.get("context_package") or {}

        teammate_lines = []
        for teammate in teammates:
            if not isinstance(teammate, dict):
                continue
            teammate_lines.append(
                f"- {teammate.get('name', '?')}({teammate.get('role', '?')}, 情绪:{teammate.get('current_mood', '未知')})"
            )
        teammates_text = "\n".join(teammate_lines) if teammate_lines else "无"

        story_directives_list = context.get("story_narrative_directives") or context.get("story_directives") or []
        story_directives_text = "\n".join(story_directives_list) if story_directives_list else "无"

        prompt_template = self._load_gm_narration_prompt()
        prompt = prompt_template.format(
            player_character=context.get("player_character_summary", "无玩家角色"),
            location_name=location.get("location_name", "未知地点"),
            location_atmosphere=location.get("atmosphere", ""),
            time=time_info.get("formatted") or time_info.get("formatted_time") or "未知",
            current_state=context.get("state", "exploring"),
            active_npc=context.get("active_npc") or "无",
            world_background=world_background,
            chapter_guidance=self._build_chapter_guidance(context),
            story_directives=story_directives_text,
            teammates=teammates_text,
            conversation_history=conversation_history,
            memory_summary=memory_summary,
            context_package=json.dumps(context_package, ensure_ascii=False) if context_package else "无",
            execution_summary=execution_summary or "无",
            player_input=player_input,
        )

        async for chunk in self.llm_service.generate_simple_stream(
            prompt,
            model_override=settings.admin_flash_model,
            thinking_level=settings.admin_flash_thinking_level,
        ):
            yield chunk

    @staticmethod
    def _sanitize_tavern_text(text: str) -> str:
        """替换 SillyTavern 占位符为通用称谓。"""
        if not text:
            return text
        return text.replace("{{user}}", "冒险者").replace("{{char}}", "")

    def _build_enriched_chapter_description(
        self, chapter_obj: Dict[str, Any], chapter_info: Dict[str, Any]
    ) -> str:
        """构建增强的章节描述，包含事件引导。"""
        chapter_desc_raw = chapter_obj.get("description", "")[:400] or "无"
        chapter_description_text = self._sanitize_tavern_text(chapter_desc_raw)
        event_dirs = chapter_info.get("event_directives", [])
        if event_dirs:
            chapter_description_text += "\n\n即将到来的事件引导：\n" + "\n".join(
                f"  {self._sanitize_tavern_text(d)}" for d in event_dirs[:3]
            )
        return chapter_description_text

    def _build_chapter_guidance(self, context: Dict[str, Any]) -> str:
        """构建章节引导文本"""
        chapter_info = context.get("chapter_info") or {}
        chapter_obj = chapter_info.get("chapter") or {}
        chapter_name = chapter_obj.get("name")
        if not chapter_name:
            return "无"
        goals = chapter_info.get("goals", [])
        event_dirs = chapter_info.get("event_directives", [])
        current_event = chapter_info.get("current_event") or {}
        pending_required_events = chapter_info.get("pending_required_events") or []
        required_events = chapter_info.get("required_events") or []

        parts = [f"当前章节：{self._sanitize_tavern_text(chapter_name)}"]
        if isinstance(current_event, dict) and (current_event.get("name") or current_event.get("id")):
            event_name = current_event.get("name") or current_event.get("id")
            parts.append(f"当前事件焦点：{self._sanitize_tavern_text(str(event_name))}")
            event_desc = current_event.get("description")
            if isinstance(event_desc, str) and event_desc.strip():
                parts.append(f"当前事件描述：{self._sanitize_tavern_text(event_desc.strip())[:120]}")
        if goals:
            sanitized_goals = [self._sanitize_tavern_text(g) for g in goals[:3]]
            parts.append(f"推进目标：{'、'.join(sanitized_goals)}")
        if pending_required_events:
            pending_text = "、".join(str(e) for e in pending_required_events[:4])
            parts.append(f"待触发关键事件：{pending_text}")
        elif required_events:
            parts.append("章节关键事件已全部触发，可准备章节收束或过渡。")
        if event_dirs:
            parts.append("即将到来的事件：")
            for d in event_dirs[:2]:
                parts.append(f"  {self._sanitize_tavern_text(d)}")
        parts.append("本回合叙述要求：至少给出一个可执行的推进线索，并与当前事件保持一致。")
        return "\n".join(parts)

    async def execute_request(
        self,
        world_id: str,
        session_id: str,
        request: FlashRequest,
        generate_narration: bool = True,
    ) -> FlashResponse:
        """Execute a Flash operation."""
        op = request.operation
        params = request.parameters or {}

        runtime_required_ops = {
            FlashOperation.NAVIGATE,
            FlashOperation.UPDATE_TIME,
            FlashOperation.ENTER_SUBLOCATION,
        }
        if op in runtime_required_ops and self.world_runtime is None:
            return FlashResponse(
                success=False,
                operation=op,
                error="world_runtime not initialized",
            )

        try:
            if op == FlashOperation.NAVIGATE:
                result = await self.world_runtime.navigate(
                    world_id=world_id,
                    session_id=session_id,
                    destination=params.get("destination"),
                    direction=params.get("direction"),
                    generate_narration=generate_narration,
                )
                await self.world_runtime.refresh_state(world_id, session_id)
                payload = result if isinstance(result, dict) else {"raw": result}
                success = bool(payload.get("success"))
                error_message = payload.get("error") if not success else None
                return FlashResponse(
                    success=success,
                    operation=op,
                    result=payload,
                    error=error_message,
                )

            if op == FlashOperation.UPDATE_TIME:
                minutes = int(params.get("minutes", 0))
                result = await self.world_runtime.advance_time(world_id, session_id, minutes)
                await self.world_runtime.refresh_state(world_id, session_id)
                payload = result if isinstance(result, dict) else {"raw": result}
                success = isinstance(payload, dict) and not payload.get("error") and payload.get("success") is not False
                delta = self._build_state_delta("update_time", {"game_time": payload.get("time")})
                error_message = payload.get("error") if not success else None
                return FlashResponse(
                    success=success,
                    operation=op,
                    result=payload,
                    state_delta=delta,
                    error=error_message,
                )

            if op == FlashOperation.ENTER_SUBLOCATION:
                sub_location_id = await self._resolve_enter_sub_location_id(
                    world_id=world_id,
                    session_id=session_id,
                    params=params,
                )
                if not sub_location_id:
                    return FlashResponse(
                        success=False,
                        operation=op,
                        error="missing sub_location_id",
                    )

                result = await self.world_runtime.enter_sub_location(
                    world_id=world_id,
                    session_id=session_id,
                    sub_location_id=sub_location_id,
                )
                await self.world_runtime.refresh_state(world_id, session_id)
                payload = result if isinstance(result, dict) else {"raw": result}
                success = bool(payload.get("success"))
                error_message = payload.get("error") if not success else None
                return FlashResponse(
                    success=success,
                    operation=op,
                    result=payload,
                    error=error_message,
                )

            if op == FlashOperation.START_COMBAT:
                # Auto-fill player_state from character data if not provided
                player_state = params.get("player_state") or {}
                if not player_state and self.character_store:
                    try:
                        character = await self.character_store.get_character(world_id, session_id)
                        if character:
                            player_state = character.to_combat_player_state()
                    except Exception as exc:
                        logger.warning("[start_combat] Failed to auto-fill player_state: %s", exc)
                result = await self._call_combat_tool(
                    "start_combat_session",
                    {
                        "world_id": world_id,
                        "session_id": session_id,
                        "enemies": params.get("enemies", []),
                        "player_state": player_state,
                        "environment": params.get("environment"),
                        "allies": params.get("allies"),
                        "combat_context": params.get("combat_context"),
                    },
                )
                if isinstance(result, dict) and result.get("combat_id"):
                    delta = self._build_state_delta("start_combat", {"combat_id": result.get("combat_id")})
                    await self._apply_delta(world_id, session_id, delta)
                success = isinstance(result, dict) and result.get("type") != "error" and not result.get("error")
                error_message = None
                if not success and isinstance(result, dict):
                    error_message = result.get("error") or result.get("response")
                return FlashResponse(success=success, operation=op, result=result, error=error_message)

            if op == FlashOperation.TRIGGER_NARRATIVE_EVENT:
                if not self.narrative_service:
                    return FlashResponse(success=False, operation=op, error="叙事系统未初始化")
                result = await self.narrative_service.trigger_event(world_id, session_id, params.get("event_id"), skip_advance=True)
                payload = result if isinstance(result, dict) else {"raw": result}
                success = isinstance(payload, dict) and not payload.get("error")
                error_message = payload.get("error") if not success else None
                return FlashResponse(success=success, operation=op, result=payload, error=error_message)

            if op == FlashOperation.SPAWN_PASSERBY:
                if not self.passerby_service:
                    return FlashResponse(success=False, operation=op, error="路人系统未初始化")
                result = await self.passerby_service.get_or_spawn_passerby(
                    world_id,
                    params.get("map_id"),
                    params.get("sub_location_id"),
                )
                payload = result.model_dump() if hasattr(result, "model_dump") else result
                success = payload is not None and (not isinstance(payload, dict) or not payload.get("error"))
                error_message = payload.get("error") if isinstance(payload, dict) and not success else None
                return FlashResponse(success=success, operation=op, result=payload, error=error_message)

            if op == FlashOperation.NPC_DIALOGUE:
                npc_id = params.get("npc_id")
                message = params.get("message", "")
                # 通过 InstanceManager 维护 NPC 对话上下文
                if self.instance_manager is not None and npc_id:
                    response_text = await self._npc_dialogue_with_instance(
                        world_id, npc_id, message,
                    )
                    payload = {"response": response_text}
                elif npc_id:
                    response_text = await self._npc_dialogue_direct_flash(npc_id, message)
                    payload = {"response": response_text}
                else:
                    payload = {"error": "missing npc_id"}
                success = payload is not None and (not isinstance(payload, dict) or not payload.get("error"))
                error_message = payload.get("error") if isinstance(payload, dict) and not success else None
                return FlashResponse(success=success, operation=op, result=payload, error=error_message)

            if op == FlashOperation.GET_PROGRESS:
                if not self.narrative_service:
                    return FlashResponse(success=False, operation=op, error="叙事系统未初始化")
                result = await self.narrative_service.get_progress(world_id, session_id)
                payload = result.to_dict() if hasattr(result, "to_dict") else result
                if not isinstance(payload, dict):
                    payload = {"raw": payload}
                success = isinstance(payload, dict) and not payload.get("error")
                return FlashResponse(success=success, operation=op, result=payload)

            if op == FlashOperation.GET_STATUS:
                if not self.world_runtime:
                    return FlashResponse(success=False, operation=op, error="world_runtime not initialized")
                # 聚合查询：位置 + 时间 + 队伍信息
                status = {}
                loc_result = await self.world_runtime.get_current_location(world_id, session_id)
                status["location"] = loc_result if isinstance(loc_result, dict) else {"raw": loc_result}
                time_result = await self.world_runtime.get_game_time(world_id, session_id)
                status["time"] = time_result if isinstance(time_result, dict) else {"raw": time_result}
                status["party"] = {"has_party": False}
                return FlashResponse(success=True, operation=op, result=status)

            if op == FlashOperation.ADD_TEAMMATE:
                if not self.party_service:
                    return FlashResponse(success=False, operation=op, error="party_service not initialized")
                from app.models.party import TeammateRole
                character_id = params.get("character_id")
                name = params.get("name", character_id or "未知")
                role_str = params.get("role", "support")
                personality = params.get("personality", "")
                response_tendency = float(params.get("response_tendency", 0.5))
                if not character_id:
                    return FlashResponse(success=False, operation=op, error="missing character_id")
                party = await self.party_service.get_or_create_party(world_id, session_id)
                if party.is_full():
                    return FlashResponse(success=False, operation=op, error="队伍已满")
                if party.get_member(character_id):
                    return FlashResponse(success=False, operation=op, error=f"{name} 已在队伍中")
                try:
                    teammate_role = TeammateRole(role_str)
                except ValueError:
                    teammate_role = TeammateRole.SUPPORT
                member = await self.party_service.add_member(
                    world_id=world_id,
                    session_id=session_id,
                    character_id=character_id,
                    name=name,
                    role=teammate_role,
                    personality=personality,
                    response_tendency=response_tendency,
                )
                if member:
                    return FlashResponse(
                        success=True, operation=op,
                        result={"summary": f"{name} 加入了队伍", "character_id": character_id, "name": name, "role": teammate_role.value},
                    )
                return FlashResponse(success=False, operation=op, error="添加队友失败")

            if op == FlashOperation.REMOVE_TEAMMATE:
                if not self.party_service:
                    return FlashResponse(success=False, operation=op, error="party_service not initialized")
                character_id = params.get("character_id")
                reason = params.get("reason", "")
                if not character_id:
                    return FlashResponse(success=False, operation=op, error="missing character_id")
                # 图谱化并持久化该队友的实例（保存对话记忆）
                if self.instance_manager is not None and self.instance_manager.has(world_id, character_id):
                    try:
                        await self.instance_manager.maybe_graphize_instance(world_id, character_id)
                        instance = self.instance_manager.get(world_id, character_id)
                        if instance and hasattr(instance, "persist") and self.instance_manager.graph_store:
                            await instance.persist(self.instance_manager.graph_store)
                    except Exception as exc:
                        logger.warning("[remove_teammate] 实例图谱化/持久化失败 (%s): %s", character_id, exc)
                success = await self.party_service.remove_member(world_id, session_id, character_id)
                name = params.get("name", character_id)
                summary = f"{name} 离开了队伍"
                if reason:
                    summary += f"（{reason}）"
                return FlashResponse(
                    success=success, operation=op,
                    result={"summary": summary, "character_id": character_id, "reason": reason},
                    error=None if success else "移除队友失败",
                )

            if op == FlashOperation.DISBAND_PARTY:
                if not self.party_service:
                    return FlashResponse(success=False, operation=op, error="party_service not initialized")
                reason = params.get("reason", "")
                party = await self.party_service.get_party(world_id, session_id)
                if not party:
                    return FlashResponse(success=False, operation=op, error="当前没有队伍")
                saved_members = []
                for member in party.get_active_members():
                    if self.instance_manager is not None and self.instance_manager.has(world_id, member.character_id):
                        try:
                            await self.instance_manager.maybe_graphize_instance(world_id, member.character_id)
                            inst = self.instance_manager.get(world_id, member.character_id)
                            if inst and hasattr(inst, "persist") and self.instance_manager.graph_store:
                                await inst.persist(self.instance_manager.graph_store)
                            saved_members.append(member.name)
                        except Exception as exc:
                            logger.warning("[disband_party] 实例图谱化失败 (%s): %s", member.character_id, exc)
                success = await self.party_service.disband_party(world_id, session_id)
                summary = "队伍已解散"
                if reason:
                    summary += f"（{reason}）"
                return FlashResponse(
                    success=success, operation=op,
                    result={"summary": summary, "reason": reason, "saved_members": saved_members},
                    error=None if success else "解散队伍失败",
                )

            if op == FlashOperation.HEAL_PLAYER:
                if not self.character_service or not self.character_store:
                    return FlashResponse(success=False, operation=op, error="character_service not initialized")
                character = await self.character_store.get_character(world_id, session_id)
                if not character:
                    return FlashResponse(success=False, operation=op, error="no player character found")
                amount = int(params.get("amount", 0))
                old_hp = character.current_hp
                character.current_hp = min(character.current_hp + amount, character.max_hp)
                character.updated_at = datetime.utcnow()
                await self.character_store.save_character(world_id, session_id, character)
                return FlashResponse(
                    success=True, operation=op,
                    result={"hp": character.current_hp, "max_hp": character.max_hp, "healed": character.current_hp - old_hp},
                )

            if op == FlashOperation.DAMAGE_PLAYER:
                if not self.character_service or not self.character_store:
                    return FlashResponse(success=False, operation=op, error="character_service not initialized")
                character = await self.character_store.get_character(world_id, session_id)
                if not character:
                    return FlashResponse(success=False, operation=op, error="no player character found")
                amount = int(params.get("amount", 0))
                old_hp = character.current_hp
                character.current_hp = max(character.current_hp - amount, 0)
                character.updated_at = datetime.utcnow()
                await self.character_store.save_character(world_id, session_id, character)
                return FlashResponse(
                    success=True, operation=op,
                    result={"hp": character.current_hp, "max_hp": character.max_hp, "damage_taken": old_hp - character.current_hp},
                )

            if op == FlashOperation.ADD_XP:
                if not self.character_service:
                    return FlashResponse(success=False, operation=op, error="character_service not initialized")
                amount = int(params.get("amount", 0))
                result = await self.character_service.add_xp(world_id, session_id, amount)
                return FlashResponse(success=True, operation=op, result=result)

            if op == FlashOperation.ADD_ITEM:
                if not self.character_store:
                    return FlashResponse(success=False, operation=op, error="character_store not initialized")
                character = await self.character_store.get_character(world_id, session_id)
                if not character:
                    return FlashResponse(success=False, operation=op, error="no player character found")
                item_id = params.get("item_id", "unknown_item")
                item_name = params.get("item_name", item_id)
                quantity = int(params.get("quantity", 1))
                properties = params.get("properties")
                character.add_item(item_id, item_name, quantity, properties)
                character.updated_at = datetime.utcnow()
                await self.character_store.save_character(world_id, session_id, character)
                return FlashResponse(
                    success=True, operation=op,
                    result={"item_id": item_id, "item_name": item_name, "quantity": quantity},
                )

            if op == FlashOperation.REMOVE_ITEM:
                if not self.character_store:
                    return FlashResponse(success=False, operation=op, error="character_store not initialized")
                character = await self.character_store.get_character(world_id, session_id)
                if not character:
                    return FlashResponse(success=False, operation=op, error="no player character found")
                item_id = params.get("item_id", "")
                quantity = int(params.get("quantity", 1))
                removed = character.remove_item(item_id, quantity)
                if removed:
                    character.updated_at = datetime.utcnow()
                    await self.character_store.save_character(world_id, session_id, character)
                return FlashResponse(
                    success=removed, operation=op,
                    result={"item_id": item_id, "removed": removed},
                    error=None if removed else f"item {item_id} not found in inventory",
                )

            if op == FlashOperation.ABILITY_CHECK:
                from app.services.ability_check_service import AbilityCheckService
                check_service = AbilityCheckService(store=self.character_store)
                result = await check_service.perform_check(
                    world_id=world_id,
                    session_id=session_id,
                    ability=params.get("ability"),
                    skill=params.get("skill"),
                    dc=int(params.get("dc", 10)),
                )
                success = result.get("success", False) and "error" not in result
                return FlashResponse(
                    success=True, operation=op,
                    result=result,
                    error=result.get("error"),
                )

            return FlashResponse(success=False, operation=op, error=f"unsupported operation: {op}")
        except asyncio.CancelledError:
            raise
        except MCPServiceUnavailableError:
            raise
        except Exception as exc:
            return FlashResponse(success=False, operation=op, error=str(exc))

    @staticmethod
    def _matches_location_candidate(candidate: str, value: Optional[str]) -> bool:
        if not candidate or not value:
            return False
        lhs = candidate.strip().lower()
        rhs = str(value).strip().lower()
        if not lhs or not rhs:
            return False
        return lhs == rhs or lhs in rhs or rhs in lhs

    def _match_sub_location_from_context(
        self,
        candidate: str,
        sub_locations: List[Dict[str, Any]],
    ) -> Optional[str]:
        for item in sub_locations:
            if not isinstance(item, dict):
                continue
            sub_id = item.get("id")
            sub_name = item.get("name")
            if self._matches_location_candidate(candidate, sub_id) or self._matches_location_candidate(candidate, sub_name):
                return str(sub_id) if sub_id else None
        return None

    async def _resolve_enter_sub_location_id(
        self,
        world_id: str,
        session_id: str,
        params: Dict[str, Any],
    ) -> Optional[str]:
        """
        兼容两种参数：
        - sub_location_id: 规范 ID
        - sub_location: 名称或ID（LLM 常返回中文名称）
        """
        raw_id = params.get("sub_location_id")
        if raw_id is not None and str(raw_id).strip():
            return str(raw_id).strip()

        raw_name = params.get("sub_location")
        if raw_name is None and params.get("target") is not None:
            raw_name = params.get("target")
        if raw_name is None and params.get("destination") is not None:
            raw_name = params.get("destination")
        if raw_name is None and params.get("location") is not None:
            raw_name = params.get("location")
        if raw_name is None:
            return None

        candidate = str(raw_name).strip()
        if not candidate:
            return None

        if not self.world_runtime:
            return candidate

        current_map_id: Optional[str] = None
        try:
            state = await self.world_runtime.get_state(world_id, session_id)
            current_map_id = getattr(state, "player_location", None)
        except Exception:
            current_map_id = None

        try:
            location = await self.world_runtime.get_current_location(world_id, session_id)
            if isinstance(location, dict):
                current_map_id = current_map_id or location.get("location_id")
                sub_locations = (
                    location.get("available_sub_locations")
                    or location.get("sub_locations")
                    or []
                )
                resolved = self._match_sub_location_from_context(candidate, sub_locations)
                if resolved:
                    return resolved
        except Exception:
            pass

        # 再用导航器做兜底匹配（支持名称/ID模糊匹配）
        try:
            navigator = self.world_runtime._get_navigator(world_id)

            if current_map_id:
                if navigator.get_sub_location(current_map_id, candidate):
                    return candidate
                for sub_loc in navigator.get_sub_locations(current_map_id):
                    if (
                        self._matches_location_candidate(candidate, sub_loc.id)
                        or self._matches_location_candidate(candidate, sub_loc.name)
                    ):
                        return sub_loc.id

            map_id, sub_location_id = navigator.resolve_location(candidate)
            if sub_location_id and (not current_map_id or map_id == current_map_id):
                return sub_location_id
        except Exception:
            pass

        # 最终兜底：按 ID 原样传递给 runtime，让 runtime 给出明确报错
        return candidate

    def _build_state_delta(self, operation: str, changes: Dict[str, Any]) -> StateDelta:
        return StateDelta(
            delta_id=uuid.uuid4().hex,
            timestamp=datetime.utcnow(),
            operation=operation,
            changes=changes,
        )

    async def _apply_delta(self, world_id: str, session_id: str, delta: StateDelta) -> None:
        state = await self.state_manager.apply_delta(world_id, session_id, delta)
        if self.world_runtime:
            await self.world_runtime.persist_state(state)

    async def sync_combat_result_to_character(
        self,
        world_id: str,
        session_id: str,
        combat_payload: Dict[str, Any],
    ) -> None:
        """Sync combat results (HP/XP/gold) back to player character."""
        if not self.character_store or not self.character_service:
            return
        character = await self.character_store.get_character(world_id, session_id)
        if not character:
            return

        try:
            # Sync HP from combat result
            player_state = combat_payload.get("player_state") or {}
            hp_remaining = player_state.get("hp_remaining")
            if hp_remaining is not None:
                character.current_hp = max(0, min(int(hp_remaining), character.max_hp))

            # Extract final_result for rewards
            final_result = combat_payload.get("final_result") or combat_payload.get("result") or {}
            if isinstance(final_result, dict):
                result_type = final_result.get("result", "")
                rewards = final_result.get("rewards") or {}

                if result_type == "victory" and rewards:
                    # XP reward (use add_xp for level-up logic)
                    xp = int(rewards.get("xp", 0))
                    if xp > 0:
                        await self.character_service.add_xp(world_id, session_id, xp)
                        # Re-fetch after add_xp (it saves internally)
                        character = await self.character_store.get_character(world_id, session_id)
                        if not character:
                            return

                    # Gold reward
                    gold = int(rewards.get("gold", 0))
                    if gold > 0:
                        character.gold += gold

                    # Item rewards
                    for item_id in rewards.get("items", []):
                        character.add_item(item_id, item_id, 1)

            character.updated_at = datetime.utcnow()
            await self.character_store.save_character(world_id, session_id, character)
            logger.info("[combat_sync] Synced combat results to character for %s/%s", world_id, session_id)
        except Exception as exc:
            logger.error("[combat_sync] Failed to sync combat results: %s", exc, exc_info=True)

    async def _npc_dialogue_with_instance(
        self, world_id: str, npc_id: str, message: str,
    ) -> str:
        """通过 InstanceManager 进行 NPC 对话（维护多轮上下文）"""
        instance = await self.instance_manager.get_or_create(npc_id, world_id)
        user_add = instance.context_window.add_message("user", message)
        if user_add.should_graphize:
            await self.instance_manager.maybe_graphize_instance(world_id, npc_id)

        # 构建最近对话历史
        recent = instance.context_window.get_recent_messages(count=20)
        history_lines = []
        for msg in recent[:-1]:  # 排除刚加入的当前消息
            history_lines.append(f"{msg.role}: {msg.content}")

        system_prompt = instance.context_window.get_system_prompt() or f"你是 {npc_id}。请保持角色一致性。"
        history_text = "\n".join(history_lines) if history_lines else "无"
        prompt = (
            f"{system_prompt}\n\n"
            f"## 近期对话\n{history_text}\n\n"
            f"## 当前玩家输入\n{message}\n\n"
            "请以角色身份简洁回复，保持与近期对话一致。"
        )
        result = await self.llm_service.generate_simple(
            prompt,
            model_override=settings.admin_flash_model,
            thinking_level=settings.admin_flash_thinking_level,
        )
        result = (result or "").strip()
        if not result:
            raise RuntimeError("npc dialogue empty response")

        assistant_add = instance.context_window.add_message("assistant", result)
        if assistant_add.should_graphize:
            await self.instance_manager.maybe_graphize_instance(world_id, npc_id)
        instance.state.conversation_turn_count += 1
        return result

    async def _npc_dialogue_direct_flash(self, npc_id: str, message: str) -> str:
        """无实例时的直接 Flash 对话。"""
        prompt = (
            f"你是 {npc_id}。\n\n"
            f"玩家说：{message}\n\n"
            "请以第一人称、符合角色身份回复1-3句。"
        )
        result = await self.llm_service.generate_simple(
            prompt,
            model_override=settings.admin_flash_model,
            thinking_level=settings.admin_flash_thinking_level,
        )
        text = (result or "").strip()
        if not text:
            raise RuntimeError("npc direct flash response empty")
        return text

    async def _npc_respond(self, world_id: str, session_id: str, npc_id: str, message: str) -> Dict[str, Any]:
        if self.instance_manager is not None:
            response = await self._npc_dialogue_with_instance(world_id, npc_id, message)
        else:
            response = await self._npc_dialogue_direct_flash(npc_id, message)
        return {"response": response}

    async def _handle_combat_input(self, world_id: str, session_id: str, player_input: str) -> Dict[str, Any]:
        session_state = await self.session_store.get_session(world_id, session_id)
        if not session_state or not session_state.active_combat_id:
            return {"type": "error", "response": "没有活跃的战斗"}

        combat_id = session_state.active_combat_id
        actions_payload = await self.call_combat_tool("get_available_actions", {"combat_id": combat_id})
        available_actions = actions_payload.get("actions", [])
        action_id = self._match_combat_action(player_input, available_actions)

        if not action_id:
            return {
                "type": "combat",
                "phase": "input",
                "response": "无法识别的行动。",
                "available_actions": available_actions,
            }

        payload = await self.call_combat_tool("execute_action", {"combat_id": combat_id, "action_id": action_id})
        if payload.get("error"):
            return {"type": "error", "response": payload["error"]}

        combat_state = payload.get("combat_state", {})
        if combat_state.get("is_ended"):
            resolve_payload = await self.call_combat_tool(
                "resolve_combat_session",
                {"world_id": world_id, "session_id": session_id, "combat_id": combat_id, "dispatch": True},
            )
            # Sync combat results (HP/XP/gold) to player character
            sync_data = dict(resolve_payload) if isinstance(resolve_payload, dict) else {}
            sync_data["final_result"] = payload.get("final_result")
            await self.sync_combat_result_to_character(world_id, session_id, sync_data)
            await self._apply_delta(world_id, session_id, self._build_state_delta("end_combat", {"combat_id": None}))
            return {
                "type": "combat",
                "phase": "end",
                "result": payload.get("final_result"),
                "narration": payload.get("final_result", {}).get("summary", "战斗结束。"),
                "event_id": resolve_payload.get("event_id"),
            }

        return {
            "type": "combat",
            "phase": "action",
            "action_result": payload.get("action_result"),
            "narration": payload.get("action_result", {}).get("display_text", ""),
            "available_actions": available_actions,
        }

    def _match_combat_action(self, player_input: str, available_actions: list) -> Optional[str]:
        input_lower = player_input.lower()
        for action in available_actions:
            action_id = action.get("action_id", "").lower()
            display_name = action.get("display_name", "").lower()
            action_type = str(action.get("action_type", "")).lower()
            if action_id and action_id == input_lower:
                return action.get("action_id")
            if display_name and display_name in input_lower:
                return action.get("action_id")
            if action_type and action_type in input_lower:
                return action.get("action_id")
        return None

    async def _call_combat_tool(self, tool_name: str, arguments: Dict[str, Any]):
        try:
            pool = await MCPClientPool.get_instance()
            return await pool.call_tool(MCPClientPool.COMBAT, tool_name, arguments)
        except asyncio.CancelledError as exc:
            logger.info("[FlashCPU] combat MCP 调用被取消: %s", exc)
            raise
        except MCPServiceUnavailableError as exc:
            logger.error("[FlashCPU] combat MCP 服务不可用: %s", exc)
            raise
        except Exception as exc:
            logger.error("[FlashCPU] combat MCP 调用失败: %s", exc)
            raise

    async def call_combat_tool(self, tool_name: str, arguments: Dict[str, Any]):
        return await self._call_combat_tool(tool_name, arguments)
