"""QuestSlice implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.game_core.state.base import StateSlice
from app.game_core.state.delta import StateChange


@dataclass(slots=True)
class MilestoneState:
    """Per-milestone runtime state (matches 状态层 §3.4)."""

    state: str = "LOCKED"
    activated_tick: int | None = None
    completed_tick: int | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "activated_tick": self.activated_tick,
            "completed_tick": self.completed_tick,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> MilestoneState:
        if isinstance(raw, MilestoneState):
            return MilestoneState(
                state=raw.state,
                activated_tick=raw.activated_tick,
                completed_tick=raw.completed_tick,
            )
        if isinstance(raw, str):
            return MilestoneState(state=raw)
        if isinstance(raw, Mapping):
            return MilestoneState(
                state=str(raw.get("state", "LOCKED")),
                activated_tick=(
                    int(raw["activated_tick"]) if raw.get("activated_tick") is not None else None
                ),
                completed_tick=(
                    int(raw["completed_tick"]) if raw.get("completed_tick") is not None else None
                ),
            )
        return MilestoneState()


class QuestSlice(StateSlice):
    """Milestone and dynamic quest runtime state."""

    def __init__(self) -> None:
        super().__init__("quests")
        self.milestone_states: dict[str, MilestoneState] = {}
        self.dynamic_quests: dict[str, dict[str, Any]] = {}
        self.chapter_completion: dict[str, float] = {}

    def restore(self, payload: Mapping[str, Any]) -> None:
        self.milestone_states = {
            str(key): MilestoneState.from_dict(value)
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
            "milestone_states": {
                key: ms.snapshot()
                for key, ms in self.milestone_states.items()
            },
            "dynamic_quests": {
                key: dict(value)
                for key, value in self.dynamic_quests.items()
            },
            "chapter_completion": dict(self.chapter_completion),
        }

    def get_milestone_state(self, milestone_id: str) -> str | None:
        ms = self.milestone_states.get(milestone_id)
        return ms.state if ms is not None else None

    def get_milestone(self, milestone_id: str) -> MilestoneState | None:
        return self.milestone_states.get(milestone_id)

    def get_dynamic_quest(self, quest_id: str) -> dict[str, Any] | None:
        quest = self.dynamic_quests.get(quest_id)
        return dict(quest) if isinstance(quest, dict) else None

    def get_available_milestones(self) -> list[str]:
        return [
            milestone_id
            for milestone_id, ms in self.milestone_states.items()
            if ms.state in {"AVAILABLE", "ACTIVE"}
        ]

    def advance_milestone(
        self,
        milestone_id: str,
        to_state: str,
        tick: int | None = None,
    ) -> None:
        ms = self.milestone_states.setdefault(milestone_id, MilestoneState())
        ms.state = to_state
        if to_state == "ACTIVE" and ms.activated_tick is None:
            ms.activated_tick = tick
        if to_state in {"COMPLETED", "FAILED"} and ms.completed_tick is None:
            ms.completed_tick = tick
        self._dirty = True

    def add_dynamic_quest(self, quest_id: str, quest: dict[str, Any]) -> None:
        self.dynamic_quests[quest_id] = dict(quest)
        self._dirty = True

    def advance_quest(self, quest_id: str, to_state: str) -> None:
        if quest_id in self.dynamic_quests:
            self.dynamic_quests[quest_id]["status"] = to_state
        else:
            self.advance_milestone(quest_id, to_state)
            return
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

    def validate(self) -> list[str]:
        issues: list[str] = []
        if not isinstance(self.milestone_states, dict):
            issues.append("milestone_states must be a dict")
        else:
            for milestone_id, milestone in self.milestone_states.items():
                if not isinstance(milestone, MilestoneState):
                    issues.append(
                        f"milestone_states[{milestone_id}] must be a MilestoneState"
                    )

        if not isinstance(self.dynamic_quests, dict):
            issues.append("dynamic_quests must be a dict")
        if not isinstance(self.chapter_completion, dict):
            issues.append("chapter_completion must be a dict")
        else:
            for chapter_id, value in self.chapter_completion.items():
                try:
                    normalized = float(value)
                except (TypeError, ValueError):
                    issues.append(
                        f"chapter_completion[{chapter_id}] must be a number"
                    )
                    continue
                if not 0.0 <= normalized <= 1.0:
                    issues.append(
                        f"chapter_completion[{chapter_id}] must be between 0.0 and 1.0"
                    )

        return issues

    def apply_state_change(self, change: StateChange) -> None:
        if change.path.startswith("milestone_states."):
            _, milestone_id = change.path.split(".", 1)
            if isinstance(change.value, Mapping):
                self.advance_milestone(
                    milestone_id,
                    str(change.value.get("state", "LOCKED")),
                    tick=change.value.get("tick"),
                )
            else:
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
