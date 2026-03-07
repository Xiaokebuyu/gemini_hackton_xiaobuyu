"""Board handlers for quest bulletin interactions."""

from __future__ import annotations

from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.content.registries.map_types import InteractableTemplate
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import get_non_empty_string, handler_success
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer


class BoardHandler(StaticCommandHandler):
    COMMAND_TYPES = (
        "browse_board",
        "board_accept_quest",
        "board_complete_quest",
        "board_retire_quest",
    )
    _TIME_COST = 1.0 / 6.0

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice required")
        if not world.has_registry("maps"):
            return ValidationResult(ok=False, reason="maps registry required")

        board_id = get_non_empty_string(cmd.params, "board_id")
        if board_id is None:
            return ValidationResult(ok=False, reason="board_id required")

        area_id = state.player.current_area
        location_id = state.player.current_location
        if not area_id:
            return ValidationResult(ok=False, reason="current area required")
        if not location_id:
            return ValidationResult(ok=False, reason="current sub-location required")

        if self._find_sub_location_interactable(
            world, area_id, str(location_id), board_id
        ) is None:
            return ValidationResult(ok=False, reason="board interactable not found")

        if cmd.type in {"board_accept_quest", "board_complete_quest", "board_retire_quest"}:
            if get_non_empty_string(cmd.params, "quest_id") is None:
                return ValidationResult(ok=False, reason="quest_id required")

        return ValidationResult(ok=True)

    def compute(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        if cmd.type == "browse_board":
            return self._compute_browse_board(cmd, state, world)
        if cmd.type == "board_accept_quest":
            return self._compute_board_accept_quest(cmd, state, world)
        if cmd.type == "board_complete_quest":
            return self._compute_board_complete_quest(cmd, state, world)
        if cmd.type == "board_retire_quest":
            return self._compute_board_retire_quest(cmd, state, world)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _compute_browse_board(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        reason = self.validate(cmd, state, world)
        if not reason.ok:
            return ExecuteResult.error(reason.reason or "validation failed")

        board_id = get_non_empty_string(cmd.params, "board_id") or ""
        entries = self._get_board_bulletins(state, board_id)
        enriched = self._enrich_with_quest_status(entries, state)

        return handler_success(
            "board",
            "browse_board",
            changes=[],
            metadata={"board_id": board_id, "entries": enriched},
            time_cost=self._TIME_COST,
        )

    def _compute_board_accept_quest(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        reason = self.validate(cmd, state, world)
        if not reason.ok:
            return ExecuteResult.error(reason.reason or "validation failed")

        board_id = get_non_empty_string(cmd.params, "board_id") or ""
        quest_id = get_non_empty_string(cmd.params, "quest_id") or ""

        if not self._board_contains_quest(state, board_id, quest_id):
            return ExecuteResult.error(f"quest not found on board: {quest_id}")
        if not state.has_slice("quests"):
            return ExecuteResult.error("quests slice required")

        quest_payload = state.quests.get_dynamic_quest(quest_id)
        if quest_payload is None:
            return ExecuteResult.error(f"dynamic quest not found: {quest_id}")

        current_status = str(quest_payload.get("status", "available")).lower()
        if current_status in {"active", "completed"}:
            return ExecuteResult.error(f"quest cannot be accepted in status: {current_status}")

        updated = dict(quest_payload)
        updated["status"] = "active"

        return handler_success(
            "board",
            "board_accept_quest",
            changes=[
                StateChange(
                    "quests",
                    "modify",
                    f"dynamic_quests.{quest_id}",
                    updated,
                )
            ],
            metadata={"board_id": board_id, "quest_id": quest_id},
            time_cost=self._TIME_COST,
        )

    def _compute_board_complete_quest(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        reason = self.validate(cmd, state, world)
        if not reason.ok:
            return ExecuteResult.error(reason.reason or "validation failed")

        board_id = get_non_empty_string(cmd.params, "board_id") or ""
        quest_id = get_non_empty_string(cmd.params, "quest_id") or ""

        if not self._board_contains_quest(state, board_id, quest_id):
            return ExecuteResult.error(f"quest not found on board: {quest_id}")
        if not state.has_slice("quests"):
            return ExecuteResult.error("quests slice required")

        quest_payload = state.quests.get_dynamic_quest(quest_id)
        if quest_payload is None:
            return ExecuteResult.error(f"dynamic quest not found: {quest_id}")

        current_status = str(quest_payload.get("status", "available")).lower()
        if current_status != "active":
            return ExecuteResult.error(
                f"quest cannot be completed in status: {current_status}"
            )

        updated = dict(quest_payload)
        updated["status"] = "completed"

        return handler_success(
            "board",
            "board_complete_quest",
            changes=[
                StateChange(
                    "quests",
                    "modify",
                    f"dynamic_quests.{quest_id}",
                    updated,
                )
            ],
            metadata={"board_id": board_id, "quest_id": quest_id},
            time_cost=self._TIME_COST,
        )

    def _compute_board_retire_quest(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        reason = self.validate(cmd, state, world)
        if not reason.ok:
            return ExecuteResult.error(reason.reason or "validation failed")

        board_id = get_non_empty_string(cmd.params, "board_id") or ""
        quest_id = get_non_empty_string(cmd.params, "quest_id") or ""

        if not self._board_contains_quest(state, board_id, quest_id):
            return ExecuteResult.error(f"quest not found on board: {quest_id}")
        if not state.has_slice("quests"):
            return ExecuteResult.error("quests slice required")

        quest_payload = state.quests.get_dynamic_quest(quest_id)
        if quest_payload is None:
            return ExecuteResult.error(f"dynamic quest not found: {quest_id}")

        updated = dict(quest_payload)
        updated["status"] = "retired"

        return handler_success(
            "board",
            "board_retire_quest",
            changes=[
                StateChange(
                    "quests",
                    "modify",
                    f"dynamic_quests.{quest_id}",
                    updated,
                )
            ],
            metadata={"board_id": board_id, "quest_id": quest_id},
            time_cost=self._TIME_COST,
        )

    def _find_sub_location_interactable(
        self,
        world: WorldInstance,
        area_id: str,
        location_id: str,
        interactable_id: str,
    ) -> InteractableTemplate | None:
        sub_loc = world.maps.get_sub_location(area_id, location_id)
        if sub_loc is None:
            return None
        for interactable in sub_loc.interactables:
            if interactable.id == interactable_id:
                return interactable
        return None

    def _get_board_bulletins(
        self,
        state: StateContainer,
        board_id: str,
    ) -> list[dict[str, Any]]:
        area_id = str(state.player.current_area or "").strip()
        if not area_id or not state.has_slice("areas"):
            return []
        return state.areas.get_board_bulletins(area_id, board_id)

    def _enrich_with_quest_status(
        self,
        entries: list[dict[str, Any]],
        state: StateContainer,
    ) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        for entry in entries:
            copy_entry = dict(entry)
            quest_id = str(copy_entry.get("quest_id", "")).strip()
            if state.has_slice("quests"):
                quest_payload = state.quests.get_dynamic_quest(quest_id)
                copy_entry["quest_status"] = (
                    quest_payload.get("status", "unknown") if quest_payload else "unknown"
                )
            else:
                copy_entry["quest_status"] = "unknown"
            enriched.append(copy_entry)
        return enriched

    def _board_contains_quest(
        self,
        state: StateContainer,
        board_id: str,
        quest_id: str,
    ) -> bool:
        if not board_id or not quest_id:
            return False
        for entry in self._get_board_bulletins(state, board_id):
            if str(entry.get("quest_id", "")).strip() == quest_id:
                return True
        return False
