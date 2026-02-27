"""QuestSlice implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.state.base import StateSlice
from app.game_core.state.delta import StateChange


class QuestSlice(StateSlice):
    """Milestone and dynamic quest runtime state."""

    def __init__(self) -> None:
        super().__init__("quests")
        self.milestone_states: dict[str, str] = {}
        self.dynamic_quests: dict[str, dict[str, Any]] = {}
        self.chapter_completion: dict[str, float] = {}

    def restore(self, payload: Mapping[str, Any]) -> None:
        self.milestone_states = {
            str(key): str(value)
            for key, value in payload.get("milestone_states", {}).items()
        }
        self.dynamic_quests = {
            str(key): dict(value)
            for key, value in payload.get("dynamic_quests", {}).items()
            if isinstance(value, Mapping)
        }
        self.chapter_completion = {
            str(key): float(value)
            for key, value in payload.get("chapter_completion", {}).items()
        }
        self.clear_dirty()

    def serialize(self) -> dict[str, Any]:
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "milestone_states": dict(self.milestone_states),
            "dynamic_quests": {
                key: dict(value)
                for key, value in self.dynamic_quests.items()
            },
            "chapter_completion": dict(self.chapter_completion),
        }

    def get_milestone_state(self, milestone_id: str) -> str | None:
        return self.milestone_states.get(milestone_id)

    def get_dynamic_quest(self, quest_id: str) -> dict[str, Any] | None:
        quest = self.dynamic_quests.get(quest_id)
        return dict(quest) if isinstance(quest, dict) else None

    def get_available_milestones(self) -> list[str]:
        return [
            milestone_id
            for milestone_id, state in self.milestone_states.items()
            if state in {"AVAILABLE", "ACTIVE"}
        ]

    def advance_milestone(self, milestone_id: str, to_state: str) -> None:
        self.milestone_states[milestone_id] = to_state
        self._dirty = True

    def add_dynamic_quest(self, quest_id: str, quest: dict[str, Any]) -> None:
        self.dynamic_quests[quest_id] = dict(quest)
        self._dirty = True

    def advance_quest(self, quest_id: str, to_state: str) -> None:
        if quest_id in self.dynamic_quests:
            self.dynamic_quests[quest_id]["status"] = to_state
        else:
            self.milestone_states[quest_id] = to_state
        self._dirty = True

    def retire_dynamic_quest(self, quest_id: str) -> None:
        if quest_id in self.dynamic_quests:
            self.dynamic_quests[quest_id]["status"] = "retired"
            self._dirty = True

    def modify_completion(self, chapter_id: str, delta: float) -> None:
        self.chapter_completion[chapter_id] = max(
            0.0,
            min(1.0, self.chapter_completion.get(chapter_id, 0.0) + delta),
        )
        self._dirty = True

    def apply_state_change(self, change: StateChange) -> None:
        if change.path.startswith("milestone_states."):
            _, milestone_id = change.path.split(".", 1)
            self.advance_milestone(milestone_id, str(change.value))
            return
        if change.path.startswith("dynamic_quests.") and isinstance(change.value, Mapping):
            _, quest_id = change.path.split(".", 1)
            if change.operation == "remove":
                self.dynamic_quests.pop(quest_id, None)
                self._dirty = True
            else:
                self.dynamic_quests[quest_id] = dict(change.value)
                self._dirty = True
            return
        if change.path.startswith("chapter_completion."):
            _, chapter_id = change.path.split(".", 1)
            if change.operation == "add":
                self.modify_completion(chapter_id, float(change.value))
            else:
                self.chapter_completion[chapter_id] = float(change.value)
                self._dirty = True
            return
        raise ValueError(
            f"unsupported quest state change: {change.operation} {change.path}"
        )
