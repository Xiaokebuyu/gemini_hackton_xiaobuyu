"""ServiceDescriptor — runtime-assigned NPC service definitions.

Decision record: D-Svc01 (narrative.md)

NPC services define executable actions (heal, bless, grant_item, etc.) that
an NPC can perform on the player. They may originate from:
- content layer: ``characters.json shop.services`` (source="content")
- narrative planner: dynamic ``assign_service`` directives (source="planner")

Services are stored in ``NarrativePlanSlice.npc_services`` keyed by npc_id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Valid effect atom types that ExecuteServiceTool can handle.
# Keys correspond to handler cases in Phase 3.
VALID_EFFECT_ATOM_TYPES: frozenset[str] = frozenset({
    "restore_hp",
    "modify_gold",
    "grant_item",
    "remove_item",
    "apply_effect",
    "remove_effect",
    "add_xp",
    "add_knowledge",
})


def validate_effects(effects: list[dict[str, Any]]) -> list[str]:
    """Validate a list of effect atom dicts.

    Returns a list of error strings (empty list = valid).
    Each item must be a dict containing at least a ``type`` key whose value
    is one of ``VALID_EFFECT_ATOM_TYPES``.
    """
    errors: list[str] = []
    if not isinstance(effects, list):
        errors.append("effects must be a list")
        return errors
    for i, atom in enumerate(effects):
        if not isinstance(atom, dict):
            errors.append(f"effects[{i}] must be a dict")
            continue
        atom_type = atom.get("type")
        if not isinstance(atom_type, str) or not atom_type:
            errors.append(f"effects[{i}] missing 'type' key")
        elif atom_type not in VALID_EFFECT_ATOM_TYPES:
            errors.append(
                f"effects[{i}] has unknown type '{atom_type}'"
                f" (valid: {sorted(VALID_EFFECT_ATOM_TYPES)})"
            )
    return errors


@dataclass(slots=True)
class ServiceDescriptor:
    """A service that an NPC can offer and execute.

    Fields
    ------
    service_id:
        Unique identifier within the npc's service list.  Should be stable
        across saves (e.g. ``"heal"``, ``"blessing"``, ``"reward_q001"``).
    npc_id:
        The NPC who owns this service.
    label:
        Human-readable name shown in UI and injected into NPC prompt.
    price:
        Gold cost for the player.  0 = free.
    effects:
        Ordered list of effect atom dicts, each with at least a ``"type"``
        key.  Executed sequentially by ``ExecuteServiceTool``.
    preconditions:
        Optional dict of extra requirements, e.g. ``{"min_gold": 25}``,
        ``{"quest_completed": "q_main_01"}``.
    notes:
        Free-text note injected into the NPC prompt (planner guidance).
    assigned_tick:
        Game tick when the service was assigned.
    expiry_tick:
        Game tick when the service expires.  0 = does not expire.
    source:
        Origin of this service: ``"content"`` or ``"planner"``.
    one_shot:
        If True the service is automatically revoked after the first
        successful execution.
    """

    service_id: str
    npc_id: str
    label: str
    price: int = 0
    effects: list[dict[str, Any]] = field(default_factory=list)
    preconditions: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    assigned_tick: int = 0
    expiry_tick: int = 0  # 0 = does not expire
    source: str = "planner"
    one_shot: bool = False

    def snapshot(self) -> dict[str, Any]:
        """Return a serialisable defensive copy."""
        return {
            "service_id": self.service_id,
            "npc_id": self.npc_id,
            "label": self.label,
            "price": self.price,
            "effects": [dict(atom) for atom in self.effects],
            "preconditions": dict(self.preconditions),
            "notes": self.notes,
            "assigned_tick": self.assigned_tick,
            "expiry_tick": self.expiry_tick,
            "source": self.source,
            "one_shot": self.one_shot,
        }

    @classmethod
    def restore(cls, data: dict[str, Any]) -> "ServiceDescriptor":
        """Reconstruct from a serialised dict (lenient on missing fields)."""
        raw_effects = data.get("effects")
        effects: list[dict[str, Any]] = []
        if isinstance(raw_effects, list):
            effects = [dict(atom) for atom in raw_effects if isinstance(atom, dict)]
        raw_pre = data.get("preconditions")
        preconditions: dict[str, Any] = dict(raw_pre) if isinstance(raw_pre, dict) else {}
        return cls(
            service_id=str(data.get("service_id", "")),
            npc_id=str(data.get("npc_id", "")),
            label=str(data.get("label", "")),
            price=int(data.get("price", 0)),
            effects=effects,
            preconditions=preconditions,
            notes=str(data.get("notes", "")),
            assigned_tick=int(data.get("assigned_tick", 0)),
            expiry_tick=int(data.get("expiry_tick", 0)),
            source=str(data.get("source", "planner")),
            one_shot=bool(data.get("one_shot", False)),
        )
