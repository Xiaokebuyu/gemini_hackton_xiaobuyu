"""队友上下文构建器 — 从 response_service.py 提取。

纯上下文组装，零副作用：提示词加载、可见性过滤、格式化。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from app.world.npc.visibility import TeammateVisibilityManager

if TYPE_CHECKING:
    from app.models.party import (
        Party,
        PartyMember,
        TeammateResponseDecision,
    )


class TeammateContextBuilder:
    """队友上下文组装器。"""

    def __init__(
        self,
        visibility_manager: TeammateVisibilityManager,
        decision_prompt_path: Path,
        response_prompt_path: Path,
        agentic_system_prompt_path: Path,
    ) -> None:
        self.visibility_manager = visibility_manager
        self.decision_prompt_path = decision_prompt_path
        self.response_prompt_path = response_prompt_path
        self.agentic_system_prompt_path = agentic_system_prompt_path

    # ── 提示词加载 ──

    @staticmethod
    def load_prompt(path: Path) -> str:
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    # ── 回合公共参数 ──

    @staticmethod
    def resolve_round_basics(
        party: "Party",
        gm_response: str,
        context: Dict[str, Any],
    ) -> Tuple[List["PartyMember"], str, str, bool, Optional[str]]:
        """统一提取回合公共参数。"""
        active_members = party.get_active_members()
        world_id = context.get("world_id") or party.world_id
        gm_narration_full = str(context.get("gm_narration_full") or gm_response or "")
        is_private = bool(context.get("is_private", False))
        private_target = context.get("private_target")
        return active_members, world_id, gm_narration_full, is_private, private_target

    # ── 上下文过滤 ──

    @staticmethod
    def get_location_context(context: Dict[str, Any]) -> Dict[str, Any]:
        """兼容 location/location_context 两种键。"""
        location = context.get("location")
        if not location:
            location = context.get("location_context")
        return location or {}

    def build_filtered_context(
        self,
        member: "PartyMember",
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
    def get_effective_player_input(
        filtered_context: Dict[str, Any],
        player_input: str,
    ) -> str:
        if filtered_context.get("player_said_privately"):
            return str(filtered_context["player_said_privately"])
        if filtered_context.get("player_said"):
            return str(filtered_context["player_said"])
        return player_input

    # ── 格式化 ──

    @staticmethod
    def format_previous_responses(previous_responses: List[Dict[str, Any]]) -> str:
        if previous_responses:
            return "\n".join(
                f"- {r['name']}: {r['response']} ({r['reaction']})"
                for r in previous_responses
            )
        return "（还没有其他队友发言）"

    @staticmethod
    def format_location_values(location: Dict[str, Any]) -> Tuple[str, str, str]:
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
    def chunk_text(text: str, chunk_size: int = 72) -> List[str]:
        payload = str(text or "")
        if not payload:
            return []
        return [payload[i:i + chunk_size] for i in range(0, len(payload), chunk_size)]

    @staticmethod
    def members_for_decision(
        active_members: List["PartyMember"],
        is_private: bool,
        private_target: Optional[str],
        private_only: bool,
    ) -> List["PartyMember"]:
        if private_only and is_private and private_target:
            return [m for m in active_members if m.character_id == private_target]
        return active_members

    # ── 提示词构建 ──

    def get_agentic_system_prompt(self) -> str:
        prompt = self.load_prompt(self.agentic_system_prompt_path)
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

    def build_response_prompt(
        self,
        *,
        member: "PartyMember",
        player_input: str,
        gm_response: str,
        context: Dict[str, Any],
        decision: "TeammateResponseDecision",
        previous_responses: List[Dict[str, Any]],
        instance_history: Optional[str],
    ) -> str:
        prompt_template = self.load_prompt(self.response_prompt_path)
        if not prompt_template:
            return ""

        location = self.get_location_context(context)
        location_desc, available_dests_text, sub_locs_text = self.format_location_values(location)
        prev_responses_text = self.format_previous_responses(previous_responses)
        filtered_context = self.build_filtered_context(
            member=member,
            player_input=player_input,
            gm_response=gm_response,
            context=context,
        )
        effective_player_input = self.get_effective_player_input(
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
            pkg_text = self.format_context_package(teammate_package)
            prompt += f"\n\n## 你的图谱上下文（Flash编排）\n{pkg_text}\n"

        teammate_memory_summaries = context.get("teammate_memory_summaries") or {}
        memory_summary = teammate_memory_summaries.get(member.character_id)
        if memory_summary:
            prompt += f"\n\n## 你的记忆摘要\n{memory_summary}\n"
        return prompt

    @staticmethod
    def format_context_package(package: Dict[str, Any]) -> str:
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
