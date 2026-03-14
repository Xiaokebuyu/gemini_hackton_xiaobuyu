"""CapabilityDescriptor — runtime-assigned dynamic capabilities for NPCs.

Decision record: D-P28-A (P28-动态能力系统与综合体验修复.md §5)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Valid functional binding types for CapabilityDescriptor.
# Empty string means no UI binding (pure behavioural guidance only).
# NOTE: "quest_accept" is intentionally excluded — GM is a pure narrator.
# Quest acceptance is handled by NPC tools (accept_quest), not GM functional options.
VALID_FUNCTIONAL_TYPES: frozenset[str] = frozenset({
    "trade_browse",
    "board_browse",
    "navigate",
    "inspect_item",
    "rest",
    "",
})


@dataclass(slots=True)
class CapabilityDescriptor:
    """Runtime capability assigned to an NPC by the narrative planner.

    A descriptor carries:
    - ``instruction``: Chinese behavioural guidance injected into the NPC system
      prompt so the NPC knows when and how to use this capability.
    - ``functional``: optional UI binding type (see ``VALID_FUNCTIONAL_TYPES``).
    - lifecycle fields (``assigned_tick``, ``expiry_tick``).
    """

    capability_id: str
    npc_id: str
    instruction: str
    functional: str = ""
    functional_params: dict[str, Any] = field(default_factory=dict)
    assigned_tick: int = 0
    expiry_tick: int = 0  # 0 = does not expire
    source: str = "planner"

    def snapshot(self) -> dict[str, Any]:
        """Return a serialisable defensive copy."""
        return {
            "capability_id": self.capability_id,
            "npc_id": self.npc_id,
            "instruction": self.instruction,
            "functional": self.functional,
            "functional_params": dict(self.functional_params),
            "assigned_tick": self.assigned_tick,
            "expiry_tick": self.expiry_tick,
            "source": self.source,
        }

    @classmethod
    def restore(cls, data: dict[str, Any]) -> "CapabilityDescriptor":
        """Reconstruct from a serialised dict (lenient on missing fields)."""
        fp = data.get("functional_params")
        return cls(
            capability_id=str(data.get("capability_id", "")),
            npc_id=str(data.get("npc_id", "")),
            instruction=str(data.get("instruction", "")),
            functional=str(data.get("functional", "")),
            functional_params=dict(fp) if isinstance(fp, dict) else {},
            assigned_tick=int(data.get("assigned_tick", 0)),
            expiry_tick=int(data.get("expiry_tick", 0)),
            source=str(data.get("source", "planner")),
        )
