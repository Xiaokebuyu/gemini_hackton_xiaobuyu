"""Deterministic opening bootstrap planner assembly.

This module only covers the new-game opening seed path. It does not replace
the normal LLM planner loop; outside the bootstrap event it returns no-op.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.adapters.planner_system import PlannerSystemAssembly


class OpeningBootstrapQuestAgent:
    """Seed the first available milestone into one starter dynamic quest."""

    @property
    def history_key(self) -> str:
        return "__opening_bootstrap_quest_agent__"

    async def evaluate(self, context: dict[str, Any]) -> dict[str, Any]:
        event = context.get("current_event", {})
        if not isinstance(event, Mapping):
            event = {}
        event_kind = str(event.get("kind", "")).strip().lower()
        if event_kind != "bootstrap":
            return self._noop(reason="stable")

        quest_context = context.get("quests", {})
        if not isinstance(quest_context, Mapping):
            quest_context = {}
        milestone_id = self._first_opening_milestone(quest_context)
        if milestone_id is None:
            return self._noop(reason="no_available_milestone")

        quest_id = f"dq_{milestone_id}"
        dynamic_quests = quest_context.get("dynamic_quests", {})
        if isinstance(dynamic_quests, Mapping) and quest_id in dynamic_quests:
            return self._noop(reason="already_seeded")

        milestone_name = self._display_name(milestone_id)
        directives: list[dict[str, Any]] = [
            {
                "kind": "create_quest",
                "payload": {
                    "quest_id": quest_id,
                    "title": f"Lead: {milestone_name}",
                    "summary": f"Follow the new lead tied to {milestone_id}.",
                    "requires_report": True,
                    "delivery_method": "board",
                    "metadata": {
                        "source_milestone": milestone_id,
                        "urgency": "medium",
                    },
                },
            }
        ]

        return {
            "directives": directives,
            "story_facts": [],
            "strategy_notes": "",
            "metadata": {
                "status": "quest_seeded",
                "provider": "opening_bootstrap_agent",
            },
        }

    def export_history(self) -> list[dict[str, Any]]:
        return []

    def import_history(self, data: list[dict[str, Any]]) -> None:
        del data

    @staticmethod
    def _noop(*, reason: str) -> dict[str, Any]:
        return {
            "directives": [],
            "story_facts": [],
            "strategy_notes": "",
            "metadata": {
                "status": "noop",
                "provider": "opening_bootstrap_agent",
                "reason": reason,
            },
        }

    @staticmethod
    def _first_opening_milestone(quest_context: Mapping[str, Any]) -> str | None:
        for key in ("available_milestones", "active_milestones"):
            raw_milestones = quest_context.get(key, [])
            if not isinstance(raw_milestones, list):
                continue
            for raw_milestone_id in raw_milestones:
                milestone_id = str(raw_milestone_id or "").strip()
                if milestone_id:
                    return milestone_id
        return None

    @staticmethod
    def _display_name(milestone_id: str) -> str:
        return milestone_id.replace("_", " ").strip().title() or milestone_id

    @staticmethod
    def _first_area_board(context: Mapping[str, Any]) -> dict[str, str] | None:
        location = context.get("location", {})
        if not isinstance(location, Mapping):
            location = {}
        area_id = str(location.get("area_id", "") or "").strip()
        if not area_id:
            return None
        raw_area_boards = context.get("area_boards", [])
        if not isinstance(raw_area_boards, list):
            return None
        for raw_board in raw_area_boards:
            if not isinstance(raw_board, Mapping):
                continue
            board_id = str(raw_board.get("id", "") or "").strip()
            sub_location = str(raw_board.get("sub_location", "") or "").strip()
            if board_id and sub_location:
                return {
                    "area_id": area_id,
                    "board_id": board_id,
                    "sub_location": sub_location,
                }
        return None


def build_opening_bootstrap_planner_system() -> PlannerSystemAssembly:
    """Return the minimal planner system needed for opening bootstrap replay."""
    return PlannerSystemAssembly(
        blackboard=None,
        quest_manager_agent=OpeningBootstrapQuestAgent(),
    )
