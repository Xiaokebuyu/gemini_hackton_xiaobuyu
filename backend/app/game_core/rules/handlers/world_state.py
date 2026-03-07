"""WorldStateHandler implementation."""

from __future__ import annotations

from typing import Any, Mapping
from uuid import uuid4

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import coerce_float, coerce_int, get_non_empty_string
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer, StateDelta


class WorldStateHandler(StaticCommandHandler):
    COMMAND_TYPES = (
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

    _DISPOSITION_DIMENSIONS = frozenset({"approval", "trust", "fear", "romance"})

    _MILESTONE_TRANSITIONS = {
        "LOCKED": {"AVAILABLE"},
        "AVAILABLE": {"ACTIVE", "FAILED"},
        "ACTIVE": {"COMPLETED", "FAILED"},
        "COMPLETED": set(),
        "FAILED": set(),
    }

    _DYNAMIC_TRANSITIONS = {
        "available": {"active", "failed", "retired"},
        "active": {"completed", "failed", "retired"},
        "completed": set(),
        "failed": set(),
        "retired": set(),
    }

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        source_gate = self._enforce_source_policy(cmd)
        if not source_gate.ok:
            return source_gate

        if cmd.type == "set_flag":
            return self._validate_set_flag(cmd, state)
        if cmd.type == "modify_disposition":
            return self._validate_modify_disposition(cmd, state, world)
        if cmd.type == "modify_approval":
            return self._validate_modify_approval(cmd, state)
        if cmd.type == "add_knowledge":
            return self._validate_add_knowledge(cmd, state, world)
        if cmd.type == "advance_quest":
            return self._validate_advance_quest(cmd, state)
        if cmd.type == "schedule_event":
            return self._validate_schedule_event(cmd, state)
        if cmd.type == "create_rumor":
            return self._validate_create_rumor(cmd, state, world)
        if cmd.type == "modify_location":
            return self._validate_modify_location(cmd, state, world)
        if cmd.type == "modify_completion":
            return self._validate_modify_completion(cmd, state, world)
        if cmd.type == "adjust_danger":
            return self._validate_adjust_danger(cmd, state, world)
        return ValidationResult(ok=False, reason=f"unsupported command: {cmd.type}")

    def compute(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        validation = self.validate(cmd, state, world)
        if not validation.ok:
            return ExecuteResult.error(validation.reason or "validation failed")

        if cmd.type == "set_flag":
            return self._compute_set_flag(cmd)
        if cmd.type == "modify_disposition":
            return self._compute_modify_disposition(cmd)
        if cmd.type == "modify_approval":
            return self._compute_modify_approval(cmd)
        if cmd.type == "add_knowledge":
            return self._compute_add_knowledge(cmd)
        if cmd.type == "advance_quest":
            return self._compute_advance_quest(cmd, state)
        if cmd.type == "schedule_event":
            return self._compute_schedule_event(cmd, state)
        if cmd.type == "create_rumor":
            return self._compute_create_rumor(cmd, state)
        if cmd.type == "modify_location":
            return self._compute_modify_location(cmd)
        if cmd.type == "modify_completion":
            return self._compute_modify_completion(cmd)
        if cmd.type == "adjust_danger":
            return self._compute_adjust_danger(cmd)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _validate_set_flag(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        if not state.has_slice("flags"):
            return ValidationResult(ok=False, reason="flags slice is required")
        key = get_non_empty_string(cmd.params, "key")
        if key is None:
            return ValidationResult(ok=False, reason="key must be a non-empty string")
        if "value" not in cmd.params:
            return ValidationResult(ok=False, reason="value is required")
        return ValidationResult(ok=True)

    def _validate_modify_disposition(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("relations"):
            return ValidationResult(ok=False, reason="relations slice is required")

        npc_id = self._normalize_modify_disposition_target(cmd.params)
        if npc_id is None:
            return ValidationResult(ok=False, reason="npc_id/target must be a non-empty string")
        if not self._world_has_character(world, state, npc_id):
            return ValidationResult(ok=False, reason=f"unknown character: {npc_id}")

        dimension = get_non_empty_string(cmd.params, "dimension")
        if dimension is None:
            return ValidationResult(
                ok=False,
                reason="dimension must be a non-empty string",
            )
        if dimension not in self._DISPOSITION_DIMENSIONS:
            return ValidationResult(
                ok=False,
                reason=f"unsupported disposition dimension: {dimension}",
            )

        delta = coerce_int(cmd.params.get("delta"))
        if delta is None:
            return ValidationResult(ok=False, reason="delta must be an integer")
        if not -50 <= delta <= 50:
            return ValidationResult(ok=False, reason="delta must be between -50 and 50")

        return ValidationResult(ok=True)

    def _validate_modify_approval(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        if not state.has_slice("party"):
            return ValidationResult(ok=False, reason="party slice is required")

        character_id = self._normalize_modify_approval_target(cmd.params)
        if character_id is None:
            return ValidationResult(
                ok=False,
                reason="character_id/character must be a non-empty string",
            )

        delta = coerce_int(cmd.params.get("delta"))
        if delta is None:
            return ValidationResult(ok=False, reason="delta must be an integer")
        if not -50 <= delta <= 50:
            return ValidationResult(ok=False, reason="delta must be between -50 and 50")

        if (
            character_id not in state.party.members
            and character_id not in state.party.companion_approval
        ):
            return ValidationResult(
                ok=False,
                reason=f"character not in party: {character_id}",
            )

        return ValidationResult(ok=True)

    def _validate_add_knowledge(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("relations"):
            return ValidationResult(ok=False, reason="relations slice is required")

        npc_id = self._normalize_add_knowledge_target(cmd.params)
        if npc_id is None:
            return ValidationResult(
                ok=False,
                reason="npc_id/character_id must be a non-empty string",
            )
        if not self._world_has_character(world, state, npc_id):
            return ValidationResult(ok=False, reason=f"unknown character: {npc_id}")

        impression = self._normalize_add_knowledge_text(cmd.params)
        if impression is None:
            return ValidationResult(
                ok=False,
                reason="impression/knowledge must be a non-empty string",
            )

        return ValidationResult(ok=True)

    def _validate_advance_quest(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        if not state.has_slice("quests"):
            return ValidationResult(ok=False, reason="quests slice is required")

        quest_id = get_non_empty_string(cmd.params, "quest_id")
        if quest_id is None:
            return ValidationResult(ok=False, reason="quest_id must be a non-empty string")

        to_state_raw = get_non_empty_string(cmd.params, "to_state")
        if to_state_raw is None:
            return ValidationResult(ok=False, reason="to_state must be a non-empty string")

        quest_kind = self._resolve_quest_kind(cmd.params, state, quest_id)
        if quest_kind is None:
            return ValidationResult(
                ok=False,
                reason="quest_kind must be one of auto/dynamic/milestone",
            )

        if quest_kind == "milestone":
            milestone = state.quests.get_milestone(quest_id)
            if milestone is None:
                return ValidationResult(ok=False, reason=f"milestone not found: {quest_id}")
            to_state = to_state_raw.upper()
            if not self._quest_transition_allowed(milestone.state.upper(), to_state):
                return ValidationResult(
                    ok=False,
                    reason=f"invalid milestone transition: {milestone.state} -> {to_state}",
                )
            if "tick" in cmd.params and coerce_int(cmd.params.get("tick")) is None:
                return ValidationResult(ok=False, reason="tick must be an integer")
            return ValidationResult(ok=True)

        dynamic_quest = state.quests.get_dynamic_quest(quest_id)
        if dynamic_quest is None:
            return ValidationResult(ok=False, reason=f"dynamic quest not found: {quest_id}")
        to_state = to_state_raw.lower()
        current_state = str(dynamic_quest.get("status", "available")).strip().lower()
        if not self._dynamic_quest_transition_allowed(current_state, to_state):
            return ValidationResult(
                ok=False,
                reason=f"invalid dynamic quest transition: {current_state} -> {to_state}",
            )
        if "tick" in cmd.params:
            return ValidationResult(
                ok=False,
                reason="tick is only supported for milestone transitions",
            )
        return ValidationResult(ok=True)

    def _validate_schedule_event(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        if not state.has_slice("events"):
            return ValidationResult(ok=False, reason="events slice is required")

        event_id = get_non_empty_string(cmd.params, "event_id")
        if event_id is None:
            return ValidationResult(ok=False, reason="event_id must be a non-empty string")

        reason = self._validate_trigger_condition(cmd.params, state)
        if reason is not None:
            return ValidationResult(ok=False, reason=reason or "invalid trigger condition")

        if "event_type" in cmd.params:
            event_type = get_non_empty_string(cmd.params, "event_type")
            if event_type is None:
                return ValidationResult(
                    ok=False,
                    reason="event_type must be a non-empty string",
                )

        payload = cmd.params.get("payload", {})
        if not isinstance(payload, Mapping):
            return ValidationResult(ok=False, reason="payload must be a dict")

        metadata = cmd.params.get("metadata", {})
        if not isinstance(metadata, Mapping):
            return ValidationResult(ok=False, reason="metadata must be a dict")

        del event_id
        return ValidationResult(ok=True)

    def _validate_create_rumor(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("events"):
            return ValidationResult(ok=False, reason="events slice is required")

        text = self._normalize_create_rumor_text(cmd.params)
        if text is None:
            return ValidationResult(ok=False, reason="text/content must be a non-empty string")

        spread_to = cmd.params.get("spread_to")
        if spread_to is not None:
            if not isinstance(spread_to, list):
                return ValidationResult(ok=False, reason="spread_to must be a list")
            for target in spread_to:
                if not isinstance(target, str) or not target.strip():
                    return ValidationResult(
                        ok=False,
                        reason="spread_to must contain non-empty strings",
                    )
                if not self._spread_target_exists(world, target.strip()):
                    return ValidationResult(
                        ok=False,
                        reason=f"spread_to target not found: {target.strip()}",
                    )

        area_id = self._get_optional_non_empty_string(cmd.params.get("area_id"))
        if "area_id" in cmd.params and area_id is None and cmd.params.get("area_id") is not None:
            return ValidationResult(
                ok=False,
                reason="area_id must be a non-empty string or null",
            )
        if area_id is not None and world.has_registry("maps") and world.maps.get(area_id) is None:
            return ValidationResult(ok=False, reason=f"unknown area: {area_id}")

        source_npc_id = self._get_optional_non_empty_string(cmd.params.get("source_npc_id"))
        if (
            "source_npc_id" in cmd.params
            and source_npc_id is None
            and cmd.params.get("source_npc_id") is not None
        ):
            return ValidationResult(
                ok=False,
                reason="source_npc_id must be a non-empty string or null",
            )
        if source_npc_id is not None and not self._world_has_character(world, state, source_npc_id):
            return ValidationResult(
                ok=False,
                reason=f"unknown character: {source_npc_id}",
            )

        tags = cmd.params.get("tags", [])
        if not isinstance(tags, list):
            return ValidationResult(ok=False, reason="tags must be a list")

        metadata = cmd.params.get("metadata", {})
        if not isinstance(metadata, Mapping):
            return ValidationResult(ok=False, reason="metadata must be a dict")

        return ValidationResult(ok=True)

    def _validate_modify_location(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        area_id = get_non_empty_string(cmd.params, "area_id")
        if area_id is None:
            return ValidationResult(ok=False, reason="area_id must be a non-empty string")

        if "key" in cmd.params:
            if not state.has_slice("areas"):
                return ValidationResult(ok=False, reason="areas slice is required")
            key = get_non_empty_string(cmd.params, "key")
            if key is None:
                return ValidationResult(ok=False, reason="key must be a non-empty string")
            if not self._world_or_state_has_area(world, state, area_id):
                return ValidationResult(ok=False, reason=f"unknown area: {area_id}")
            del key
            return ValidationResult(ok=True)

        if cmd.source == "ai_osiris":
            return ValidationResult(
                ok=False,
                reason="ai_osiris cannot modify player location directly",
            )
        if cmd.source not in {"engine", "system"}:
            return ValidationResult(
                ok=False,
                reason="player location updates require engine or system source",
            )
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        if world.has_registry("maps") and world.maps.get(area_id) is None:
            return ValidationResult(ok=False, reason=f"unknown area: {area_id}")

        if "location_id" in cmd.params:
            location_id = self._get_optional_non_empty_string(cmd.params.get("location_id"))
            if location_id is None and cmd.params.get("location_id") is not None:
                return ValidationResult(
                    ok=False,
                    reason="location_id must be a non-empty string or null",
                )
            if location_id is not None:
                location_ok, reason = self._area_has_location(world, area_id, location_id)
                if not location_ok:
                    return ValidationResult(ok=False, reason=reason or "invalid location")

        return ValidationResult(ok=True)

    def _validate_modify_completion(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("quests"):
            return ValidationResult(ok=False, reason="quests slice is required")

        chapter_id = get_non_empty_string(cmd.params, "chapter_id")
        if chapter_id is None:
            return ValidationResult(
                ok=False,
                reason="chapter_id must be a non-empty string",
            )

        delta = coerce_float(cmd.params.get("delta"))
        if delta is None:
            return ValidationResult(ok=False, reason="delta must be a float")
        if not -0.20 <= delta <= 0.50:
            return ValidationResult(ok=False, reason="delta must be between -0.2 and 0.5")

        if not self._world_has_chapter(world, state, chapter_id):
            return ValidationResult(ok=False, reason=f"unknown chapter: {chapter_id}")

        return ValidationResult(ok=True)

    def _validate_adjust_danger(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice is required")

        area_id = get_non_empty_string(cmd.params, "area_id")
        if area_id is None:
            return ValidationResult(ok=False, reason="area_id must be a non-empty string")

        delta = coerce_float(cmd.params.get("delta"))
        if delta is None:
            return ValidationResult(ok=False, reason="delta must be a float")
        if not -0.5 <= delta <= 0.5:
            return ValidationResult(ok=False, reason="delta must be between -0.5 and 0.5")

        if not self._world_or_state_has_area(world, state, area_id):
            return ValidationResult(ok=False, reason=f"unknown area: {area_id}")

        return ValidationResult(ok=True)

    def _compute_set_flag(self, cmd: Command) -> ExecuteResult:
        key = str(cmd.params["key"]).strip()
        value = cmd.params["value"]
        return self._success(
            cmd,
            StateChange(
                slice="flags",
                operation="set",
                path=f"flags.{key}",
                value=value,
            ),
        )

    def _compute_modify_disposition(self, cmd: Command) -> ExecuteResult:
        npc_id = self._normalize_modify_disposition_target(cmd.params) or ""
        dimension = str(cmd.params["dimension"]).strip()
        delta = coerce_int(cmd.params["delta"])
        return self._success(
            cmd,
            StateChange(
                slice="relations",
                operation="add",
                path=f"npc_dispositions.{npc_id}.{dimension}",
                value=delta,
            ),
        )

    def _compute_modify_approval(self, cmd: Command) -> ExecuteResult:
        character_id = self._normalize_modify_approval_target(cmd.params) or ""
        delta = coerce_int(cmd.params["delta"])
        return self._success(
            cmd,
            StateChange(
                slice="party",
                operation="add",
                path=f"companion_approval.{character_id}",
                value=delta,
            ),
        )

    def _compute_add_knowledge(self, cmd: Command) -> ExecuteResult:
        npc_id = self._normalize_add_knowledge_target(cmd.params) or ""
        impression = self._normalize_add_knowledge_text(cmd.params) or ""
        return self._success(
            cmd,
            StateChange(
                slice="relations",
                operation="add",
                path=f"npc_impressions.{npc_id}",
                value=impression,
            ),
        )

    def _compute_advance_quest(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        quest_id = str(cmd.params["quest_id"]).strip()
        to_state_raw = str(cmd.params["to_state"]).strip()
        quest_kind = self._resolve_quest_kind(cmd.params, state, quest_id) or "milestone"

        if quest_kind == "dynamic":
            dynamic_quest = state.quests.get_dynamic_quest(quest_id) or {}
            dynamic_quest["status"] = to_state_raw.lower()
            return self._success(
                cmd,
                StateChange(
                    slice="quests",
                    operation="modify",
                    path=f"dynamic_quests.{quest_id}",
                    value=dynamic_quest,
                ),
            )

        payload: dict[str, Any] = {"state": to_state_raw.upper()}
        if "tick" in cmd.params:
            payload["tick"] = int(cmd.params["tick"])
        return self._success(
            cmd,
            StateChange(
                slice="quests",
                operation="modify",
                path=f"milestone_states.{quest_id}",
                value=payload,
            ),
        )

    def _compute_modify_completion(self, cmd: Command) -> ExecuteResult:
        chapter_id = str(cmd.params["chapter_id"]).strip()
        delta = float(cmd.params["delta"])
        return self._success(
            cmd,
            StateChange(
                slice="quests",
                operation="add",
                path=f"chapter_completion.{chapter_id}",
                value=delta,
            ),
        )

    def _compute_schedule_event(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        event_id = str(cmd.params["event_id"]).strip()
        event_type = str(cmd.params.get("event_type", "generic")).strip() or "generic"
        payload = self._coerce_mapping(cmd.params.get("payload", {}))
        metadata = self._coerce_mapping(cmd.params.get("metadata", {}))
        trigger_condition = self._normalize_trigger_condition(cmd.params)
        created_at = (
            dict(state.time.get_current_time())
            if state.has_slice("time")
            else {"day": 1, "slot": 0}
        )
        pending_event = {
            "event_id": event_id,
            "event_type": event_type,
            "trigger_condition": trigger_condition,
            "created_at": created_at,
            "payload": payload,
            "metadata": metadata,
            "source": cmd.source,
        }
        return self._success(
            cmd,
            StateChange(
                slice="events",
                operation="add",
                path="pending_events",
                value=pending_event,
            ),
        )

    @classmethod
    def _normalize_trigger_condition(cls, params: Mapping[str, Any]) -> dict[str, Any]:
        """Convert schedule_event params to a canonical trigger_condition dict."""
        direct_tick = coerce_int(params.get("trigger_tick"))
        if direct_tick is not None and direct_tick >= 0:
            return {"type": "absolute_tick", "tick": direct_tick}
        raw = params.get("trigger_condition")
        if isinstance(raw, Mapping):
            return {str(k): v for k, v in raw.items()}
        return {"type": "absolute_tick", "tick": 0}

    def _compute_create_rumor(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        area_id = self._get_optional_non_empty_string(cmd.params.get("area_id"))
        source_npc_id = self._get_optional_non_empty_string(cmd.params.get("source_npc_id"))
        tags = [str(tag) for tag in cmd.params.get("tags", [])]
        spread_to = [str(target).strip() for target in cmd.params.get("spread_to", [])]
        metadata = self._coerce_mapping(cmd.params.get("metadata", {}))
        created_at = state.time.get_current_time() if state.has_slice("time") else None
        text = self._normalize_create_rumor_text(cmd.params) or ""
        rumor = {
            "rumor_id": cmd.params.get("rumor_id") or f"rumor_{uuid4().hex[:12]}",
            "text": text,
            "content": text,
            "spread_to": spread_to,
            "area_id": area_id,
            "source_npc_id": source_npc_id,
            "tags": tags,
            "metadata": metadata,
            "source": cmd.source,
            "created_at": dict(created_at) if isinstance(created_at, dict) else None,
        }
        return self._success(
            cmd,
            StateChange(
                slice="events",
                operation="add",
                path="rumors",
                value=rumor,
            ),
        )

    def _compute_modify_location(self, cmd: Command) -> ExecuteResult:
        area_id = str(cmd.params["area_id"]).strip()
        if "key" in cmd.params:
            key = str(cmd.params["key"]).strip()
            return self._success(
                cmd,
                StateChange(
                    slice="areas",
                    operation="set",
                    path=f"{area_id}.properties.{key}",
                    value=cmd.params.get("value"),
                ),
            )

        location_id = self._get_optional_non_empty_string(cmd.params.get("location_id"))
        return self._success(
            cmd,
            StateChange(
                slice="player",
                operation="set",
                path="current_area",
                value=area_id,
            ),
            StateChange(
                slice="player",
                operation="set",
                path="current_location",
                value=location_id,
            ),
        )

    def _compute_adjust_danger(self, cmd: Command) -> ExecuteResult:
        area_id = str(cmd.params["area_id"]).strip()
        delta = float(cmd.params["delta"])
        return self._success(
            cmd,
            StateChange(
                slice="areas",
                operation="add",
                path=f"{area_id}.danger_level",
                value=delta,
            ),
        )

    def _success(self, cmd: Command, *changes: StateChange) -> ExecuteResult:
        return ExecuteResult(
            success=True,
            delta=StateDelta(
                changes=list(changes),
                reason=cmd.type,
                metadata={"handler": "world_state", "command": cmd.type},
            ),
            metadata={"handler": "world_state", "command": cmd.type},
        )

    def _enforce_source_policy(self, cmd: Command) -> ValidationResult:
        if cmd.type != "modify_location":
            return ValidationResult(ok=True)
        if cmd.source != "ai_osiris":
            return ValidationResult(ok=True)
        if "key" in cmd.params:
            return ValidationResult(ok=True)
        return ValidationResult(
            ok=False,
            reason="ai_osiris cannot modify player location directly",
        )

    def _resolve_quest_kind(
        self,
        params: Mapping[str, Any],
        state: StateContainer,
        quest_id: str,
    ) -> str | None:
        raw_kind = str(params.get("quest_kind", "auto")).strip().lower()
        if raw_kind not in {"auto", "dynamic", "milestone"}:
            return None
        if raw_kind == "auto":
            return "dynamic" if state.quests.get_dynamic_quest(quest_id) is not None else "milestone"
        return raw_kind

    @classmethod
    def _quest_transition_allowed(cls, current_state: str, to_state: str) -> bool:
        if current_state == to_state:
            return True
        return to_state in cls._MILESTONE_TRANSITIONS.get(current_state, set())

    @classmethod
    def _dynamic_quest_transition_allowed(cls, current_state: str, to_state: str) -> bool:
        if current_state == to_state:
            return True
        return to_state in cls._DYNAMIC_TRANSITIONS.get(current_state, set())

    @classmethod
    def _validate_trigger_condition(
        cls,
        params: Mapping[str, Any],
        state: StateContainer,
    ) -> str | None:
        direct_tick = coerce_int(params.get("trigger_tick"))
        if direct_tick is not None:
            if direct_tick < 0:
                return "trigger_tick must be a non-negative integer"
            return None
        if "trigger_tick" in params:
            return "trigger_tick must be a non-negative integer"

        raw_condition = params.get("trigger_condition")
        if not isinstance(raw_condition, Mapping):
            return "trigger_tick or trigger_condition is required"

        condition_type = cls._get_optional_non_empty_string(raw_condition.get("type"))
        if condition_type is None:
            return "trigger_condition.type must be a non-empty string"
        if condition_type == "absolute_tick":
            tick = coerce_int(raw_condition.get("tick"))
            if tick is None or tick < 0:
                return "trigger_condition.tick must be a non-negative integer"
            return None
        if condition_type == "time_slots_elapsed":
            count = coerce_int(raw_condition.get("count"))
            if count is None or count < 0:
                return "trigger_condition.count must be a non-negative integer"
            if not state.has_slice("time"):
                return "time slice is required for time_slots_elapsed"
            return None
        if condition_type == "period_reached":
            period = cls._get_optional_non_empty_string(raw_condition.get("period"))
            if period is None:
                return "trigger_condition.period must be a non-empty string"
            return None
        if condition_type == "location_entered":
            area_id = cls._get_optional_non_empty_string(raw_condition.get("area_id"))
            location_id = cls._get_optional_non_empty_string(raw_condition.get("location_id"))
            if area_id is None and location_id is None:
                return "trigger_condition.location_entered requires area_id or location_id"
            return None
        if condition_type == "flag_set":
            key = cls._get_optional_non_empty_string(raw_condition.get("key"))
            if key is None:
                return "trigger_condition.key must be a non-empty string"
            return None
        return f"unsupported trigger_condition.type: {condition_type}"

    @classmethod
    def _resolve_trigger_tick(
        cls,
        params: Mapping[str, Any],
        state: StateContainer,
    ) -> tuple[int | None, str | None]:
        direct_tick = coerce_int(params.get("trigger_tick"))
        if direct_tick is not None:
            if direct_tick < 0:
                return None, "trigger_tick must be a non-negative integer"
            return direct_tick, None
        if "trigger_tick" in params:
            return None, "trigger_tick must be a non-negative integer"

        raw_condition = params.get("trigger_condition")
        if not isinstance(raw_condition, Mapping):
            return None, "trigger_tick or trigger_condition is required"
        condition_type = cls._get_optional_non_empty_string(raw_condition.get("type"))
        if condition_type is None:
            return None, "trigger_condition.type must be a non-empty string"
        if condition_type == "absolute_tick":
            tick = coerce_int(raw_condition.get("tick"))
            if tick is None or tick < 0:
                return None, "trigger_condition.tick must be a non-negative integer"
            return tick, None
        if condition_type == "time_slots_elapsed":
            count = coerce_int(raw_condition.get("count"))
            if count is None or count < 0:
                return None, "trigger_condition.count must be a non-negative integer"
            if not state.has_slice("time"):
                return None, "time slice is required for time_slots_elapsed"
            return state.time.absolute_tick() + count, None
        return None, f"unsupported trigger_condition.type: {condition_type}"

    @staticmethod
    def _normalize_modify_disposition_target(params: Mapping[str, Any]) -> str | None:
        value = get_non_empty_string(params, "npc_id")
        if value is not None:
            return value
        return get_non_empty_string(params, "target")

    @staticmethod
    def _normalize_modify_approval_target(params: Mapping[str, Any]) -> str | None:
        value = get_non_empty_string(params, "character_id")
        if value is not None:
            return value
        return get_non_empty_string(params, "character")

    @staticmethod
    def _normalize_add_knowledge_target(params: Mapping[str, Any]) -> str | None:
        value = get_non_empty_string(params, "npc_id")
        if value is not None:
            return value
        return get_non_empty_string(params, "character_id")

    @staticmethod
    def _normalize_add_knowledge_text(params: Mapping[str, Any]) -> str | None:
        value = get_non_empty_string(params, "impression")
        if value is not None:
            return value
        return get_non_empty_string(params, "knowledge")

    @staticmethod
    def _normalize_create_rumor_text(params: Mapping[str, Any]) -> str | None:
        value = get_non_empty_string(params, "text")
        if value is not None:
            return value
        return get_non_empty_string(params, "content")

    @staticmethod
    def _world_has_character(
        world: WorldInstance,
        state: StateContainer,
        character_id: str,
    ) -> bool:
        if world.has_registry("characters"):
            return world.characters.get(character_id) is not None
        if state.has_slice("relations"):
            return (
                character_id in state.relations.npc_dispositions
                or character_id in state.relations.npc_impressions
            )
        return True

    @staticmethod
    def _world_or_state_has_area(
        world: WorldInstance,
        state: StateContainer,
        area_id: str,
    ) -> bool:
        if world.has_registry("maps") and world.maps.get(area_id) is not None:
            return True
        if state.has_slice("areas") and area_id in state.areas.areas:
            return True
        return False

    @staticmethod
    def _world_has_chapter(
        world: WorldInstance,
        state: StateContainer,
        chapter_id: str,
    ) -> bool:
        if world.has_registry("quests"):
            for chapter in world.quests.chapters():
                if chapter.id == chapter_id:
                    return True
            return False
        if state.has_slice("quests"):
            return chapter_id in state.quests.chapter_completion
        return True

    @staticmethod
    def _area_has_location(
        world: WorldInstance,
        area_id: str,
        location_id: str,
    ) -> tuple[bool, str | None]:
        if not world.has_registry("maps"):
            return True, None
        area_template = world.maps.get(area_id)
        if area_template is None:
            return False, f"unknown area: {area_id}"
        if not area_template.sub_locations:
            return False, f"area has no sub_locations: {area_id}"
        if location_id not in area_template.sub_locations:
            return False, f"unknown location '{location_id}' in area '{area_id}'"
        return True, None

    @staticmethod
    def _spread_target_exists(world: WorldInstance, target: str) -> bool:
        if target.startswith("npc:"):
            character_id = target.split(":", 1)[1].strip()
            if not character_id:
                return False
            if world.has_registry("characters"):
                return world.characters.get(character_id) is not None
            return True
        if target.startswith("area:"):
            area_id = target.split(":", 1)[1].strip()
            if not area_id:
                return False
            if world.has_registry("maps"):
                return world.maps.get(area_id) is not None
            return True

        if world.has_registry("maps") and world.maps.get(target) is not None:
            return True
        if world.has_registry("characters") and world.characters.get(target) is not None:
            return True
        if not world.has_registry("maps") and not world.has_registry("characters"):
            return True
        return False

    @staticmethod
    def _get_optional_non_empty_string(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _coerce_mapping(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        return {str(key): item for key, item in value.items()}
