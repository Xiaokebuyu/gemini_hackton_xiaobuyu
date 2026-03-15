"""AIOsirisHook MVP implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time
from typing import Any, Mapping, Protocol

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.hooks.rest_phase import (
    is_quiet_rest_slot,
    resolve_rest_phase,
)
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules.models import Command
from app.game_core.state import StateChange


logger = logging.getLogger(__name__)


class OsirisVisibleConsequenceRenderer:
    """Render executed Osiris consequences into SceneBus system entries."""

    _VISIBLE_COMMANDS: frozenset[str] = frozenset({
        "create_rumor",
        "adjust_danger",
        "advance_quest",
        "schedule_event",
        "modify_completion",
    })
    _INVISIBLE_COMMANDS: frozenset[str] = frozenset({
        "set_flag",
        "modify_disposition",
        "modify_approval",
        "modify_location",
        "add_knowledge",
    })

    @classmethod
    def render_visible_entries(
        cls,
        context: SettlementContext,
        executed_commands: list[Command],
    ) -> list[dict[str, Any]]:
        if not executed_commands:
            return []

        current_tick = context.state.time.absolute_tick() if context.state.has_slice("time") else 0.0
        entries: list[dict[str, Any]] = []
        for command in executed_commands:
            command_type = str(command.type).strip().lower()
            if not cls._is_visible_command(command_type):
                continue

            metadata = cls._build_metadata(command_type, command)
            content = cls._build_content(command_type, command.params, metadata)
            if not content:
                continue

            entries.append({
                "source": "ai_osiris",
                "content": content,
                "visibility": "system",
                "audience": None,
                "tags": ["ai_osiris", "visible_consequence", command_type],
                "timestamp": float(current_tick),
                "metadata": metadata,
            })
        return entries

    @classmethod
    def _is_visible_command(cls, command_type: str) -> bool:
        normalized = str(command_type).strip().lower()
        if normalized in cls._INVISIBLE_COMMANDS:
            return False
        return normalized in cls._VISIBLE_COMMANDS

    @classmethod
    def _build_content(
        cls,
        command_type: str,
        params: Mapping[str, Any],
        metadata: Mapping[str, Any],
    ) -> str:
        reason = metadata.get("reason")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()
        if command_type == "create_rumor":
            raw_rumor = params.get("rumor")
            rumor_title = (
                raw_rumor.get("title")
                if isinstance(raw_rumor, Mapping)
                else params.get("rumor_title")
            )
            if rumor_title is None:
                rumor_title = params.get("text")
            if rumor_title is None:
                rumor_title = params.get("content")
            rumor_desc = (
                str(rumor_title).strip()
                if rumor_title is not None
                else "A rumor"
            )
            return f"{rumor_desc} has been recorded in world knowledge."
        if command_type == "adjust_danger":
            return "Environmental danger level changed."
        if command_type == "advance_quest":
            quest_id = params.get("quest_id") or params.get("id")
            if quest_id is not None:
                return f"Quest state changed: {str(quest_id).strip()}."
            return "A quest state has changed."
        if command_type == "schedule_event":
            event_type = params.get("event_type")
            if event_type is not None:
                return f"World event scheduled: {str(event_type).strip()}."
            return "A world event has been scheduled."
        if command_type == "modify_completion":
            chapter = params.get("chapter_id") or params.get("chapter")
            if chapter is not None:
                return f"Chapter progress changed: {str(chapter).strip()}."
            return "Chapter progress has been updated."
        return ""

    @classmethod
    def _build_metadata(
        cls,
        command_type: str,
        command: Command,
    ) -> dict[str, Any]:
        raw_meta = {}
        if isinstance(command.context, Mapping):
            nested = command.context.get("osiris_meta")
            if isinstance(nested, Mapping):
                raw_meta = {str(key): value for key, value in nested.items()}
        return {
            "kind": "visible_consequence",
            "command_type": command_type,
            "reason": str(raw_meta.get("reason", "")).strip(),
            "visibility_hint": str(raw_meta.get("visibility_hint", "visible")).strip() or "visible",
            "confidence": str(raw_meta.get("confidence", "")).strip().lower(),
            "refs": cls._build_refs(command_type, command.params),
        }

    @staticmethod
    def _build_refs(command_type: str, params: Mapping[str, Any]) -> dict[str, Any]:
        refs: dict[str, Any] = {}
        if command_type == "advance_quest":
            quest_id = params.get("quest_id") or params.get("id")
            if isinstance(quest_id, str) and quest_id.strip():
                refs["quest_id"] = quest_id.strip()
        elif command_type == "adjust_danger":
            area_id = params.get("area_id")
            if isinstance(area_id, str) and area_id.strip():
                refs["area_id"] = area_id.strip()
        elif command_type == "schedule_event":
            event_id = params.get("event_id")
            if isinstance(event_id, str) and event_id.strip():
                refs["event_id"] = event_id.strip()
            event_type = params.get("event_type")
            if isinstance(event_type, str) and event_type.strip():
                refs["event_type"] = event_type.strip()
        elif command_type == "modify_completion":
            chapter_id = params.get("chapter_id") or params.get("chapter")
            if isinstance(chapter_id, str) and chapter_id.strip():
                refs["chapter_id"] = chapter_id.strip()
        elif command_type == "create_rumor":
            rumor_id = params.get("rumor_id")
            if isinstance(rumor_id, str) and rumor_id.strip():
                refs["rumor_id"] = rumor_id.strip()
            area_id = params.get("area_id")
            if isinstance(area_id, str) and area_id.strip():
                refs["area_id"] = area_id.strip()
        return refs

_ALLOWED_COMMAND_TYPES: tuple[str, ...] = (
    "set_flag",
    "adjust_danger",
)

_TRIVIAL_ACTION_TYPES = frozenset(
    {
        "look_inventory",
        "check_stats",
        "check_quest_log",
        "check_map",
        "equip",
        "unequip",
        "save_game",
        "load_game",
    }
)
_TICK_KIND_TRAVEL = frozenset({"move_area", "enter_sub_location", "leave_sub_location"})
_TICK_KIND_REST = frozenset({"rest_short", "rest_long", "night_watch", "set_camp"})
_TICK_KIND_CONVERSATION = frozenset(
    {
        "speak",
        "dialogue",
        "talk",
        "emote",
        "dialogue_turn",
        "public_utterance_turn",
        "party_chat_turn",
        "free_chat_turn",
        "private_chat_turn",
        "investigate_clue",
        "resolve_clue_option",
    }
)
_TICK_KIND_COMBAT = frozenset(
    {
        "attack",
        "defend",
        "disengage",
        "dash",
        "shove",
        "flee",
        "offhand_attack",
        "stand_up",
        "use_combat_item",
        "saving_throw",
        "contest",
    }
)

_TARGET_PARAM_KEYS: tuple[str, ...] = (
    "target", "seller_npc", "buyer_npc", "npc_id",
    "target_npc", "character_id", "character",
    "container_id", "object_id",
)

_ACTION_VERBS: dict[str, str] = {
    # Combat
    "attack": "attacked", "defend": "defended", "disengage": "disengaged",
    "dash": "dashed", "shove": "shoved", "flee": "fled",
    "offhand_attack": "attacked (off-hand)", "stand_up": "stood up",
    "use_combat_item": "used",
    # Trade
    "trade_buy": "purchased", "trade_sell": "sold", "refresh_shop": "refreshed shop",
    # Crime
    "steal": "stole", "lockpick": "picked lock on",
    # Navigation
    "move_area": "traveled to", "enter_sub_location": "entered",
    "leave_sub_location": "left",
    # Skill checks
    "skill_check": "", "saving_throw": "saving throw",
    "contest": "contested", "investigate": "investigated",
    "investigate_clue": "investigated clue", "resolve_clue_option": "followed clue lead",
    # Inventory
    "pick_up": "picked up", "drop": "dropped",
    "equip": "equipped", "unequip": "unequipped",
    "use_item": "used", "consume_resource": "consumed",
    # Rest
    "rest_short": "took short rest", "rest_long": "took long rest",
    "night_watch": "kept watch", "set_camp": "set up camp",
    "dialogue_turn": "spoke with",
    "public_utterance_turn": "spoke aloud",
    "party_chat_turn": "spoke to the party",
    "free_chat_turn": "spoke to the party",
    "private_chat_turn": "spoke privately with",
    # Spellcasting
    "cast_spell": "cast", "prepare_spells": "prepared spells",
    "break_concentration": "broke concentration",
    # Growth
    "add_xp": "gained experience", "level_up": "leveled up",
    "apply_asi": "improved ability", "choose_subclass": "chose subclass",
    "create_character": "created character",
    # Container
    "open_container": "opened", "disarm_trap": "disarmed trap on",
    "take_from_container": "took from", "take_all": "took all from",
    "interact_object": "interacted with",
    # World state
    "set_flag": "set flag", "modify_disposition": "influenced",
    "modify_approval": "affected approval of", "advance_quest": "advanced quest",
    "schedule_event": "scheduled event", "create_rumor": "spread rumor",
    "modify_location": "modified", "add_knowledge": "revealed knowledge to",
    "modify_completion": "progressed chapter", "adjust_danger": "adjusted danger in",
}

_TARGET_PREPOSITIONS: dict[str, str] = {
    "trade_buy": "from", "trade_sell": "to",
    "steal": "from", "lockpick": "on",
    "cast_spell": "on", "shove": "",
    "attack": "", "offhand_attack": "",
}

_ACTION_CATEGORY_TAGS: dict[str, list[str]] = {
    # Combat
    "attack": ["COMBAT"], "defend": ["COMBAT"], "disengage": ["COMBAT"],
    "dash": ["COMBAT"], "shove": ["COMBAT"], "flee": ["COMBAT"],
    "offhand_attack": ["COMBAT"], "stand_up": ["COMBAT"],
    "use_combat_item": ["COMBAT", "ITEM_USE"],
    # Trade
    "trade_buy": ["TRANSACTION"], "trade_sell": ["TRANSACTION"],
    "refresh_shop": ["TRANSACTION"],
    # Crime
    "steal": ["CRIME", "THEFT"], "lockpick": ["CRIME"],
    # Navigation
    "move_area": ["NAVIGATION"], "enter_sub_location": ["NAVIGATION"],
    "leave_sub_location": ["NAVIGATION"],
    # Skill checks
    "skill_check": ["SKILL_CHECK"], "saving_throw": ["SKILL_CHECK"],
    "contest": ["SKILL_CHECK"], "investigate": ["SKILL_CHECK", "EXPLORATION"],
    "investigate_clue": ["DIALOGUE", "INVESTIGATION", "CLUE"],
    "resolve_clue_option": ["INVESTIGATION", "CLUE"],
    # Inventory
    "pick_up": ["INVENTORY"], "drop": ["INVENTORY"],
    "equip": ["INVENTORY"], "unequip": ["INVENTORY"],
    "use_item": ["ITEM_USE"], "consume_resource": ["ITEM_USE"],
    # Rest
    "rest_short": ["REST"], "rest_long": ["REST"],
    "night_watch": ["REST"], "set_camp": ["REST"],
    "dialogue_turn": ["DIALOGUE", "NPC_INTERACTION"],
    "public_utterance_turn": ["DIALOGUE", "PUBLIC_UTTERANCE"],
    "party_chat_turn": ["DIALOGUE", "PARTY_CHAT"],
    "free_chat_turn": ["DIALOGUE", "PARTY_CHAT"],
    "private_chat_turn": ["DIALOGUE", "PRIVATE_CHAT"],
    # Spellcasting
    "cast_spell": ["SPELLCASTING"], "prepare_spells": ["SPELLCASTING"],
    "break_concentration": ["SPELLCASTING"],
    # Growth
    "add_xp": ["PROGRESSION"], "level_up": ["PROGRESSION"],
    "apply_asi": ["PROGRESSION"], "choose_subclass": ["PROGRESSION"],
    "create_character": ["PROGRESSION"],
    # Container
    "open_container": ["CONTAINER"], "disarm_trap": ["CONTAINER", "TRAP"],
    "take_from_container": ["CONTAINER", "LOOT"],
    "take_all": ["CONTAINER", "LOOT"], "interact_object": ["INTERACTION"],
    # World state
    "set_flag": ["WORLD_STATE"],
    "modify_disposition": ["WORLD_STATE", "SOCIAL"],
    "modify_approval": ["WORLD_STATE", "SOCIAL"],
    "advance_quest": ["WORLD_STATE", "QUEST"],
    "schedule_event": ["WORLD_STATE"],
    "create_rumor": ["WORLD_STATE", "SOCIAL"],
    "modify_location": ["WORLD_STATE"],
    "add_knowledge": ["WORLD_STATE", "SOCIAL"],
    "modify_completion": ["WORLD_STATE", "QUEST"],
    "adjust_danger": ["WORLD_STATE"],
}


@dataclass(slots=True)
class OsirisEffect:
    """One effect emitted by the MechanicalOsirisEngine."""

    effect_type: str  # "set_flag" | "adjust_danger" | "area_event"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OsirisRuleMatch:
    """Result of matching one rule table entry."""

    rule_index: int
    trigger_tag: str
    area_tag_matched: str | None
    effects: list[OsirisEffect] = field(default_factory=list)


class MechanicalOsirisEngine:
    """Pure rule-table driven causal engine based on SceneBus ACTION_TAGS.

    Matches action_tags against CONSEQUENCE_RULES and returns a list of
    Commands + area_events for the AIOsirisHook to apply.
    """

    # Rules keyed on SceneBus ENGINE tags (from tick_coordinator._SEMANTIC_TAGS)
    # Each rule has:
    #   trigger.tag       — required tag in action_tags
    #   trigger.area_tag  — optional: area must have this tag (from AreaState.tags)
    #   effects           — list of effect descriptors
    CONSEQUENCE_RULES: list[dict[str, Any]] = [
        # ── Combat ──
        {
            "trigger": {"tag": "COMBAT", "area_tag": "safe_zone"},
            "effects": [
                {"type": "adjust_danger", "delta": +0.5},
                {"type": "set_flag", "flag": "disturbance_{area_id}"},
                {"type": "area_event", "event": "区域内发生了战斗", "severity": "major"},
            ],
        },
        {
            "trigger": {"tag": "COMBAT_END"},
            "effects": [
                {"type": "adjust_danger", "delta": -0.3},
                {"type": "area_event", "event": "战斗结束，紧张态势缓和", "severity": "minor"},
            ],
        },
        # ── Exploration ──
        {
            "trigger": {"tag": "NAVIGATION"},
            "effects": [
                {"type": "area_event", "event": "冒险者移动到新区域", "severity": "minor"},
            ],
        },
        # Investigation: area_event is written directly by clue handler (1-C)
        {
            "trigger": {"tag": "INVESTIGATION"},
            "effects": [],
        },
        # ── Rest ──
        {
            "trigger": {"tag": "LONG_REST", "area_tag": "hostile"},
            "effects": [
                {"type": "adjust_danger", "delta": +0.1},
                {"type": "area_event", "event": "在危险区域休息，敌人有时间重新部署", "severity": "minor"},
            ],
        },
        # ── Quest progress ──
        {
            "trigger": {"tag": "QUEST_PROGRESS"},
            "effects": [
                {"type": "area_event", "event": "任务取得重要进展", "severity": "major"},
            ],
        },
    ]

    # Faction propagation config (reserved for future use — witnesses path)
    FACTION_PROPAGATION: dict[str, Any] = {
        "decay": 0.5,   # delta decays 50% when propagated to faction members
        "max_hops": 1,  # no multi-hop propagation
        # Trigger: COMBAT + safe_zone + has_witnesses
    }

    def evaluate(
        self,
        action_tags: set[str],
        area_tags: list[str],
        has_witnesses: bool,
        area_id: str,
    ) -> list[dict[str, Any]]:
        """Match rules and return a list of effect descriptors.

        Each effect has:
          type: "set_flag" | "adjust_danger" | "area_event"
          (plus type-specific fields: delta, area_id, flag, event, severity)
        """
        if not action_tags:
            return []

        area_tag_set = set(area_tags)
        effects: list[dict[str, Any]] = []
        seen_rule_indices: set[int] = set()

        for idx, rule in enumerate(self.CONSEQUENCE_RULES):
            trigger = rule.get("trigger", {})
            required_tag = str(trigger.get("tag", ""))
            required_area_tag = trigger.get("area_tag")

            if required_tag not in action_tags:
                continue
            if required_area_tag is not None and required_area_tag not in area_tag_set:
                continue
            if idx in seen_rule_indices:
                continue
            seen_rule_indices.add(idx)

            for raw_effect in rule.get("effects", []):
                if not isinstance(raw_effect, dict):
                    continue
                effect = dict(raw_effect)
                effect["area_id"] = area_id
                # Template expansion: {area_id} in string values
                for key, val in effect.items():
                    if isinstance(val, str) and "{area_id}" in val:
                        effect[key] = val.replace("{area_id}", area_id)
                effects.append(effect)

        return effects


@dataclass(slots=True)
class AIOsirisDecision:
    consequences: list[Command | Mapping[str, Any]] = field(default_factory=list)
    reasoning: str = ""
    visible_change: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class AIOsirisEvaluator(Protocol):
    async def evaluate(
        self,
        summary: dict[str, Any],
        snapshot: dict[str, Any],
        rules_context: dict[str, Any],
    ) -> AIOsirisDecision | Mapping[str, Any]:
        ...


class AIOsirisProvider(AIOsirisEvaluator, Protocol):
    """Structured provider boundary for P30 AI Osiris evaluation."""

    async def evaluate(
        self,
        summary: dict[str, Any],
        snapshot: dict[str, Any],
        rules_context: dict[str, Any],
    ) -> AIOsirisDecision | Mapping[str, Any]:
        ...


class NullAIOsirisEvaluator:
    async def evaluate(
        self,
        summary: dict[str, Any],
        snapshot: dict[str, Any],
        rules_context: dict[str, Any],
    ) -> AIOsirisDecision:
        del summary, snapshot, rules_context
        return AIOsirisDecision(metadata={"status": "noop"})


class BasicAIOsirisEvaluator:
    """Deterministic default evaluator for the runtime skeleton."""

    async def evaluate(
        self,
        summary: dict[str, Any],
        snapshot: dict[str, Any],
        rules_context: dict[str, Any],
    ) -> AIOsirisDecision:
        del rules_context
        if not isinstance(summary, Mapping) or not isinstance(snapshot, Mapping):
            return AIOsirisDecision(
                metadata={
                    "status": "noop",
                    "provider": "default_evaluator",
                    "reason": "invalid_context",
                }
            )

        changed_slices = self._normalize_changed_slices(summary.get("changed_slices", []))
        active_flags = self._normalize_mapping(snapshot.get("active_flags"))

        if "quests" in changed_slices:
            raw_chapter = snapshot.get("current_chapter")
            current_chapter = (
                str(raw_chapter).strip()
                if raw_chapter is not None
                else ""
            )
            target_value = current_chapter or "untracked"
            if active_flags.get("osiris_last_quest_change_chapter") == target_value:
                return self._noop(reason="stable")
            return AIOsirisDecision(
                consequences=[
                    {
                        "type": "set_flag",
                        "params": {
                            "key": "osiris_last_quest_change_chapter",
                            "value": target_value,
                        },
                    }
                ],
                reasoning="Recorded the latest quest-side change for follow-up.",
                visible_change=False,
                metadata={
                    "status": "deterministic",
                    "provider": "default_evaluator",
                    "branch": "quests",
                    "command_count": 1,
                },
            )

        if "flags" in changed_slices and bool(active_flags.get("quest_started")):
            if active_flags.get("osiris_ack_quest_started") is True:
                return self._noop(reason="stable")
            return AIOsirisDecision(
                consequences=[
                    {
                        "type": "set_flag",
                        "params": {
                            "key": "osiris_ack_quest_started",
                            "value": True,
                        },
                    }
                ],
                reasoning="Acknowledged the quest_started flag.",
                visible_change=False,
                metadata={
                    "status": "deterministic",
                    "provider": "default_evaluator",
                    "branch": "flags",
                    "command_count": 1,
                },
            )

        return self._noop(reason="stable")

    @staticmethod
    def _normalize_changed_slices(raw_value: Any) -> list[str]:
        if not isinstance(raw_value, list):
            return []
        changed_slices: list[str] = []
        for item in raw_value:
            if not isinstance(item, str):
                continue
            normalized = item.strip()
            if normalized:
                changed_slices.append(normalized)
        return changed_slices

    @staticmethod
    def _normalize_mapping(raw_value: Any) -> dict[str, Any]:
        if not isinstance(raw_value, Mapping):
            return {}
        return {str(key): value for key, value in raw_value.items()}

    @staticmethod
    def _noop(*, reason: str) -> AIOsirisDecision:
        return AIOsirisDecision(
            metadata={
                "status": "noop",
                "provider": "default_evaluator",
                "reason": reason,
            }
        )


class AIOsirisHook(NoOpSettlementHook):
    HOOK_PRIORITY = 30
    HOOK_NAME = "ai_osiris"
    MAX_CONSEQUENCES = 5

    def __init__(
        self,
        evaluator: AIOsirisProvider | None = None,
        engine: MechanicalOsirisEngine | None = None,
        encounter_phase: Any = None,
        perception_phase: Any = None,
        event_phase: Any = None,
    ) -> None:
        del evaluator  # LLM evaluator path removed; MechanicalOsirisEngine is now the sole engine
        self._engine = engine or MechanicalOsirisEngine()
        # Phase coordinators (lazy-imported to avoid circular imports)
        self._encounter_phase = encounter_phase  # EncounterPhase | None
        self._perception_phase = perception_phase  # PerceptionPhase | None
        self._event_phase = event_phase  # EventConditionPhase | None

    def should_skip(
        self,
        change_log: list[StateChange],
        action_log: list[dict[str, Any]] | None = None,
    ) -> bool:
        if action_log is None:
            return len(change_log) == 0
        if not action_log:
            return len(change_log) == 0
        return all(self._is_trivial_zero_cost_action(action) for action in action_log)

    @classmethod
    def _is_trivial_zero_cost_action(cls, action: Any) -> bool:
        if not isinstance(action, Mapping):
            return False
        action_type = str(action.get("type", "")).strip().lower()
        if not action_type or action_type not in _TRIVIAL_ACTION_TYPES:
            return False
        return cls._coerce_float(action.get("time_cost")) == 0.0

    @staticmethod
    def _coerce_float(raw_value: Any) -> float | None:
        if isinstance(raw_value, (int, float)):
            return float(raw_value)
        if isinstance(raw_value, str):
            try:
                return float(raw_value)
            except ValueError:
                return None
        return None

    async def execute(self, context: SettlementContext) -> HookResult:
        rest_phase = resolve_rest_phase(context)
        quiet_rest_slot = is_quiet_rest_slot(context, rest_phase)
        evaluate_started = time.perf_counter()

        # QF-4: signal that Osiris evaluator is starting
        sse_events_pre: list[SSEEvent] = [
            SSEEvent(event_type="ai_processing", payload={"system": "osiris", "status": "start"})
        ]

        # Extract action_tags from SceneBus ENGINE entries
        action_tags = self._collect_action_tags(context)

        # Extract area tags from AreaSlice
        area_id = ""
        area_tags: list[str] = []
        if context.state.has_slice("player"):
            area_id = context.state.player.current_area or ""
        if area_id and context.state.has_slice("areas"):
            area_state = context.state.areas.areas.get(area_id)
            if area_state is not None and isinstance(getattr(area_state, "tags", None), list):
                area_tags = [str(t) for t in area_state.tags if isinstance(t, str)]

        # Determine whether any witnesses were present
        has_witnesses = self._has_witnesses(context)

        # Quiet rest slot: suppress all mechanical consequences
        if quiet_rest_slot:
            evaluation_ms = (time.perf_counter() - evaluate_started) * 1000.0
            return HookResult(
                sse_events=[
                    *sse_events_pre,
                    SSEEvent(event_type="ai_processing", payload={"system": "osiris", "status": "done"}),
                ],
                metadata={
                    "status": "quiet_rest_slot",
                    "evaluated": True,
                    "quiet_rest_slot": True,
                    "provider_status": "mechanical",
                    "provider_name": "MechanicalOsirisEngine",
                    "action_tags": sorted(action_tags),
                    "area_tags": area_tags,
                    "executed_count": 0,
                    "failed_count": 0,
                    "evaluation_ms": evaluation_ms,
                },
            )

        # Evaluate rules
        effects = self._engine.evaluate(action_tags, area_tags, has_witnesses, area_id)

        # Separate effects into commands and area_events
        commands: list[Command] = []
        area_event_dicts: list[dict[str, Any]] = []
        skipped_invalid_count = 0
        truncated_count = 0

        current_tick = (
            int(context.state.time.absolute_tick())
            if context.state.has_slice("time")
            else 0
        )

        for effect in effects:
            effect_type = str(effect.get("type", "")).strip()

            if effect_type == "area_event":
                if len(area_event_dicts) < self.MAX_CONSEQUENCES:
                    area_event_dicts.append({
                        "tick": current_tick,
                        "event": str(effect.get("event", "")),
                        "source": "ai_osiris",
                        "severity": str(effect.get("severity", "minor")),
                    })
                else:
                    truncated_count += 1
                continue

            if effect_type not in _ALLOWED_COMMAND_TYPES:
                skipped_invalid_count += 1
                continue

            command = self._effect_to_command(effect, area_id)
            if command is None:
                skipped_invalid_count += 1
                continue
            if len(commands) >= self.MAX_CONSEQUENCES:
                truncated_count += 1
                continue
            commands.append(command)

        # Execute commands
        command_results: list[dict[str, Any]] = []
        failed_count = 0
        for command in commands:
            result = context.execute_command(command)
            if not result.executed:
                failed_count += 1
            applied_change_count = len(result.delta.changes) if result.delta is not None else 0
            command_results.append(
                {
                    "command_type": command.type,
                    "executed": result.executed,
                    "errors": list(result.errors),
                    "applied_change_count": applied_change_count,
                }
            )

        # Write area_events via StateChange
        for event_dict in area_event_dicts:
            if area_id and context.state.has_slice("areas"):
                try:
                    context.state.areas.append_area_event(area_id, event_dict)
                except Exception:
                    logger.debug(
                        "AIOsirisHook: failed to append area_event for area %s", area_id
                    )

        executed_count = len(command_results)

        # Phase 2: Passive Perception
        perception_sse: list[SSEEvent] = []
        perception_meta: dict[str, Any] = {}
        if self._perception_phase is not None:
            try:
                p_result = await self._perception_phase.run(context)
                perception_sse = p_result.sse_events
                perception_meta = p_result.metadata
            except Exception:
                logger.exception("AIOsirisHook: perception_phase.run() failed")

        # Phase 3: Encounter Detection
        encounter_sse: list[SSEEvent] = []
        encounter_meta: dict[str, Any] = {}
        if self._encounter_phase is not None:
            try:
                e_result = await self._encounter_phase.run(context)
                encounter_sse = e_result.sse_events
                encounter_meta = e_result.metadata
            except Exception:
                logger.exception("AIOsirisHook: encounter_phase.run() failed")

        # Phase 4: Event Condition Checks
        event_sse: list[SSEEvent] = []
        event_meta: dict[str, Any] = {}
        if self._event_phase is not None:
            try:
                ev_result = await self._event_phase.run(context)
                event_sse = ev_result.sse_events
                event_meta = ev_result.metadata
            except Exception:
                logger.exception("AIOsirisHook: event_phase.run() failed")

        evaluation_ms = (time.perf_counter() - evaluate_started) * 1000.0

        status = self._resolve_status(
            requested_count=len(commands) + len(area_event_dicts),
            normalized_count=len(commands),
            executed_count=executed_count,
            failed_count=failed_count,
        )

        # QF-4: signal that Osiris evaluator finished
        sse_events: list[SSEEvent] = [
            *sse_events_pre,
            SSEEvent(event_type="ai_processing", payload={"system": "osiris", "status": "done"}),
        ]
        if executed_count > 0:
            sse_events.append(
                SSEEvent(
                    event_type="ai_osiris_applied",
                    payload={
                        "executed_count": executed_count,
                        "failed_count": failed_count,
                        "command_types": [command.type for command in commands],
                    },
                )
            )
        # Append phase SSE events (perception → encounter → event conditions)
        sse_events.extend(perception_sse)
        sse_events.extend(encounter_sse)
        sse_events.extend(event_sse)

        return HookResult(
            sse_events=sse_events,
            metadata={
                "status": status,
                "evaluated": True,
                "provider_status": "mechanical",
                "provider_name": "MechanicalOsirisEngine",
                "action_tags": sorted(action_tags),
                "area_tags": area_tags,
                "has_witnesses": has_witnesses,
                "executed_count": executed_count,
                "failed_count": failed_count,
                "skipped_invalid_count": skipped_invalid_count,
                "truncated_count": truncated_count,
                "area_events_written": len(area_event_dicts),
                "command_results": command_results,
                "evaluation_ms": evaluation_ms,
                "quiet_rest_slot": quiet_rest_slot,
                "phases": {
                    "perception": perception_meta,
                    "encounter": encounter_meta,
                    "event_conditions": event_meta,
                },
            },
        )

    @classmethod
    def _collect_action_tags(cls, context: SettlementContext) -> set[str]:
        """Collect ENGINE-tagged action tags from the SceneBus."""
        tags: set[str] = set()
        scene_snapshot = context.scene_bus.snapshot()
        raw_entries = scene_snapshot.get("entries", [])
        if not isinstance(raw_entries, list):
            return tags
        for entry in raw_entries:
            if not isinstance(entry, dict):
                continue
            source = str(entry.get("source", "")).upper()
            if source != "ENGINE":
                continue
            entry_tags = entry.get("tags", [])
            if isinstance(entry_tags, list):
                for tag in entry_tags:
                    if isinstance(tag, str) and tag:
                        tags.add(tag)
        # Also extract from action_log's type field using _ACTION_CATEGORY_TAGS
        for action in context.action_log:
            if not isinstance(action, dict):
                continue
            action_type = str(action.get("type", "")).strip()
            for cat_tag in _ACTION_CATEGORY_TAGS.get(action_type, []):
                tags.add(cat_tag)
        return tags

    @classmethod
    def _has_witnesses(cls, context: SettlementContext) -> bool:
        """Return True if any NPC or party member witnessed this tick's actions."""
        # Check party members
        if context.state.has_slice("party"):
            if context.state.party.members:
                return True
        # Check co-located NPCs
        if context.state.has_slice("player") and context.state.has_slice("areas"):
            area_id = context.state.player.current_area or ""
            if area_id:
                area_state = context.state.areas.areas.get(area_id)
                if area_state is not None and area_state.npc_locations:
                    return True
        return False

    @classmethod
    def _effect_to_command(
        cls,
        effect: dict[str, Any],
        area_id: str,
    ) -> Command | None:
        """Convert one mechanical effect descriptor to a Command."""
        effect_type = str(effect.get("type", "")).strip()

        if effect_type == "set_flag":
            flag_key = str(effect.get("flag", "")).strip()
            if not flag_key:
                return None
            return Command(
                type="set_flag",
                params={"key": flag_key, "value": True},
                source="ai_osiris",
            )

        if effect_type == "adjust_danger":
            raw_delta = effect.get("delta")
            if not isinstance(raw_delta, (int, float)):
                return None
            if not area_id:
                return None
            return Command(
                type="adjust_danger",
                params={"area_id": area_id, "delta": float(raw_delta)},
                source="ai_osiris",
            )

        return None

    @classmethod
    def _build_summary(cls, context: SettlementContext) -> dict[str, Any]:
        state_changes = [cls._serialize_change(change) for change in context.change_log]
        time_cost = cls._build_time_cost(context.action_log)
        rest_phase = resolve_rest_phase(context)
        rest_phase_snapshot = rest_phase.snapshot() if rest_phase is not None else None
        if rest_phase_snapshot is not None:
            rest_phase_snapshot["is_quiet_rest_slot"] = is_quiet_rest_slot(context, rest_phase)
        return {
            "time_slot": cls._build_time_slot(context),
            "location": cls._build_location(context),
            "tick_kind": cls._build_tick_kind(context.action_log),
            "time_cost": time_cost,
            "duration_minutes": round(time_cost * 60, 2),
            "actions": cls._enrich_actions(context),
            "state_changes": state_changes,
            "change_count": len(state_changes),
            "changed_slices": cls._changed_slices(context.change_log),
            "rest_phase": rest_phase_snapshot,
        }

    @classmethod
    def _build_snapshot(cls, context: SettlementContext) -> dict[str, Any]:
        location = cls._build_location(context)
        rest_phase = resolve_rest_phase(context)
        rest_phase_snapshot = rest_phase.snapshot() if rest_phase is not None else None
        if rest_phase_snapshot is not None:
            rest_phase_snapshot["is_quiet_rest_slot"] = is_quiet_rest_slot(context, rest_phase)
        # Phase 3: also pass current_room for room-level NPC locality
        current_room: str | None = None
        if context.state.has_slice("player"):
            raw_room = context.state.player.current_room
            current_room = (raw_room or "").strip() or None
        nearby_npcs = cls._build_nearby_npcs(
            context,
            location["area_id"],
            location.get("location_id"),
            current_room=current_room,
        )
        faction_standings: dict[str, Any] = {}
        if context.state.has_slice("relations"):
            relation_snapshot = context.state.relations.snapshot()
            raw_factions = relation_snapshot.get("faction_standings", {})
            if isinstance(raw_factions, Mapping):
                faction_standings = cls._normalize_mapping(raw_factions)

        active_flags: dict[str, Any] = {}
        if context.state.has_slice("flags"):
            flag_snapshot = context.state.flags.snapshot()
            raw_flags = flag_snapshot.get("flags", {})
            if isinstance(raw_flags, Mapping):
                active_flags = cls._normalize_mapping(raw_flags)

        current_chapter = ""
        chapter_completion: float | None = None
        if context.state.has_slice("narrative_plan"):
            plan_snapshot = context.state.narrative_plan.snapshot()
            current_chapter = cls._coerce_string(plan_snapshot.get("current_chapter"))
            raw_completion = plan_snapshot.get("chapter_completion")
            if isinstance(raw_completion, (int, float)):
                chapter_completion = float(raw_completion)

        pending_events: list[dict[str, Any]] = []
        if context.state.has_slice("events"):
            events_snapshot = context.state.events.snapshot()
            raw_pending = events_snapshot.get("pending_events")
            if isinstance(raw_pending, list):
                for item in raw_pending:
                    if isinstance(item, Mapping):
                        pending_events.append(cls._normalize_mapping(item))

        danger: dict[str, Any] = {}
        current_area = location["area_id"]
        if current_area and context.state.has_slice("areas"):
            danger["area_id"] = current_area
            danger["area_level"] = float(context.state.areas.get_danger(current_area))
            danger["location_id"] = location.get("location_id")
            danger["location_level"] = None
            area_state = context.state.areas.areas.get(current_area)
            if area_state is not None and isinstance(location.get("location_id"), str):
                for raw_entry in area_state.temporary_sub_areas:
                    if not isinstance(raw_entry, Mapping):
                        continue
                    if str(raw_entry.get("id", "")).strip() != str(location["location_id"]).strip():
                        continue
                    location_danger = raw_entry.get("threat_level")
                    if location_danger is not None:
                        danger["location_level"] = (
                            str(location_danger).strip()
                            if not isinstance(location_danger, (int, float))
                            else float(location_danger)
                        )
                    break

        active_dynamic_quests: list[dict[str, Any]] = []
        if context.state.has_slice("quests"):
            quest_snapshot = context.state.quests.snapshot()
            raw_dynamic_quests = quest_snapshot.get("dynamic_quests")
            if isinstance(raw_dynamic_quests, dict):
                for quest in raw_dynamic_quests.values():
                    if not isinstance(quest, Mapping):
                        continue
                    status = str(quest.get("status", "")).strip().lower()
                    if status in {"retired", "completed", "failed"}:
                        continue
                    active_dynamic_quests.append(dict(quest))

        return {
            "player": cls._build_player(context),
            "party": cls._build_party(context),
            "nearby_npcs": nearby_npcs,
            "scene_presence": cls._build_scene_presence(context, nearby_npcs),
            "faction_standings": faction_standings,
            "active_flags": active_flags,
            "current_chapter": current_chapter,
            "chapter_completion": chapter_completion,
            "time": cls._build_time_slot(context),
            "location": location,
            "pending_events": pending_events,
            "danger": danger,
            "active_dynamic_quests": active_dynamic_quests,
            "rest_phase": rest_phase_snapshot,
            "recent_visible_system_entries": cls._build_recent_visible_system_entries(context),
        }

    @classmethod
    def _build_player(cls, context: SettlementContext) -> dict[str, Any] | None:
        if not context.state.has_slice("player"):
            return None
        snap = context.state.player.snapshot()
        curated: dict[str, Any] = {
            "character_id": snap.get("character_id", ""),
            "character_name": snap.get("character_name", ""),
            "level": snap.get("level", 0),
            "hp": snap.get("hp", 0),
            "max_hp": snap.get("max_hp", 0),
            "gold": snap.get("gold", 0),
            "character_class": snap.get("character_class", ""),
            "current_area": snap.get("current_area", ""),
            "current_location": snap.get("current_location"),
            "guild_rank": snap.get("guild_rank", ""),
            "ac": snap.get("ac", 0),
        }
        curated["active_quests"] = cls._build_active_quests(context)
        curated["tags"] = cls._build_player_tags(context)
        return curated

    @classmethod
    def _build_active_quests(cls, context: SettlementContext) -> list[str]:
        if not context.state.has_slice("quests"):
            return []
        quests = context.state.quests
        active: list[str] = list(quests.get_available_milestones())
        for qid, quest in quests.dynamic_quests.items():
            if isinstance(quest, Mapping) and quest.get("status") not in {
                "retired", "completed", "failed",
            }:
                active.append(qid)
        return active

    @classmethod
    def _build_player_tags(cls, context: SettlementContext) -> list[str]:
        if not context.state.has_slice("player"):
            return []
        character_id = context.state.player.character_id
        if not character_id or not context.world.has_registry("characters"):
            return []
        template = context.world.characters.get(character_id)
        if template is None:
            return []
        if not isinstance(template.tags, list):
            return []
        return [str(t) for t in template.tags if isinstance(t, str)]

    @classmethod
    def _build_party(cls, context: SettlementContext) -> list[dict[str, Any]]:
        if not context.state.has_slice("party"):
            return []
        party_snap = context.state.party.snapshot()
        members = party_snap.get("members", {})
        approvals = party_snap.get("companion_approval", {})

        result: list[dict[str, Any]] = []
        for member_id in members:
            entry: dict[str, Any] = {"id": member_id}
            entry["approval"] = approvals.get(member_id, 0)

            if context.state.has_slice("relations"):
                dispositions = context.state.relations.npc_dispositions.get(member_id)
                if dispositions:
                    entry["disposition"] = dict(dispositions)
                stage = context.state.relations.relationship_stages.get(member_id)
                if stage:
                    entry["relationship_stage"] = stage

            if context.world.has_registry("characters"):
                template = context.world.characters.get(member_id)
                if template is not None:
                    entry["name"] = cls._coerce_string(template.name)
                    if isinstance(template.tags, list):
                        entry["tags"] = [str(t) for t in template.tags if isinstance(t, str)]
                    entry["faction"] = cls._coerce_string(
                        template.faction or template.faction_id
                    )

            result.append(entry)
        return result

    @classmethod
    def _build_scene_presence(
        cls,
        context: SettlementContext,
        nearby_npcs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        current_area = ""
        current_location = None
        current_room = None
        player_id = ""
        if context.state.has_slice("player"):
            current_area = context.state.player.current_area
            current_location = context.state.player.current_location
            current_room = getattr(context.state.player, "current_room", None)
            player_id = cls._coerce_string(context.state.player.character_id)
        present_character_ids: list[str] = []
        if player_id:
            present_character_ids.append(player_id)
        for npc in nearby_npcs:
            if not isinstance(npc, Mapping):
                continue
            npc_id = npc.get("id")
            if not isinstance(npc_id, str) or not npc_id or npc_id in present_character_ids:
                continue
            present_character_ids.append(npc_id)
        return {
            "area_id": current_area,
            "location_id": current_location,
            "room_id": current_room,
            "present_character_ids": present_character_ids,
        }

    @classmethod
    def _build_rules_context(cls, context: SettlementContext) -> dict[str, Any]:
        world_lore: list[dict[str, Any]] = []
        if context.world.has_registry("lore"):
            for entry in context.world.lore.list_all():
                world_lore.append({
                    "id": entry.id,
                    "content": entry.content,
                    "tags": list(entry.tags),
                })

        faction_rules: list[dict[str, Any]] = []
        if context.world.has_registry("factions"):
            for faction in context.world.factions.list_all():
                rule: dict[str, Any] = {
                    "id": faction.id,
                    "name": faction.name,
                    "alignment": faction.alignment,
                }
                if faction.behavioral_rules:
                    rule["behavioral_rules"] = faction.behavioral_rules
                faction_rules.append(rule)

        tag_dimensions: dict[str, list[str]] = {}
        if context.world.has_registry("tags"):
            for dimension in context.world.tags.list_all():
                if dimension.id:
                    tag_dimensions[dimension.id] = list(dimension.tags)

        world_rules: list[dict[str, Any]] = []
        if context.world.has_registry("lore"):
            current_area = (
                context.state.player.current_area
                if context.state.has_slice("player") else ""
            )
            for rule in context.world.lore.get_rules_for_context(area_id=current_area):
                world_rules.append({
                    "id": rule.id,
                    "title": rule.title,
                    "description": rule.description,
                    "priority": rule.priority,
                })

        return {
            "allowed_commands": list(_ALLOWED_COMMAND_TYPES),
            "command_source": "ai_osiris",
            "command_schema": cls._build_command_schema(),
            "trigger_condition_schema": cls._build_trigger_condition_schema(),
            "visibility_rules": cls._build_visibility_rules(),
            "semantic_constraints": cls._build_semantic_constraints(),
            "constraints": {
                "modify_location_player_mode_forbidden": True,
                "schedule_event_prefers_trigger_tick": True,
                "scene_bus_text_deferred": True,
            },
            "world_lore": world_lore,
            "faction_rules": faction_rules,
            "tag_dimensions": tag_dimensions,
            "world_rules": world_rules,
        }

    @staticmethod
    def _build_recent_visible_system_entries(
        context: SettlementContext,
    ) -> list[dict[str, Any]]:
        scene_snapshot = context.scene_bus.snapshot()
        raw_entries = scene_snapshot.get("entries", [])
        if not isinstance(raw_entries, list):
            return []
        digest: list[dict[str, Any]] = []
        for entry in raw_entries:
            if not isinstance(entry, Mapping):
                continue
            if str(entry.get("visibility", "")).strip().lower() != "system":
                continue
            source = str(entry.get("source", "")).strip()
            if source.upper() == "ENGINE":
                continue
            tags = entry.get("tags", [])
            if not isinstance(tags, list):
                tags = []
            digest.append(
                {
                    "source": source,
                    "content": str(entry.get("content", "")).strip(),
                    "tags": [str(tag) for tag in tags if isinstance(tag, str)],
                }
            )
        return digest[:5]

    @classmethod
    def _build_command_schema(cls) -> dict[str, Any]:
        return {
            "set_flag": {"required_params": ["key", "value"]},
            "modify_disposition": {
                "required_params": ["dimension", "delta"],
                "target_keys": ["npc_id", "target"],
            },
            "modify_approval": {
                "required_params": ["delta"],
                "target_keys": ["character_id", "character"],
            },
            "advance_quest": {"required_params": ["quest_id", "to_state"]},
            "schedule_event": {
                "required_params": ["event_id"],
                "one_of": ["trigger_condition", "trigger_tick"],
            },
            "create_rumor": {"one_of": ["text", "content"]},
            "modify_location": {"required_params": ["area_id"]},
            "add_knowledge": {
                "target_keys": ["npc_id", "character_id"],
                "one_of": ["impression", "knowledge"],
            },
            "modify_completion": {"required_params": ["chapter_id", "delta"]},
            "adjust_danger": {"required_params": ["area_id", "delta"]},
        }

    @staticmethod
    def _build_trigger_condition_schema() -> dict[str, Any]:
        return {
            "allowed_types": [
                "absolute_tick",
                "time_slots_elapsed",
                "period_reached",
                "location_entered",
                "location_visited",
                "flag_set",
            ],
            "location_keys": ["area_id", "location_id", "sub_location", "sub_location_id", "room_id"],
        }

    @staticmethod
    def _build_visibility_rules() -> dict[str, Any]:
        return {
            "visible_commands": [
                "create_rumor",
                "adjust_danger",
                "advance_quest",
                "schedule_event",
                "modify_completion",
            ],
            "hidden_commands": [
                "set_flag",
                "modify_disposition",
                "modify_approval",
                "modify_location",
                "add_knowledge",
            ],
        }

    @staticmethod
    def _build_semantic_constraints() -> dict[str, Any]:
        return {
            "modify_location_player_mode_forbidden": True,
            "approval_targets_must_be_in_party": True,
            "disposition_targets_must_exist": True,
            "danger_delta_range": [-0.5, 0.5],
            "approval_delta_range": [-50, 50],
            "disposition_delta_range": [-50, 50],
        }

    @classmethod
    def _normalize_decision(
        cls,
        raw_decision: AIOsirisDecision | Mapping[str, Any],
    ) -> AIOsirisDecision:
        if isinstance(raw_decision, AIOsirisDecision):
            return AIOsirisDecision(
                consequences=list(raw_decision.consequences),
                reasoning=str(raw_decision.reasoning),
                visible_change=bool(raw_decision.visible_change),
                metadata=cls._normalize_mapping(raw_decision.metadata),
            )
        if not isinstance(raw_decision, Mapping):
            return AIOsirisDecision(metadata={"status": "invalid_response"})

        raw_consequences = raw_decision.get("consequences", [])
        consequences = list(raw_consequences) if isinstance(raw_consequences, list) else []
        return AIOsirisDecision(
            consequences=consequences,
            reasoning=cls._coerce_string(raw_decision.get("reasoning")),
            visible_change=bool(raw_decision.get("visible_change", False)),
            metadata=cls._normalize_mapping(raw_decision.get("metadata")),
        )

    @classmethod
    def _normalize_consequence(cls, raw_consequence: Any) -> Command | None:
        if isinstance(raw_consequence, Command):
            return Command(
                type=raw_consequence.type,
                params=dict(raw_consequence.params),
                source="ai_osiris",
                context=(
                    cls._normalize_mapping(raw_consequence.context)
                    if isinstance(raw_consequence.context, Mapping)
                    else None
                ),
            )

        if not isinstance(raw_consequence, Mapping):
            return None

        command_type = cls._coerce_non_empty_string(raw_consequence.get("type"))
        if command_type is None:
            return None
        if command_type not in _ALLOWED_COMMAND_TYPES:
            return None

        params = cls._normalize_mapping(raw_consequence.get("params"))
        raw_context = raw_consequence.get("context")
        context = (
            cls._normalize_mapping(raw_context)
            if isinstance(raw_context, Mapping)
            else None
        )
        extras: dict[str, Any] = {}
        reason = raw_consequence.get("reason")
        if isinstance(reason, str) and reason.strip():
            extras["reason"] = reason.strip()
        visibility_hint = raw_consequence.get("visibility_hint")
        if isinstance(visibility_hint, str) and visibility_hint.strip():
            extras["visibility_hint"] = visibility_hint.strip().lower()
        confidence = raw_consequence.get("confidence")
        if isinstance(confidence, str) and confidence.strip():
            extras["confidence"] = confidence.strip().lower()
        if extras:
            context = dict(context or {})
            context["osiris_meta"] = extras
        return Command(
            type=command_type,
            params=params,
            source="ai_osiris",
            context=context,
        )

    @staticmethod
    def _resolve_status(
        *,
        requested_count: int,
        normalized_count: int,
        executed_count: int,
        failed_count: int,
    ) -> str:
        if requested_count == 0:
            return "noop"
        if normalized_count == 0:
            return "invalid_consequences"
        if failed_count == 0:
            return "applied"
        if executed_count > 0 and failed_count == executed_count:
            return "failed"
        return "partial_failure"

    @classmethod
    def _enrich_actions(
        cls, context: SettlementContext,
    ) -> list[dict[str, Any]]:
        all_witnesses = cls._collect_witness_ids(context)
        result: list[dict[str, Any]] = []
        for action in context.action_log:
            enriched = dict(action)
            params = action.get("params", {})

            target = cls._extract_target(params)
            if target:
                enriched["target"] = target

            tags = list(_ACTION_CATEGORY_TAGS.get(action.get("type", ""), []))
            tags.extend(cls._extract_content_tags(context, params))
            if tags:
                enriched["tags"] = tags

            enriched["detail"] = cls._build_action_detail(context, action)

            witnesses = [w for w in all_witnesses if w != target]
            if witnesses:
                enriched["witnessed_by"] = witnesses

            enriched["visibility_scope"] = cls._resolve_visibility_scope(
                action.get("type", ""),
                witnesses,
            )

            result.append(enriched)
        return result

    @staticmethod
    def _resolve_visibility_scope(action_type: str, witnesses: list[str]) -> str:
        normalized = str(action_type).strip().lower()
        if not witnesses:
            return "private"
        if normalized in {"look_inventory", "check_stats", "check_quest_log", "check_map"}:
            return "private"
        return "local"

    @staticmethod
    def _build_time_cost(action_log: list[dict[str, Any]]) -> float:
        total = 0.0
        for action in action_log:
            if not isinstance(action, Mapping):
                continue
            raw_cost = action.get("time_cost")
            cost = AIOsirisHook._coerce_float(raw_cost)
            if cost is None:
                continue
            total += cost
        return total

    @classmethod
    def _build_tick_kind(cls, action_log: list[dict[str, Any]]) -> str:
        action_types = {
            str(raw_action.get("type", "")).strip().lower()
            for raw_action in action_log
            if isinstance(raw_action, Mapping)
        } - {""}
        if not action_types:
            return "normal"
        if action_types & _TICK_KIND_TRAVEL:
            return "travel"
        if action_types & _TICK_KIND_REST:
            return "rest"
        if action_types & _TICK_KIND_CONVERSATION:
            return "conversation"
        if action_types & _TICK_KIND_COMBAT:
            return "combat_resolution"
        return "normal"

    @classmethod
    def _build_action_detail(
        cls,
        context: SettlementContext,
        action: dict[str, Any],
    ) -> str:
        action_type = action.get("type", "")
        params = action.get("params", {})
        executed = action.get("executed", True)
        hints = action.get("narrative_hints", [])

        verb = _ACTION_VERBS.get(action_type, action_type.replace("_", " "))
        parts: list[str] = []
        if verb:
            parts.append(verb)

        # Primary entity: item or spell/skill name
        item_id = params.get("item_id")
        spell_id = params.get("spell_id") or params.get("skill_id")
        skill = params.get("skill")
        if item_id and isinstance(item_id, str):
            parts.append(cls._resolve_entity_name(context, "items", item_id))
        elif spell_id and isinstance(spell_id, str):
            parts.append(cls._resolve_entity_name(context, "skills", spell_id))
        elif skill and isinstance(skill, str):
            parts.append(f"{skill} check" if action_type == "skill_check" else str(skill))

        # Target with preposition
        target = cls._extract_target(params)
        if target:
            prep = _TARGET_PREPOSITIONS.get(action_type, "")
            target_name = cls._resolve_entity_name(context, "characters", target)
            if prep:
                parts.append(f"{prep} {target_name}")
            else:
                parts.append(target_name)
        else:
            area_id = params.get("area_id") or params.get("to")
            location = params.get("location_id") or params.get("location")
            if area_id and isinstance(area_id, str):
                parts.append(area_id)
            elif location and isinstance(location, str):
                parts.append(location)

        # DC modifier
        dc = params.get("dc")
        if dc is not None:
            parts.append(f"(DC {dc})")

        detail = " ".join(parts)

        if hints:
            detail += "; " + "; ".join(str(h) for h in hints if isinstance(h, str))

        if not executed:
            detail += " — failed"

        return detail

    @classmethod
    def _resolve_entity_name(
        cls, context: SettlementContext, registry_name: str, entity_id: str,
    ) -> str:
        if context.world.has_registry(registry_name):
            entry = getattr(context.world, registry_name).get(entity_id)
            if entry is not None:
                if isinstance(entry, Mapping):
                    name = entry.get("name")
                else:
                    name = getattr(entry, "name", None)
                if isinstance(name, str) and name:
                    return name
        return entity_id

    @classmethod
    def _collect_witness_ids(cls, context: SettlementContext) -> list[str]:
        witnesses: list[str] = []
        seen: set[str] = set()

        if context.state.has_slice("party"):
            for member_id in context.state.party.members:
                if member_id not in seen:
                    seen.add(member_id)
                    witnesses.append(member_id)

        if context.state.has_slice("player"):
            current_area = context.state.player.current_area
            if current_area:
                if context.state.has_slice("areas"):
                    area_state = context.state.areas.areas.get(current_area)
                    if area_state is not None:
                        for npc_id in area_state.npc_locations:
                            if npc_id not in seen:
                                seen.add(npc_id)
                                witnesses.append(npc_id)
                if context.world.has_registry("characters"):
                    for raw_char in context.world.characters.list_all():
                        char_id = cls._coerce_non_empty_string(raw_char.id)
                        if char_id is None or char_id in seen:
                            continue
                        area_id = cls._coerce_non_empty_string(
                            raw_char.area_id or raw_char.current_area
                        )
                        if area_id == current_area:
                            seen.add(char_id)
                            witnesses.append(char_id)

        return witnesses

    @staticmethod
    def _extract_target(params: dict[str, Any]) -> str | None:
        for key in _TARGET_PARAM_KEYS:
            value = params.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @classmethod
    def _extract_content_tags(
        cls, context: SettlementContext, params: dict[str, Any],
    ) -> list[str]:
        tags: list[str] = []

        item_id = params.get("item_id")
        if item_id and isinstance(item_id, str) and context.world.has_registry("items"):
            item = context.world.items.get(item_id)
            if item is not None:
                tags.extend(str(t).upper() for t in item.tags if isinstance(t, str))
                if item.type:
                    tags.append(item.type.upper())

        spell_id = params.get("spell_id") or params.get("skill_id")
        if spell_id and isinstance(spell_id, str) and context.world.has_registry("skills"):
            skill = context.world.skills.get(spell_id)
            if skill is not None:
                if skill.school:
                    tags.append(skill.school.upper())
                effect_type = getattr(skill.effect, "type", None) or None
                if isinstance(effect_type, str) and effect_type:
                    tags.append(effect_type.upper())

        return tags

    @staticmethod
    def _build_time_slot(context: SettlementContext) -> dict[str, Any] | None:
        if not context.state.has_slice("time"):
            return None
        current = context.state.time.get_current_time()
        return {
            "day": current["day"],
            "slot": current["slot"],
            "period": current["period"],
            "absolute_tick": context.state.time.absolute_tick(),
        }

    @staticmethod
    def _build_location(context: SettlementContext) -> dict[str, Any]:
        if not context.state.has_slice("player"):
            return {"area_id": "", "location_id": None, "room_id": None}
        return {
            "area_id": context.state.player.current_area,
            "location_id": context.state.player.current_location,
            "room_id": getattr(context.state.player, "current_room", None),
        }

    @classmethod
    def _build_nearby_npcs(
        cls,
        context: SettlementContext,
        current_area: str,
        current_location: str | None = None,
        current_room: str | None = None,
    ) -> list[dict[str, Any]]:
        if not current_area:
            return []

        seen_ids: set[str] = set()
        nearby: list[dict[str, Any]] = []
        normalized_location = (
            current_location.strip()
            if isinstance(current_location, str)
            else None
        )
        normalized_room = (
            current_room.strip()
            if isinstance(current_room, str)
            else None
        )

        def is_scene_local(raw_location: str | None) -> bool:
            if normalized_location is None:
                return not raw_location
            return raw_location == normalized_location

        # Build npc_rooms lookup once (only needed when player is in a room)
        npc_rooms_lookup: dict[str, str | None] = {}
        if normalized_room is not None and context.state.has_slice("areas"):
            area_state_for_rooms = context.state.areas.areas.get(current_area)
            if area_state_for_rooms is not None:
                npc_rooms_lookup = dict(area_state_for_rooms.npc_rooms)

        def is_room_local(npc_id: str) -> bool:
            """Return True if NPC is in the same room as the player (or player not in a room)."""
            if normalized_room is None:
                return True
            npc_room = (npc_rooms_lookup.get(npc_id) or "").strip() or None
            return npc_room == normalized_room

        area_local: list[tuple[str, str | None]] = []
        area_other: list[tuple[str, str | None]] = []
        static_local: list[str] = []
        static_other: list[str] = []

        # 1. 动态源：AreaSlice.npc_locations
        if context.state.has_slice("areas"):
            area_state = context.state.areas.areas.get(current_area)
            if area_state is not None:
                for npc_id, location_id in area_state.npc_locations.items():
                    normalized_npc_location = (
                        str(location_id).strip() if isinstance(location_id, str) else None
                    )
                    if is_scene_local(normalized_npc_location):
                        area_local.append((str(npc_id), normalized_npc_location))
                    else:
                        area_other.append((str(npc_id), normalized_npc_location))
                    seen_ids.add(str(npc_id))

        # 2. 动态源：有子地点时只返回同 sub_location 的 NPC；否则返回所有同区 NPC
        #    Phase 3: 进一步按 room 过滤（仅当玩家在 room 中时）
        if normalized_location is not None:
            # Sub-location known: only include scene-local NPCs (further filtered by room)
            for npc_id, location_id in area_local:
                if not is_room_local(npc_id):
                    continue
                nearby.append(
                    cls._build_npc_entry(context, npc_id, location_id=location_id)
                )
        else:
            # No sub-location: include all area NPCs (original behavior)
            for npc_id, location_id in area_local + area_other:
                nearby.append(
                    cls._build_npc_entry(context, npc_id, location_id=location_id)
                )

        # 2. 静态补源：CharacterRegistry 模板 area_id
        if context.world.has_registry("characters"):
            for raw_char in context.world.characters.list_all():
                char_id = cls._coerce_non_empty_string(raw_char.id)
                if char_id is None or char_id in seen_ids:
                    continue
                area_id = cls._coerce_non_empty_string(
                    raw_char.area_id or raw_char.current_area
                )
                if area_id != current_area:
                    continue

                char_location = cls._coerce_non_empty_string(
                    raw_char.location_id or raw_char.current_location
                )
                if is_scene_local(char_location):
                    static_local.append(char_id)
                else:
                    static_other.append(char_id)

        # 3. 静态源：有子地点时只返回同 sub_location 的 NPC；否则补齐其它同区 NPC
        #    Phase 3: 静态补源无 room 信息 → 有 room 过滤时不加入静态补源
        if normalized_location is not None:
            for char_id in static_local:
                if not is_room_local(char_id):
                    continue
                nearby.append(cls._build_npc_entry(context, char_id))
        else:
            for char_id in static_local + static_other:
                nearby.append(cls._build_npc_entry(context, char_id))

        return nearby

    @classmethod
    def _build_npc_entry(
        cls,
        context: SettlementContext,
        npc_id: str,
        *,
        location_id: str | None = None,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {"id": npc_id}

        # 模板数据（name, tags, faction）
        if context.world.has_registry("characters"):
            template = context.world.characters.get(npc_id)
            if template is not None:
                entry["name"] = cls._coerce_string(template.name)
                if isinstance(template.tags, list):
                    entry["tags"] = [str(t) for t in template.tags if isinstance(t, str)]
                entry["faction"] = cls._coerce_string(
                    template.faction or template.faction_id
                )

        # 好感度
        if context.state.has_slice("relations"):
            dispositions = context.state.relations.npc_dispositions.get(npc_id)
            if dispositions:
                entry["disposition"] = dict(dispositions)

        if location_id is not None:
            entry["location_id"] = location_id

        return entry

    @staticmethod
    def _serialize_change(change: StateChange) -> dict[str, Any]:
        return {
            "slice": change.slice,
            "operation": change.operation,
            "path": change.path,
            "value": change.value,
        }

    @staticmethod
    def _changed_slices(change_log: list[StateChange]) -> list[str]:
        changed: list[str] = []
        seen: set[str] = set()
        for change in change_log:
            if change.slice in seen:
                continue
            seen.add(change.slice)
            changed.append(change.slice)
        return changed

    @staticmethod
    def _snapshot_digest(snapshot: dict[str, Any]) -> dict[str, Any]:
        location = snapshot.get("location")
        normalized_location = location if isinstance(location, Mapping) else {}
        nearby_npcs = snapshot.get("nearby_npcs")
        nearby_count = len(nearby_npcs) if isinstance(nearby_npcs, list) else 0
        return {
            "current_area": str(normalized_location.get("area_id", "")),
            "current_location": normalized_location.get("location_id"),
            "current_room": normalized_location.get("room_id"),
            "nearby_npc_count": nearby_count,
            "has_player": snapshot.get("player") is not None,
            "has_party": snapshot.get("party") is not None,
        }

    @classmethod
    def _passes_minimal_semantic_validation(cls, command: Command) -> bool:
        params = command.params
        if command.type == "set_flag":
            return "key" in params and "value" in params
        if command.type == "modify_disposition":
            return bool(params.get("dimension")) and "delta" in params and (
                params.get("npc_id") or params.get("target")
            )
        if command.type == "modify_approval":
            return "delta" in params and (
                params.get("character_id") or params.get("character")
            )
        if command.type == "advance_quest":
            return bool(params.get("quest_id")) and bool(params.get("to_state"))
        if command.type == "schedule_event":
            return bool(params.get("event_id")) and (
                isinstance(params.get("trigger_condition"), Mapping)
                or params.get("trigger_tick") is not None
            )
        if command.type == "create_rumor":
            return bool(params.get("text") or params.get("content"))
        if command.type == "modify_location":
            return bool(params.get("area_id"))
        if command.type == "add_knowledge":
            return bool(params.get("npc_id") or params.get("character_id")) and bool(
                params.get("impression") or params.get("knowledge")
            )
        if command.type == "modify_completion":
            return bool(params.get("chapter_id")) and "delta" in params
        if command.type == "adjust_danger":
            return bool(params.get("area_id")) and "delta" in params
        return True

    @staticmethod
    def _normalize_mapping(raw_value: Any) -> dict[str, Any]:
        if not isinstance(raw_value, Mapping):
            return {}
        return {str(key): value for key, value in raw_value.items()}

    @staticmethod
    def _coerce_string(raw_value: Any) -> str:
        if raw_value is None:
            return ""
        return str(raw_value)

    @classmethod
    def _coerce_non_empty_string(cls, raw_value: Any) -> str | None:
        normalized = cls._coerce_string(raw_value).strip()
        return normalized or None
