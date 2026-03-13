"""Donation handler for temple offerings and NPC donation services."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content import WorldInstance
from app.game_core.environment_access import find_current_interactable
from app.game_core.orchestration.presence import get_area_npcs, get_npc_room, is_colocated
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import coerce_int, get_non_empty_string, handler_success
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.scene_interactables import functional_type
from app.game_core.state import StateChange, StateContainer


class DonationHandler(StaticCommandHandler):
    COMMAND_TYPES = ("donate",)
    _TIME_COST = 1.0 / 6.0

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice required")
        target_kind = get_non_empty_string(cmd.params, "target_kind")
        if target_kind not in {"npc", "interactable"}:
            return ValidationResult(ok=False, reason="target_kind must be npc or interactable")
        target_id = get_non_empty_string(cmd.params, "target_id")
        if target_id is None:
            return ValidationResult(ok=False, reason="target_id required")
        amount = coerce_int(cmd.params.get("amount"))
        if amount is None or amount <= 0:
            return ValidationResult(ok=False, reason="amount must be a positive integer")
        if state.player.gold < amount:
            return ValidationResult(ok=False, reason="insufficient_gold")

        if target_kind == "interactable":
            if not world.has_registry("maps"):
                return ValidationResult(ok=False, reason="maps registry required")
            entry, visible = find_current_interactable(state, world, target_id)
            if entry is None or not visible:
                return ValidationResult(ok=False, reason="donation target not found")
            if functional_type(entry.functional) != "donation":
                return ValidationResult(ok=False, reason="interactable is not a donation target")
            return ValidationResult(ok=True)

        presence_check = self._validate_npc_target(state, world, target_id)
        if presence_check is not None:
            return presence_check
        if self._resolve_npc_donation_service(world, target_id) is None:
            return ValidationResult(ok=False, reason="npc does not support donation")
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

        area_id = str(state.player.current_area or "").strip()
        target_kind = get_non_empty_string(cmd.params, "target_kind") or "interactable"
        target_id = get_non_empty_string(cmd.params, "target_id") or ""
        amount = coerce_int(cmd.params.get("amount")) or 0
        current_day = int(state.time.day) if state.has_slice("time") else 0

        source_entry = target_id
        if target_kind == "interactable":
            entry, _ = find_current_interactable(state, world, target_id)
            if entry is not None:
                source_entry = entry.name or target_id
        else:
            npc_template = world.characters.get(target_id) if world.has_registry("characters") else None
            if npc_template is not None:
                source_entry = npc_template.name.strip() or target_id

        area_state = state.areas.get_area(area_id) if state.has_slice("areas") else None
        properties = area_state.properties if area_state is not None else {}
        existing_total = _coerce_int(properties.get("temple_donation_total"), 0)
        last_donation_day = _coerce_int(properties.get("temple_last_donation_day"), -1)
        first_donation_today = current_day >= 0 and last_donation_day != current_day
        trust_delta = 1 if first_donation_today and self._has_priestess(world) else 0
        remaining_gold = max(0, state.player.gold - amount)

        changes = [
            StateChange("player", "add", "gold", -amount),
            StateChange(
                "areas",
                "set",
                f"{area_id}.properties.temple_donation_total",
                existing_total + amount,
            ),
            StateChange(
                "areas",
                "set",
                f"{area_id}.properties.temple_last_donation_day",
                current_day,
            ),
        ]
        if trust_delta > 0:
            changes.append(
                StateChange(
                    "relations",
                    "add",
                    "npc_dispositions.priestess.trust",
                    trust_delta,
                )
            )

        narrative = (
            f"你向{source_entry}奉献了{amount}G。"
            if trust_delta <= 0
            else f"你向{source_entry}奉献了{amount}G。女神官对你的善意更加信任。"
        )

        return handler_success(
            "donation",
            "donate",
            changes=changes,
            metadata={
                "passed": True,
                "target_kind": target_kind,
                "target_id": target_id,
                "amount": amount,
                "remaining_gold": remaining_gold,
                "first_donation_today": first_donation_today,
                "trust_delta": trust_delta,
                "source_entry": source_entry,
                "area_id": area_id,
            },
            narrative_hints=[narrative],
            time_cost=self._TIME_COST,
            omit_empty_delta=False,
        )

    def _validate_npc_target(
        self,
        state: StateContainer,
        world: WorldInstance,
        npc_id: str,
    ) -> ValidationResult | None:
        current_area = str(state.player.current_area or "").strip()
        current_location = str(state.player.current_location or "").strip() or None
        current_room = str(getattr(state.player, "current_room", None) or "").strip() or None
        if not current_area:
            return ValidationResult(ok=False, reason="current area required")
        area_npcs = get_area_npcs(state, world, current_area)
        npc_location = area_npcs.get(npc_id)
        if npc_location is None:
            return ValidationResult(ok=False, reason="npc is not in the current area")
        npc_room = None
        if current_room is not None:
            npc_room = get_npc_room(state, world, current_area, npc_id)
        if is_colocated(npc_location, current_location, npc_room, current_room):
            return None
        return ValidationResult(ok=False, reason="npc is not in the current location")

    @staticmethod
    def _resolve_npc_donation_service(
        world: WorldInstance,
        npc_id: str,
    ) -> dict[str, Any] | None:
        if not world.has_registry("characters"):
            return None
        npc_template = world.characters.get(npc_id)
        if npc_template is None:
            return None
        raw_shop = getattr(npc_template, "shop", None)
        if not isinstance(raw_shop, Mapping):
            return None
        raw_services = raw_shop.get("services", [])
        if not isinstance(raw_services, list):
            return None
        for raw_service in raw_services:
            if not isinstance(raw_service, Mapping):
                continue
            service_id = str(
                raw_service.get("service_id") or raw_service.get("id") or ""
            ).strip()
            if service_id == "donation":
                return dict(raw_service)
        return None

    @staticmethod
    def _has_priestess(world: WorldInstance) -> bool:
        return bool(world.has_registry("characters") and world.characters.get("priestess") is not None)


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
