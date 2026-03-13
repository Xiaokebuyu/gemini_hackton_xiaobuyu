"""Receptionist handlers for NPC-mediated quest acceptance / reporting."""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import get_non_empty_string, handler_success
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.rules.reward_utils import build_reward_summary
from app.game_core.state import StateChange, StateContainer
from app.game_core.rules.handlers.board import _build_reward_changes


class ReceptionistHandler(StaticCommandHandler):
    COMMAND_TYPES = ("receptionist_accept_quest", "receptionist_report_quest")
    _TIME_COST = 1.0 / 6.0

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice required")
        if not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice required")
        if not state.has_slice("quests"):
            return ValidationResult(ok=False, reason="quests slice required")
        if not world.has_registry("characters"):
            return ValidationResult(ok=False, reason="characters registry required")

        npc_id = get_non_empty_string(cmd.params, "npc_id")
        if npc_id is None:
            return ValidationResult(ok=False, reason="npc_id required")
        quest_id = get_non_empty_string(cmd.params, "quest_id")
        if quest_id is None:
            return ValidationResult(ok=False, reason="quest_id required")

        area_id = str(state.player.current_area or "").strip()
        location_id = str(state.player.current_location or "").strip()
        if not area_id:
            return ValidationResult(ok=False, reason="current area required")
        if not location_id:
            return ValidationResult(ok=False, reason="current sub-location required")

        npc_template = world.characters.get(npc_id)
        if npc_template is None:
            return ValidationResult(ok=False, reason="npc not found")
        tags = {
            str(tag).strip().lower()
            for tag in getattr(npc_template, "tags", [])
            if str(tag).strip()
        }
        if "receptionist" not in tags:
            return ValidationResult(ok=False, reason="npc is not a receptionist")

        area_npcs = self._get_area_npcs(state, world, area_id)
        if npc_id not in area_npcs:
            return ValidationResult(ok=False, reason="npc not in current area")
        if not self._is_colocated(area_npcs.get(npc_id), location_id):
            return ValidationResult(ok=False, reason="npc not in current location")

        quest_payload = state.quests.get_dynamic_quest(quest_id)
        if quest_payload is None:
            return ValidationResult(ok=False, reason=f"dynamic quest not found: {quest_id}")

        if cmd.type == "receptionist_accept_quest":
            board_offer = self._resolve_board_offer(
                state,
                area_id,
                quest_id,
                board_id_hint=get_non_empty_string(cmd.params, "board_id"),
            )
            if board_offer is None:
                # Fallback: quest not on board but exists as available dynamic quest
                current_status_check = str(quest_payload.get("status", "available")).lower()
                if current_status_check != "available":
                    return ValidationResult(ok=False, reason="quest not currently offerable")
            current_status = str(quest_payload.get("status", "available")).lower()
            if current_status in {"active", "completed"}:
                return ValidationResult(
                    ok=False,
                    reason=f"quest cannot be accepted in status: {current_status}",
                )
            return ValidationResult(ok=True)

        if cmd.type == "receptionist_report_quest":
            if not self._quest_requires_report(quest_payload):
                return ValidationResult(ok=False, reason="quest does not require reporting")
            current_status = str(quest_payload.get("status", "")).lower()
            if current_status != "completed":
                return ValidationResult(ok=False, reason="quest is not ready to report")
            if self._quest_reported(quest_payload):
                return ValidationResult(ok=False, reason="quest already reported")
            return ValidationResult(ok=True)

        return ValidationResult(ok=True)

    def compute(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        reason = self.validate(cmd, state, world)
        if not reason.ok:
            return ExecuteResult.error(reason.reason or "validation failed")

        if cmd.type == "receptionist_accept_quest":
            return self._compute_accept_quest(cmd, state)
        if cmd.type == "receptionist_report_quest":
            return self._compute_report_quest(cmd, state)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _compute_accept_quest(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        npc_id = get_non_empty_string(cmd.params, "npc_id") or ""
        quest_id = get_non_empty_string(cmd.params, "quest_id") or ""
        area_id = str(state.player.current_area or "").strip()
        board_id = self._resolve_board_offer(
            state,
            area_id,
            quest_id,
            board_id_hint=get_non_empty_string(cmd.params, "board_id"),
        )
        # board_id may be None if quest is available as a dynamic quest but not on board
        # (B-6 relaxed accept — validated already in validate())

        quest_payload = state.quests.get_dynamic_quest(quest_id)
        if quest_payload is None:
            return ExecuteResult.error(f"dynamic quest not found: {quest_id}")

        updated = dict(quest_payload)
        updated["status"] = "active"

        return handler_success(
            "receptionist",
            "receptionist_accept_quest",
            changes=[
                StateChange(
                    "quests",
                    "modify",
                    f"dynamic_quests.{quest_id}",
                    updated,
                )
            ],
            metadata={
                "npc_id": npc_id,
                "board_id": board_id,
                "quest_id": quest_id,
            },
            time_cost=self._TIME_COST,
        )

    def _compute_report_quest(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        npc_id = get_non_empty_string(cmd.params, "npc_id") or ""
        quest_id = get_non_empty_string(cmd.params, "quest_id") or ""

        quest_payload = state.quests.get_dynamic_quest(quest_id)
        rewards = quest_payload.get("rewards", {}) if quest_payload else {}
        rewards_already_claimed = bool(
            quest_payload.get("rewards_claimed") if quest_payload else False
        )

        reward_changes: list[StateChange] = []
        reward_summary: dict = {}
        if not rewards_already_claimed and isinstance(rewards, dict) and rewards:
            reward_changes = _build_reward_changes(state, rewards)
            reward_summary = build_reward_summary(rewards)

        changes: list[StateChange] = [
            StateChange(
                "quests",
                "set",
                f"dynamic_quests.{quest_id}.reported",
                True,
            ),
        ]
        if reward_changes:
            # Mark rewards claimed to prevent double-grant
            changes.append(
                StateChange(
                    "quests",
                    "set",
                    f"dynamic_quests.{quest_id}.rewards_claimed",
                    True,
                )
            )
            changes.extend(reward_changes)

        return handler_success(
            "receptionist",
            "receptionist_report_quest",
            changes=changes,
            metadata={
                "npc_id": npc_id,
                "quest_id": quest_id,
                "reported": True,
                "reward_summary": reward_summary,
                "rewards_claimed": bool(reward_changes),
            },
            time_cost=self._TIME_COST,
        )

    @staticmethod
    def _resolve_board_offer(
        state: StateContainer,
        area_id: str,
        quest_id: str,
        *,
        board_id_hint: str | None = None,
    ) -> str | None:
        all_boards = state.areas.get_all_board_bulletins(area_id)
        for board_id, entries in all_boards.items():
            if board_id_hint is not None and board_id != board_id_hint:
                continue
            for entry in entries:
                if str(entry.get("quest_id", "")).strip() == quest_id:
                    return board_id
        return None

    @staticmethod
    def _get_area_npcs(
        state: StateContainer,
        world: WorldInstance,
        area_id: str,
    ) -> dict[str, str | None]:
        normalized_area = area_id.strip()
        result: dict[str, str | None] = {}
        area_state = state.areas.areas.get(normalized_area)
        if area_state is not None:
            for raw_npc_id, raw_loc in area_state.npc_locations.items():
                npc_id = str(raw_npc_id).strip()
                if not npc_id:
                    continue
                loc_str = str(raw_loc).strip() if isinstance(raw_loc, str) else ""
                result[npc_id] = loc_str or None
        for template in world.characters.list_all():
            npc_id = template.id.strip()
            if not npc_id or npc_id in result:
                continue
            resolved_area = ""
            for field_name in ("area_id", "current_area"):
                raw_val = str(getattr(template, field_name, "") or "").strip()
                if raw_val:
                    resolved_area = raw_val
                    break
            if resolved_area != normalized_area:
                continue
            resolved_loc = ""
            for field_name in ("location_id", "current_location"):
                raw_val = str(getattr(template, field_name, "") or "").strip()
                if raw_val:
                    resolved_loc = raw_val
                    break
            result[npc_id] = resolved_loc or None
        return result

    @staticmethod
    def _is_colocated(
        npc_location: str | None,
        player_location: str | None,
    ) -> bool:
        return ((npc_location or "").strip() or None) == ((player_location or "").strip() or None)

    @staticmethod
    def _quest_requires_report(quest_payload: dict[str, object]) -> bool:
        metadata = quest_payload.get("metadata")
        metadata_map = metadata if isinstance(metadata, dict) else {}
        return ReceptionistHandler._coerce_bool(
            quest_payload.get("requires_report", metadata_map.get("requires_report"))
        )

    @staticmethod
    def _quest_reported(quest_payload: dict[str, object]) -> bool:
        metadata = quest_payload.get("metadata")
        metadata_map = metadata if isinstance(metadata, dict) else {}
        return ReceptionistHandler._coerce_bool(
            quest_payload.get("reported", metadata_map.get("reported"))
        )

    @staticmethod
    def _coerce_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        if isinstance(value, (int, float)):
            return bool(value)
        return False
