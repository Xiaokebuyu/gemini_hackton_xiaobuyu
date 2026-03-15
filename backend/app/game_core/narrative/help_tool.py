"""OfferHelpTool — NPC/Teammate self-initiated aid.

An NPC or teammate that chooses to spontaneously help the player calls this
tool.  The allowed help types are constrained by the character's tags:

    healer / temple_keeper  → heal, buff, cure
    merchant                → gift_item
    recruitable             → gift_item, heal, information
    guard                   → information
    (no special tag)        → information only

Effect atoms are executed through the shared ``execute_service_effects()``
helper from ``service_tool.py``, which routes each atom to the appropriate
game command.

Decision record: D-WE-offer-help (cheeky-waddling-dongarra plan, task 1)
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from app.game_core.narrative.character_tools import _CharacterTool
from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.models import ToolResult
from app.game_core.rules.models import Command, ExecuteResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tags → allowed help_type sets
# ---------------------------------------------------------------------------

TAG_HELP_ALLOWANCES: dict[str, set[str]] = {
    "healer":        {"heal", "buff", "cure"},
    "temple_keeper": {"heal", "buff", "cure"},
    "merchant":      {"gift_item"},
    "recruitable":   {"gift_item", "heal", "information"},
    "guard":         {"information"},
}

# Every character can share information; special tags unlock additional types.
_DEFAULT_ALLOWANCES: set[str] = {"information"}


def _allowed_types_for_tags(tags: list[str]) -> set[str]:
    """Return the union of all help_types permitted by the given tag list."""
    allowed: set[str] = set(_DEFAULT_ALLOWANCES)
    for tag in tags:
        extra = TAG_HELP_ALLOWANCES.get(tag)
        if extra:
            allowed |= extra
    return allowed


# ---------------------------------------------------------------------------
# help_type → effect atoms factory
# ---------------------------------------------------------------------------

HELP_TYPE_EFFECTS: dict[str, Callable[[dict[str, Any]], list[dict[str, Any]]]] = {
    "heal": lambda d: [
        {"type": "restore_hp", "amount": min(int(d.get("amount", 15)), 50)}
    ],
    "buff": lambda d: [
        {
            "type": "apply_effect",
            "effect_id": d.get("effect_id", "blessed"),
            "duration_ticks": 4,
        }
    ],
    "cure": lambda d: [
        {
            "type": "remove_effect",
            "effect_id": d.get("effect_id", ""),
        }
    ],
    "gift_item": lambda d: [
        {
            "type": "grant_item",
            "item_id": d.get("item_id", ""),
            "count": min(int(d.get("count", 1)), 3),
        }
    ],
    "information": lambda d: [
        {"type": "add_knowledge", "fact": d.get("fact", "")}
    ],
}


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


class OfferHelpTool(_CharacterTool):
    """Spontaneously offer aid to the player.

    NPC/teammate decides on their own to help — heal, buff, cure a status
    effect, gift an item, or share information.  The specific help types
    available depend on the character's tags.
    """

    @property
    def name(self) -> str:
        return "offer_help"

    @property
    def description(self) -> str:
        return (
            "根据自身能力和对冒险者的印象，主动提供援助（赠送物品、治疗、增益、分享情报）。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "help_type": {
                    "type": "string",
                    "enum": ["heal", "buff", "cure", "gift_item", "information"],
                    "description": "援助类型",
                },
                "details": {
                    "type": "object",
                    "description": (
                        "效果参数。heal: {amount}; buff: {effect_id, duration_ticks}; "
                        "cure: {effect_id}; gift_item: {item_id, count}; "
                        "information: {fact}"
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "为什么要帮助（NPC 的内心动机）",
                },
            },
            "required": ["help_type", "reason"],
        }

    @property
    def allowed_roles(self) -> list[str]:
        return ["npc", "teammate"]

    @property
    def applicable_traits(self) -> list[str]:
        # All NPC/Teammate can use this tool; tag constraints are enforced at runtime.
        return []

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        character_id = self._get_character_id(context)
        if not character_id:
            return self._no_character_id()

        # 1. Validate help_type param.
        help_type = params.get("help_type", "")
        if not isinstance(help_type, str) or help_type not in HELP_TYPE_EFFECTS:
            return ToolResult(
                ok=False,
                message=f"无效的援助类型 '{help_type}'。",
                metadata={"status": "invalid_help_type"},
            )

        # 2. Read character tags from context metadata (injected by context builder).
        npc_tags: list[str] = []
        if isinstance(context.metadata, dict):
            raw_tags = context.metadata.get("npc_tags", [])
            if isinstance(raw_tags, list):
                npc_tags = [str(t) for t in raw_tags]

        # 3. Tag-based capability check.
        allowed = _allowed_types_for_tags(npc_tags)
        if help_type not in allowed:
            return ToolResult(
                ok=False,
                message=(
                    f"'{help_type}' 超出当前角色的援助能力范围（"
                    f"角色标签：{npc_tags or ['无']}）。"
                ),
                metadata={
                    "status": "capability_exceeded",
                    "help_type": help_type,
                    "allowed_types": sorted(allowed),
                    "npc_tags": npc_tags,
                },
            )

        # 4. Build effect atoms via HELP_TYPE_EFFECTS factory.
        details: dict[str, Any] = {}
        raw_details = params.get("details")
        if isinstance(raw_details, dict):
            details = raw_details

        try:
            effects = HELP_TYPE_EFFECTS[help_type](details)
        except (TypeError, ValueError, KeyError) as exc:
            return ToolResult(
                ok=False,
                message=f"效果参数无效：{exc}",
                metadata={"status": "invalid_details"},
            )

        # 5. Construct a one-shot, free service descriptor and delegate to
        #    execute_service_effects() to run the atoms through the command engine.
        service: dict[str, Any] = {
            "service_id": f"spontaneous_{help_type}",
            "label": _HELP_TYPE_LABELS.get(help_type, help_type),
            "price": 0,
            "one_shot": True,
            "source": "npc_spontaneous",
            "effects": effects,
        }

        # Lazy import to avoid circular dependency.
        from app.game_core.narrative.service_tool import execute_service_effects  # noqa: PLC0415

        exec_result = execute_service_effects(
            service,
            context.run_command,
            player_gold=0,
            npc_id=character_id,
        )

        if not exec_result.success:
            return ToolResult(
                ok=False,
                message=exec_result.message,
                metadata={"status": exec_result.status, **exec_result.extra},
            )

        # 6. Write to SceneBus.
        reason = ""
        raw_reason = params.get("reason", "")
        if isinstance(raw_reason, str):
            reason = raw_reason.strip()

        label = _HELP_TYPE_LABELS.get(help_type, help_type)
        scene_content = f"主动为冒险者提供了{label}"
        if reason:
            scene_content = f"{scene_content}（{reason}）"

        self._add_scene_entry(
            context,
            character_id,
            scene_content,
            tags=["help", f"help_{help_type}"],
        )

        return ToolResult(
            ok=True,
            message=f"已为冒险者提供{label}援助。",
            metadata={
                "status": "ok",
                "event_type": "npc_help_offered",
                "character_id": character_id,
                "help_type": help_type,
                "reason": reason,
                "effects_applied": exec_result.applied_effects,
                "label": label,
            },
        )


# ---------------------------------------------------------------------------
# Display labels
# ---------------------------------------------------------------------------

_HELP_TYPE_LABELS: dict[str, str] = {
    "heal":        "治疗",
    "buff":        "增益",
    "cure":        "解除状态",
    "gift_item":   "赠送物品",
    "information": "情报分享",
}
