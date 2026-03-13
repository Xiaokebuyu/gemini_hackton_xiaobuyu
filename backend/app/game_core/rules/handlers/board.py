"""Board handlers for quest bulletin interactions."""

from __future__ import annotations

from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.environment_access import find_current_interactable
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import get_non_empty_string, handler_success
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.rules.reward_utils import (
    build_reward_changes,
    build_reward_summary,
)
from app.game_core.scene_interactables import functional_type
from app.game_core.state import StateChange, StateContainer


def _build_reward_changes(
    state: StateContainer,
    rewards: Any,
) -> list[StateChange]:
    """Backward-compatible wrapper around the shared reward helper."""
    return build_reward_changes(state, rewards)


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

        if cmd.type in {"board_accept_quest", "board_complete_quest", "board_retire_quest"}:
            if get_non_empty_string(cmd.params, "quest_id") is None:
                return ValidationResult(ok=False, reason="quest_id required")

        board_entry, visible = find_current_interactable(state, world, board_id)
        if board_entry is None or not visible:
            return ValidationResult(ok=False, reason="board interactable not found")
        resolved_function = functional_type(board_entry.functional)
        if resolved_function not in {None, "board_browse"} and "quest_source" not in board_entry.tags:
            return ValidationResult(ok=False, reason="board interactable not found")

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

        # Phase 3 (P26-3-2): level wall — reject if player level below quest minimum
        min_level = int(quest_payload.get("min_level", 1))
        if min_level > 1 and state.has_slice("player"):
            if state.player.level < min_level:
                return ExecuteResult.error(
                    f"player level {state.player.level} below quest minimum {min_level}"
                )

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
        if current_status not in ("ready_to_report", "completed"):
            return ExecuteResult.error(
                f"quest not ready to report (current status: {current_status})"
            )

        updated = dict(quest_payload)
        updated["status"] = "completed"
        updated["rewards_claimed"] = True

        # Build reward changes (gold / xp / items via StateDelta)
        rewards = quest_payload.get("rewards", {})
        reward_changes = _build_reward_changes(state, rewards)

        # Summarise reward for SSE / metadata consumption
        reward_summary = build_reward_summary(rewards)

        return handler_success(
            "board",
            "board_complete_quest",
            changes=[
                StateChange(
                    "quests",
                    "modify",
                    f"dynamic_quests.{quest_id}",
                    updated,
                ),
                *reward_changes,
            ],
            metadata={
                "board_id": board_id,
                "quest_id": quest_id,
                "reward_summary": reward_summary,
            },
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
        # Phase 3 (P26-3-3): read player level once for level_locked computation
        player_level = 1
        if state.has_slice("player"):
            try:
                player_level = int(state.player.level)
            except (TypeError, ValueError):
                player_level = 1

        enriched: list[dict[str, Any]] = []
        for entry in entries:
            copy_entry = dict(entry)
            quest_id = str(copy_entry.get("quest_id", "")).strip()
            if state.has_slice("quests"):
                quest_payload = state.quests.get_dynamic_quest(quest_id)
                copy_entry["quest_status"] = (
                    quest_payload.get("status", "unknown") if quest_payload else "unknown"
                )
                # Expose min_level and level_locked for frontend display
                min_level = int(quest_payload.get("min_level", 1)) if quest_payload else 1
                copy_entry["min_level"] = min_level
                copy_entry["level_locked"] = min_level > 1 and player_level < min_level
            else:
                copy_entry["quest_status"] = "unknown"
                copy_entry["min_level"] = 1
                copy_entry["level_locked"] = False
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
