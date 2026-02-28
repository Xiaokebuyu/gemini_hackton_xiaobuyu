"""Deterministic default narrative planner with L0-L5 escalation ladder."""

from __future__ import annotations

from typing import Any, Mapping


class NarrativePlanner:
    """Deterministic default planner with graduated escalation.

    Priority chain:
    1. Quest seeding — first available milestone not yet backed by a quest
    2. Thaw — pacing frozen + progress resumed → unfreeze
    3. Escalation ladder (L1-L5) — graduated intervention by stall depth
    4. Area fill — enrich area with dynamic sub-areas when capacity allows
    5. Noop
    """

    _LEVEL_THRESHOLDS = [4, 7, 10, 13, 16]

    def plan(self, context: Any) -> dict[str, Any]:
        normalized = self._normalize_context(context)
        if normalized is None:
            return self._noop(current_tick=0, reason="invalid_context")

        result = self._try_seed_quest(normalized)
        if result is not None:
            return result

        result = self._try_thaw(normalized)
        if result is not None:
            return result

        result = self._try_escalation(normalized)
        if result is not None:
            return result

        result = self._try_area_fill(normalized)
        if result is not None:
            return result

        return self._noop(current_tick=normalized["current_tick"], reason="stable")

    # ------------------------------------------------------------------
    # Priority 1: Quest seeding
    # ------------------------------------------------------------------

    def _try_seed_quest(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        available = ctx["available_milestones"]
        if not available:
            return None
        milestone_id = available[0]
        quest_id = f"dq_{milestone_id}"
        if quest_id in ctx["dynamic_quest_ids"]:
            return None
        current_tick = ctx["current_tick"]
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

    # ------------------------------------------------------------------
    # Priority 2: Thaw
    # ------------------------------------------------------------------

    def _try_thaw(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        if not ctx["pacing_frozen"]:
            return None
        if ctx["ticks_since_milestone_progress"] > 1:
            return None
        current_tick = ctx["current_tick"]
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

    # ------------------------------------------------------------------
    # Priority 3: Escalation ladder (L0-L5)
    # ------------------------------------------------------------------

    def _try_escalation(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        stalled = ctx["ticks_since_milestone_progress"]
        if stalled <= 3:
            return None

        target_level = self._compute_target_level(stalled)
        current_level = ctx["escalation_level"]
        if target_level <= current_level:
            return None

        milestone_id = self._pick_target_milestone(ctx)
        if milestone_id is None:
            return None

        return self._level_response(target_level, milestone_id, ctx)

    @staticmethod
    def _compute_target_level(stalled_ticks: int) -> int:
        if stalled_ticks <= 3:
            return 0
        if stalled_ticks <= 6:
            return 1
        if stalled_ticks <= 9:
            return 2
        if stalled_ticks <= 12:
            return 3
        if stalled_ticks <= 15:
            return 4
        return 5

    @staticmethod
    def _pick_target_milestone(ctx: dict[str, Any]) -> str | None:
        if ctx["active_milestones"]:
            return ctx["active_milestones"][0]
        if ctx["available_milestones"]:
            return ctx["available_milestones"][0]
        return None

    def _level_response(
        self,
        level: int,
        milestone_id: str,
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        current_tick = ctx["current_tick"]
        milestone_label = self._milestone_label(milestone_id)
        quest_id = f"dq_{milestone_id}"

        if level == 1:
            return self._l1_hint(current_tick, milestone_id, milestone_label)
        if level == 2:
            return self._l2_recommend(current_tick, milestone_id, milestone_label)
        if level == 3:
            quest_exists = quest_id in ctx["dynamic_quest_ids"]
            return self._l3_urgent(
                current_tick, milestone_id, milestone_label,
                quest_id, quest_exists,
            )
        if level == 4:
            area_cluster = ctx.get("area_cluster")
            return self._l4_crisis(
                current_tick, milestone_id, milestone_label, area_cluster,
            )
        return self._l5_final_warning(current_tick, milestone_id)

    def _l1_hint(
        self, tick: int, milestone_id: str, label: str,
    ) -> dict[str, Any]:
        return {
            "directives": [
                {
                    "kind": "publish_bulletin",
                    "payload": {
                        "board_id": "board",
                        "title": f"Notice: {label}",
                        "content": f"Rumors about {label} are spreading.",
                        "tags": ["hint", "planner"],
                        "metadata": {"source_milestone": milestone_id},
                    },
                },
                {"kind": "escalate", "payload": {"delta": 1}},
            ],
            "strategy_notes": f"L1 hint: subtly guide toward {milestone_id}.",
            "next_scheduled_tick": tick + 3,
            "metadata": {
                "status": "escalation_l1",
                "provider": "default_planner",
                "target_milestone": milestone_id,
                "directive_count": 2,
            },
        }

    def _l2_recommend(
        self, tick: int, milestone_id: str, label: str,
    ) -> dict[str, Any]:
        return {
            "directives": [
                {
                    "kind": "direct_npc",
                    "payload": {
                        "npc_id": "guild_clerk",
                        "directive": {
                            "kind": "recommend_quest",
                            "milestone_id": milestone_id,
                            "label": label,
                            "urgency": "medium",
                        },
                    },
                },
                {"kind": "escalate", "payload": {"delta": 1}},
            ],
            "strategy_notes": f"L2 recommend: NPC guides toward {milestone_id}.",
            "next_scheduled_tick": tick + 3,
            "metadata": {
                "status": "escalation_l2",
                "provider": "default_planner",
                "target_milestone": milestone_id,
                "directive_count": 2,
            },
        }

    def _l3_urgent(
        self,
        tick: int,
        milestone_id: str,
        label: str,
        quest_id: str,
        quest_exists: bool,
    ) -> dict[str, Any]:
        directives: list[dict[str, Any]] = []
        if not quest_exists:
            directives.append({
                "kind": "create_quest",
                "payload": {
                    "quest_id": quest_id,
                    "title": f"Urgent: {label}",
                    "summary": f"An urgent matter tied to {milestone_id}.",
                    "status": "available",
                    "metadata": {
                        "source_milestone": milestone_id,
                        "urgency": "high",
                    },
                },
            })
        directives.append({
            "kind": "direct_npc",
            "payload": {
                "npc_id": "guild_clerk",
                "directive": {
                    "kind": "present_quest",
                    "quest_id": quest_id,
                    "milestone_id": milestone_id,
                    "urgency": "high",
                },
            },
        })
        directives.append({"kind": "escalate", "payload": {"delta": 1}})
        return {
            "directives": directives,
            "strategy_notes": f"L3 urgent: press player toward {milestone_id}.",
            "next_scheduled_tick": tick + 3,
            "metadata": {
                "status": "escalation_l3",
                "provider": "default_planner",
                "target_milestone": milestone_id,
                "quest_created": not quest_exists,
                "directive_count": len(directives),
            },
        }

    def _l4_crisis(
        self,
        tick: int,
        milestone_id: str,
        label: str,
        area_cluster: dict[str, Any] | None,
    ) -> dict[str, Any]:
        directives: list[dict[str, Any]] = [
            {"kind": "escalate", "payload": {"delta": 1}},
            {"kind": "adjust_pacing", "payload": {"frozen": True}},
        ]
        if area_cluster and area_cluster.get("area_id"):
            directives.append({
                "kind": "plant_environmental",
                "payload": {
                    "area_id": area_cluster["area_id"],
                    "dc": 10,
                    "description": f"Clues related to {label}.",
                },
            })
        return {
            "directives": directives,
            "strategy_notes": f"L4 crisis: world deteriorates around {milestone_id}.",
            "next_scheduled_tick": tick + 3,
            "metadata": {
                "status": "escalation_l4",
                "provider": "default_planner",
                "target_milestone": milestone_id,
                "directive_count": len(directives),
            },
        }

    @staticmethod
    def _l5_final_warning(tick: int, milestone_id: str) -> dict[str, Any]:
        return {
            "directives": [
                {"kind": "escalate", "payload": {"delta": 2}},
            ],
            "strategy_notes": f"L5 final warning: last chance for {milestone_id}.",
            "next_scheduled_tick": tick + 3,
            "metadata": {
                "status": "escalation_l5",
                "provider": "default_planner",
                "target_milestone": milestone_id,
                "directive_count": 1,
            },
        }

    # ------------------------------------------------------------------
    # Priority 4: Area fill
    # ------------------------------------------------------------------

    def _try_area_fill(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        area = ctx.get("area_cluster")
        if area is None or not area.get("has_capacity"):
            return None
        if area.get("total_dynamic", 0) >= 2:
            return None

        current_tick = ctx["current_tick"]
        area_id = area["area_id"]
        if not area_id:
            return None
        return {
            "directives": [
                {
                    "kind": "fill_area",
                    "payload": {
                        "area_id": area_id,
                        "id": f"fill_{current_tick}",
                        "label": "Point of Interest",
                        "description": "A location worth exploring.",
                    },
                },
            ],
            "strategy_notes": f"Area {area_id} needs content enrichment.",
            "next_scheduled_tick": current_tick + 6,
            "metadata": {
                "status": "area_fill",
                "provider": "default_planner",
                "area_id": area_id,
                "directive_count": 1,
            },
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
        dynamic_quests = (
            raw_dynamic_quests if isinstance(raw_dynamic_quests, Mapping) else {}
        )

        return {
            "current_tick": self._coerce_int(context.get("current_tick"), 0),
            "available_milestones": self._normalize_strings(
                quests.get("available_milestones", [])
            ),
            "active_milestones": self._normalize_strings(
                quests.get("active_milestones", [])
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
            "area_cluster": self._normalize_area_cluster(
                context.get("area_cluster")
            ),
        }

    @staticmethod
    def _normalize_area_cluster(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, Mapping):
            return None
        area_id = raw.get("area_id", "")
        if not isinstance(area_id, str) or not area_id.strip():
            return None
        try:
            total = int(raw.get("total_dynamic", 0))
        except (TypeError, ValueError):
            total = 0
        return {
            "area_id": area_id.strip(),
            "has_capacity": bool(raw.get("has_capacity", False)),
            "total_dynamic": total,
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
