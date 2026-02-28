"""AIOsirisHook MVP implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules.models import Command
from app.game_core.state import StateChange


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

_MEANINGFUL_SLICES = frozenset(
    {"player", "flags", "relations", "party", "quests", "areas", "events"}
)


@dataclass(slots=True)
class AIOsirisDecision:
    consequences: list[Command | Mapping[str, Any]] = field(default_factory=list)
    reasoning: str = ""
    visible_change: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class AIOsirisEvaluator(Protocol):
    def evaluate(
        self,
        summary: dict[str, Any],
        snapshot: dict[str, Any],
        rules_context: dict[str, Any],
    ) -> AIOsirisDecision | Mapping[str, Any]:
        ...


class NullAIOsirisEvaluator:
    def evaluate(
        self,
        summary: dict[str, Any],
        snapshot: dict[str, Any],
        rules_context: dict[str, Any],
    ) -> AIOsirisDecision:
        del summary, snapshot, rules_context
        return AIOsirisDecision(metadata={"status": "noop"})


class BasicAIOsirisEvaluator:
    """Deterministic default evaluator for the runtime skeleton."""

    def evaluate(
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

    def __init__(self, evaluator: AIOsirisEvaluator | None = None) -> None:
        self._evaluator = evaluator or BasicAIOsirisEvaluator()

    def should_skip(self, change_log: list[StateChange]) -> bool:
        return not self._has_meaningful_changes(change_log)

    async def execute(self, context: SettlementContext) -> HookResult:
        summary = self._build_summary(context)
        snapshot = self._build_snapshot(context)
        rules_context = self._build_rules_context()

        try:
            raw_decision = self._evaluator.evaluate(summary, snapshot, rules_context)
        except Exception as exc:
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
                    "decision_reasoning": "",
                    "requested_count": 0,
                    "normalized_count": 0,
                    "executed_count": 0,
                    "failed_count": 0,
                    "truncated_count": 0,
                    "skipped_invalid_count": 0,
                    "allowed_command_enforced": True,
                    "command_results": [],
                    "summary": summary,
                    "snapshot_digest": self._snapshot_digest(snapshot),
                    "evaluator_metadata": {},
                },
            )

        decision = self._normalize_decision(raw_decision)
        requested_count = len(decision.consequences)
        commands: list[Command] = []
        skipped_invalid_count = 0
        truncated_count = 0
        for consequence in decision.consequences:
            command = self._normalize_consequence(consequence)
            if command is None:
                skipped_invalid_count += 1
                continue
            if len(commands) >= self.MAX_CONSEQUENCES:
                truncated_count += 1
                continue
            commands.append(command)

        command_results: list[dict[str, Any]] = []
        failed_count = 0
        for command in commands:
            result = context.execute_command(command)
            if not result.success:
                failed_count += 1
            applied_change_count = len(result.delta.changes) if result.delta is not None else 0
            command_results.append(
                {
                    "command_type": command.type,
                    "success": result.success,
                    "errors": list(result.errors),
                    "applied_change_count": applied_change_count,
                }
            )

        executed_count = len(command_results)
        normalized_count = len(commands)
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
                "requested_count": requested_count,
                "normalized_count": normalized_count,
                "executed_count": executed_count,
                "failed_count": failed_count,
                "truncated_count": truncated_count,
                "skipped_invalid_count": skipped_invalid_count,
                "allowed_command_enforced": True,
                "command_results": command_results,
                "summary": summary,
                "snapshot_digest": self._snapshot_digest(snapshot),
                "evaluator_metadata": dict(decision.metadata),
            },
        )

    @staticmethod
    def _has_meaningful_changes(change_log: list[StateChange]) -> bool:
        return any(change.slice in _MEANINGFUL_SLICES for change in change_log)

    @classmethod
    def _build_summary(cls, context: SettlementContext) -> dict[str, Any]:
        state_changes = [cls._serialize_change(change) for change in context.change_log]
        return {
            "time_slot": cls._build_time_slot(context),
            "location": cls._build_location(context),
            "duration_minutes": 0,
            "actions": [],
            "state_changes": state_changes,
            "change_count": len(state_changes),
            "changed_slices": cls._changed_slices(context.change_log),
        }

    @classmethod
    def _build_snapshot(cls, context: SettlementContext) -> dict[str, Any]:
        location = cls._build_location(context)
        nearby_npcs = cls._build_nearby_npcs(context, location["area_id"])
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
        if context.state.has_slice("narrative_plan"):
            plan_snapshot = context.state.narrative_plan.snapshot()
            current_chapter = cls._coerce_string(plan_snapshot.get("current_chapter"))

        return {
            "player": (
                context.state.player.snapshot()
                if context.state.has_slice("player")
                else None
            ),
            "party": (
                context.state.party.snapshot()
                if context.state.has_slice("party")
                else None
            ),
            "nearby_npcs": nearby_npcs,
            "faction_standings": faction_standings,
            "active_flags": active_flags,
            "current_chapter": current_chapter,
            "time": cls._build_time_slot(context),
            "location": location,
        }

    @staticmethod
    def _build_rules_context() -> dict[str, Any]:
        return {
            "allowed_commands": list(_ALLOWED_COMMAND_TYPES),
            "command_source": "ai_osiris",
            "constraints": {
                "modify_location_player_mode_forbidden": True,
                "schedule_event_prefers_trigger_tick": True,
                "scene_bus_text_deferred": True,
            },
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
    ) -> list[dict[str, Any]]:
        if not current_area or not context.world.has_registry("characters"):
            return []

        nearby: list[dict[str, Any]] = []
        for raw_character in context.world.characters.list_all():
            if not isinstance(raw_character, Mapping):
                continue
            area_id = cls._coerce_non_empty_string(raw_character.get("area_id"))
            if area_id is None:
                area_id = cls._coerce_non_empty_string(raw_character.get("current_area"))
            if area_id != current_area:
                continue
            nearby.append(cls._normalize_mapping(raw_character))
        return nearby

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
