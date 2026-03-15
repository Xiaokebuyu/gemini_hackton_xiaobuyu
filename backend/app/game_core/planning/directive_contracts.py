"""Shared planner directive normalization and contract validation.

This module is the canonical planner-directive contract surface for runtime
validation. It only covers contract-level checks:
- directive shape (``kind`` + mapping payload)
- minimal required fields
- basic type/range normalization

It does not replace deeper subsystem or handler validation. A directive may
pass here and still be rejected later for world/state-specific reasons.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Collection, Mapping

logger = logging.getLogger(__name__)

from app.game_core.clue_investigation import (
    CLUE_FUNCTIONAL_TYPE,
    normalize_clue_definition,
    validate_clue_definition,
)
from app.game_core.planning.models import (
    AdjustPacingPlan,
    CreateQuestPlan,
    DirectNpcPlan,
    EscalatePlan,
    FillAreaPlan,
    FillLocationPlan,
    PlanningDirective,
    PlantEnvironmentalPlan,
    PublishBulletinPlan,
    RetireQuestPlan,
    SpawnQuestNpcPlan,
)
from app.game_core.planning.capabilities import VALID_FUNCTIONAL_TYPES
from app.game_core.planning.service_descriptors import validate_effects
from app.game_core.planning.utils import (
    coerce_non_empty_string,
    normalize_mapping,
    string_or_empty,
)


SUPPORTED_PLANNER_DIRECTIVE_KINDS = frozenset(
    {
        "create_quest",
        "direct_npc",
        "publish_bulletin",
        "escalate",
        "adjust_pacing",
        "retire_quest",
        "spawn_quest_npc",
        "plant_environmental",
        "plant_encounter",
        "fill_area",
        "fill_location",
        "update_quest",
        "curate_shop",
        "discover_room",
        "fill_room",
        "assign_capability",
        "revoke_capability",
        "advance_milestone",
        "assign_service",
        "revoke_service",
        "schedule_event",
        "create_rumor",
        "modify_location",
        "set_task_monitor",
    }
)

UNSUPPORTED_DIRECTIVE_REASON_CODES = frozenset({"unsupported_kind", "kind_not_allowed"})
_DIGEST_ID_KEYS = (
    "quest_id",
    "linked_quest_id",
    "npc_id",
    "board_id",
    "area_id",
    "location_id",
    "room_id",
    "sub_area_id",
)


@dataclass(slots=True)
class DirectiveValidationResult:
    """Stable result shape produced by planner-directive contract validation."""

    ok: bool
    kind: str
    payload: dict[str, Any]
    reason_code: str | None
    payload_digest: dict[str, Any]


def normalize_planner_directive(
    raw: Any,
) -> tuple[str, dict[str, Any]] | None:
    if isinstance(raw, PlanningDirective):
        kind = coerce_non_empty_string(raw.kind)
        if kind is None:
            return None
        return kind, normalize_mapping(raw.payload)
    if isinstance(raw, CreateQuestPlan):
        return "create_quest", _merge_payload({"quest_id": raw.quest_id}, raw.payload)
    if isinstance(raw, DirectNpcPlan):
        if not raw.directive:
            return None
        return "direct_npc", {
            "npc_id": raw.npc_id,
            "directive": dict(raw.directive) if isinstance(raw.directive, Mapping) else {},
        }
    if isinstance(raw, PublishBulletinPlan):
        return "publish_bulletin", _merge_payload({"board_id": raw.board_id}, raw.payload)
    if isinstance(raw, EscalatePlan):
        return "escalate", _merge_payload({"delta": raw.delta}, raw.payload)
    if isinstance(raw, AdjustPacingPlan):
        return "adjust_pacing", _merge_payload({"frozen": raw.frozen}, raw.payload)
    if isinstance(raw, RetireQuestPlan):
        return "retire_quest", _merge_payload({"quest_id": raw.quest_id}, raw.payload)
    if isinstance(raw, SpawnQuestNpcPlan):
        return "spawn_quest_npc", _merge_payload({"npc_id": raw.npc_id}, raw.payload)
    if isinstance(raw, PlantEnvironmentalPlan):
        return "plant_environmental", _merge_payload({"area_id": raw.area_id}, raw.payload)
    if isinstance(raw, FillAreaPlan):
        return "fill_area", _merge_payload({"area_id": raw.area_id}, raw.payload)
    if isinstance(raw, FillLocationPlan):
        return "fill_location", _merge_payload({"area_id": raw.area_id}, raw.payload)
    if not isinstance(raw, Mapping):
        return None
    kind = coerce_non_empty_string(raw.get("kind"))
    if kind is None:
        return None
    return kind, normalize_mapping(raw.get("payload"))


def validate_planner_directive(
    raw: Any,
    *,
    allowed_directives: Collection[str] | None = None,
) -> DirectiveValidationResult:
    """Validate one planner directive against the shared runtime contract."""

    normalized = normalize_planner_directive(raw)
    if normalized is None:
        return DirectiveValidationResult(
            ok=False,
            kind="",
            payload={},
            reason_code="invalid_directive_shape",
            payload_digest={"payload_keys": []},
        )
    kind, payload = normalized
    payload_digest = build_payload_digest(kind, payload)
    if kind not in SUPPORTED_PLANNER_DIRECTIVE_KINDS:
        return DirectiveValidationResult(
            ok=False,
            kind=kind,
            payload=payload,
            reason_code="unsupported_kind",
            payload_digest=payload_digest,
        )
    if allowed_directives is not None and kind not in set(allowed_directives):
        return DirectiveValidationResult(
            ok=False,
            kind=kind,
            payload=payload,
            reason_code="kind_not_allowed",
            payload_digest=payload_digest,
        )
    contract_ok, normalized_payload, reason_code = _validate_contract(kind, payload)
    return DirectiveValidationResult(
        ok=contract_ok,
        kind=kind,
        payload=normalized_payload,
        reason_code=reason_code,
        payload_digest=build_payload_digest(kind, normalized_payload),
    )


def expand_planner_directive(raw: Any) -> list[Any]:
    """Expand one legacy planner directive into canonical runtime directives.

    This is intentionally narrow and only covers confirmed legacy world-builder
    payloads that would otherwise pass contract validation but silently lose
    most of their content at apply time.
    """

    normalized = normalize_planner_directive(raw)
    if normalized is None:
        return [raw]
    kind, payload = normalized
    expanded_payloads = _expand_legacy_payloads(kind, payload)
    if not expanded_payloads:
        return [{"kind": kind, "payload": payload}]
    return [{"kind": kind, "payload": item} for item in expanded_payloads]


def build_payload_digest(kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    digest: dict[str, Any] = {
        "kind": str(kind).strip(),
        "payload_keys": sorted(str(key) for key in payload.keys()),
    }
    for key in _DIGEST_ID_KEYS:
        value = payload.get(key)
        normalized = coerce_non_empty_string(value)
        if normalized is None and key == "sub_area_id":
            normalized = coerce_non_empty_string(payload.get("id"))
        if normalized is not None:
            digest[key] = normalized
    return digest


def is_unsupported_directive_reason(reason_code: str | None) -> bool:
    return reason_code in UNSUPPORTED_DIRECTIVE_REASON_CODES


def _validate_contract(
    kind: str,
    payload: dict[str, Any],
) -> tuple[bool, dict[str, Any], str | None]:
    """Apply contract-level validation for one supported directive kind."""

    normalized = dict(payload)

    if kind in {"create_quest", "retire_quest", "update_quest"}:
        if kind == "create_quest":
            _normalize_legacy_create_quest_payload(normalized)
        quest_id = coerce_non_empty_string(normalized.get("quest_id"))
        if quest_id is None:
            return False, normalized, "missing_quest_id"
        normalized["quest_id"] = quest_id
        if kind == "create_quest":
            raw_expiry_ticks = normalized.get("expiry_ticks")
            if raw_expiry_ticks is not None:
                try:
                    normalized["expiry_ticks"] = int(raw_expiry_ticks)
                except (TypeError, ValueError):
                    return False, normalized, "invalid_expiry_ticks"
        return True, normalized, None

    if kind == "publish_bulletin":
        _normalize_legacy_publish_bulletin_payload(normalized)
        board_id = coerce_non_empty_string(normalized.get("board_id"))
        if board_id is None:
            return False, normalized, "missing_board_id"
        normalized["board_id"] = board_id
        area_id = coerce_non_empty_string(normalized.get("area_id"))
        if area_id is not None:
            normalized["area_id"] = area_id
        return True, normalized, None

    if kind == "direct_npc":
        _normalize_legacy_direct_npc_payload(normalized)
        npc_id = coerce_non_empty_string(normalized.get("npc_id"))
        if npc_id is None:
            return False, normalized, "missing_npc_id"
        directive = normalized.get("directive")
        if directive is not None and not isinstance(directive, Mapping):
            return False, normalized, "invalid_directive_mapping"
        if not directive:
            return False, normalized, "missing_directive"
        normalized["npc_id"] = npc_id
        normalized["directive"] = normalize_mapping(directive)
        raw_expiry = normalized.get("expires_at_tick")
        if raw_expiry is not None:
            try:
                normalized["expires_at_tick"] = int(raw_expiry)
            except (TypeError, ValueError):
                return False, normalized, "invalid_expires_at_tick"
        priority = normalized.get("priority")
        if isinstance(priority, str):
            normalized_priority = priority.strip().lower() or "medium"
            if normalized_priority not in {"high", "medium", "low"}:
                return False, normalized, "invalid_priority"
            normalized["priority"] = normalized_priority
        return True, normalized, None

    if kind == "spawn_quest_npc":
        area_id = coerce_non_empty_string(normalized.get("area_id"))
        if area_id is None:
            return False, normalized, "missing_area_id"
        normalized["area_id"] = area_id
        location_id = coerce_non_empty_string(normalized.get("location_id"))
        if location_id is not None:
            normalized["location_id"] = location_id
        else:
            normalized.pop("location_id", None)
        room_id = coerce_non_empty_string(normalized.get("room_id"))
        if room_id is not None:
            if location_id is None:
                return False, normalized, "room_id_requires_location_id"
            normalized["room_id"] = room_id
        else:
            normalized.pop("room_id", None)
        npc_id = coerce_non_empty_string(normalized.get("npc_id"))
        if npc_id is not None:
            normalized["npc_id"] = npc_id
        else:
            normalized.pop("npc_id", None)
        return True, normalized, None

    if kind in {"plant_environmental", "fill_area"}:
        area_id = coerce_non_empty_string(normalized.get("area_id"))
        if area_id is None:
            return False, normalized, "missing_area_id"
        normalized["area_id"] = area_id
        if kind == "fill_area":
            sub_area_id = coerce_non_empty_string(normalized.get("id"))
            if sub_area_id is not None:
                normalized["id"] = sub_area_id
            if (
                sub_area_id is None
                and coerce_non_empty_string(normalized.get("label")) is None
                and coerce_non_empty_string(normalized.get("description")) is None
                and not _has_legacy_fill_area_entries(normalized)
            ):
                return False, normalized, "missing_sub_area_content"
        elif (
            coerce_non_empty_string(normalized.get("clue_id")) is None
            and coerce_non_empty_string(normalized.get("description")) is None
            and not _has_legacy_environmental_entries(normalized)
        ):
            return False, normalized, "missing_environmental_content"
        return True, normalized, None

    if kind == "plant_encounter":
        _normalize_legacy_plant_encounter_payload(normalized)
        area_id = coerce_non_empty_string(normalized.get("area_id"))
        if area_id is None:
            return False, normalized, "missing_area_id"
        sub_area_id = coerce_non_empty_string(normalized.get("sub_area_id"))
        if sub_area_id is None:
            return False, normalized, "missing_sub_area_id"
        monster_ids = normalized.get("monster_ids")
        if not isinstance(monster_ids, list):
            if _looks_like_legacy_scene_encounter(normalized):
                return False, normalized, "legacy_scene_encounter_not_supported"
            return False, normalized, "missing_monster_ids"
        normalized_monster_ids = [
            monster_id
            for item in monster_ids
            if (monster_id := coerce_non_empty_string(item)) is not None
        ]
        if not normalized_monster_ids:
            if _looks_like_legacy_scene_encounter(normalized):
                return False, normalized, "legacy_scene_encounter_not_supported"
            return False, normalized, "missing_monster_ids"
        normalized["area_id"] = area_id
        normalized["sub_area_id"] = sub_area_id
        normalized["monster_ids"] = normalized_monster_ids
        map_category = coerce_non_empty_string(normalized.get("map_category"))
        if map_category is not None:
            normalized["map_category"] = map_category
        map_tags = normalized.get("map_tags")
        if map_tags is not None:
            if not isinstance(map_tags, list):
                return False, normalized, "invalid_map_tags"
            normalized["map_tags"] = [
                tag
                for item in map_tags
                if (tag := coerce_non_empty_string(item)) is not None
            ]
        raw_expiry_ticks = normalized.get("expiry_ticks")
        if raw_expiry_ticks is not None:
            try:
                normalized["expiry_ticks"] = int(raw_expiry_ticks)
            except (TypeError, ValueError):
                return False, normalized, "invalid_expiry_ticks"
        return True, normalized, None

    if kind == "escalate":
        raw_delta = normalized.get("delta")
        if isinstance(raw_delta, bool):
            return False, normalized, "invalid_delta"
        try:
            delta = int(raw_delta)
        except (TypeError, ValueError):
            return False, normalized, "invalid_delta"
        normalized["delta"] = delta
        if delta < -3 or delta > 3:
            return False, normalized, "delta_out_of_range"
        return True, normalized, None

    if kind == "adjust_pacing":
        frozen = normalized.get("frozen")
        if not isinstance(frozen, bool):
            if normalized.get("pacing_factor") is not None:
                return False, normalized, "legacy_pacing_factor_not_supported"
            return False, normalized, "invalid_frozen"
        return True, normalized, None

    if kind == "curate_shop":
        npc_id = coerce_non_empty_string(normalized.get("npc_id"))
        if npc_id is None:
            return False, normalized, "missing_npc_id"
        normalized["npc_id"] = npc_id
        return True, normalized, None

    if kind == "discover_room":
        area_id = coerce_non_empty_string(normalized.get("area_id"))
        if area_id is None:
            return False, normalized, "missing_area_id"
        normalized["area_id"] = area_id
        location_id = coerce_non_empty_string(normalized.get("location_id"))
        if location_id is None:
            return False, normalized, "missing_location_id"
        normalized["location_id"] = location_id
        room_id = coerce_non_empty_string(normalized.get("room_id"))
        if room_id is None:
            return False, normalized, "missing_room_id"
        normalized["room_id"] = room_id
        return True, normalized, None

    if kind == "fill_room":
        area_id = coerce_non_empty_string(normalized.get("area_id"))
        if area_id is None:
            return False, normalized, "missing_area_id"
        normalized["area_id"] = area_id
        location_id = coerce_non_empty_string(normalized.get("location_id"))
        if location_id is None:
            return False, normalized, "missing_location_id"
        normalized["location_id"] = location_id
        room_id = coerce_non_empty_string(normalized.get("room_id"))
        if room_id is None:
            return False, normalized, "missing_room_id"
        normalized["room_id"] = room_id
        name = coerce_non_empty_string(normalized.get("name"))
        if name is None:
            return False, normalized, "missing_name"
        normalized["name"] = name
        # Optional fields
        discoverable = normalized.get("discoverable")
        if discoverable is not None:
            if not isinstance(discoverable, bool):
                return False, normalized, "invalid_discoverable"
            normalized["discoverable"] = discoverable
        else:
            normalized["discoverable"] = False
        raw_expiry = normalized.get("expiry_ticks")
        if raw_expiry is not None:
            try:
                normalized["expiry_ticks"] = int(raw_expiry)
            except (TypeError, ValueError):
                return False, normalized, "invalid_expiry_ticks"
        return True, normalized, None

    if kind == "fill_location":
        area_id = coerce_non_empty_string(normalized.get("area_id"))
        if area_id is None:
            return False, normalized, "missing_area_id"
        normalized["area_id"] = area_id
        location_id = coerce_non_empty_string(normalized.get("location_id"))
        if location_id is None:
            return False, normalized, "missing_location_id"
        normalized["location_id"] = location_id
        room_id = coerce_non_empty_string(normalized.get("room_id"))
        if room_id is not None:
            normalized["room_id"] = room_id
        else:
            normalized.pop("room_id", None)
        interactables = normalized.get("interactables")
        if not isinstance(interactables, list) or not interactables:
            return False, normalized, "missing_interactables"
        normalized["interactables"] = [
            dict(item)
            for item in interactables
            if isinstance(item, Mapping) and coerce_non_empty_string(item.get("id")) is not None
        ]
        if not normalized["interactables"]:
            return False, normalized, "missing_interactables"
        valid_interactables = []
        for interactable in normalized["interactables"]:
            functional = normalize_mapping(interactable.get("functional"))
            if coerce_non_empty_string(functional.get("type")) != CLUE_FUNCTIONAL_TYPE:
                valid_interactables.append(interactable)
                continue
            clue = normalize_clue_definition(
                functional,
                interactable_id=coerce_non_empty_string(interactable.get("id")),
                name=string_or_empty(interactable.get("name")),
                description=string_or_empty(interactable.get("description")),
            )
            error = validate_clue_definition(clue)
            if error is not None:
                logger.warning("stripping invalid clue interactable: %s", error)
                continue
            valid_interactables.append(interactable)
        normalized["interactables"] = valid_interactables
        if not valid_interactables:
            return False, normalized, "all_interactables_invalid"
        return True, normalized, None

    if kind == "assign_capability":
        npc_id = coerce_non_empty_string(normalized.get("npc_id"))
        if npc_id is None:
            return False, normalized, "missing_npc_id"
        normalized["npc_id"] = npc_id
        capability_id = coerce_non_empty_string(normalized.get("capability_id"))
        if capability_id is None:
            return False, normalized, "missing_capability_id"
        normalized["capability_id"] = capability_id
        instruction = coerce_non_empty_string(normalized.get("instruction"))
        if instruction is None:
            return False, normalized, "missing_instruction"
        normalized["instruction"] = instruction
        functional = normalized.get("functional")
        if functional is not None:
            functional_str = str(functional)
            if functional_str not in VALID_FUNCTIONAL_TYPES:
                return False, normalized, "invalid_functional"
            normalized["functional"] = functional_str
        raw_expiry = normalized.get("expiry_ticks")
        if raw_expiry is not None:
            try:
                expiry_ticks = int(raw_expiry)
            except (TypeError, ValueError):
                return False, normalized, "invalid_expiry_ticks"
            if expiry_ticks < 0:
                return False, normalized, "invalid_expiry_ticks"
            normalized["expiry_ticks"] = expiry_ticks
        return True, normalized, None

    if kind == "revoke_capability":
        npc_id = coerce_non_empty_string(normalized.get("npc_id"))
        if npc_id is None:
            return False, normalized, "missing_npc_id"
        normalized["npc_id"] = npc_id
        capability_id = coerce_non_empty_string(normalized.get("capability_id"))
        if capability_id is None:
            return False, normalized, "missing_capability_id"
        normalized["capability_id"] = capability_id
        return True, normalized, None

    if kind == "advance_milestone":
        milestone_id = coerce_non_empty_string(normalized.get("milestone_id"))
        if milestone_id is None:
            return False, normalized, "missing_milestone_id"
        normalized["milestone_id"] = milestone_id
        to_state = coerce_non_empty_string(normalized.get("to_state"))
        if to_state is None:
            to_state = "COMPLETED"
        normalized["to_state"] = to_state.upper()
        return True, normalized, None

    if kind == "assign_service":
        npc_id = coerce_non_empty_string(normalized.get("npc_id"))
        if npc_id is None:
            return False, normalized, "missing_npc_id"
        normalized["npc_id"] = npc_id
        service_id = coerce_non_empty_string(normalized.get("service_id"))
        if service_id is None:
            return False, normalized, "missing_service_id"
        normalized["service_id"] = service_id
        label = coerce_non_empty_string(normalized.get("label"))
        if label is None:
            return False, normalized, "missing_label"
        normalized["label"] = label
        # Optional: price (int >= 0)
        raw_price = normalized.get("price")
        if raw_price is not None:
            try:
                price = int(raw_price)
            except (TypeError, ValueError):
                return False, normalized, "invalid_price"
            if price < 0:
                return False, normalized, "invalid_price"
            normalized["price"] = price
        # Optional: effects (list of dicts)
        raw_effects = normalized.get("effects")
        if raw_effects is not None:
            if not isinstance(raw_effects, list):
                return False, normalized, "invalid_effects"
            errors = validate_effects(raw_effects)
            if errors:
                return False, normalized, "invalid_effects"
        # Optional: notes (str)
        raw_notes = normalized.get("notes")
        if raw_notes is not None:
            normalized["notes"] = str(raw_notes)
        # Optional: expiry_ticks (int >= 0)
        raw_expiry = normalized.get("expiry_ticks")
        if raw_expiry is not None:
            try:
                expiry_ticks = int(raw_expiry)
            except (TypeError, ValueError):
                return False, normalized, "invalid_expiry_ticks"
            if expiry_ticks < 0:
                return False, normalized, "invalid_expiry_ticks"
            normalized["expiry_ticks"] = expiry_ticks
        # Optional: preconditions (dict)
        raw_pre = normalized.get("preconditions")
        if raw_pre is not None and not isinstance(raw_pre, dict):
            return False, normalized, "invalid_preconditions"
        # Optional: one_shot (bool)
        raw_one_shot = normalized.get("one_shot")
        if raw_one_shot is not None:
            normalized["one_shot"] = bool(raw_one_shot)
        return True, normalized, None

    if kind == "revoke_service":
        npc_id = coerce_non_empty_string(normalized.get("npc_id"))
        if npc_id is None:
            return False, normalized, "missing_npc_id"
        normalized["npc_id"] = npc_id
        service_id = coerce_non_empty_string(normalized.get("service_id"))
        if service_id is None:
            return False, normalized, "missing_service_id"
        normalized["service_id"] = service_id
        return True, normalized, None

    if kind == "schedule_event":
        # event_id is required; all other fields are optional (trigger_condition,
        # event_type, payload, metadata).
        event_id = coerce_non_empty_string(normalized.get("event_id"))
        if event_id is None:
            return False, normalized, "missing_event_id"
        normalized["event_id"] = event_id
        event_type = coerce_non_empty_string(normalized.get("event_type"))
        if event_type is not None:
            normalized["event_type"] = event_type
        return True, normalized, None

    if kind == "create_rumor":
        # At least one of text/content is required so the rumor has substance.
        text = coerce_non_empty_string(
            normalized.get("text") or normalized.get("content")
        )
        if text is None:
            return False, normalized, "missing_rumor_text"
        normalized["text"] = text
        normalized["content"] = text
        area_id = coerce_non_empty_string(normalized.get("area_id"))
        if area_id is not None:
            normalized["area_id"] = area_id
        return True, normalized, None

    if kind == "modify_location":
        # area_id is always required; the rest depends on which variant is used
        # (key/value property update vs. NPC/player move).
        area_id = coerce_non_empty_string(normalized.get("area_id"))
        if area_id is None:
            return False, normalized, "missing_area_id"
        normalized["area_id"] = area_id
        location_id = coerce_non_empty_string(normalized.get("location_id"))
        if location_id is not None:
            normalized["location_id"] = location_id
        return True, normalized, None

    if kind == "set_task_monitor":
        # quest_id is required.
        quest_id = coerce_non_empty_string(normalized.get("quest_id"))
        if quest_id is None:
            return False, normalized, "missing_quest_id"
        normalized["quest_id"] = quest_id
        # conditions must be a non-empty list.
        conditions = normalized.get("conditions")
        if not isinstance(conditions, list) or not conditions:
            return False, normalized, "missing_conditions"
        normalized["conditions"] = [
            dict(c) for c in conditions if isinstance(c, Mapping)
        ]
        # on_complete defaults to "auto"; valid values: "auto", "notify".
        on_complete = coerce_non_empty_string(normalized.get("on_complete"))
        if on_complete is None:
            on_complete = "auto"
        on_complete = on_complete.strip().lower()
        if on_complete not in {"auto", "notify"}:
            return False, normalized, "invalid_on_complete"
        normalized["on_complete"] = on_complete
        return True, normalized, None

    return True, normalized, None


def _merge_payload(
    base: Mapping[str, Any],
    extra: Any,
) -> dict[str, Any]:
    merged = normalize_mapping(extra)
    result = dict(merged)
    for key, value in base.items():
        result[str(key)] = value
    return result


def _normalize_legacy_create_quest_payload(payload: dict[str, Any]) -> None:
    """Compat shim for older planner outputs that still use legacy quest keys."""

    quest_id = coerce_non_empty_string(payload.get("quest_id"))
    legacy_id = coerce_non_empty_string(payload.get("id"))
    if quest_id is None and legacy_id is not None:
        payload["quest_id"] = legacy_id
    summary = coerce_non_empty_string(payload.get("summary"))
    legacy_description = coerce_non_empty_string(payload.get("description"))
    if summary is None and legacy_description is not None:
        payload["summary"] = legacy_description


def _normalize_legacy_publish_bulletin_payload(payload: dict[str, Any]) -> None:
    """Compat shim for older planner outputs that put quest_id at the root."""

    quest_id = coerce_non_empty_string(payload.get("quest_id"))
    if quest_id is None:
        return
    metadata = normalize_mapping(payload.get("metadata"))
    if coerce_non_empty_string(metadata.get("quest_id")) is None:
        metadata["quest_id"] = quest_id
    payload["metadata"] = metadata


def _normalize_legacy_direct_npc_payload(payload: dict[str, Any]) -> None:
    """Compat shim for older planner outputs that inline directive fields at root."""

    directive = payload.get("directive")
    if isinstance(directive, Mapping) and directive:
        return
    reserved_keys = {
        "npc_id",
        "directive",
        "priority",
        "expires_at_tick",
        "linked_quest_id",
        "current_tick",
    }
    legacy_directive = {
        str(key): value
        for key, value in payload.items()
        if str(key) not in reserved_keys
    }
    if legacy_directive:
        if coerce_non_empty_string(legacy_directive.get("kind")) is None:
            legacy_directive["kind"] = "talk" if bool(legacy_directive.get("interactable")) else "react"
        payload["directive"] = legacy_directive


def _normalize_legacy_plant_encounter_payload(payload: dict[str, Any]) -> None:
    """Compat shim for older planner outputs that still use location_id."""

    sub_area_id = coerce_non_empty_string(payload.get("sub_area_id"))
    legacy_location_id = coerce_non_empty_string(payload.get("location_id"))
    if sub_area_id is None and legacy_location_id is not None:
        payload["sub_area_id"] = legacy_location_id


def _expand_legacy_payloads(kind: str, payload: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    if kind == "fill_area":
        return _expand_legacy_fill_area_payloads(payload)
    if kind == "plant_environmental":
        return _expand_legacy_environmental_payloads(payload)
    return None


def _expand_legacy_fill_area_payloads(payload: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    if coerce_non_empty_string(payload.get("id")) is not None:
        return None
    raw_locations = payload.get("locations")
    if isinstance(raw_locations, list) and raw_locations:
        expanded = [
            item
            for raw_item in raw_locations
            if (item := _normalize_legacy_fill_area_entry(payload, raw_item)) is not None
        ]
        if expanded:
            return expanded
    raw_elements = payload.get("elements")
    if isinstance(raw_elements, list) and raw_elements:
        expanded = [
            item
            for raw_item in raw_elements
            if (item := _normalize_legacy_fill_area_entry(payload, raw_item)) is not None
        ]
        if expanded:
            return expanded
    return None


def _expand_legacy_environmental_payloads(payload: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    if coerce_non_empty_string(payload.get("clue_id")) is not None:
        return None
    raw_elements = payload.get("elements")
    if not isinstance(raw_elements, list) or not raw_elements:
        return None
    expanded = [
        item
        for raw_item in raw_elements
        if (item := _normalize_legacy_environmental_entry(payload, raw_item)) is not None
    ]
    return expanded or None


def _normalize_legacy_fill_area_entry(
    base_payload: Mapping[str, Any],
    raw_item: Any,
) -> dict[str, Any] | None:
    item = normalize_mapping(raw_item)
    if not item:
        return None
    entry_id = (
        coerce_non_empty_string(item.get("id"))
        or coerce_non_empty_string(item.get("location_id"))
        or coerce_non_empty_string(item.get("sub_area_id"))
    )
    label = (
        coerce_non_empty_string(item.get("label"))
        or coerce_non_empty_string(item.get("name"))
        or coerce_non_empty_string(item.get("title"))
    )
    description = string_or_empty(item.get("description") or item.get("summary"))
    if entry_id is None and label is None and not description:
        return None

    normalized = _legacy_payload_base(base_payload)
    if entry_id is not None:
        normalized["id"] = entry_id
    if label is not None:
        normalized["label"] = label
    if description:
        normalized["description"] = description
    tags = _merge_string_lists(item.get("tags"), item.get("traits"))
    if item.get("interactive") is True or item.get("interactable") is True:
        tags.append("interactive")
    if tags:
        normalized["tags"] = tags
    sub_area_type = coerce_non_empty_string(item.get("type"))
    if sub_area_type is not None:
        normalized["type"] = sub_area_type
    interactables = _normalize_interactable_list(item.get("interactables"))
    if interactables:
        normalized["interactables"] = interactables
    resident_npcs = _normalize_string_list(item.get("resident_npcs"))
    if resident_npcs:
        normalized["resident_npcs"] = resident_npcs
    linked_quest_id = coerce_non_empty_string(item.get("linked_quest_id"))
    if linked_quest_id is not None:
        normalized["linked_quest_id"] = linked_quest_id
    linked_milestone = coerce_non_empty_string(item.get("linked_milestone"))
    if linked_milestone is not None:
        normalized["linked_milestone"] = linked_milestone
    raw_expiry = item.get("expiry_ticks")
    if raw_expiry is None:
        raw_expiry = item.get("persistence")
    if raw_expiry is not None:
        try:
            normalized["expiry_ticks"] = int(raw_expiry)
        except (TypeError, ValueError):
            pass
    return normalized


def _normalize_legacy_environmental_entry(
    base_payload: Mapping[str, Any],
    raw_item: Any,
) -> dict[str, Any] | None:
    item = normalize_mapping(raw_item)
    if not item:
        return None
    clue_id = (
        coerce_non_empty_string(item.get("clue_id"))
        or coerce_non_empty_string(item.get("id"))
    )
    description = (
        string_or_empty(item.get("description"))
        or string_or_empty(item.get("summary"))
        or string_or_empty(item.get("name"))
    )
    if clue_id is None and not description:
        return None

    normalized = _legacy_payload_base(base_payload)
    if clue_id is not None:
        normalized["clue_id"] = clue_id
    if description:
        normalized["description"] = description
    tags = _merge_string_lists(item.get("tags"), item.get("traits"))
    if tags:
        normalized["tags"] = tags
    discovery_mode = coerce_non_empty_string(item.get("discovery_mode"))
    if discovery_mode is not None:
        normalized["discovery_mode"] = discovery_mode
    dc = item.get("dc")
    if dc is not None:
        normalized["dc"] = dc
    linked_quest_id = coerce_non_empty_string(item.get("linked_quest_id"))
    if linked_quest_id is not None:
        normalized["linked_quest_id"] = linked_quest_id
    linked_milestone = coerce_non_empty_string(item.get("linked_milestone"))
    if linked_milestone is not None:
        normalized["linked_milestone"] = linked_milestone
    raw_expiry = item.get("expiry_ticks")
    if raw_expiry is None:
        raw_expiry = item.get("persistence")
    if raw_expiry is not None:
        try:
            normalized["expiry_ticks"] = int(raw_expiry)
        except (TypeError, ValueError):
            pass
    return normalized


def _legacy_payload_base(payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized = normalize_mapping(payload)
    normalized.pop("locations", None)
    normalized.pop("elements", None)
    normalized.pop("location_id", None)
    return normalized


def _has_legacy_fill_area_entries(payload: Mapping[str, Any]) -> bool:
    raw_locations = payload.get("locations")
    raw_elements = payload.get("elements")
    return (
        isinstance(raw_locations, list)
        and bool(raw_locations)
    ) or (
        isinstance(raw_elements, list)
        and bool(raw_elements)
    )


def _has_legacy_environmental_entries(payload: Mapping[str, Any]) -> bool:
    raw_elements = payload.get("elements")
    return isinstance(raw_elements, list) and bool(raw_elements)


def _looks_like_legacy_scene_encounter(payload: Mapping[str, Any]) -> bool:
    return (
        payload.get("participants") is not None
        or payload.get("encounter_id") is not None
        or payload.get("trigger_condition") is not None
    )


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        normalized = coerce_non_empty_string(item)
        if normalized is not None:
            result.append(normalized)
    return result


def _normalize_interactable_list(value: Any) -> list[Any]:
    """Normalize an interactables list, preserving dict objects.

    Unlike _normalize_string_list (which coerces everything to strings via
    coerce_non_empty_string), this function keeps dict elements intact so that
    downstream handlers can access interactable fields (id, name, description,
    type, tags, checks). Plain string entries are still kept for backward
    compatibility.
    """
    if not isinstance(value, list):
        return []
    result: list[Any] = []
    for item in value:
        if isinstance(item, dict):
            if item:
                result.append(item)
        else:
            normalized = coerce_non_empty_string(item)
            if normalized is not None:
                result.append(normalized)
    return result


def _merge_string_lists(*values: Any) -> list[str]:
    merged: list[str] = []
    for value in values:
        for item in _normalize_string_list(value):
            if item not in merged:
                merged.append(item)
    return merged
