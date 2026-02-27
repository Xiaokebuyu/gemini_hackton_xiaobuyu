"""Narrative planner skeleton."""

from __future__ import annotations

from typing import Any

from app.game_core.planning.models import PlanningDirective


class NarrativePlanner:
    """Planner skeleton that returns explicit no-op output."""

    def plan(self, context: Any) -> list[PlanningDirective]:
        del context
        return []
