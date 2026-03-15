"""Exploration-related agent tools for NPC and Teammate roles.

Provides:
- DiscoverClueTool: NPC/Teammate can trigger clue investigation for an
  unresolved interactable in the current location.
- ShareDiscoveryTool: Teammate-only tool to share a discovery with the player
  via a teammate_discovery SSE event.

Design ref: known_issues.md §4-B.
"""

from __future__ import annotations

import logging
from typing import Any

from app.game_core.narrative.character_tools import _CharacterTool
from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.models import ToolResult
from app.game_core.rules.models import Command

logger = logging.getLogger(__name__)


class DiscoverClueTool(_CharacterTool):
    """Investigate an unresolved clue in the current location.

    Checks whether *clue_id* exists as an unresolved interactable in the
    scoped_interactable_overlays for the player's current location, then
    executes the ``investigate_clue`` command and writes a result entry to
    area_events.

    Allowed for both NPC and Teammate roles.
    """

    @property
    def name(self) -> str:
        return "discover_clue"

    @property
    def description(self) -> str:
        return (
            "Investigate an interactable clue in the current location. "
            "Provide the clue_id of an unexamined clue to inspect it and "
            "record the discovery in the area event log."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "clue_id": {
                    "type": "string",
                    "description": "The ID of the clue/interactable to investigate.",
                },
            },
            "required": ["clue_id"],
        }

    @property
    def allowed_roles(self) -> list[str]:
        return ["npc", "teammate"]

    @property
    def applicable_traits(self) -> list[str]:
        return []

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        character_id = self._get_character_id(context)
        if not character_id:
            return self._no_character_id()

        clue_id = self._require_text(params, "clue_id")
        if not clue_id:
            return ToolResult(
                ok=False,
                message="clue_id is required.",
                metadata={"status": "invalid_params"},
            )

        # Validate state slices are present.
        if not context.state.has_slice("areas"):
            return ToolResult(
                ok=False,
                message="areas slice unavailable.",
                metadata={"status": "state_unavailable"},
            )
        if not context.state.has_slice("player"):
            return ToolResult(
                ok=False,
                message="player slice unavailable.",
                metadata={"status": "state_unavailable"},
            )

        area_id: str = context.state.player.current_area
        location_id: str | None = context.state.player.current_location

        # Check clue exists in scoped overlays for current location and is
        # not yet resolved (i.e., not in interactable_states).
        overlay_check = self._find_overlay(context, area_id, location_id, clue_id)
        if overlay_check is None:
            return ToolResult(
                ok=False,
                message=f"Clue '{clue_id}' not found or not accessible in the current location.",
                metadata={"status": "clue_not_found", "clue_id": clue_id},
            )

        already_resolved = self._is_resolved(context, area_id, clue_id)
        if already_resolved:
            return ToolResult(
                ok=False,
                message=f"Clue '{clue_id}' has already been investigated.",
                metadata={"status": "clue_already_resolved", "clue_id": clue_id},
            )

        # Issue the investigate_clue command.
        cmd = Command(
            type="investigate_clue",
            params={"interactable_id": clue_id},
            source="npc_autonomy",
        )
        result = context.run_command(cmd)
        if not result.executed:
            err = result.errors[0] if result.errors else "command failed"
            return ToolResult(
                ok=False,
                message=f"Failed to investigate clue: {err}",
                metadata={"status": "command_failed", "clue_id": clue_id},
            )

        # Write a discovery event to area_events.
        clue_name = overlay_check.get("name", clue_id)
        event_dict = {
            "tick": self._current_tick(context),
            "event": f"{character_id} investigated {clue_name} ({clue_id})",
            "source": "npc_autonomy",
            "severity": "minor",
        }
        try:
            context.state.areas.append_area_event(area_id, event_dict)
        except Exception:  # noqa: BLE001
            logger.debug(
                "discover_clue: failed to write area_event for clue=%s", clue_id, exc_info=True,
            )

        return ToolResult(
            ok=True,
            message=f"Investigated clue '{clue_name}'.",
            metadata={
                "status": "ok",
                "event_type": "clue_investigated",
                "character_id": character_id,
                "clue_id": clue_id,
                "clue_name": clue_name,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_overlay(
        context: AgentContext,
        area_id: str,
        location_id: str | None,
        clue_id: str,
    ) -> dict[str, Any] | None:
        """Return the overlay dict for *clue_id* if found in the current location, else None."""
        if location_id is None:
            return None

        area_state = context.state.areas.areas.get(area_id)
        if area_state is None:
            return None

        # Match any scope key that starts with the location prefix.
        for scope_key, overlays in area_state.scoped_interactable_overlays.items():
            if scope_key != location_id and not scope_key.startswith(f"{location_id}__"):
                continue
            if not isinstance(overlays, list):
                continue
            for overlay in overlays:
                if not isinstance(overlay, dict):
                    continue
                if overlay.get("id") == clue_id:
                    return overlay
        return None

    @staticmethod
    def _is_resolved(
        context: AgentContext,
        area_id: str,
        clue_id: str,
    ) -> bool:
        """Return True if the clue is already in interactable_states."""
        area_state = context.state.areas.areas.get(area_id)
        if area_state is None:
            return False
        return clue_id in area_state.interactable_states

    @staticmethod
    def _current_tick(context: AgentContext) -> int:
        """Return the current game tick, or 0 if unavailable."""
        if not context.state.has_slice("time"):
            return 0
        snap = context.state.time.snapshot()
        return int(snap.get("slot", snap.get("tick", 0)))


class ShareDiscoveryTool(_CharacterTool):
    """Share a discovery or observation with the player.

    Teammate-only tool.  Emits a ``teammate_discovery`` SSE event via the
    tool result metadata.  The content is free-form text describing what the
    teammate has noticed or found.
    """

    @property
    def name(self) -> str:
        return "share_discovery"

    @property
    def description(self) -> str:
        return (
            "Share a discovery or notable observation with the player. "
            "Use this when you notice something important that the player "
            "should know about."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "What you have discovered or noticed.",
                },
            },
            "required": ["content"],
        }

    @property
    def allowed_roles(self) -> list[str]:
        return ["teammate"]

    @property
    def applicable_traits(self) -> list[str]:
        return []

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        character_id = self._get_character_id(context)
        if not character_id:
            return self._no_character_id()

        content = self._require_text(params, "content")
        if not content:
            return ToolResult(
                ok=False,
                message="content is required.",
                metadata={"status": "invalid_params"},
            )

        # Write to scene bus so the discovery appears in the scene log.
        self._add_scene_entry(
            context,
            character_id,
            content,
            tags=["teammate_discovery", "discovery"],
        )

        return ToolResult(
            ok=True,
            message=content,
            metadata={
                "status": "ok",
                "event_type": "teammate_discovery",
                "character_id": character_id,
                "content": content,
            },
        )
