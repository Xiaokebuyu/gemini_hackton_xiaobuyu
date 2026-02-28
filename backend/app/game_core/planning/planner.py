"""Narrative planner skeleton."""

from __future__ import annotations

from typing import Any, Mapping


class NarrativePlanner:
    """Deterministic default planner for the runtime skeleton."""

    def plan(self, context: Any) -> dict[str, Any]:
        normalized = self._normalize_context(context)
        if normalized is None:
            return self._noop(current_tick=0, reason="invalid_context")

        current_tick = normalized["current_tick"]
        available_milestones = normalized["available_milestones"]
        dynamic_quest_ids = normalized["dynamic_quest_ids"]
        escalation_level = normalized["escalation_level"]
        stalled_ticks = normalized["ticks_since_milestone_progress"]
        pacing_frozen = normalized["pacing_frozen"]

        if available_milestones:
            milestone_id = available_milestones[0]
            quest_id = f"dq_{milestone_id}"
            if quest_id not in dynamic_quest_ids:
                milestone_label = self._milestone_label(milestone_id)
                return {
                    "directives": [
                        {
                            "kind": "create_quest",
                            "payload": {
                                "quest_id": quest_id,
                                "title": f"Lead: {milestone_label}",
                                "summary": f"Follow the new lead tied to {milestone_id}.",
                                "status": "available",
                                "metadata": {"source_milestone": milestone_id},
                            },
                        },
                        {
                            "kind": "publish_bulletin",
                            "payload": {
                                "board_id": "board",
                                "title": "New Lead Posted",
                                "content": f"A fresh lead is available: {milestone_label}.",
                                "tags": ["quest", "planner"],
                                "metadata": {
                                    "quest_id": quest_id,
                                    "source_milestone": milestone_id,
                                },
                            },
                        },
                        {
                            "kind": "direct_npc",
                            "payload": {
                                "npc_id": "guild_clerk",
                                "directive": {
                                    "kind": "present_quest",
                                    "quest_id": quest_id,
                                    "source_milestone": milestone_id,
                                },
                            },
                        },
                    ],
                    "strategy_notes": f"Guide the player toward {milestone_id}.",
                    "next_scheduled_tick": current_tick + 6,
                    "metadata": {
                        "status": "quest_seeded",
                        "provider": "default_planner",
                        "seeded_milestone": milestone_id,
                        "quest_id": quest_id,
                        "directive_count": 3,
                    },
                }

        if stalled_ticks >= 6:
            directives: list[dict[str, Any]] = []
            escalation_applied = False
            pacing_applied = False
            if escalation_level < 3:
                directives.append({"kind": "escalate", "payload": {"delta": 1}})
                escalation_applied = True
            if not pacing_frozen:
                directives.append(
                    {"kind": "adjust_pacing", "payload": {"frozen": True}}
                )
                pacing_applied = True
            return {
                "directives": directives,
                "strategy_notes": "Increase pressure until milestone progress resumes.",
                "next_scheduled_tick": current_tick + 3,
                "metadata": {
                    "status": "stall_response",
                    "provider": "default_planner",
                    "directive_count": len(directives),
                    "escalation_applied": escalation_applied,
                    "pacing_frozen_applied": pacing_applied,
                },
            }

        if pacing_frozen and stalled_ticks <= 1:
            return {
                "directives": [
                    {"kind": "adjust_pacing", "payload": {"frozen": False}}
                ],
                "strategy_notes": "Resume normal pacing.",
                "next_scheduled_tick": current_tick + 6,
                "metadata": {
                    "status": "thaw",
                    "provider": "default_planner",
                    "directive_count": 1,
                },
            }

        return self._noop(current_tick=current_tick, reason="stable")

    def _noop(self, *, current_tick: int, reason: str) -> dict[str, Any]:
        return {
            "directives": [],
            "strategy_notes": "",
            "next_scheduled_tick": current_tick + 6,
            "metadata": {
                "status": "noop",
                "provider": "default_planner",
                "reason": reason,
            },
        }

    def _normalize_context(self, context: Any) -> dict[str, Any] | None:
        if not isinstance(context, Mapping):
            return None

        raw_quests = context.get("quests", {})
        quests = raw_quests if isinstance(raw_quests, Mapping) else {}
        raw_plan = context.get("narrative_plan", {})
        narrative_plan = raw_plan if isinstance(raw_plan, Mapping) else {}
        raw_dynamic_quests = quests.get("dynamic_quests", {})
        dynamic_quests = raw_dynamic_quests if isinstance(raw_dynamic_quests, Mapping) else {}

        return {
            "current_tick": self._coerce_int(context.get("current_tick"), 0),
            "available_milestones": self._normalize_strings(
                quests.get("available_milestones", [])
            ),
            "dynamic_quest_ids": {
                quest_id
                for quest_id in (
                    self._normalize_string(item) for item in dynamic_quests.keys()
                )
                if quest_id is not None
            },
            "escalation_level": self._coerce_int(
                narrative_plan.get("escalation_level"),
                0,
            ),
            "ticks_since_milestone_progress": self._coerce_int(
                narrative_plan.get("ticks_since_milestone_progress"),
                0,
            ),
            "pacing_frozen": bool(narrative_plan.get("pacing_frozen", False)),
        }

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:
        if value is None or isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _normalize_strings(cls, raw: Any) -> list[str]:
        if not isinstance(raw, list):
            return []
        normalized: list[str] = []
        for item in raw:
            value = cls._normalize_string(item)
            if value is not None:
                normalized.append(value)
        return normalized

    @staticmethod
    def _normalize_string(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _milestone_label(milestone_id: str) -> str:
        parts = [part for part in milestone_id.replace("-", "_").split("_") if part]
        if not parts:
            return milestone_id
        return " ".join(part.capitalize() for part in parts)
