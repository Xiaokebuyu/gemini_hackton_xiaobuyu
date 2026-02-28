"""TimeSlice implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.game_core.state.base import StateSlice
from app.game_core.state.delta import StateChange


@dataclass(slots=True)
class PeriodInfo:
    name: str
    danger_factor: float
    business_state: str
    npc_activity: str


class TimeSlice(StateSlice):
    """Runtime clock and per-tick action accumulator."""

    PERIODS: dict[str, PeriodInfo] = {
        "dawn": PeriodInfo("dawn", 0.8, "opening", "shift_change"),
        "day": PeriodInfo("day", 0.5, "open", "normal"),
        "dusk": PeriodInfo("dusk", 1.0, "closing", "returning_home"),
        "night": PeriodInfo("night", 1.5, "nightlife", "night_activity"),
    }

    def __init__(self) -> None:
        super().__init__("time")
        self.day = 1
        self.slot = 8
        self.period = self._period_for_slot(self.slot)
        self.action_count = 0
        self.accumulated = 0.0

    def restore(self, payload: Mapping[str, Any]) -> None:
        self.day = int(payload.get("day", 1))
        self.slot = int(payload.get("slot", 8))
        self.period = str(payload.get("period", self._period_for_slot(self.slot)))
        self.action_count = int(payload.get("action_count", 0))
        self.accumulated = float(payload.get("accumulated", 0.0))
        self.clear_dirty()

    def serialize(self) -> dict[str, Any]:
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "slot": self.slot,
            "period": self.period,
            "action_count": self.action_count,
            "accumulated": self.accumulated,
        }

    def get_current_time(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "slot": self.slot,
            "period": self.period,
        }

    def absolute_tick(self) -> int:
        return ((self.day - 1) * 24) + self.slot

    def get_period_info(self) -> dict[str, Any]:
        info = self.PERIODS[self.period]
        return {
            "period": info.name,
            "danger_factor": info.danger_factor,
            "business_state": info.business_state,
            "npc_activity": info.npc_activity,
        }

    def advance(self, slots: int = 1) -> None:
        if slots < 0:
            raise ValueError("slots must be >= 0")
        for _ in range(slots):
            self.slot += 1
            if self.slot > 24:
                self.slot = 1
                self.day += 1
        self.period = self._period_for_slot(self.slot)
        self._dirty = True

    def add_action(self, cost: float) -> None:
        if cost < 0:
            raise ValueError("cost must be >= 0")
        self.action_count += 1
        self.accumulated += cost
        self._dirty = True

    def reset_action_count(self) -> None:
        self.action_count = 0
        self.accumulated = 0.0
        self._dirty = True

    def consume_tick(self, tick_cost: float = 1.0) -> None:
        if tick_cost <= 0:
            raise ValueError("tick_cost must be > 0")
        if self.accumulated < tick_cost:
            raise ValueError("insufficient accumulated time for settlement")
        self.accumulated -= tick_cost
        self.action_count = 0
        self._dirty = True

    def apply_state_change(self, change: StateChange) -> None:
        if change.operation in {"set", "modify"}:
            if not hasattr(self, change.path):
                raise KeyError(f"unknown time field: {change.path}")
            setattr(self, change.path, change.value)
        elif change.operation == "add":
            current = getattr(self, change.path)
            setattr(self, change.path, current + change.value)
        else:
            raise ValueError(f"unsupported time operation: {change.operation}")
        if change.path in {"slot", "day"}:
            self.period = self._period_for_slot(self.slot)
        self._dirty = True

    def validate(self) -> list[str]:
        issues: list[str] = []
        if not isinstance(self.day, int) or self.day < 1:
            issues.append("day must be >= 1")
        if not isinstance(self.slot, int) or not 1 <= self.slot <= 24:
            issues.append("slot must be between 1 and 24")
        expected_period = (
            self._period_for_slot(self.slot) if isinstance(self.slot, int) else None
        )
        if expected_period is not None and self.period != expected_period:
            issues.append(
                f"period mismatch: expected '{expected_period}', got '{self.period}'"
            )
        if not isinstance(self.action_count, int) or self.action_count < 0:
            issues.append("action_count must be >= 0")
        if not isinstance(self.accumulated, (int, float)) or self.accumulated < 0:
            issues.append("accumulated must be >= 0")
        return issues

    @classmethod
    def _period_for_slot(cls, slot: int) -> str:
        if 5 <= slot <= 7:
            return "dawn"
        if 8 <= slot <= 17:
            return "day"
        if 18 <= slot <= 19:
            return "dusk"
        return "night"
