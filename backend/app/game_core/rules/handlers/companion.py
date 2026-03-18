"""Companion command handler."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import get_non_empty_string
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer, StateDelta


class CompanionHandler(StaticCommandHandler):
    """Rules-layer companion recruit / dismiss handler."""

    COMMAND_TYPES = (
        "recruit_companion",
        "dismiss_companion",
        "force_leave_companion",
        "restore_companion_after_combat",
    )

    MAX_PARTY_SIZE = 4

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if cmd.type == "recruit_companion":
            return self._validate_recruit(cmd, state, world)
        if cmd.type == "dismiss_companion":
            return self._validate_dismiss(cmd, state)
        if cmd.type == "force_leave_companion":
            return self._validate_force_leave(cmd, state)
        if cmd.type == "restore_companion_after_combat":
            return self._validate_restore_after_combat(cmd, state)
        return ValidationResult(ok=False, reason=f"unsupported command: {cmd.type}")

    def compute(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        if cmd.type == "recruit_companion":
            return self._compute_recruit(cmd, state, world)
        if cmd.type == "dismiss_companion":
            return self._compute_dismiss(cmd, state, world)
        if cmd.type == "force_leave_companion":
            return self._compute_dismiss(cmd, state, world)
        if cmd.type == "restore_companion_after_combat":
            return self._compute_restore_after_combat(cmd, state)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _validate_recruit(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        npc_id = self._require_npc_id(cmd.params)
        if npc_id is None:
            return ValidationResult(ok=False, reason="npc_id must be a non-empty string")
        if not state.has_slice("party"):
            return ValidationResult(ok=False, reason="no_party_slice")
        if not world.has_registry("characters"):
            return ValidationResult(ok=False, reason="no_character_registry")

        profile = world.characters.get(npc_id)
        if profile is None:
            return ValidationResult(ok=False, reason="npc_not_found")

        tags = [str(tag).strip().lower() for tag in getattr(profile, "tags", [])]
        if "recruitable" not in tags:
            return ValidationResult(ok=False, reason="not_recruitable")

        members = state.party.members
        if not isinstance(members, dict):
            return ValidationResult(ok=False, reason="no_party_slice")
        if npc_id in members:
            return ValidationResult(ok=False, reason="already_member")
        if len(members) >= self.MAX_PARTY_SIZE:
            return ValidationResult(ok=False, reason="party_full")

        if state.has_slice("relations"):
            stage = state.relations.get_stage(npc_id) or "stranger"
            if stage == "stranger":
                return ValidationResult(ok=False, reason="stranger")
            disposition = state.relations.get_disposition(npc_id)
            if (
                isinstance(disposition, dict)
                and int(disposition.get("approval", 0)) <= 0
            ):
                return ValidationResult(ok=False, reason="npc_refuses")

        return ValidationResult(ok=True)

    def _validate_dismiss(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        npc_id = self._require_npc_id(cmd.params)
        if npc_id is None:
            return ValidationResult(ok=False, reason="npc_id must be a non-empty string")
        if not state.has_slice("party"):
            return ValidationResult(ok=False, reason="no_party_slice")
        if npc_id not in (state.party.members or {}):
            return ValidationResult(ok=False, reason="not_member")
        return ValidationResult(ok=True)

    def _validate_force_leave(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        reason = get_non_empty_string(cmd.params, "reason")
        if reason is None:
            return ValidationResult(ok=False, reason="reason must be a non-empty string")
        return self._validate_dismiss(cmd, state)

    def _validate_restore_after_combat(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        if cmd.source not in {"engine", "system"}:
            return ValidationResult(
                ok=False,
                reason="restore_companion_after_combat is restricted to engine/system",
            )
        npc_id = self._require_npc_id(cmd.params)
        if npc_id is None:
            return ValidationResult(ok=False, reason="npc_id must be a non-empty string")
        if not state.has_slice("party"):
            return ValidationResult(ok=False, reason="no_party_slice")
        if npc_id not in (state.party.members or {}):
            return ValidationResult(ok=False, reason="not_member")
        try:
            restored_hp = int(cmd.params.get("restored_hp"))
        except (TypeError, ValueError):
            return ValidationResult(ok=False, reason="restored_hp must be an integer")
        if restored_hp <= 0:
            return ValidationResult(ok=False, reason="restored_hp must be positive")
        return ValidationResult(ok=True)

    def _compute_recruit(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        npc_id = self._require_npc_id(cmd.params) or ""
        profile = world.characters.get(npc_id) if world.has_registry("characters") else None
        tick = state.time.absolute_tick() if state.has_slice("time") else 0
        member_payload = {
            "name": getattr(profile, "name", npc_id),
            "class_id": getattr(profile, "class_id", ""),
            "recruited_tick": tick,
        }

        changes = [
            StateChange(
                slice="party",
                operation="set",
                path=f"members.{npc_id}",
                value=member_payload,
            ),
        ]
        if state.has_slice("player") and state.has_slice("areas"):
            player_area = state.player.current_area
            if player_area:
                player_room = get_non_empty_string(
                    {"room_id": getattr(state.player, "current_room", None)},
                    "room_id",
                )
                changes.append(
                    StateChange(
                        slice="areas",
                        operation="set",
                        path=f"npc_presence.{npc_id}",
                        value={
                            "area_id": player_area,
                            "location_id": state.player.current_location,
                            "room_id": player_room,
                            "source": "companion",
                        },
                    )
                )

        party_members = list((state.party.members or {}).keys())
        if npc_id not in party_members:
            party_members.append(npc_id)

        return self._success(
            cmd,
            changes=changes,
            event_type="companion_recruited",
            npc_id=npc_id,
            reason="recruited",
            party_members=party_members,
        )

    def _compute_dismiss(
        self,
        cmd: Command,
        state: StateContainer,
        world: Any = None,
    ) -> ExecuteResult:
        npc_id = self._require_npc_id(cmd.params) or ""
        reason = get_non_empty_string(cmd.params, "reason") or "dismissed"
        party_members = [
            member_id
            for member_id in (state.party.members or {}).keys()
            if member_id != npc_id
        ]
        changes: list[StateChange] = [
            StateChange(
                slice="party",
                operation="remove",
                path=f"members.{npc_id}",
                value=None,
            )
        ]
        # Move NPC back to their home location immediately
        if world is not None and hasattr(world, "has_registry") and world.has_registry("characters"):
            template = world.characters.get(npc_id)
            if template is not None:
                home_area = getattr(template, "area_id", None)
                home_loc = getattr(template, "location_id", None)
                if isinstance(home_area, str) and home_area.strip():
                    changes.append(
                        StateChange(
                            slice="areas",
                            operation="set",
                            path=f"npc_presence.{npc_id}",
                            value={
                                "area_id": home_area,
                                "location_id": home_loc,
                                "source": "schedule",
                            },
                        )
                    )
        # Clear wants_to_leave from blackboard
        if state.has_slice("relations"):
            bb = state.relations.get_blackboard(npc_id)
            if isinstance(bb, dict) and bb.get("wants_to_leave"):
                state.relations.update_blackboard(npc_id, {
                    "wants_to_leave": False,
                    "pending_topic": "",
                })
        return self._success(
            cmd,
            changes=changes,
            event_type="companion_dismissed",
            npc_id=npc_id,
            reason=reason,
            party_members=party_members,
        )

    def _compute_force_leave(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        return self._compute_dismiss(cmd, state)

    def _compute_restore_after_combat(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        npc_id = self._require_npc_id(cmd.params) or ""
        try:
            restored_hp = int(cmd.params.get("restored_hp"))
        except (TypeError, ValueError):
            return ExecuteResult.error("restored_hp must be an integer")
        member = state.party.members.get(npc_id)
        if not isinstance(member, Mapping):
            return ExecuteResult.error("not_member")
        max_hp_raw = member.get("max_hp")
        try:
            max_hp = int(max_hp_raw) if max_hp_raw is not None else None
        except (TypeError, ValueError):
            max_hp = None
        if max_hp is not None and max_hp > 0:
            restored_hp = min(restored_hp, max_hp)

        updated_member = dict(member)
        updated_member["hp"] = restored_hp

        return self._success(
            cmd,
            changes=[
                StateChange(
                    slice="party",
                    operation="set",
                    path=f"members.{npc_id}",
                    value=updated_member,
                )
            ],
            event_type="companion_restored_after_combat",
            npc_id=npc_id,
            reason="combat_recovery",
            party_members=list((state.party.members or {}).keys()),
        )

    def _success(
        self,
        cmd: Command,
        *,
        changes: list[StateChange],
        event_type: str,
        npc_id: str,
        reason: str,
        party_members: list[str],
    ) -> ExecuteResult:
        metadata = {
            "handler": "companion",
            "command": cmd.type,
            "status": "ok",
            "event_type": event_type,
            "npc_id": npc_id,
            "reason": reason,
            "party_members": list(party_members),
        }
        return ExecuteResult(
            executed=True,
            delta=StateDelta(
                changes=changes,
                reason=cmd.type,
                metadata=metadata,
            ),
            time_cost=0.0,
            metadata=metadata,
        )

    @staticmethod
    def _require_npc_id(params: Mapping[str, Any]) -> str | None:
        return get_non_empty_string(params, "npc_id")
