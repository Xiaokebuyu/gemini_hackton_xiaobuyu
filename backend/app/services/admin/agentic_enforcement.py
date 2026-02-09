"""
Strict tool-calling enforcement for v3 agentic flow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

from app.models.admin_protocol import IntentType


READ_ONLY_TOOLS: Set[str] = {
    "get_progress",
    "get_status",
    "recall_memory",
    "evaluate_story_conditions",
    "get_combat_options",
}

SIDE_EFFECT_TOOLS: Set[str] = {
    "navigate",
    "enter_sublocation",
    "update_time",
    "npc_dialogue",
    "start_combat",
    "choose_combat_action",
    "trigger_narrative_event",
    "add_teammate",
    "remove_teammate",
    "disband_party",
    "heal_player",
    "damage_player",
    "add_xp",
    "add_item",
    "remove_item",
    "ability_check",
    "generate_scene_image",
}

REPAIR_NAME_MAP: Dict[str, List[str]] = {
    # enforcement name → Python method name(s) for repair.
    # After unification, enforcement names == Python __name__.
    "navigate": ["navigate"],
    "enter_sublocation": ["enter_sublocation"],
    "update_time": ["update_time"],
    "npc_dialogue": ["npc_dialogue"],
    "start_combat": ["start_combat"],
    "trigger_narrative_event": ["trigger_narrative_event"],
    "add_teammate": ["add_teammate"],
    "remove_teammate": ["remove_teammate"],
    "disband_party": ["disband_party"],
    "heal_player": ["heal_player"],
    "damage_player": ["damage_player"],
    "add_xp": ["add_xp"],
    "add_item": ["add_item"],
    "remove_item": ["remove_item"],
    "ability_check": ["ability_check"],
    "get_progress": ["get_progress"],
    "get_status": ["get_status"],
    "get_combat_options": ["get_combat_options"],
    "choose_combat_action": ["choose_combat_action"],
    "recall_memory": ["recall_memory"],
    "evaluate_story_conditions": ["evaluate_story_conditions"],
    "generate_scene_image": ["generate_scene_image"],
}


class AgenticToolExecutionRequiredError(RuntimeError):
    """Raised when strict agentic tool policy cannot be satisfied."""

    def __init__(
        self,
        *,
        reason: str,
        expected_intent: str,
        missing_requirements: List[str],
        called_tools: List[str],
        repair_attempted: bool = False,
        repair_tool_names: Optional[List[str]] = None,
        repair_summary: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.reason = str(reason or "required tool calls missing")
        self.expected_intent = str(expected_intent or "unknown")
        self.missing_requirements = list(missing_requirements or [])
        self.called_tools = list(called_tools or [])
        self.repair_attempted = bool(repair_attempted)
        self.repair_tool_names = list(repair_tool_names or [])
        self.repair_summary = dict(repair_summary or {})
        super().__init__(self.reason)

    def to_http_detail(self) -> Dict[str, Any]:
        return {
            "error_type": "agentic_required_tool_missing",
            "reason": self.reason,
            "expected_intent": self.expected_intent,
            "missing_requirements": self.missing_requirements,
            "called_tools": self.called_tools,
            "repair_attempted": self.repair_attempted,
            "repair_tool_names": self.repair_tool_names,
            "repair_summary": self.repair_summary,
        }


@dataclass
class AgenticEnforcementResult:
    """Result of strict tool-calling enforcement."""

    passed: bool
    expected_intent: str
    reason: str
    required_all: List[str] = field(default_factory=list)
    required_any_groups: List[List[str]] = field(default_factory=list)
    missing_requirements: List[str] = field(default_factory=list)
    called_tools: List[str] = field(default_factory=list)
    successful_side_effect_tools: List[str] = field(default_factory=list)
    successful_read_tools: List[str] = field(default_factory=list)
    repair_allowed: bool = False
    repair_tool_names: List[str] = field(default_factory=list)

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "expected_intent": self.expected_intent,
            "reason": self.reason,
            "required_tools": self.required_all,
            "required_any_groups": self.required_any_groups,
            "missing_requirements": self.missing_requirements,
            "called_tools": self.called_tools,
            "repair_allowed": self.repair_allowed,
            "repair_tool_names": self.repair_tool_names,
        }


def _normalize_intent(value: Any) -> IntentType:
    if isinstance(value, IntentType):
        return value
    if isinstance(value, str):
        try:
            return IntentType(value)
        except ValueError:
            return IntentType.UNKNOWN
    return IntentType.UNKNOWN


def _keyword_any(text: str, keywords: Iterable[str]) -> bool:
    lowered = (text or "").lower()
    return any(str(keyword).lower() in lowered for keyword in keywords)


def infer_expected_intent(
    *,
    player_input: str,
    inferred_intent: Any,
) -> IntentType:
    """Infer expected intent for strict enforcement."""
    intent = _normalize_intent(inferred_intent)
    if intent not in {IntentType.ROLEPLAY, IntentType.UNKNOWN}:
        return intent

    text = (player_input or "").strip()
    if not text:
        return IntentType.ROLEPLAY

    if _keyword_any(text, ("状态", "进度", "任务", "主线", "目标", "现在", "当前位置", "时间")):
        return IntentType.SYSTEM_COMMAND
    if _keyword_any(text, ("等待", "休息", "快进", "过一会", "睡")):
        return IntentType.WAIT
    if _keyword_any(text, ("进入", "前往", "去", "移动", "赶往")):
        return IntentType.NAVIGATION
    if _keyword_any(text, ("组队", "队友", "加入队伍", "离队", "解散队伍")):
        return IntentType.TEAM_INTERACTION
    if _keyword_any(text, ("对话", "交谈", "询问", "问", "聊天")):
        return IntentType.NPC_INTERACTION
    if _keyword_any(text, ("攻击", "战斗", "开打", "交战", "冲锋")):
        return IntentType.START_COMBAT
    return IntentType.ROLEPLAY


def _extract_successful_tool_names(tool_calls: Iterable[Any]) -> List[str]:
    names: List[str] = []
    for call in tool_calls or []:
        if not bool(getattr(call, "success", False)):
            continue
        name = str(getattr(call, "name", "") or "").strip()
        if not name:
            continue
        names.append(name)
    # preserve order and deduplicate
    return list(dict.fromkeys(names))


def _build_requirements(
    *,
    expected_intent: IntentType,
    player_input: str,
) -> tuple[List[str], List[Set[str]], str]:
    required_all: List[str] = []
    required_any: List[Set[str]] = []
    reason = "missing required tools"
    text = (player_input or "").strip()

    if expected_intent == IntentType.SYSTEM_COMMAND:
        if _keyword_any(text, ("进度", "任务", "目标", "主线")):
            required_all.append("get_progress")
        if _keyword_any(text, ("状态", "时间", "地点", "位置", "队伍", "血量", "背包")):
            required_all.append("get_status")
        if not required_all:
            # Generic system query — at least one grounding tool
            required_any.append({"get_progress", "get_status"})
        reason = "system query requires grounding tools"
    elif expected_intent in {IntentType.NAVIGATION, IntentType.ENTER_SUB_LOCATION, IntentType.LEAVE_SUB_LOCATION}:
        required_any.append({"navigate", "enter_sublocation"})
        reason = "navigation intent requires movement tool call"
    elif expected_intent in {IntentType.WAIT, IntentType.REST}:
        required_all.append("update_time")
        reason = "wait/rest intent requires time advancement tool"
    elif expected_intent == IntentType.NPC_INTERACTION:
        required_all.append("npc_dialogue")
        reason = "npc interaction requires npc_dialogue tool"
    elif expected_intent == IntentType.TEAM_INTERACTION:
        required_any.append({"add_teammate", "remove_teammate", "disband_party", "npc_dialogue"})
        reason = "team interaction requires party/npc interaction tool"
    elif expected_intent == IntentType.START_COMBAT:
        required_all.append("start_combat")
        reason = "combat intent requires start_combat tool"
    elif expected_intent == IntentType.COMBAT_ACTION:
        required_any.append({"choose_combat_action", "get_combat_options", "start_combat"})
        reason = "combat action intent requires explicit combat tool call"
    elif expected_intent == IntentType.ROLEPLAY:
        # Relaxed: roleplay does not enforce specific tools.
        # The model decides which tools (if any) are appropriate.
        reason = "roleplay turn"

    # Local/state mutation keywords: enforce explicit side-effect tool calls.
    # These are applied on top of intent-derived rules.
    local_requirements: List[str] = []
    if _keyword_any(
        text,
        (
            "对话",
            "交谈",
            "询问",
            "聊天",
            "聊聊",
            "talk to",
            "speak to",
        ),
    ):
        local_requirements.append("npc_dialogue")
    if _keyword_any(
        text,
        (
            "加入队伍",
            "入队",
            "招募",
            "成为队友",
            "join the team",
            "recruit",
        ),
    ):
        local_requirements.append("add_teammate")
    if _keyword_any(
        text,
        (
            "离队",
            "退队",
            "踢出队伍",
            "移出队伍",
            "remove teammate",
        ),
    ):
        local_requirements.append("remove_teammate")
    if _keyword_any(text, ("解散队伍", "队伍解散", "disband party")):
        local_requirements.append("disband_party")
    if _keyword_any(
        text,
        (
            "治疗",
            "回血",
            "恢复生命",
            "回复生命",
            "加血",
            "heal",
        ),
    ):
        local_requirements.append("heal_player")
    if _keyword_any(
        text,
        (
            "扣血",
            "掉血",
            "受到伤害",
            "伤害我",
            "damage player",
        ),
    ):
        local_requirements.append("damage_player")
    if _keyword_any(
        text,
        (
            "加经验",
            "获得经验",
            "经验值",
            "xp",
        ),
    ):
        local_requirements.append("add_xp")
    if _keyword_any(
        text,
        (
            "获得道具",
            "拿到道具",
            "拾取",
            "捡到",
            "放入背包",
            "add item",
        ),
    ):
        local_requirements.append("add_item")
    if _keyword_any(
        text,
        (
            "使用道具",
            "消耗道具",
            "移除道具",
            "丢弃道具",
            "remove item",
        ),
    ):
        local_requirements.append("remove_item")
    if _keyword_any(
        text,
        (
            "检定",
            "判定",
            "掷骰",
            "dc",
            "ability check",
            "skill check",
        ),
    ):
        local_requirements.append("ability_check")

    if local_requirements:
        required_all.extend(local_requirements)
        reason = "explicit local state mutation requires corresponding tool call"

    # keep deterministic order, remove duplicates
    required_all = list(dict.fromkeys(required_all))

    return required_all, required_any, reason


def evaluate_agentic_tool_usage(
    *,
    player_input: str,
    inferred_intent: Any,
    tool_calls: Iterable[Any],
) -> AgenticEnforcementResult:
    """Evaluate whether this round satisfies strict tool-calling policy."""
    expected_intent = infer_expected_intent(
        player_input=player_input,
        inferred_intent=inferred_intent,
    )
    called = _extract_successful_tool_names(tool_calls)
    called_set = set(called)
    required_all, required_any, reason = _build_requirements(
        expected_intent=expected_intent,
        player_input=player_input,
    )

    missing: List[str] = []
    for req in required_all:
        if req not in called_set:
            missing.append(req)
    for group in required_any:
        if not (called_set & group):
            missing.append(f"one_of({', '.join(sorted(group))})")

    side_effect_tools = sorted(called_set & SIDE_EFFECT_TOOLS)
    read_tools = sorted(called_set & READ_ONLY_TOOLS)
    passed = len(missing) == 0

    repair_tool_names: List[str] = []
    if not passed:
        for item in missing:
            if item.startswith("one_of(") and item.endswith(")"):
                tool_names = item[7:-1].split(",")
                for name in tool_names:
                    name = name.strip()
                    if name in REPAIR_NAME_MAP:
                        repair_tool_names.extend(REPAIR_NAME_MAP[name])
                continue
            if item in REPAIR_NAME_MAP:
                repair_tool_names.extend(REPAIR_NAME_MAP[item])
        repair_tool_names = list(dict.fromkeys(repair_tool_names))

    # Safety: only auto-repair when no successful side-effect tools were executed.
    repair_allowed = (not passed) and len(side_effect_tools) == 0

    return AgenticEnforcementResult(
        passed=passed,
        expected_intent=expected_intent.value,
        reason=reason if not passed else "ok",
        required_all=required_all,
        required_any_groups=[sorted(group) for group in required_any],
        missing_requirements=missing,
        called_tools=called,
        successful_side_effect_tools=side_effect_tools,
        successful_read_tools=read_tools,
        repair_allowed=repair_allowed,
        repair_tool_names=repair_tool_names,
    )
