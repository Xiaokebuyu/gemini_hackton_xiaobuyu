"""Internal planner command handlers.

These commands are only emitted by the narrative planner runtime. They keep
planner-owned state mutations on the standard Command -> Handler -> StateDelta
path without exposing planner internals to player-facing action routes.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from app.game_core.content import WorldInstance
from app.game_core.orchestration.event_engine import BasicEventConditionEvaluator
from app.game_core.orchestration.presence import resolve_npc_room, room_exists
from app.game_core.scene_interactables import canonical_facility_ids
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import (
    coerce_float,
    coerce_int,
    coerce_non_empty_string,
    handler_success,
)
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer


def _normalize_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _string_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _coerce_optional_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "on"}
    return False


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text = coerce_non_empty_string(item)
        if text is not None:
            normalized.append(text)
    return normalized


def _normalize_interactable_list(value: Any) -> list[Any]:
    """Normalize an interactables list, preserving dict objects.

    Interactable elements should be dicts with id/name/description/type/tags/
    checks so that InteractableHandler can match and process them. Plain string
    entries are kept for backward compatibility.
    """
    if not isinstance(value, list):
        return []
    result: list[Any] = []
    for item in value:
        if isinstance(item, dict):
            if item:
                result.append(item)
        else:
            text = coerce_non_empty_string(item)
            if text is not None:
                result.append(text)
    return result


def _normalize_interactable_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        interactable_id = coerce_non_empty_string(item.get("id"))
        if interactable_id is None:
            continue
        normalized_item = dict(item)
        normalized_item["id"] = interactable_id
        normalized.append(normalized_item)
    return normalized


def _planner_location_exists(
    state: StateContainer,
    world: WorldInstance,
    area_id: str,
    location_id: str | None,
) -> bool:
    if location_id is None:
        return True
    if world.has_registry("maps") and world.maps.get_sub_location(area_id, location_id) is not None:
        return True
    area_state = state.areas.areas.get(area_id) if state.has_slice("areas") else None
    if area_state is None:
        return False
    return any(
        str(item.get("id", "")).strip() == location_id
        for item in area_state.temporary_sub_areas
        if isinstance(item, Mapping)
    )


def _planner_source_gate(cmd: Command) -> ValidationResult:
    if cmd.source in ("narrative_planner", "npc", "npc_service"):
        return ValidationResult(ok=True)
    return ValidationResult(ok=False, reason="planner command source must be narrative_planner or npc")


def _planner_success(
    command_type: str,
    *,
    changes: list[StateChange],
    metadata: dict[str, Any] | None = None,
) -> ExecuteResult:
    return handler_success(
        "planner",
        command_type,
        changes=changes,
        metadata=metadata or {},
        omit_empty_delta=False,
    )


class PlannerQuestHandler(StaticCommandHandler):
    COMMAND_TYPES = (
        "planner_create_quest",
        "planner_publish_bulletin",
        "planner_retire_quest",
        "planner_update_quest",
        "planner_set_task_monitor",
    )

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        source_gate = _planner_source_gate(cmd)
        if not source_gate.ok:
            return source_gate
        if not state.has_slice("quests"):
            return ValidationResult(ok=False, reason="quests slice is required")
        if not state.has_slice("narrative_plan"):
            return ValidationResult(ok=False, reason="narrative_plan slice is required")
        if cmd.type == "planner_create_quest":
            quest_id = coerce_non_empty_string(cmd.params.get("quest_id"))
            if quest_id is None:
                return ValidationResult(ok=False, reason="quest_id must be a non-empty string")
            if quest_id in state.quests.dynamic_quests:
                return ValidationResult(ok=False, reason=f"dynamic quest already exists: {quest_id}")
            return ValidationResult(ok=True)
        if cmd.type == "planner_publish_bulletin":
            board_id = coerce_non_empty_string(cmd.params.get("board_id"))
            if board_id is None:
                return ValidationResult(ok=False, reason="board_id must be a non-empty string")
            if not state.has_slice("areas"):
                return ValidationResult(ok=False, reason="areas slice is required")
            area_id = self._resolve_area_id(cmd.params, state)
            if area_id is None:
                return ValidationResult(ok=False, reason="area_id must be resolvable")
            if area_id not in state.areas.areas:
                return ValidationResult(ok=False, reason=f"unknown area_id: {area_id}")
            # S3-03: validate quest_id exists if provided
            quest_id = self._resolve_bulletin_quest_id(cmd.params)
            if quest_id is not None and quest_id not in state.quests.dynamic_quests:
                return ValidationResult(
                    ok=False,
                    reason=f"quest not found in dynamic_quests: {quest_id}",
                )
            return ValidationResult(ok=True)
        if cmd.type == "planner_retire_quest":
            quest_id = coerce_non_empty_string(cmd.params.get("quest_id"))
            if quest_id is None:
                return ValidationResult(ok=False, reason="quest_id must be a non-empty string")
            if quest_id not in state.quests.dynamic_quests:
                return ValidationResult(ok=False, reason=f"dynamic quest not found: {quest_id}")
            return ValidationResult(ok=True)
        if cmd.type == "planner_update_quest":
            quest_id = coerce_non_empty_string(cmd.params.get("quest_id"))
            if quest_id is None:
                return ValidationResult(ok=False, reason="quest_id must be a non-empty string")
            quest = state.quests.dynamic_quests.get(quest_id)
            if not isinstance(quest, Mapping):
                return ValidationResult(ok=False, reason=f"dynamic quest not found: {quest_id}")
            if str(quest.get("status", "")).strip().lower() != "active":
                return ValidationResult(ok=False, reason="planner_update_quest only supports active quests")
            return ValidationResult(ok=True)
        if cmd.type == "planner_set_task_monitor":
            quest_id = coerce_non_empty_string(cmd.params.get("quest_id"))
            if quest_id is None:
                return ValidationResult(ok=False, reason="quest_id must be a non-empty string")
            quest = state.quests.dynamic_quests.get(quest_id)
            if not isinstance(quest, Mapping):
                return ValidationResult(ok=False, reason=f"dynamic quest not found: {quest_id}")
            status = str(quest.get("status", "")).strip().lower()
            if status not in {"active", "in_progress", "accepted"}:
                return ValidationResult(ok=False, reason="planner_set_task_monitor only supports active quests")
            return ValidationResult(ok=True)
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
        if cmd.type == "planner_create_quest":
            return self._compute_create_quest(cmd, state, world)
        if cmd.type == "planner_publish_bulletin":
            return self._compute_publish_bulletin(cmd, state)
        if cmd.type == "planner_retire_quest":
            return self._compute_retire_quest(cmd, state)
        if cmd.type == "planner_update_quest":
            return self._compute_update_quest(cmd, state)
        if cmd.type == "planner_set_task_monitor":
            return self._compute_set_task_monitor(cmd, state)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _compute_create_quest(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        quest_id = coerce_non_empty_string(cmd.params.get("quest_id")) or ""
        current_tick = coerce_int(cmd.params.get("current_tick")) or 0
        status = coerce_non_empty_string(cmd.params.get("status")) or "available"
        # A-5 (S3-06): reject if quest_id collides with a milestone_id
        if quest_id in state.quests.milestone_states:
            return ExecuteResult.error(
                f"quest_id '{quest_id}' collides with an existing milestone_id"
            )
        metadata = _normalize_mapping(cmd.params.get("metadata"))
        raw_objectives = cmd.params.get("objectives")
        if not isinstance(raw_objectives, list):
            raw_objectives = metadata.get("objectives")
        raw_objectives_list = raw_objectives if isinstance(raw_objectives, list) else []
        # Normalize objectives: ensure each entry is a dict with description + completed,
        # and preserve optional structured condition binding (P25-14 Phase 2).
        # Unknown keys in existing dicts are preserved for backward compatibility
        # (e.g. legacy "type"/"target" keys used by _build_objective_event_changes).
        normalized_objectives = []
        for obj in raw_objectives_list:
            if isinstance(obj, str):
                normalized_objectives.append({"description": obj, "completed": False})
            elif isinstance(obj, dict):
                # Start from a copy of the original to preserve unknown keys
                entry: dict[str, Any] = dict(obj)
                # Ensure canonical fields are present
                if "description" not in entry:
                    entry["description"] = str(obj.get("text", ""))
                else:
                    entry["description"] = str(entry["description"])
                entry["completed"] = bool(obj.get("completed", False))
                # Validate structured condition if present
                condition = obj.get("condition")
                if isinstance(condition, dict) and condition.get("type"):
                    entry["condition"] = condition
                elif "condition" in entry and not (isinstance(entry["condition"], dict) and entry["condition"].get("type")):
                    # Remove invalid condition entries
                    del entry["condition"]
                # A8b: auto-generate condition when type present but condition absent
                if "condition" not in entry:
                    obj_type = coerce_non_empty_string(str(obj.get("type", "")))
                    if obj_type:
                        auto_cond_type = self._objective_to_condition_type(obj_type.lower())
                        if auto_cond_type is not None:
                            auto_params = self._objective_target_to_params(
                                auto_cond_type, obj.get("target")
                            )
                            if auto_params is not None:
                                entry["condition"] = {"type": auto_cond_type, "params": auto_params}
                            else:
                                entry.setdefault("metadata_warnings", []).append(
                                    f"could not derive condition params for type={obj_type}"
                                )
                        else:
                            entry.setdefault("metadata_warnings", []).append(
                                f"unknown objective type for auto-condition: {obj_type}"
                            )
                normalized_objectives.append(entry)
            else:
                normalized_objectives.append({"description": str(obj), "completed": False})
        objectives = normalized_objectives
        raw_rewards = cmd.params.get("rewards")
        if not isinstance(raw_rewards, Mapping):
            raw_rewards = metadata.get("rewards")
        rewards = dict(raw_rewards) if isinstance(raw_rewards, Mapping) else {}
        raw_expiry_ticks = cmd.params.get("expiry_ticks")
        if raw_expiry_ticks is None:
            raw_expiry_ticks = metadata.get("expiry_ticks")
        expiry_ticks: int | None = None
        if raw_expiry_ticks is not None:
            expiry_ticks = coerce_int(raw_expiry_ticks)
            if expiry_ticks is None:
                return ExecuteResult.error("expiry_ticks must be an integer")
        urgency = coerce_non_empty_string(metadata.get("urgency")) or "medium"
        on_expire = coerce_non_empty_string(cmd.params.get("on_expire"))
        if on_expire is None:
            on_expire = coerce_non_empty_string(metadata.get("on_expire"))
        if on_expire is None:
            on_expire = "ignore"
        on_expire = on_expire.strip().lower()
        if on_expire not in {"ignore", "escalate", "retire"}:
            on_expire = "ignore"
        generated_by_escalation = coerce_int(metadata.get("generated_by_escalation")) or 0
        target_milestone = coerce_non_empty_string(metadata.get("source_milestone"))
        delivery_method = (
            coerce_non_empty_string(cmd.params.get("delivery_method"))
            or coerce_non_empty_string(metadata.get("delivery_method"))
            or "board"
        )
        raw_requires_report = (
            cmd.params.get("requires_report")
            if "requires_report" in cmd.params
            else metadata.get("requires_report")
        )
        raw_reported = (
            cmd.params.get("reported")
            if "reported" in cmd.params
            else metadata.get("reported")
        )
        # Phase 3 (P26-3-1): level wall — min_level from cmd params or metadata, default 1
        raw_min_level = cmd.params.get("min_level")
        if raw_min_level is None:
            raw_min_level = metadata.get("min_level")
        min_level = max(1, coerce_int(raw_min_level) or 1)
        quest_payload = {
            "quest_id": quest_id,
            "status": status,
            "title": _string_or_empty(cmd.params.get("title")),
            "summary": _string_or_empty(cmd.params.get("summary")),
            "source": "narrative_planner",
            "created_at_tick": current_tick,
            "target_milestone": target_milestone,
            "urgency": urgency,
            "objectives": objectives,
            "rewards": rewards,
            "delivery_method": delivery_method,
            "expiry_ticks": expiry_ticks,
            "on_expire": on_expire,
            "generated_by_escalation": generated_by_escalation,
            "planner_reasoning": _string_or_empty(metadata.get("planner_reasoning")),
            "requires_report": _coerce_optional_bool(raw_requires_report) if raw_requires_report is not None else (delivery_method == "board"),
            "reported": _coerce_optional_bool(raw_reported),
            "min_level": min_level,
            "metadata": metadata,
        }

        changes: list[StateChange] = [
            StateChange("quests", "set", f"dynamic_quests.{quest_id}", quest_payload),
            StateChange(
                "narrative_plan",
                "add",
                "quest_history",
                {"kind": "create_quest", "quest_id": quest_id, "tick": current_tick},
            ),
        ]
        if target_milestone is not None and state.quests.get_milestone_state(target_milestone) == "AVAILABLE":
            changes.append(
                StateChange(
                    "quests",
                    "set",
                    f"milestone_states.{target_milestone}",
                    {"state": "ACTIVE", "tick": current_tick},
                )
            )
            # Sync current_target_milestone when null — avoids overwriting an active target
            if state.has_slice("narrative_plan"):
                current_target = state.narrative_plan.current_target_milestone
                if not current_target:
                    changes.append(
                        StateChange(
                            "narrative_plan",
                            "set",
                            "current_target_milestone",
                            target_milestone,
                        )
                    )
        changes.extend(
            self._build_milestone_condition_event_changes(
                quest_id,
                current_tick=current_tick,
                state=state,
                world=world,
            )
        )
        # Path B (EventEngine-based objective tracking) removed — objectives are now
        # tracked directly by QuestObjectiveTrackingHook (P56) via condition fields.
        # A-1 (S3-04/S5-06): auto-publish bulletin when delivery_method=="board"
        # and quest status is "available", so create_quest is atomic with publish.
        if status == "available" and delivery_method == "board" and state.has_slice("areas"):
            auto_area_id = (
                coerce_non_empty_string(cmd.params.get("area_id"))
                or (state.player.current_area if state.has_slice("player") else None)
            )
            # derive board_id from params or fall back to area-local default
            auto_board_id = coerce_non_empty_string(cmd.params.get("board_id")) or "quest_board"
            if auto_area_id and auto_area_id in state.areas.areas:
                board_entry: dict[str, Any] = {
                    "board_id": auto_board_id,
                    "quest_id": quest_id,
                    "title": quest_payload["title"],
                    "content": quest_payload["summary"],
                    "published_at_tick": current_tick,
                    "source": "narrative_planner",
                    "area_id": auto_area_id,
                }
                changes.append(
                    StateChange(
                        "areas",
                        "append",
                        f"board_bulletins.{auto_board_id}",
                        board_entry,
                    )
                )
        # A9e: write quest_id back to milestone outline step when step_index is supplied
        step_index = coerce_int(cmd.params.get("step_index"))
        if step_index is not None and step_index >= 0 and state.has_slice("narrative_plan"):
            changes.append(
                StateChange(
                    "narrative_plan",
                    "set",
                    "milestone_outline.step_quest_id",
                    {"step_index": step_index, "quest_id": quest_id},
                )
            )
        return _planner_success(
            cmd.type,
            changes=changes,
            metadata={"quest_id": quest_id, "step_index": step_index},
        )

    def _compute_publish_bulletin(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        current_tick = coerce_int(cmd.params.get("current_tick")) or 0
        board_id = coerce_non_empty_string(cmd.params.get("board_id")) or ""
        raw_area_id = self._resolve_area_id(cmd.params, state)
        # 3b: coerce_non_empty_string returns None for empty-after-strip strings, but
        # _resolve_area_id can return an empty-string value that passed validate() via
        # location.area_id before being stripped here.  Re-apply coerce to catch it.
        area_id_checked = coerce_non_empty_string(raw_area_id)
        if area_id_checked is None:
            return ExecuteResult.error("area_id must be a non-empty string")
        area_id = area_id_checked
        location_payload = cmd.params.get("location")
        resolved_sub_location = (
            coerce_non_empty_string(location_payload.get("sub_location"))
            if isinstance(location_payload, Mapping)
            else None
        )
        metadata = _normalize_mapping(cmd.params.get("metadata"))
        quest_id = self._resolve_bulletin_quest_id(cmd.params)
        if quest_id is not None and coerce_non_empty_string(metadata.get("quest_id")) is None:
            metadata["quest_id"] = quest_id
        board_entry: dict[str, Any] = {
            "board_id": board_id,
            "quest_id": quest_id or "",
            "title": _string_or_empty(cmd.params.get("title")),
            "content": _string_or_empty(cmd.params.get("content")),
            "published_at_tick": current_tick,
            "source": "narrative_planner",
            "area_id": area_id,
        }
        if resolved_sub_location is not None:
            board_entry["sub_location"] = resolved_sub_location
            board_entry["location"] = {"area_id": area_id, "sub_location": resolved_sub_location}
        return _planner_success(
            cmd.type,
            changes=[
                StateChange(
                    "areas",
                    "append",
                    f"board_bulletins.{board_id}",
                    board_entry,
                )
            ],
            metadata={
                "area_id": area_id,
                "board_id": board_id,
                "quest_id": quest_id or "",
                "sub_location": resolved_sub_location,
                "notify_resident_npcs": bool(cmd.params.get("notify_resident_npcs", True)),
            },
        )

    @staticmethod
    def _resolve_bulletin_quest_id(params: Mapping[str, Any]) -> str | None:
        metadata = _normalize_mapping(params.get("metadata"))
        return (
            coerce_non_empty_string(metadata.get("quest_id"))
            or coerce_non_empty_string(params.get("quest_id"))
        )

    def _compute_retire_quest(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        quest_id = coerce_non_empty_string(cmd.params.get("quest_id")) or ""
        current_tick = coerce_int(cmd.params.get("current_tick")) or 0
        changes: list[StateChange] = [
            StateChange("quests", "set", f"dynamic_quests.{quest_id}.status", "retired"),
            StateChange(
                "narrative_plan",
                "add",
                "quest_history",
                {"kind": "retire_quest", "quest_id": quest_id, "tick": current_tick},
            ),
        ]

        linked_temp_npcs: list[str] = []
        for entry in list(state.narrative_plan.quest_history):
            if entry.get("kind") != "spawn_quest_npc":
                continue
            if entry.get("linked_quest_id") != quest_id:
                continue
            npc_id = coerce_non_empty_string(entry.get("npc_id"))
            if npc_id is None:
                continue
            linked_temp_npcs.append(npc_id)
            changes.append(StateChange("areas", "remove", f"npc_presence.{npc_id}", None))
            changes.append(StateChange("narrative_plan", "remove", f"temporary_npcs.{npc_id}", None))

        for area_id, area_state in state.areas.areas.items() if state.has_slice("areas") else []:
            updated_boards: dict[str, list[dict[str, Any]]] = {}
            board_changed = False
            for board_id, entries in area_state.board_bulletins.items():
                filtered = [
                    dict(entry)
                    for entry in entries
                    if str(entry.get("quest_id", "")).strip() != quest_id
                ]
                if len(filtered) != len(entries):
                    board_changed = True
                updated_boards[str(board_id)] = filtered
            if board_changed:
                changes.append(
                    StateChange("areas", "set", f"{area_id}.board_bulletins", updated_boards)
                )

            filtered_sub_areas = [
                dict(item)
                for item in area_state.temporary_sub_areas
                if str(item.get("linked_quest_id", "")).strip() != quest_id
            ]
            if len(filtered_sub_areas) != len(area_state.temporary_sub_areas):
                changes.append(
                    StateChange(
                        "areas",
                        "set",
                        f"{area_id}.temporary_sub_areas",
                        filtered_sub_areas,
                    )
                )

        directives = list(state.narrative_plan.npc_directives)
        filtered_directives = [
            dict(item)
            for item in directives
            if str(item.get("linked_quest_id", "")).strip() != quest_id
        ]
        if len(filtered_directives) != len(directives):
            changes.append(
                StateChange("narrative_plan", "set", "npc_directives", filtered_directives)
            )

        return _planner_success(
            cmd.type,
            changes=changes,
            metadata={"quest_id": quest_id, "removed_temporary_npcs": linked_temp_npcs},
        )

    def _compute_update_quest(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        quest_id = coerce_non_empty_string(cmd.params.get("quest_id")) or ""
        current_tick = coerce_int(cmd.params.get("current_tick")) or 0
        quest = state.quests.dynamic_quests.get(quest_id)
        if not isinstance(quest, Mapping):
            return ExecuteResult.error(f"dynamic quest not found: {quest_id}")
        updated = dict(quest)
        current_step = cmd.params.get("current_step")
        if isinstance(current_step, str) and current_step.strip():
            updated["current_step"] = current_step.strip()
        next_steps = cmd.params.get("next_steps")
        if isinstance(next_steps, list):
            updated["next_steps"] = [str(step).strip() for step in next_steps if str(step).strip()]
        hints = cmd.params.get("hints")
        if isinstance(hints, list):
            updated["hints"] = [str(hint).strip() for hint in hints if str(hint).strip()]
        completed_objectives = cmd.params.get("completed_objectives")
        if isinstance(completed_objectives, list):
            updated["completed_objectives"] = [
                str(item).strip() for item in completed_objectives if str(item).strip()
            ]
        return _planner_success(
            cmd.type,
            changes=[
                StateChange("quests", "modify", f"dynamic_quests.{quest_id}", updated),
                StateChange(
                    "narrative_plan",
                    "add",
                    "quest_history",
                    {"kind": "update_quest", "quest_id": quest_id, "tick": current_tick},
                ),
            ],
            metadata={
                "quest_id": quest_id,
                "current_step": updated.get("current_step"),
                "next_steps": list(updated.get("next_steps", [])),
                "hints": list(updated.get("hints", [])),
            },
        )

    def _compute_set_task_monitor(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        """Attach a task_monitor dict to an active dynamic quest.

        The task_monitor records:
          - conditions: list of condition dicts (same format as objective conditions)
          - on_complete: "auto" (complete quest) or "notify" (SSE only)
        """
        quest_id = coerce_non_empty_string(cmd.params.get("quest_id")) or ""
        quest = state.quests.dynamic_quests.get(quest_id)
        if not isinstance(quest, Mapping):
            return ExecuteResult.error(f"dynamic quest not found: {quest_id}")
        conditions = cmd.params.get("conditions")
        if not isinstance(conditions, list):
            conditions = []
        on_complete = coerce_non_empty_string(cmd.params.get("on_complete")) or "auto"
        if on_complete not in {"auto", "notify"}:
            on_complete = "auto"
        updated = dict(quest)
        updated["task_monitor"] = {
            "conditions": [dict(c) for c in conditions if isinstance(c, Mapping)],
            "on_complete": on_complete,
        }
        return _planner_success(
            cmd.type,
            changes=[
                StateChange("quests", "modify", f"dynamic_quests.{quest_id}", updated),
            ],
            metadata={"quest_id": quest_id, "on_complete": on_complete},
        )

    @staticmethod
    def _resolve_area_id(params: Mapping[str, Any], state: StateContainer) -> str | None:
        area_id = coerce_non_empty_string(params.get("area_id"))
        location_payload = params.get("location")
        if isinstance(location_payload, Mapping):
            area_id = coerce_non_empty_string(location_payload.get("area_id")) or area_id
        if area_id is None and state.has_slice("player"):
            area_id = state.player.current_area
        return coerce_non_empty_string(area_id)

    def _build_milestone_condition_event_changes(
        self,
        milestone_id: str,
        *,
        current_tick: int,
        state: StateContainer,
        world: WorldInstance,
    ) -> list[StateChange]:
        if not state.has_slice("events"):
            return []
        if not world.has_registry("quests"):
            return []
        milestone = world.quests.get_milestone(milestone_id)
        if milestone is None:
            return []

        changes: list[StateChange] = []
        specs: list[tuple[list[Any], str]] = []
        if milestone.success_conditions:
            specs.append((list(milestone.success_conditions), "COMPLETED"))
        if milestone.failure_conditions:
            specs.append((list(milestone.failure_conditions), "FAILED"))

        for conditions, outcome_state in specs:
            prefix = "sc" if outcome_state == "COMPLETED" else "fc"
            for idx, cond in enumerate(conditions):
                event_id = f"milestone_{milestone_id}_{prefix}_{idx}"
                if state.events.get_event(event_id) is not None:
                    continue
                changes.append(
                    StateChange(
                        "events",
                        "set",
                        f"active_events.{event_id}",
                        {
                            "id": event_id,
                            "event_id": event_id,
                            "state": "dormant",
                            "status": "dormant",
                            "conditions": [{"type": cond.type, "params": dict(cond.params)}],
                            "on_trigger": [{
                                "type": "advance_quest",
                                "params": {"quest_id": milestone_id, "to_state": outcome_state},
                            }],
                            "source": "narrative_planner",
                            "milestone_id": milestone_id,
                            "created_at_tick": current_tick,
                        },
                    )
                )
        return changes

    def _build_objective_event_changes(
        self,
        quest_id: str,
        quest_payload: Mapping[str, Any],
        *,
        current_tick: int,
        state: StateContainer,
    ) -> list[StateChange]:
        if not state.has_slice("events"):
            return []
        raw_objectives = quest_payload.get("objectives")
        if not isinstance(raw_objectives, list):
            return []
        changes: list[StateChange] = []
        for idx, objective in enumerate(raw_objectives):
            if not isinstance(objective, Mapping):
                continue
            obj_type = coerce_non_empty_string(objective.get("type"))
            if obj_type is None:
                continue
            if _coerce_optional_bool(objective.get("optional")):
                continue
            condition_type = self._objective_to_condition_type(obj_type.lower())
            if condition_type is None:
                continue
            params = self._objective_target_to_params(condition_type, objective.get("target"))
            if params is None:
                continue
            event_id = f"dq_{quest_id}_obj_{idx}"
            if state.events.get_event(event_id) is not None:
                continue
            changes.append(
                StateChange(
                    "events",
                    "set",
                    f"active_events.{event_id}",
                    {
                        "id": event_id,
                        "event_id": event_id,
                        "state": "dormant",
                        "status": "dormant",
                        "conditions": [{"type": condition_type, "params": params}],
                        "on_trigger": [{
                            "type": "complete_objective",
                            "params": {"quest_id": quest_id, "objective_index": idx},
                        }],
                        "source": "narrative_planner",
                        "created_at_tick": current_tick,
                    },
                )
            )
        return changes

    @staticmethod
    def _objective_to_condition_type(obj_type: str) -> str | None:
        mapping = {
            "reach_location": "location_visited",
            "location_visited": "location_visited",
            "talk_to": "npc_talked",
            "collect": "item_obtained",
            "kill": "kill_count",
        }
        return mapping.get(obj_type)

    @staticmethod
    def _objective_target_to_params(
        condition_type: str,
        target: Any,
    ) -> dict[str, Any] | None:
        if isinstance(target, Mapping):
            if condition_type in {"item_obtained", "kill_count", "talk_to", "npc_talked"}:
                return {str(key): value for key, value in target.items()}
            if condition_type in {"location_entered", "location_visited"}:
                area_id = coerce_non_empty_string(target.get("area_id"))
                location_id = coerce_non_empty_string(target.get("location_id"))
                sub_location_id = coerce_non_empty_string(target.get("sub_location_id"))
                params = {
                    "area_id": area_id,
                    "location_id": location_id,
                    "sub_location_id": sub_location_id,
                }
                if area_id is None and location_id is None:
                    # A8c: fall back to passthrough instead of discarding params
                    return {str(key): value for key, value in target.items()}
                return params
            return None
        if condition_type == "item_obtained" and isinstance(target, str):
            return {"item_id": target}
        if condition_type == "kill_count" and isinstance(target, str):
            return {"monster_type": target}
        if condition_type in {"talk_to", "npc_talked"} and isinstance(target, str):
            return {"npc_id": target}
        if condition_type in {"location_entered", "location_visited"} and isinstance(target, str):
            return {"area_id": target}
        return None


class PlannerNpcHandler(StaticCommandHandler):
    COMMAND_TYPES = (
        "planner_direct_npc",
        "planner_spawn_quest_npc",
        "planner_despawn_quest_npc",
        "planner_prune_npc_directives",
        "planner_assign_capability",
        "planner_revoke_capability",
        "planner_assign_service",
        "planner_revoke_service",
    )

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        source_gate = _planner_source_gate(cmd)
        if not source_gate.ok:
            return source_gate
        if not state.has_slice("narrative_plan"):
            return ValidationResult(ok=False, reason="narrative_plan slice is required")
        if cmd.type in {"planner_spawn_quest_npc", "planner_despawn_quest_npc"} and not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice is required")
        if cmd.type == "planner_direct_npc":
            npc_id = coerce_non_empty_string(cmd.params.get("npc_id"))
            directive = cmd.params.get("directive")
            if npc_id is None:
                return ValidationResult(ok=False, reason="npc_id must be a non-empty string")
            if not isinstance(directive, Mapping) or not directive:
                return ValidationResult(ok=False, reason="directive must be a non-empty mapping")
            return ValidationResult(ok=True)
        if cmd.type == "planner_spawn_quest_npc":
            area_id = coerce_non_empty_string(cmd.params.get("area_id"))
            if area_id is None:
                return ValidationResult(ok=False, reason="area_id must be a non-empty string")
            if area_id not in state.areas.areas:
                return ValidationResult(ok=False, reason=f"unknown area_id: {area_id}")
            location_id = coerce_non_empty_string(cmd.params.get("location_id"))
            room_id = coerce_non_empty_string(cmd.params.get("room_id"))
            if not _planner_location_exists(state, world, area_id, location_id):
                return ValidationResult(
                    ok=False,
                    reason=f"unknown location_id: {location_id}",
                )
            if room_id is not None and location_id is None:
                return ValidationResult(ok=False, reason="room_id requires location_id")
            if room_id is not None and not room_exists(state, world, area_id, location_id, room_id):
                return ValidationResult(ok=False, reason=f"unknown room_id: {room_id}")
            return ValidationResult(ok=True)
        if cmd.type == "planner_despawn_quest_npc":
            npc_id = coerce_non_empty_string(cmd.params.get("npc_id"))
            if npc_id is None:
                return ValidationResult(ok=False, reason="npc_id must be a non-empty string")
            return ValidationResult(ok=True)
        if cmd.type == "planner_prune_npc_directives":
            current_tick = coerce_int(cmd.params.get("current_tick"))
            if current_tick is None:
                return ValidationResult(ok=False, reason="current_tick must be an integer")
            return ValidationResult(ok=True)
        if cmd.type == "planner_assign_capability":
            npc_id = coerce_non_empty_string(cmd.params.get("npc_id"))
            if npc_id is None:
                return ValidationResult(ok=False, reason="npc_id must be a non-empty string")
            capability_id = coerce_non_empty_string(cmd.params.get("capability_id"))
            if capability_id is None:
                return ValidationResult(ok=False, reason="capability_id must be a non-empty string")
            instruction = coerce_non_empty_string(cmd.params.get("instruction"))
            if instruction is None:
                return ValidationResult(ok=False, reason="instruction must be a non-empty string")
            return ValidationResult(ok=True)
        if cmd.type == "planner_revoke_capability":
            npc_id = coerce_non_empty_string(cmd.params.get("npc_id"))
            if npc_id is None:
                return ValidationResult(ok=False, reason="npc_id must be a non-empty string")
            capability_id = coerce_non_empty_string(cmd.params.get("capability_id"))
            if capability_id is None:
                return ValidationResult(ok=False, reason="capability_id must be a non-empty string")
            return ValidationResult(ok=True)
        if cmd.type == "planner_assign_service":
            npc_id = coerce_non_empty_string(cmd.params.get("npc_id"))
            if npc_id is None:
                return ValidationResult(ok=False, reason="npc_id must be a non-empty string")
            service_id = coerce_non_empty_string(cmd.params.get("service_id"))
            if service_id is None:
                return ValidationResult(ok=False, reason="service_id must be a non-empty string")
            label = coerce_non_empty_string(cmd.params.get("label"))
            if label is None:
                return ValidationResult(ok=False, reason="label must be a non-empty string")
            return ValidationResult(ok=True)
        if cmd.type == "planner_revoke_service":
            npc_id = coerce_non_empty_string(cmd.params.get("npc_id"))
            if npc_id is None:
                return ValidationResult(ok=False, reason="npc_id must be a non-empty string")
            service_id = coerce_non_empty_string(cmd.params.get("service_id"))
            if service_id is None:
                return ValidationResult(ok=False, reason="service_id must be a non-empty string")
            return ValidationResult(ok=True)
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
        if cmd.type == "planner_direct_npc":
            return self._compute_direct_npc(cmd)
        if cmd.type == "planner_spawn_quest_npc":
            return self._compute_spawn_quest_npc(cmd, state, world)
        if cmd.type == "planner_despawn_quest_npc":
            return self._compute_despawn_quest_npc(cmd, state)
        if cmd.type == "planner_prune_npc_directives":
            return self._compute_prune_npc_directives(cmd, state)
        if cmd.type == "planner_assign_capability":
            return self._compute_assign_capability(cmd)
        if cmd.type == "planner_revoke_capability":
            return self._compute_revoke_capability(cmd)
        if cmd.type == "planner_assign_service":
            return self._compute_assign_service(cmd)
        if cmd.type == "planner_revoke_service":
            return self._compute_revoke_service(cmd)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _compute_direct_npc(self, cmd: Command) -> ExecuteResult:
        current_tick = coerce_int(cmd.params.get("current_tick")) or 0
        npc_id = coerce_non_empty_string(cmd.params.get("npc_id")) or ""
        raw_expiry = cmd.params.get("expires_at_tick")
        expires_at_tick = current_tick + 24 if raw_expiry is None else (coerce_int(raw_expiry) or -1)
        if expires_at_tick < 0:
            return ExecuteResult.error("expires_at_tick must be an integer")
        # 3c: clamp so that expires_at_tick is never in the past (LLM may supply
        # a negative value or an absolute tick that is less than current_tick).
        if expires_at_tick < current_tick:
            expires_at_tick = current_tick
        priority = _string_or_empty(cmd.params.get("priority")).strip().lower() or "medium"
        if priority not in {"high", "medium", "low"}:
            return ExecuteResult.error("priority must be one of high/medium/low")
        stored_directive = {
            "npc_id": npc_id,
            "directive": dict(cmd.params.get("directive", {})),
            "priority": priority,
            "issued_at_tick": current_tick,
            "expires_at_tick": expires_at_tick,
            "linked_quest_id": coerce_non_empty_string(cmd.params.get("linked_quest_id")),
            "source": "narrative_planner",
            "consumed": False,
        }
        return _planner_success(
            cmd.type,
            changes=[StateChange("narrative_plan", "add", "npc_directives", stored_directive)],
            metadata={"npc_id": npc_id, "stored_directive": stored_directive},
        )

    def _compute_spawn_quest_npc(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        current_tick = coerce_int(cmd.params.get("current_tick")) or 0
        area_id = coerce_non_empty_string(cmd.params.get("area_id")) or ""
        npc_id = coerce_non_empty_string(cmd.params.get("npc_id"))
        if npc_id is None:
            npc_id = self._find_available_npc_id(state, f"temp_npc_{current_tick}")
        elif state.areas.find_npc_area(npc_id) is not None:
            return ExecuteResult.error(f"npc already present: {npc_id}")
        location_id = coerce_non_empty_string(cmd.params.get("location_id"))
        requested_room_id = coerce_non_empty_string(cmd.params.get("room_id"))
        resolved_room_id = resolve_npc_room(
            state,
            world,
            area_id=area_id,
            location_id=location_id,
            npc_id=npc_id,
            requested_room_id=requested_room_id,
        )
        # 3a: area slice enforces that room_id requires location_id.  If
        # resolve_npc_room returned a room but location_id is absent, clear the
        # room so the StateChange is always consistent.
        if resolved_room_id is not None and not location_id:
            resolved_room_id = None
        name = _string_or_empty(cmd.params.get("name")) or npc_id
        appearance = _string_or_empty(cmd.params.get("appearance"))
        personality = _string_or_empty(cmd.params.get("personality"))
        dialogue_hook = _string_or_empty(cmd.params.get("dialogue_hook"))
        role = _string_or_empty(cmd.params.get("role"))
        tags = _normalize_string_list(cmd.params.get("tags"))
        metadata = _normalize_mapping(cmd.params.get("metadata"))
        linked_quest_id = (
            coerce_non_empty_string(cmd.params.get("linked_quest_id"))
            or coerce_non_empty_string(metadata.get("linked_quest_id"))
        )
        raw_despawn_tick = cmd.params.get("despawn_tick")
        if raw_despawn_tick is None:
            raw_despawn_tick = metadata.get("despawn_tick")
        despawn_tick = coerce_int(raw_despawn_tick) if raw_despawn_tick is not None else None
        if despawn_tick is None:
            raw_despawn_in_ticks = cmd.params.get("despawn_in_ticks")
            if raw_despawn_in_ticks is None:
                raw_despawn_in_ticks = metadata.get("despawn_in_ticks")
            despawn_in_ticks = coerce_int(raw_despawn_in_ticks)
            if despawn_in_ticks is None:
                despawn_in_ticks = 24
            despawn_tick = current_tick + max(0, despawn_in_ticks)
        npc_profile = {
            "npc_id": npc_id,
            "name": name,
            "appearance": appearance,
            "personality": personality,
            "dialogue_hook": dialogue_hook,
            "description": _string_or_empty(cmd.params.get("description")),
            "role": role,
            "tags": tags,
            "linked_quest_id": linked_quest_id,
            "area_id": area_id,
            "location_id": location_id,
            "room_id": resolved_room_id,
            "despawn_tick": despawn_tick,
            "issued_at_tick": current_tick,
        }
        history_entry = {
            "kind": "spawn_quest_npc",
            "npc_id": npc_id,
            "name": name,
            "appearance": appearance,
            "personality": personality,
            "dialogue_hook": dialogue_hook,
            "tags": tags,
            "linked_quest_id": linked_quest_id,
            "role": role,
            "location_id": location_id,
            "room_id": resolved_room_id,
            "area_id": area_id,
            "issued_at_tick": current_tick,
            "despawn_tick": despawn_tick,
        }
        directive = {
            "npc_id": npc_id,
            "directive": {
                "kind": "spawn_quest_npc",
                "role": role,
                "description": _string_or_empty(cmd.params.get("description")),
                "personality": personality,
                "dialogue_hook": dialogue_hook,
                "name": name,
                "area_id": area_id,
            },
            "issued_at_tick": current_tick,
            "source": "narrative_planner",
        }
        return _planner_success(
            cmd.type,
            changes=[
                StateChange("narrative_plan", "add", "quest_history", history_entry),
                StateChange("narrative_plan", "set", f"temporary_npcs.{npc_id}", npc_profile),
                StateChange(
                    "areas",
                    "set",
                    f"npc_presence.{npc_id}",
                    {
                        "area_id": area_id,
                        "location_id": location_id,
                        "room_id": resolved_room_id,
                        "source": "planner",
                    },
                ),
                StateChange("narrative_plan", "add", "npc_directives", directive),
            ],
            metadata={"npc_id": npc_id, "directive": directive, "npc_profile": npc_profile},
        )

    def _compute_despawn_quest_npc(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        npc_id = coerce_non_empty_string(cmd.params.get("npc_id")) or ""
        changes: list[StateChange] = []
        if state.areas.find_npc_area(npc_id) is not None:
            changes.append(StateChange("areas", "remove", f"npc_presence.{npc_id}", None))
        if state.narrative_plan.get_temporary_npc(npc_id) is not None:
            changes.append(StateChange("narrative_plan", "remove", f"temporary_npcs.{npc_id}", None))
        return _planner_success(
            cmd.type,
            changes=changes,
            metadata={"npc_id": npc_id, "despawned": bool(changes)},
        )

    def _compute_prune_npc_directives(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        current_tick = coerce_int(cmd.params.get("current_tick")) or 0
        before = list(state.narrative_plan.npc_directives)
        after = [
            dict(item)
            for item in before
            if not item.get("consumed", False)
            and (coerce_int(item.get("expires_at_tick")) or current_tick + 1) >= current_tick
        ]
        pruned_count = len(before) - len(after)
        changes: list[StateChange] = []
        if pruned_count > 0:
            changes.append(StateChange("narrative_plan", "set", "npc_directives", after))

        # Prune expired capabilities (direct slice mutation is a controlled exception)
        capabilities_pruned = 0
        if state.has_slice("narrative_plan"):
            capabilities_pruned = state.narrative_plan.prune_expired_capabilities(current_tick)

        metadata: dict[str, Any] = {"pruned_count": pruned_count}
        if capabilities_pruned > 0:
            metadata["capabilities_pruned"] = capabilities_pruned
        return _planner_success(
            cmd.type,
            changes=changes,
            metadata=metadata,
        )

    def _compute_assign_capability(self, cmd: Command) -> ExecuteResult:
        current_tick = coerce_int(cmd.params.get("current_tick")) or 0
        npc_id = coerce_non_empty_string(cmd.params.get("npc_id")) or ""
        capability_id = coerce_non_empty_string(cmd.params.get("capability_id")) or ""
        instruction = coerce_non_empty_string(cmd.params.get("instruction")) or ""
        functional = _string_or_empty(cmd.params.get("functional"))
        raw_fp = cmd.params.get("functional_params")
        functional_params = _normalize_mapping(raw_fp) if raw_fp is not None else {}
        raw_expiry = cmd.params.get("expiry_ticks")
        if raw_expiry is not None:
            expiry_tick = (coerce_int(raw_expiry) or 0) + current_tick
        else:
            expiry_tick = 0
        cap_dict = {
            "capability_id": capability_id,
            "npc_id": npc_id,
            "instruction": instruction,
            "functional": functional,
            "functional_params": functional_params,
            "assigned_tick": current_tick,
            "expiry_tick": expiry_tick,
            "source": "planner",
        }
        return _planner_success(
            cmd.type,
            changes=[StateChange("narrative_plan", "set", "npc_capabilities.assign", cap_dict)],
            metadata={"npc_id": npc_id, "capability_id": capability_id},
        )

    def _compute_revoke_capability(self, cmd: Command) -> ExecuteResult:
        npc_id = coerce_non_empty_string(cmd.params.get("npc_id")) or ""
        capability_id = coerce_non_empty_string(cmd.params.get("capability_id")) or ""
        revoke_dict = {"npc_id": npc_id, "capability_id": capability_id}
        return _planner_success(
            cmd.type,
            changes=[StateChange("narrative_plan", "remove", "npc_capabilities.revoke", revoke_dict)],
            metadata={"npc_id": npc_id, "capability_id": capability_id},
        )

    def _compute_assign_service(self, cmd: Command) -> ExecuteResult:
        current_tick = coerce_int(cmd.params.get("current_tick")) or 0
        npc_id = coerce_non_empty_string(cmd.params.get("npc_id")) or ""
        service_id = coerce_non_empty_string(cmd.params.get("service_id")) or ""
        label = coerce_non_empty_string(cmd.params.get("label")) or ""
        raw_price = cmd.params.get("price")
        price = (coerce_int(raw_price) or 0) if raw_price is not None else 0
        if price < 0:
            price = 0
        raw_effects = cmd.params.get("effects")
        effects: list[dict] = []
        if isinstance(raw_effects, list):
            effects = [dict(atom) for atom in raw_effects if isinstance(atom, dict)]
        raw_pre = cmd.params.get("preconditions")
        preconditions: dict = _normalize_mapping(raw_pre) if raw_pre is not None else {}
        notes = _string_or_empty(cmd.params.get("notes"))
        raw_expiry = cmd.params.get("expiry_ticks")
        if raw_expiry is not None:
            expiry_tick = (coerce_int(raw_expiry) or 0) + current_tick
        else:
            expiry_tick = 0
        one_shot = _coerce_optional_bool(cmd.params.get("one_shot"))
        svc_dict = {
            "service_id": service_id,
            "npc_id": npc_id,
            "label": label,
            "price": price,
            "effects": effects,
            "preconditions": preconditions,
            "notes": notes,
            "assigned_tick": current_tick,
            "expiry_tick": expiry_tick,
            "source": "planner",
            "one_shot": one_shot,
        }
        return _planner_success(
            cmd.type,
            changes=[StateChange("narrative_plan", "set", "npc_services.assign", svc_dict)],
            metadata={"npc_id": npc_id, "service_id": service_id},
        )

    def _compute_revoke_service(self, cmd: Command) -> ExecuteResult:
        npc_id = coerce_non_empty_string(cmd.params.get("npc_id")) or ""
        service_id = coerce_non_empty_string(cmd.params.get("service_id")) or ""
        revoke_dict = {"npc_id": npc_id, "service_id": service_id}
        return _planner_success(
            cmd.type,
            changes=[StateChange("narrative_plan", "remove", "npc_services.revoke", revoke_dict)],
            metadata={"npc_id": npc_id, "service_id": service_id},
        )

    @staticmethod
    def _find_available_npc_id(state: StateContainer, seed_id: str, max_attempts: int = 20) -> str:
        base = coerce_non_empty_string(seed_id) or "temp_npc"
        candidate = base
        for index in range(max_attempts):
            if state.areas.find_npc_area(candidate) is None and state.narrative_plan.get_temporary_npc(candidate) is None:
                return candidate
            candidate = f"{base}_{index + 1}"
        return f"{base}_{max_attempts + 1}"


class PlannerWorldHandler(StaticCommandHandler):
    COMMAND_TYPES = (
        "planner_plant_environmental",
        "planner_fill_area",
        "planner_fill_location",
        "planner_plant_encounter",
        "planner_discover_room",
        "planner_fill_room",
    )

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        source_gate = _planner_source_gate(cmd)
        if not source_gate.ok:
            return source_gate
        if not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice is required")
        area_id = coerce_non_empty_string(cmd.params.get("area_id"))
        if area_id is None:
            return ValidationResult(ok=False, reason="area_id must be a non-empty string")
        if area_id not in state.areas.areas:
            return ValidationResult(ok=False, reason=f"unknown area_id: {area_id}")
        if cmd.type == "planner_plant_encounter":
            sub_area_id = coerce_non_empty_string(cmd.params.get("sub_area_id"))
            monster_ids = cmd.params.get("monster_ids")
            if sub_area_id is None:
                return ValidationResult(ok=False, reason="sub_area_id must be a non-empty string")
            if not isinstance(monster_ids, list) or not monster_ids:
                return ValidationResult(ok=False, reason="monster_ids must be a non-empty list")
        if cmd.type == "planner_fill_area":
            conflict_reason = self._validate_fill_area_target(area_id, cmd.params, world)
            if conflict_reason is not None:
                return ValidationResult(ok=False, reason=conflict_reason)
        if cmd.type == "planner_discover_room":
            location_id = coerce_non_empty_string(cmd.params.get("location_id"))
            room_id = coerce_non_empty_string(cmd.params.get("room_id"))
            if location_id is None:
                return ValidationResult(ok=False, reason="location_id must be a non-empty string")
            if room_id is None:
                return ValidationResult(ok=False, reason="room_id must be a non-empty string")
            if not world.has_registry("maps"):
                return ValidationResult(ok=False, reason="maps registry is required")
            sub_loc = world.maps.get_sub_location(area_id, location_id)
            if sub_loc is None:
                return ValidationResult(ok=False, reason=f"unknown sub_location '{location_id}'")
            room_template = sub_loc.rooms.get(room_id)
            if room_template is None:
                return ValidationResult(ok=False, reason=f"unknown room '{room_id}' in sub_location '{location_id}'")
            if not getattr(room_template, "discoverable", False):
                return ValidationResult(ok=False, reason=f"room '{room_id}' is not discoverable")
        if cmd.type == "planner_fill_room":
            location_id = coerce_non_empty_string(cmd.params.get("location_id"))
            room_id = coerce_non_empty_string(cmd.params.get("room_id"))
            name = coerce_non_empty_string(cmd.params.get("name"))
            if location_id is None:
                return ValidationResult(ok=False, reason="location_id must be a non-empty string")
            if room_id is None:
                return ValidationResult(ok=False, reason="room_id must be a non-empty string")
            if name is None:
                return ValidationResult(ok=False, reason="name must be a non-empty string")
            # Check capacity: max 5 dynamic rooms per sub_location
            if state.areas.count_dynamic_rooms(area_id, location_id) >= 5:
                return ValidationResult(ok=False, reason=f"dynamic room capacity exceeded for '{location_id}'")
            # Check no duplicate room_id (static + dynamic)
            if world.has_registry("maps"):
                sub_loc = world.maps.get_sub_location(area_id, location_id)
                if sub_loc is not None and room_id in sub_loc.rooms:
                    return ValidationResult(ok=False, reason=f"room_id '{room_id}' already exists as a static room")
            existing = state.areas.list_dynamic_rooms(area_id, location_id)
            if any(r.get("room_id") == room_id for r in existing):
                return ValidationResult(ok=False, reason=f"dynamic room_id '{room_id}' already exists")
        if cmd.type == "planner_fill_location":
            location_id = coerce_non_empty_string(cmd.params.get("location_id"))
            if location_id is None:
                return ValidationResult(ok=False, reason="location_id must be a non-empty string")
            if not world.has_registry("maps"):
                return ValidationResult(ok=False, reason="maps registry is required")
            sub_loc = world.maps.get_sub_location(area_id, location_id)
            if sub_loc is None:
                return ValidationResult(ok=False, reason=f"unknown sub_location '{location_id}'")
            room_id = coerce_non_empty_string(cmd.params.get("room_id"))
            if room_id is not None:
                static_room = sub_loc.rooms.get(room_id)
                has_dynamic_room = any(
                    str(room.get("room_id", "")).strip() == room_id
                    for room in state.areas.list_dynamic_rooms(area_id, location_id)
                )
                if static_room is None and not has_dynamic_room:
                    return ValidationResult(
                        ok=False,
                        reason=f"unknown room '{room_id}' in sub_location '{location_id}'",
                    )
            interactables = _normalize_interactable_dict_list(cmd.params.get("interactables"))
            if not interactables:
                return ValidationResult(ok=False, reason="interactables must be a non-empty list")
            existing_count = state.areas.count_scoped_interactable_overlays(
                area_id,
                location_id,
                room_id,
            )
            existing_ids = {
                str(item.get("id", "")).strip()
                for item in state.areas.list_scoped_interactable_overlays(area_id, location_id, room_id)
            }
            new_ids = {
                str(item.get("id", "")).strip()
                for item in interactables
                if str(item.get("id", "")).strip()
            }
            projected_count = existing_count + len(new_ids - existing_ids)
            if projected_count > 4:
                return ValidationResult(ok=False, reason="scene interactable overlay capacity exceeded")
        return ValidationResult(ok=True)

    def compute(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        validation = self.validate(cmd, state, world)
        if not validation.ok:
            return ExecuteResult.error(validation.reason or "validation failed")
        if cmd.type == "planner_plant_environmental":
            return self._compute_plant_environmental(cmd, state)
        if cmd.type == "planner_fill_area":
            return self._compute_fill_area(cmd, state)
        if cmd.type == "planner_fill_location":
            return self._compute_fill_location(cmd, state)
        if cmd.type == "planner_plant_encounter":
            return self._compute_plant_encounter(cmd)
        if cmd.type == "planner_discover_room":
            return self._compute_discover_room(cmd)
        if cmd.type == "planner_fill_room":
            return self._compute_fill_room(cmd)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _validate_fill_area_target(
        self,
        area_id: str,
        params: Mapping[str, Any],
        world: WorldInstance,
    ) -> str | None:
        sub_area_id = coerce_non_empty_string(params.get("id"))
        if sub_area_id is None:
            return None
        if world.has_registry("maps"):
            sub_loc = world.maps.get_sub_location(area_id, sub_area_id)
            if sub_loc is not None:
                return f"fill_area duplicates existing sub_location '{sub_area_id}'"
            area_template = world.maps.get(area_id)
            if area_template is not None:
                for existing_sub_loc in area_template.sub_locations.values():
                    if sub_area_id in getattr(existing_sub_loc, "rooms", {}):
                        return f"fill_area duplicates existing room '{sub_area_id}'"
        if sub_area_id in canonical_facility_ids(area_id):
            return f"fill_area duplicates canonical facility '{sub_area_id}'"
        return None

    def _compute_plant_environmental(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        # For plant_environmental, "description" is used as the sub-area label
        # (label_fallback_key="description"). Validate that it looks like a
        # place name rather than a narrative sentence.
        label = str(cmd.params.get("label") or cmd.params.get("description") or "").strip()
        if label:
            if len(label) > 30:
                return ExecuteResult.error("label_too_long")
            if any(ch in label for ch in ("。", "，", "！", ".", ",", "!")):
                return ExecuteResult.error("label_contains_punctuation")
        return self._compute_sub_area_command(
            cmd,
            state,
            default_type="discovery",
            default_tier="temporary",
            default_expiry=12,
            change_type="plant_environmental",
            label_fallback_key="description",
            spec_overrides={
                "discovery_mode": coerce_non_empty_string(cmd.params.get("discovery_mode")) or "check",
                "discovery_dc": coerce_int(cmd.params.get("dc")) or 12,
                "linked_quest_id": coerce_non_empty_string(cmd.params.get("linked_quest_id")),
                "linked_milestone": coerce_non_empty_string(cmd.params.get("linked_milestone")),
            },
        )

    def _compute_fill_area(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        return self._compute_sub_area_command(
            cmd,
            state,
            default_type=coerce_non_empty_string(cmd.params.get("type")) or "visit",
            default_tier="permanent",
            default_expiry=-1,
            change_type="fill_area",
            label_fallback_key="label",
            spec_overrides={},
        )

    def _compute_fill_location(self, cmd: Command, state: StateContainer) -> ExecuteResult:
        area_id = coerce_non_empty_string(cmd.params.get("area_id")) or ""
        location_id = coerce_non_empty_string(cmd.params.get("location_id")) or ""
        room_id = coerce_non_empty_string(cmd.params.get("room_id"))
        # Auto-fill room_id when player is in the target location and has a room
        if room_id is None and state.has_slice("player"):
            player = state.player
            if (
                (player.current_area or "") == area_id
                and (player.current_location or "") == location_id
                and player.current_room
            ):
                room_id = player.current_room
        interactables = _normalize_interactable_dict_list(cmd.params.get("interactables"))
        scope_key = (
            f"{location_id}__{room_id}"
            if room_id is not None
            else location_id
        )
        # 2a: capacity double-check using the same upsert logic as AreaSlice.
        # Simulate what upsert_scoped_interactable_overlays would do so that
        # compute() returns ExecuteResult.error instead of letting the state
        # layer raise ValueError.
        _MAX_OVERLAY_ENTRIES = 4
        existing_overlays = state.areas.list_scoped_interactable_overlays(
            area_id, location_id, room_id
        )
        existing_by_id = {
            str(item.get("id", "")).strip()
            for item in existing_overlays
            if str(item.get("id", "")).strip()
        }
        simulated_count = len(existing_overlays)
        for item in interactables:
            item_id = str(item.get("id", "")).strip()
            if not item_id:
                continue
            if item_id not in existing_by_id:
                simulated_count += 1
                existing_by_id.add(item_id)
        if simulated_count > _MAX_OVERLAY_ENTRIES:
            return ExecuteResult.error("scene interactable overlay capacity exceeded")
        metadata = {
            "area_id": area_id,
            "location_id": location_id,
            "room_id": room_id,
            "scope_key": scope_key,
            "interactable_count": len(interactables),
        }
        return _planner_success(
            cmd.type,
            changes=[
                StateChange(
                    "areas",
                    "modify",
                    f"{area_id}.scoped_interactable_overlay.{scope_key}",
                    interactables,
                )
            ],
            metadata=metadata,
        )

    def _compute_sub_area_command(
        self,
        cmd: Command,
        state: StateContainer,
        *,
        default_type: str,
        default_tier: str,
        default_expiry: int,
        change_type: str,
        label_fallback_key: str,
        spec_overrides: Mapping[str, Any],
    ) -> ExecuteResult:
        area_id = coerce_non_empty_string(cmd.params.get("area_id")) or ""
        current_tick = coerce_int(cmd.params.get("current_tick")) or 0
        area_state = state.areas.areas.get(area_id)
        if area_state is None:
            return ExecuteResult.error(f"unknown area_id: {area_id}")
        temporary = [dict(item) for item in area_state.temporary_sub_areas]
        counts = state.areas.count_dynamic_sub_areas(area_id)
        tier = default_tier
        if default_tier == "permanent":
            if counts.get("permanent", 0) >= 8 or counts.get("total", 0) >= 15:
                return ExecuteResult.error("dynamic sub-area capacity exceeded")
        else:
            if counts.get("total", 0) >= 15:
                return ExecuteResult.error("dynamic sub-area capacity exceeded")
        sub_area_id = (
            coerce_non_empty_string(cmd.params.get("clue_id"))
            or coerce_non_empty_string(cmd.params.get("id"))
            or f"{'clue' if change_type == 'plant_environmental' else 'fill'}_{current_tick}"
        )
        expiry_ticks = coerce_int(cmd.params.get("expiry_ticks"))
        if expiry_ticks is None:
            expiry_ticks = default_expiry
        # 2b: normalize interactables and resident_npcs to lists defensively,
        # so that downstream code reading these fields never encounters non-list values.
        raw_interactables = _normalize_interactable_list(cmd.params.get("interactables"))
        raw_resident_npcs = _normalize_string_list(cmd.params.get("resident_npcs"))
        created = {
            "id": sub_area_id,
            "label": _string_or_empty(cmd.params.get(label_fallback_key)),
            "description": _string_or_empty(cmd.params.get("description")),
            "tags": _normalize_string_list(cmd.params.get("tags")),
            "type": default_type,
            "tier": tier,
            "discovery_mode": coerce_non_empty_string(spec_overrides.get("discovery_mode")) or "auto",
            "discovery_dc": coerce_int(spec_overrides.get("discovery_dc")) or 0,
            "hostile_config": cmd.params.get("hostile_config"),
            "interactables": raw_interactables if isinstance(raw_interactables, list) else [],
            "resident_npcs": raw_resident_npcs if isinstance(raw_resident_npcs, list) else [],
            "linked_quest_id": spec_overrides.get("linked_quest_id"),
            "linked_milestone": spec_overrides.get("linked_milestone"),
            "source": "narrative_planner",
            "created_at_tick": current_tick,
            "expiry": expiry_ticks,
            "status": "active",
        }
        temporary.append(created)
        # A4: for each resident NPC listed, emit an npc_presence StateChange
        extra_changes: list[StateChange] = []
        for npc_id in created.get("resident_npcs") or []:
            npc_id_str = coerce_non_empty_string(npc_id)
            if npc_id_str is None:
                continue
            extra_changes.append(
                StateChange(
                    "areas",
                    "set",
                    f"npc_presence.{npc_id_str}",
                    {
                        "area_id": area_id,
                        "location_id": sub_area_id,
                        "source": "planner",
                    },
                )
            )
        return _planner_success(
            cmd.type,
            changes=[StateChange("areas", "set", f"{area_id}.temporary_sub_areas", temporary), *extra_changes],
            metadata={
                "area_id": area_id,
                "sub_area_id": created["id"],
                "sub_area_label": created.get("label", ""),
                "change_type": change_type,
                "sub_area": created,
                "npc_presence_count": len(extra_changes),
            },
        )

    def _compute_plant_encounter(self, cmd: Command) -> ExecuteResult:
        area_id = coerce_non_empty_string(cmd.params.get("area_id"))
        # 2c: guard against empty area_id which would produce an invalid hostile_tracking entry
        if not area_id:
            return ExecuteResult.error("area_id must be a non-empty string")
        current_tick = coerce_int(cmd.params.get("current_tick")) or 0
        sub_area_id = coerce_non_empty_string(cmd.params.get("sub_area_id")) or ""
        surprise_modifier = coerce_int(cmd.params.get("surprise_modifier"))
        expiry_ticks = coerce_int(cmd.params.get("expiry_ticks"))
        entry: dict[str, Any] = {
            "area_id": area_id,
            "status": "planted",
            "combat_active": False,
            "cleared": False,
            "blocking": bool(cmd.params.get("blocking", True)),
            "monster_ids": list(cmd.params.get("monster_ids", [])),
            "threat_level": _string_or_empty(cmd.params.get("threat_level")) or "moderate",
            "surprise_modifier": surprise_modifier or 0,
            "map_category": coerce_non_empty_string(cmd.params.get("map_category")),
            "map_tags": list(cmd.params.get("map_tags", [])) if isinstance(cmd.params.get("map_tags"), list) else [],
            "description": _string_or_empty(cmd.params.get("description")),
            "one_shot": bool(cmd.params.get("one_shot", True)),
            "created_at_tick": current_tick,
            "expiry_ticks": expiry_ticks if expiry_ticks is not None else -1,
            "source": "narrative_planner",
        }
        return _planner_success(
            cmd.type,
            changes=[StateChange("areas", "modify", f"hostile_tracking.{sub_area_id}", entry)],
            metadata={"area_id": area_id, "sub_area_id": sub_area_id, "entry": entry},
        )

    def _compute_discover_room(self, cmd: Command) -> ExecuteResult:
        area_id = coerce_non_empty_string(cmd.params.get("area_id")) or ""
        location_id = coerce_non_empty_string(cmd.params.get("location_id")) or ""
        room_id = coerce_non_empty_string(cmd.params.get("room_id")) or ""
        return _planner_success(
            cmd.type,
            changes=[
                StateChange(
                    "areas",
                    "set",
                    f"{area_id}.discovered_room.{location_id}.{room_id}",
                    True,
                )
            ],
            metadata={
                "area_id": area_id,
                "location_id": location_id,
                "room_id": room_id,
            },
        )

    def _compute_fill_room(self, cmd: Command) -> ExecuteResult:
        area_id = coerce_non_empty_string(cmd.params.get("area_id")) or ""
        location_id = coerce_non_empty_string(cmd.params.get("location_id")) or ""
        room_id = coerce_non_empty_string(cmd.params.get("room_id")) or ""
        name = coerce_non_empty_string(cmd.params.get("name")) or ""
        current_tick = coerce_int(cmd.params.get("current_tick")) or 0
        description = _string_or_empty(cmd.params.get("description"))
        discoverable = _coerce_optional_bool(cmd.params.get("discoverable"))
        expiry_ticks = coerce_int(cmd.params.get("expiry_ticks"))
        room_dict: dict[str, Any] = {
            "sub_loc_id": location_id,
            "room_id": room_id,
            "name": name,
            "description": description,
            "discoverable": discoverable,
            "expiry": expiry_ticks if expiry_ticks is not None else -1,
            "created_at_tick": current_tick,
            "source": "narrative_planner",
        }
        # 2d: defensive isinstance check — room_dict must be a Mapping before
        # emitting a StateChange. It is always a dict here, but verify explicitly
        # to guard against future refactors that might substitute a different type.
        if not isinstance(room_dict, Mapping):
            return ExecuteResult.error("fill_room: room entry must be a mapping")
        return _planner_success(
            cmd.type,
            changes=[
                StateChange(
                    "areas",
                    "add",
                    f"{area_id}.dynamic_room",
                    room_dict,
                )
            ],
            metadata={
                "area_id": area_id,
                "location_id": location_id,
                "room_id": room_id,
                "name": name,
            },
        )


class PlannerItemHandler(StaticCommandHandler):
    COMMAND_TYPES = ("planner_curate_shop",)

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        source_gate = _planner_source_gate(cmd)
        if not source_gate.ok:
            return source_gate
        if cmd.type == "planner_curate_shop":
            if not state.has_slice("relations"):
                return ValidationResult(ok=False, reason="relations slice is required")
            if not world.has_registry("characters"):
                return ValidationResult(ok=False, reason="characters registry unavailable")
            npc_id = coerce_non_empty_string(cmd.params.get("npc_id"))
            if npc_id is None:
                return ValidationResult(ok=False, reason="npc_id must be a non-empty string")
            if world.characters.get(npc_id) is None:
                return ValidationResult(ok=False, reason=f"unknown npc_id: {npc_id}")
            raw_shop = state.relations.shop_states.get(npc_id)
            if not isinstance(raw_shop, Mapping):
                return ValidationResult(ok=False, reason=f"shop not initialized: {npc_id}")
            if world.has_registry("items"):
                for raw_entry in list(cmd.params.get("add_items", [])) + list(cmd.params.get("restock_items", [])):
                    if not isinstance(raw_entry, Mapping):
                        continue
                    item_id = coerce_non_empty_string(raw_entry.get("item_id"))
                    if item_id is None:
                        continue
                    if world.items.get(item_id) is None:
                        return ValidationResult(ok=False, reason=f"unknown item_id: {item_id}")
            return ValidationResult(ok=True)
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
        if cmd.type == "planner_curate_shop":
            return self._compute_curate_shop(cmd, state, world)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _compute_curate_shop(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        npc_id = coerce_non_empty_string(cmd.params.get("npc_id")) or ""
        raw_shop = state.relations.shop_states.get(npc_id)
        if not isinstance(raw_shop, Mapping):
            return ExecuteResult.error(f"shop not initialized: {npc_id}")
        item_registry = world.items if world.has_registry("items") else None
        shop_state = dict(raw_shop)
        current_stock: list[dict[str, Any]] = [
            dict(row)
            for row in shop_state.get("current_stock", [])
            if isinstance(row, Mapping)
        ]

        remove_ids: set[str] = set()
        for entry in cmd.params.get("remove_items", []):
            if isinstance(entry, str) and entry.strip():
                remove_ids.add(entry.strip())
        if remove_ids:
            current_stock = [row for row in current_stock if row.get("item_id") not in remove_ids]

        for entry in cmd.params.get("add_items", []):
            if not isinstance(entry, Mapping):
                continue
            item_id = coerce_non_empty_string(entry.get("item_id"))
            if item_id is None:
                continue
            base_price: int | None = None
            price_override = entry.get("price_override")
            if price_override is not None:
                base_price = coerce_int(price_override)
            if base_price is None and item_registry is not None:
                item_template = item_registry.get(item_id)
                if item_template is not None and item_template.base_price is not None:
                    base_price = item_template.base_price
            remaining = coerce_int(entry.get("count"))
            existing_row = next((row for row in current_stock if row.get("item_id") == item_id), None)
            if existing_row is not None:
                if base_price is not None:
                    existing_row["base_price"] = base_price
                if remaining is not None:
                    existing_remaining = coerce_int(existing_row.get("remaining")) or 0
                    existing_row["remaining"] = max(0, existing_remaining) + remaining
                existing_row["source"] = "curated"
                continue
            new_row: dict[str, Any] = {
                "item_id": item_id,
                "base_price": base_price if base_price is not None else 0,
                "source": "curated",
            }
            if remaining is not None:
                new_row["remaining"] = remaining
            current_stock.append(new_row)

        for entry in cmd.params.get("restock_items", []):
            if not isinstance(entry, Mapping):
                continue
            item_id = coerce_non_empty_string(entry.get("item_id"))
            if item_id is None:
                continue
            new_count = coerce_int(entry.get("count", 0))
            if new_count is None:
                continue
            for row in current_stock:
                if row.get("item_id") == item_id:
                    row["remaining"] = new_count
                    break

        shop_state["current_stock"] = current_stock
        return _planner_success(
            cmd.type,
            changes=[StateChange("relations", "modify", f"shop_states.{npc_id}", shop_state)],
            metadata={
                "npc_id": npc_id,
                "stock_count": len(current_stock),
                "current_stock": [dict(row) for row in current_stock],
            },
        )



class PlannerRuntimeHandler(StaticCommandHandler):
    COMMAND_TYPES = (
        "planner_escalate",
        "planner_set_pacing_frozen",
        "planner_expire_dynamic_quest",
        "planner_add_story_facts",
        "planner_commit_runtime_state",
        "planner_advance_milestone",
    )

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        source_gate = _planner_source_gate(cmd)
        if not source_gate.ok:
            return source_gate
        if not state.has_slice("narrative_plan"):
            return ValidationResult(ok=False, reason="narrative_plan slice is required")
        if cmd.type == "planner_escalate":
            delta = coerce_int(cmd.params.get("delta"))
            if delta is None or delta < -3 or delta > 3:
                return ValidationResult(ok=False, reason="delta must be an integer between -3 and 3")
            return ValidationResult(ok=True)
        if cmd.type == "planner_set_pacing_frozen":
            if not isinstance(cmd.params.get("frozen"), bool):
                return ValidationResult(ok=False, reason="frozen must be a bool")
            return ValidationResult(ok=True)
        if cmd.type == "planner_expire_dynamic_quest":
            if not state.has_slice("quests"):
                return ValidationResult(ok=False, reason="quests slice is required")
            quest_id = coerce_non_empty_string(cmd.params.get("quest_id"))
            if quest_id is None:
                return ValidationResult(ok=False, reason="quest_id must be a non-empty string")
            quest = state.quests.dynamic_quests.get(quest_id)
            if not isinstance(quest, Mapping):
                return ValidationResult(ok=False, reason=f"dynamic quest not found: {quest_id}")
            return ValidationResult(ok=True)
        if cmd.type == "planner_add_story_facts":
            facts = cmd.params.get("facts")
            if not isinstance(facts, list):
                return ValidationResult(ok=False, reason="facts must be a list")
            return ValidationResult(ok=True)
        if cmd.type == "planner_commit_runtime_state":
            trace = cmd.params.get("last_planner_replay_trace")
            if trace is not None and not isinstance(trace, Mapping):
                return ValidationResult(ok=False, reason="last_planner_replay_trace must be a mapping")
            return ValidationResult(ok=True)
        if cmd.type == "planner_advance_milestone":
            if not state.has_slice("quests"):
                return ValidationResult(ok=False, reason="quests slice is required")
            if not world.has_registry("quests"):
                return ValidationResult(ok=False, reason="quests registry is required")
            milestone_id = coerce_non_empty_string(cmd.params.get("milestone_id"))
            if milestone_id is None:
                return ValidationResult(ok=False, reason="milestone_id must be a non-empty string")
            milestone = world.quests.get_milestone(milestone_id)
            if milestone is None:
                return ValidationResult(ok=False, reason=f"milestone not found: {milestone_id}")
            to_state = coerce_non_empty_string(cmd.params.get("to_state")) or "COMPLETED"
            valid_states = {"COMPLETED", "FAILED", "AVAILABLE", "ACTIVE"}
            if to_state.upper() not in valid_states:
                return ValidationResult(ok=False, reason=f"invalid to_state: {to_state}")
            return ValidationResult(ok=True)
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
        if cmd.type == "planner_escalate":
            return self._compute_escalate(cmd, state)
        if cmd.type == "planner_set_pacing_frozen":
            return self._compute_set_pacing_frozen(cmd)
        if cmd.type == "planner_expire_dynamic_quest":
            return self._compute_expire_dynamic_quest(cmd, state)
        if cmd.type == "planner_add_story_facts":
            return self._compute_add_story_facts(cmd)
        if cmd.type == "planner_commit_runtime_state":
            return self._compute_commit_runtime_state(cmd, state)
        if cmd.type == "planner_advance_milestone":
            return self._compute_advance_milestone(cmd, state, world)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _compute_escalate(self, cmd: Command, state: StateContainer) -> ExecuteResult:
        delta = coerce_int(cmd.params.get("delta")) or 0
        new_level = max(0, state.narrative_plan.escalation_level + delta)
        changes: list[StateChange] = [
            StateChange("narrative_plan", "set", "escalation_level", new_level),
        ]
        if state.has_slice("flags"):
            changes.append(
                StateChange("flags", "set", "flags.narrative_escalation_level", new_level)
            )
        if state.has_slice("areas") and state.has_slice("player"):
            area_id = coerce_non_empty_string(state.player.current_area)
            if area_id is not None:
                danger_delta = 0.05 * delta
                if danger_delta:
                    changes.append(StateChange("areas", "add", f"{area_id}.danger_level", danger_delta))
        return _planner_success(
            cmd.type,
            changes=changes,
            metadata={"delta": delta, "escalation_level": new_level},
        )

    def _compute_set_pacing_frozen(self, cmd: Command) -> ExecuteResult:
        frozen = bool(cmd.params.get("frozen"))
        return _planner_success(
            cmd.type,
            changes=[StateChange("narrative_plan", "set", "pacing_frozen", frozen)],
            metadata={"pacing_frozen": frozen},
        )

    def _compute_expire_dynamic_quest(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        quest_id = coerce_non_empty_string(cmd.params.get("quest_id")) or ""
        current_tick = coerce_int(cmd.params.get("current_tick")) or 0
        quest = state.quests.dynamic_quests.get(quest_id)
        if not isinstance(quest, Mapping):
            return ExecuteResult.error(f"dynamic quest not found: {quest_id}")
        status_before = _string_or_empty(quest.get("status")).strip().lower()
        if status_before in {"completed", "retired", "expired"}:
            return ExecuteResult.error(f"dynamic quest already terminal: {status_before}")
        on_expire = coerce_non_empty_string(quest.get("on_expire")) or "ignore"
        on_expire = on_expire.strip().lower()
        if on_expire not in {"ignore", "escalate", "retire"}:
            on_expire = "ignore"
        status_after = "retired" if on_expire in {"retire", "escalate"} else "expired"
        changes: list[StateChange] = [
            StateChange("quests", "set", f"dynamic_quests.{quest_id}.status", status_after),
            StateChange(
                "narrative_plan",
                "add",
                "quest_history",
                {
                    "kind": "dynamic_quest_expired",
                    "quest_id": quest_id,
                    "tick": current_tick,
                    "on_expire": on_expire,
                    "status_before": status_before,
                    "status_after": status_after,
                },
            ),
        ]
        escalation_level = state.narrative_plan.escalation_level
        if on_expire == "escalate":
            escalation_level = max(0, escalation_level + 1)
            changes.append(StateChange("narrative_plan", "set", "escalation_level", escalation_level))
            if state.has_slice("flags"):
                changes.append(
                    StateChange(
                        "flags",
                        "set",
                        "flags.narrative_escalation_level",
                        escalation_level,
                    )
                )
            if state.has_slice("areas") and state.has_slice("player"):
                area_id = coerce_non_empty_string(state.player.current_area)
                if area_id is not None:
                    changes.append(StateChange("areas", "add", f"{area_id}.danger_level", 0.05))
        return _planner_success(
            cmd.type,
            changes=changes,
            metadata={
                "quest_id": quest_id,
                "on_expire": on_expire,
                "tick": current_tick,
                "status": status_after,
                "escalation_level": escalation_level,
            },
        )

    def _compute_add_story_facts(self, cmd: Command) -> ExecuteResult:
        facts = [
            dict(item)
            for item in cmd.params.get("facts", [])
            if isinstance(item, Mapping)
        ]
        if not facts:
            return _planner_success(cmd.type, changes=[], metadata={"story_fact_count": 0})
        return _planner_success(
            cmd.type,
            changes=[StateChange("narrative_plan", "add", "story_facts", facts)],
            metadata={"story_fact_count": len(facts)},
        )

    def _compute_commit_runtime_state(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        del state
        changes: list[StateChange] = []
        metadata: dict[str, Any] = {}
        if isinstance(cmd.params.get("last_planner_replay_trace"), Mapping):
            trace = dict(cmd.params["last_planner_replay_trace"])
            changes.append(StateChange("narrative_plan", "set", "last_planner_replay_trace", trace))
            metadata["trace_committed"] = True
        scalar_fields = (
            "last_run_tick",
            "ticks_since_milestone_progress",
            "strategy_notes",
            "next_scheduled_tick",
            "pacing_frozen",
        )
        for field_name in scalar_fields:
            if field_name not in cmd.params:
                continue
            changes.append(StateChange("narrative_plan", "set", field_name, cmd.params.get(field_name)))
        if "play_style_tags" in cmd.params and isinstance(cmd.params.get("play_style_tags"), list):
            changes.append(
                StateChange(
                    "narrative_plan",
                    "set",
                    "play_style_tags",
                    [str(item) for item in cmd.params.get("play_style_tags", [])],
                )
            )
        behavior_entry = cmd.params.get("behavior_entry")
        if isinstance(behavior_entry, Mapping):
            changes.append(StateChange("narrative_plan", "add", "behavior_window", dict(behavior_entry)))
        return _planner_success(cmd.type, changes=changes, metadata=metadata)

    def _compute_advance_milestone(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        """Advance a milestone state, enforcing an 80% success-condition threshold.

        The planner may request advancement only when at least 80% of the
        milestone's success_conditions are already satisfied.  This prevents the
        planner from bypassing meaningful player progress requirements while still
        allowing it to nudge a nearly-complete milestone over the finish line.
        """
        milestone_id = coerce_non_empty_string(cmd.params.get("milestone_id")) or ""
        to_state = (coerce_non_empty_string(cmd.params.get("to_state")) or "COMPLETED").upper()
        current_tick = coerce_int(cmd.params.get("current_tick")) or 0

        milestone = world.quests.get_milestone(milestone_id)
        if milestone is None:
            return ExecuteResult.error(f"milestone not found: {milestone_id}")

        # Evaluate success_conditions satisfaction ratio
        success_conditions = list(milestone.success_conditions) if milestone.success_conditions else []
        if success_conditions:
            evaluator = BasicEventConditionEvaluator()
            met_count = 0
            for cond in success_conditions:
                cond_dict = {"type": cond.type, "params": dict(cond.params)}
                is_met, _ = evaluator._condition_met(state, cond_dict)
                if is_met:
                    met_count += 1
            satisfaction_ratio = met_count / len(success_conditions)
            if satisfaction_ratio < 0.8:
                return ExecuteResult.error(
                    f"milestone_conditions_insufficient: "
                    f"{met_count}/{len(success_conditions)} conditions met "
                    f"({satisfaction_ratio:.0%} < 80%)"
                )

        changes: list[StateChange] = [
            StateChange(
                "quests",
                "set",
                f"milestone_states.{milestone_id}",
                {"state": to_state, "tick": current_tick},
            ),
        ]
        return _planner_success(
            cmd.type,
            changes=changes,
            metadata={
                "milestone_id": milestone_id,
                "to_state": to_state,
                "tick": current_tick,
                "conditions_total": len(success_conditions),
            },
        )
