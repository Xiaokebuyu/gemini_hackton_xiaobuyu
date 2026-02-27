"""NarrativePlanSlice implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.state.base import StateSlice
from app.game_core.state.delta import StateChange


class NarrativePlanSlice(StateSlice):
    """NarrativePlanner-owned planning metadata."""

    def __init__(self) -> None:
        super().__init__("narrative_plan")
        self.current_chapter = ""
        self.current_target_milestone: str | None = None
        self.chapter_completion = 0.0
        self.npc_directives: list[dict[str, Any]] = []
        self.active_bulletins: list[dict[str, Any]] = []
        self.quest_history: list[dict[str, Any]] = []
        self.escalation_level = 0
        self.ticks_since_milestone_progress = 0
        self.strategy_notes = ""
        self.last_run_tick = 0
        self.next_scheduled_tick: int | None = None
        self.pacing_frozen = False
        self.play_style_tags: list[str] = []
        self.behavior_window: list[dict[str, Any]] = []

    def restore(self, payload: Mapping[str, Any]) -> None:
        self.current_chapter = str(payload.get("current_chapter", ""))
        raw_target = payload.get("current_target_milestone")
        self.current_target_milestone = str(raw_target) if raw_target is not None else None
        self.chapter_completion = float(payload.get("chapter_completion", 0.0))
        self.npc_directives = [
            dict(item) for item in payload.get("npc_directives", [])
            if isinstance(item, Mapping)
        ]
        self.active_bulletins = [
            dict(item) for item in payload.get("active_bulletins", [])
            if isinstance(item, Mapping)
        ]
        self.quest_history = [
            dict(item) for item in payload.get("quest_history", [])
            if isinstance(item, Mapping)
        ]
        self.escalation_level = int(payload.get("escalation_level", 0))
        self.ticks_since_milestone_progress = int(
            payload.get("ticks_since_milestone_progress", 0)
        )
        self.strategy_notes = str(payload.get("strategy_notes", ""))
        self.last_run_tick = int(payload.get("last_run_tick", 0))
        raw_next = payload.get("next_scheduled_tick")
        self.next_scheduled_tick = int(raw_next) if raw_next is not None else None
        self.pacing_frozen = bool(payload.get("pacing_frozen", False))
        self.play_style_tags = [
            str(item) for item in payload.get("play_style_tags", [])
        ]
        self.behavior_window = [
            dict(item) for item in payload.get("behavior_window", [])
            if isinstance(item, Mapping)
        ]
        self.clear_dirty()

    def serialize(self) -> dict[str, Any]:
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "current_chapter": self.current_chapter,
            "current_target_milestone": self.current_target_milestone,
            "chapter_completion": self.chapter_completion,
            "npc_directives": [dict(item) for item in self.npc_directives],
            "active_bulletins": [dict(item) for item in self.active_bulletins],
            "quest_history": [dict(item) for item in self.quest_history],
            "escalation_level": self.escalation_level,
            "ticks_since_milestone_progress": self.ticks_since_milestone_progress,
            "strategy_notes": self.strategy_notes,
            "last_run_tick": self.last_run_tick,
            "next_scheduled_tick": self.next_scheduled_tick,
            "pacing_frozen": self.pacing_frozen,
            "play_style_tags": list(self.play_style_tags),
            "behavior_window": [dict(item) for item in self.behavior_window],
        }

    def record_behavior(self, entry: dict[str, Any]) -> None:
        self.behavior_window.append(dict(entry))
        self.behavior_window = self.behavior_window[-24:]
        self._dirty = True

    def set_target_milestone(self, milestone_id: str | None) -> None:
        self.current_target_milestone = milestone_id
        self._dirty = True

    def add_directive(self, directive: dict[str, Any]) -> None:
        self.npc_directives.append(dict(directive))
        self._dirty = True

    def add_bulletin(self, bulletin: dict[str, Any]) -> None:
        self.active_bulletins.append(dict(bulletin))
        self._dirty = True

    def add_history(self, summary: dict[str, Any]) -> None:
        self.quest_history.append(dict(summary))
        self._dirty = True

    def set_strategy(self, notes: str) -> None:
        self.strategy_notes = notes
        self._dirty = True

    def schedule_next(self, tick: int | None) -> None:
        self.next_scheduled_tick = tick
        self._dirty = True

    def adjust_escalation(self, delta: int) -> None:
        self.escalation_level = max(0, self.escalation_level + delta)
        self._dirty = True

    def set_pacing_frozen(self, frozen: bool) -> None:
        self.pacing_frozen = frozen
        self._dirty = True

    def apply_state_change(self, change: StateChange) -> None:
        if change.path == "npc_directives" and isinstance(change.value, Mapping):
            self.add_directive(dict(change.value))
            return
        if change.path == "active_bulletins" and isinstance(change.value, Mapping):
            self.add_bulletin(dict(change.value))
            return
        if change.path == "quest_history" and isinstance(change.value, Mapping):
            self.add_history(dict(change.value))
            return
        if change.path == "behavior_window" and isinstance(change.value, Mapping):
            self.record_behavior(dict(change.value))
            return
        if change.path == "escalation_level":
            if change.operation == "add":
                self.adjust_escalation(int(change.value))
            else:
                self.escalation_level = max(0, int(change.value))
                self._dirty = True
            return
        if change.operation in {"set", "modify"} and hasattr(self, change.path):
            setattr(self, change.path, change.value)
            self._dirty = True
            return
        raise ValueError(
            f"unsupported narrative plan change: {change.operation} {change.path}"
        )
