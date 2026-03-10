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
from typing import Any, Collection, Mapping

from app.game_core.planning.models import (
    AdjustPacingPlan,
    CreateQuestPlan,
    DirectNpcPlan,
    EscalatePlan,
    FillAreaPlan,
    PlanningDirective,
    PlantEnvironmentalPlan,
    PublishBulletinPlan,
    RetireQuestPlan,
    SpawnQuestNpcPlan,
)
from app.game_core.planning.utils import coerce_non_empty_string, normalize_mapping


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
        "fill_area",
        "update_quest",
        "design_reward",
        "curate_shop",
    }
)

UNSUPPORTED_DIRECTIVE_REASON_CODES = frozenset({"unsupported_kind", "kind_not_allowed"})
_DIGEST_ID_KEYS = (
    "quest_id",
    "linked_quest_id",
    "npc_id",
    "board_id",
    "area_id",
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
        board_id = coerce_non_empty_string(normalized.get("board_id"))
        if board_id is None:
            return False, normalized, "missing_board_id"
        normalized["board_id"] = board_id
        area_id = coerce_non_empty_string(normalized.get("area_id"))
        if area_id is not None:
            normalized["area_id"] = area_id
        return True, normalized, None

    if kind == "direct_npc":
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
        return True, normalized, None

    if kind == "escalate":
        delta = normalized.get("delta")
        if not isinstance(delta, int) or isinstance(delta, bool):
            return False, normalized, "invalid_delta"
        if delta < -3 or delta > 3:
            return False, normalized, "delta_out_of_range"
        return True, normalized, None

    if kind == "adjust_pacing":
        frozen = normalized.get("frozen")
        if not isinstance(frozen, bool):
            return False, normalized, "invalid_frozen"
        return True, normalized, None

    if kind == "design_reward":
        reward_type = coerce_non_empty_string(normalized.get("reward_type")) or "item"
        if reward_type.lower() != "item":
            return False, normalized, "invalid_reward_type"
        linked_quest_id = coerce_non_empty_string(normalized.get("linked_quest_id"))
        if linked_quest_id is None:
            return False, normalized, "missing_linked_quest_id"
        item_id = coerce_non_empty_string(normalized.get("item_id"))
        if item_id is None:
            return False, normalized, "missing_item_id"
        try:
            quantity = int(normalized.get("quantity", 1))
        except (TypeError, ValueError):
            return False, normalized, "invalid_quantity"
        if quantity < 1:
            return False, normalized, "invalid_quantity"
        normalized["reward_type"] = "item"
        normalized["linked_quest_id"] = linked_quest_id
        normalized["item_id"] = item_id
        normalized["quantity"] = quantity
        return True, normalized, None

    if kind == "curate_shop":
        npc_id = coerce_non_empty_string(normalized.get("npc_id"))
        if npc_id is None:
            return False, normalized, "missing_npc_id"
        normalized["npc_id"] = npc_id
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
