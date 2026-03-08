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
    "modify_disposition",
    "modify_approval",
    "advance_quest",
    "schedule_event",
    "create_rumor",
    "modify_location",
    "add_knowledge",
    "modify_completion",
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

    def __init__(self, evaluator: AIOsirisProvider | None = None) -> None:
        self._evaluator = evaluator or BasicAIOsirisEvaluator()

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
        summary = self._build_summary(context)
        snapshot = self._build_snapshot(context)
        rules_context = self._build_rules_context(context)
        rest_phase = resolve_rest_phase(context)
        quiet_rest_slot = is_quiet_rest_slot(context, rest_phase)
        evaluate_started = time.perf_counter()
        raw_consequence_count = 0

        try:
            raw_decision = await self._evaluator.evaluate(summary, snapshot, rules_context)
            if isinstance(raw_decision, AIOsirisDecision):
                raw_consequence_count = len(raw_decision.consequences)
            elif isinstance(raw_decision, Mapping):
                raw_consequence_count = len(
                    raw_decision.get("consequences", [])
                    if isinstance(raw_decision.get("consequences"), list)
                    else []
                )
        except Exception as exc:
            logger.exception(
                "hook failed: ai_osiris",
                extra={
                    "hook_name": self.HOOK_NAME,
                    "change_count": len(context.change_log),
                },
            )
            return HookResult(
                sse_events=[
                    SSEEvent(
                        event_type="ai_osiris_error",
                        payload={"error": str(exc)},
                    )
                ],
                metadata={
                    "status": "evaluator_error",
                    "evaluated": False,
                    "provider_status": "failed",
                    "provider_name": "unknown",
                    "decision_reasoning": "",
                    "requested_count": 0,
                    "raw_consequence_count": raw_consequence_count,
                    "normalized_count": 0,
                    "normalized_consequence_count": 0,
                    "executed_count": 0,
                    "failed_count": 0,
                    "truncated_count": 0,
                    "skipped_invalid_count": 0,
                    "invalid_count": 0,
                    "allowed_command_enforced": True,
                    "visible_change_count": 0,
                    "visible_command_types": [],
                    "visible_tags_count": 0,
                    "evaluation_ms": (time.perf_counter() - evaluate_started) * 1000.0,
                    "command_results": [],
                    "summary": summary,
                    "snapshot_digest": self._snapshot_digest(snapshot),
                    "evaluator_metadata": {},
                },
            )

        decision = self._normalize_decision(raw_decision)
        requested_count = len(decision.consequences)
        if not raw_consequence_count:
            raw_consequence_count = requested_count
        if quiet_rest_slot:
            decision = AIOsirisDecision(
                consequences=[],
                reasoning=decision.reasoning,
                visible_change=False,
                metadata={
                    **dict(decision.metadata),
                    "status": "quiet_rest_slot",
                    "quiet_rest_slot": True,
                    "suppressed_consequence_count": requested_count,
                },
            )
            requested_count = 0
            raw_consequence_count = 0
        commands: list[Command] = []
        skipped_invalid_count = 0
        truncated_count = 0
        for consequence in decision.consequences:
            command = self._normalize_consequence(consequence)
            if command is None:
                skipped_invalid_count += 1
                continue
            if not self._passes_minimal_semantic_validation(command):
                skipped_invalid_count += 1
                continue
            if len(commands) >= self.MAX_CONSEQUENCES:
                truncated_count += 1
                continue
            commands.append(command)

        command_results: list[dict[str, Any]] = []
        failed_count = 0
        successful_commands: list[Command] = []
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
            if result.executed:
                successful_commands.append(command)

        visible_entries: list[dict[str, Any]] = []
        if decision.visible_change:
            visible_entries = OsirisVisibleConsequenceRenderer.render_visible_entries(
                context,
                successful_commands,
            )
            for entry in visible_entries:
                context.scene_bus.add_entry(entry)

        executed_count = len(command_results)
        normalized_count = len(commands)
        evaluation_ms = (time.perf_counter() - evaluate_started) * 1000.0
        visible_command_types = [
            str(entry["tags"][2])
            for entry in visible_entries
            if isinstance(entry.get("tags"), list) and len(entry["tags"]) >= 3
        ]
        status = self._resolve_status(
            requested_count=requested_count,
            normalized_count=normalized_count,
            executed_count=executed_count,
            failed_count=failed_count,
        )

        sse_events: list[SSEEvent] = []
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

        return HookResult(
            sse_events=sse_events,
            metadata={
                "status": status,
                "evaluated": True,
                "decision_reasoning": decision.reasoning,
                "visible_change": decision.visible_change,
                "provider_status": decision.metadata.get("status", ""),
                "provider_name": decision.metadata.get("provider", ""),
                "provider_profile": decision.metadata.get("profile", ""),
                "provider_model": decision.metadata.get("model", ""),
                "provider_thinking_level": decision.metadata.get("thinking_level", ""),
                "provider_latency_ms": decision.metadata.get("latency_ms"),
                "provider_token_usage": decision.metadata.get("token_usage", {}),
                "requested_count": requested_count,
                "raw_consequence_count": raw_consequence_count,
                "normalized_consequence_count": normalized_count,
                "normalized_count": normalized_count,
                "executed_count": executed_count,
                "failed_count": failed_count,
                "invalid_count": skipped_invalid_count,
                "truncated_count": truncated_count,
                "skipped_invalid_count": skipped_invalid_count,
                "allowed_command_enforced": True,
                "command_results": command_results,
                "visible_change_count": len(visible_entries),
                "visible_command_types": visible_command_types,
                "visible_tags_count": len(visible_entries),
                "evaluation_ms": evaluation_ms,
                "summary": summary,
                "snapshot_digest": self._snapshot_digest(snapshot),
                "evaluator_metadata": dict(decision.metadata),
                "quiet_rest_slot": quiet_rest_slot,
            },
        )

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
        nearby_npcs = cls._build_nearby_npcs(
            context,
            location["area_id"],
            location.get("location_id"),
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
        player_id = ""
        if context.state.has_slice("player"):
            current_area = context.state.player.current_area
            current_location = context.state.player.current_location
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
                "flag_set",
            ]
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
            return {"area_id": "", "location_id": None}
        return {
            "area_id": context.state.player.current_area,
            "location_id": context.state.player.current_location,
        }

    @classmethod
    def _build_nearby_npcs(
        cls,
        context: SettlementContext,
        current_area: str,
        current_location: str | None = None,
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

        def is_scene_local(raw_location: str | None) -> bool:
            if normalized_location is None:
                return not raw_location
            return raw_location == normalized_location

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

        # 2. 动态源：Scene-local 优先，其次按其他动态来源补齐
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

        # 3. 静态源：Scene-local 优先，其次补齐其它同区 NPC
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
