"""QuestSlice implementation."""

from __future__ import annotations

from copy import deepcopy
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
            "dynamic_quests": deepcopy(self.dynamic_quests),
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

    def get_active_quests(self) -> list[dict[str, Any]]:
        """Return dynamic quests with status in_progress, active, or accepted."""
        active_statuses = {"in_progress", "active", "accepted"}
        return [
            dict(q) for q in self.dynamic_quests.values()
            if isinstance(q, dict) and q.get("status") in active_statuses
        ]

    def get_completion(self, chapter_id: str) -> float:
        """Return chapter completion (0.0-1.0), 0.0 if not tracked."""
        return self.chapter_completion.get(chapter_id, 0.0)

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
        dynamic_path = change.path
        if dynamic_path.startswith("dynamic."):
            dynamic_path = f"dynamic_quests.{dynamic_path[len('dynamic.'):]}"
        if dynamic_path.startswith("dynamic_quests."):
            _, rest = dynamic_path.split(".", 1)
            if "." not in rest:
                quest_id = rest
                if change.operation == "remove":
                    self.dynamic_quests.pop(quest_id, None)
                    self._dirty = True
                    return
                if not isinstance(change.value, Mapping):
                    raise ValueError(
                        f"unsupported quest state change: {change.operation} {change.path}"
                    )
                self.dynamic_quests[quest_id] = dict(change.value)
                self._dirty = True
                return

            quest_id, nested_path = rest.split(".", 1)
            if quest_id not in self.dynamic_quests:
                raise ValueError(
                    f"unsupported quest state change: missing dynamic quest '{quest_id}'"
                )
            updated = deepcopy(self.dynamic_quests[quest_id])
            self._apply_nested_dynamic_change(
                updated,
                nested_path.split("."),
                value=change.value,
                remove=change.operation == "remove",
            )
            self.dynamic_quests[quest_id] = updated
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

    @classmethod
    def _apply_nested_dynamic_change(
        cls,
        container: Any,
        path_parts: list[str],
        *,
        value: Any,
        remove: bool,
    ) -> None:
        if not path_parts:
            raise ValueError("nested quest change requires path parts")
        key = path_parts[0]
        is_leaf = len(path_parts) == 1

        if isinstance(container, list):
            try:
                index = int(key)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid list index in quest path: {key}") from exc
            if not (0 <= index < len(container)):
                raise ValueError(f"quest list index out of range: {index}")
            if is_leaf:
                if remove:
                    container.pop(index)
                else:
                    container[index] = value
                return
            cls._apply_nested_dynamic_change(
                container[index],
                path_parts[1:],
                value=value,
                remove=remove,
            )
            return

        if not isinstance(container, dict):
            raise ValueError(f"quest nested container must be dict/list, got {type(container)!r}")

        if is_leaf:
            if remove:
                container.pop(key, None)
            else:
                container[key] = value
            return

        child = container.get(key)
        if not isinstance(child, (dict, list)):
            next_key = path_parts[1]
            child = [] if next_key.isdigit() else {}
            container[key] = child
        cls._apply_nested_dynamic_change(
            child,
            path_parts[1:],
            value=value,
            remove=remove,
        )
