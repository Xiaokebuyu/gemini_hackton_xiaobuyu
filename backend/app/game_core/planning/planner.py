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

    async def plan(self, context: Any) -> dict[str, Any]:
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
        board_entry = self._get_default_board_entry(ctx)
        board_id = board_entry["board_id"] if board_entry is not None else None
        area_id = board_entry["area_id"] if board_entry is not None else None
        if area_id is None and isinstance(ctx.get("location"), Mapping):
            area_id = self._normalize_string(ctx["location"].get("area_id"))
        current_tick = ctx["current_tick"]
        milestone_label = self._milestone_label(milestone_id)
        directives: list[dict[str, Any]] = [
            {
                "kind": "create_quest",
                "payload": {
                    "quest_id": quest_id,
                    "title": f"Lead: {milestone_label}",
                    "summary": f"Follow the new lead tied to {milestone_id}.",
                    "status": "available",
                    "objectives": [],
                    "rewards": {},
                    "delivery_method": "board" if board_id is not None else "system",
                    "expiry_ticks": None,
                    "on_expire": "ignore",
                    "generated_by_escalation": 0,
                    "planner_reasoning": f"Seeded from {milestone_id} availability.",
                    "metadata": {
                        "source_milestone": milestone_id,
                        "urgency": "medium",
                    },
                },
            },
        ]
        if board_id is not None:
            publish_payload: dict[str, Any] = {
                "board_id": board_id,
                "title": "New Lead Posted",
                "content": f"A fresh lead is available: {milestone_label}.",
                "tags": ["quest", "planner"],
                "urgency": "medium",
                "posted_by": "narrative_planner",
                "metadata": {
                    "quest_id": quest_id,
                    "source_milestone": milestone_id,
                },
            }
            if area_id is not None:
                publish_payload["area_id"] = area_id
            if board_entry is not None:
                publish_payload["location"] = {
                    "area_id": board_entry.get("area_id", ""),
                    "sub_location": board_entry.get("sub_location", ""),
                }
            directives.append({
                "kind": "publish_bulletin",
                "payload": publish_payload,
            })
        default_npc = self._pick_npc(ctx, ctx.get("target_milestone_detail"))
        if default_npc is not None:
            directives.append({
                "kind": "direct_npc",
                "payload": {
                    "npc_id": default_npc,
                    "directive": {
                        "kind": "present_quest",
                        "quest_id": quest_id,
                        "source_milestone": milestone_id,
                    },
                },
            })
        return {
            "directives": directives,
            "strategy_notes": f"Guide the player toward {milestone_id}.",
            "next_scheduled_tick": current_tick + 6,
            "metadata": {
                "status": "quest_seeded",
                "provider": "default_planner",
                "seeded_milestone": milestone_id,
                "quest_id": quest_id,
                "directive_count": len(directives),
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
        target_detail: dict[str, Any] = ctx.get("target_milestone_detail") or {}

        if level == 1:
            board_info = self._resolve_quest_board(ctx)
            return self._l1_hint(
                current_tick,
                milestone_id,
                milestone_label,
                ctx=ctx,
                board_info=board_info,
            )
        if level == 2:
            return self._l2_recommend(
                current_tick,
                milestone_id,
                milestone_label,
                ctx,
                target_detail,
            )
        if level == 3:
            quest_exists = quest_id in ctx["dynamic_quest_ids"]
            return self._l3_urgent(
                current_tick,
                milestone_id,
                milestone_label,
                quest_id,
                quest_exists,
                ctx,
                target_detail,
            )
        if level == 4:
            area_cluster = ctx.get("area_cluster")
            return self._l4_crisis(
                current_tick, milestone_id, milestone_label, area_cluster,
            )
        return self._l5_final_warning(current_tick, milestone_id)

    def _l1_hint(
        self,
        tick: int,
        milestone_id: str,
        label: str,
        ctx: dict[str, Any] | None = None,
        board_info: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        if board_info is None:
            if ctx is not None:
                board_info = self._get_default_board(ctx)
            if board_info is None:
                return {
                    "directives": [
                        {"kind": "escalate", "payload": {"delta": 1}},
                    ],
                    "strategy_notes": f"L1 hint: subtly guide toward {milestone_id}.",
                    "next_scheduled_tick": tick + 3,
                    "metadata": {
                        "status": "escalation_l1",
                        "provider": "default_planner",
                        "target_milestone": milestone_id,
                        "directive_count": 1,
                    },
                }
            _, fallback_board = board_info
            board_id = fallback_board
        else:
            board_id = board_info[1]
        if not board_id:
            return {
                "directives": [
                    {"kind": "escalate", "payload": {"delta": 1}},
                ],
                "strategy_notes": f"L1 hint: subtly guide toward {milestone_id}.",
                "next_scheduled_tick": tick + 3,
                "metadata": {
                    "status": "escalation_l1",
                    "provider": "default_planner",
                    "target_milestone": milestone_id,
                    "directive_count": 1,
                },
            }
        return {
            "directives": [
                {
                    "kind": "publish_bulletin",
                    "payload": {
                        "board_id": board_id,
                        "title": f"Notice: {label}",
                        "content": f"Rumors about {label} are spreading.",
                        "tags": ["hint", "planner"],
                        "urgency": "low",
                        "posted_by": "narrative_planner",
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
        self,
        tick: int,
        milestone_id: str,
        label: str,
        ctx: dict[str, Any] | None = None,
        target_detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        npc_id = self._pick_npc(ctx or {}, target_detail or {})
        if npc_id is None:
            return {
                "directives": [
                    {"kind": "escalate", "payload": {"delta": 1}},
                ],
                "strategy_notes": f"L2 recommend: no available NPC for {milestone_id}.",
                "next_scheduled_tick": tick + 3,
                "metadata": {
                    "status": "escalation_l2",
                    "provider": "default_planner",
                    "target_milestone": milestone_id,
                    "directive_count": 1,
                },
            }
        return {
            "directives": [
                {
                    "kind": "direct_npc",
                    "payload": {
                        "npc_id": npc_id,
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
            "strategy_notes": f"L2 recommend: NPC {npc_id} guides toward {milestone_id}.",
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
        ctx: dict[str, Any] | None = None,
        target_detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        detail = target_detail or {}
        npc_id = self._pick_npc(ctx or {}, target_detail or {})
        key_elements: list[str] = detail.get("key_elements", [])
        involved_locations: list[str] = detail.get("involved_locations", [])
        narrative_context: str = detail.get("narrative_context", "")

        if narrative_context:
            summary = narrative_context
        elif key_elements:
            summary = f"Involves: {', '.join(key_elements[:3])}."
        else:
            summary = f"An urgent matter tied to {milestone_id}."

        directives: list[dict[str, Any]] = []
        if not quest_exists:
            directives.append({
                "kind": "create_quest",
                "payload": {
                    "quest_id": quest_id,
                    "title": f"Urgent: {label}",
                    "summary": summary,
                    "status": "available",
                    "objectives": [
                        {"kind": "key_element", "value": value}
                        for value in key_elements
                    ],
                    "rewards": {},
                    "delivery_method": "board",
                    "expiry_ticks": 12,
                    "on_expire": "escalate",
                    "generated_by_escalation": 3,
                    "planner_reasoning": f"L3 urgent escalation for {milestone_id}.",
                    "metadata": {
                        "source_milestone": milestone_id,
                        "urgency": "high",
                        "generated_by_escalation": 3,
                        "key_elements": key_elements,
                        "involved_locations": involved_locations,
                    },
                },
            })
        if npc_id is not None:
            directives.append({
                "kind": "direct_npc",
                "payload": {
                    "npc_id": npc_id,
                    "directive": {
                        "kind": "present_quest",
                        "quest_id": quest_id,
                        "milestone_id": milestone_id,
                        "urgency": "high",
                    },
                },
            })
        directives.append({"kind": "escalate", "payload": {"delta": 1}})
        strategy_notes = f"L3 urgent: milestone {milestone_id} requires escalation."
        if npc_id is not None:
            strategy_notes = f"L3 urgent: NPC {npc_id} presses player toward {milestone_id}."
        return {
            "directives": directives,
            "strategy_notes": strategy_notes,
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
        if (
            ctx.get("available_milestones")
            or ctx.get("active_milestones")
            or not ctx.get("dynamic_quest_ids")
        ):
            return None

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
            "location": context.get("location") if isinstance(context.get("location"), Mapping) else {},
            "maps": context.get("maps"),
            "area_boards": self._normalize_area_boards(context.get("area_boards")),
            "default_board_id": self._normalize_default_board_id(context.get("area_boards")),
            "area_npcs": self._normalize_strings(context.get("area_npcs")),
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
            "target_milestone_detail": dict(context.get("target_milestone_detail") or {}),
        }

    @staticmethod
    def _normalize_interactable_tags(raw_tags: Any) -> list[str]:
        if not isinstance(raw_tags, list):
            return []
        return [str(tag).strip() for tag in raw_tags if str(tag).strip()]

    @classmethod
    def _normalize_area_boards(cls, raw: Any) -> list[dict[str, str]]:
        if not isinstance(raw, list):
            return []
        result: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            raw_board_id = cls._normalize_string(item.get("id"))
            if raw_board_id is None:
                continue
            raw_sub_location = str(item.get("sub_location", "")).strip()
            result.append({
                "id": raw_board_id,
                "sub_location": raw_sub_location,
            })
        return result

    @classmethod
    def _normalize_default_board_id(cls, area_boards: Any) -> str | None:
        boards = cls._normalize_area_boards(area_boards)
        first_board = boards[0] if boards else None
        if first_board is None:
            return None
        return first_board.get("id")

    @staticmethod
    def _pick_npc(ctx: Mapping[str, Any], target_detail: Mapping[str, Any] | None = None) -> str | None:
        target_detail = target_detail or {}
        involved_raw = target_detail.get("involved_npcs")
        if isinstance(involved_raw, list):
            involved = [str(item) for item in involved_raw if str(item).strip()]
        else:
            involved = []
        area_npcs = []
        raw_area_npcs = ctx.get("area_npcs", [])
        if isinstance(raw_area_npcs, list):
            area_npcs = [str(item) for item in raw_area_npcs if str(item).strip()]

        for npc in involved:
            if not str(npc).strip():
                continue
            if npc in area_npcs:
                return str(npc).strip()
        if involved:
            return str(involved[0]).strip()
        if area_npcs:
            return str(area_npcs[0]).strip()
        return None

    @classmethod
    def _get_default_board(cls, ctx: Mapping[str, Any]) -> tuple[str, str] | None:
        board_entry = cls._get_default_board_entry(ctx)
        if board_entry is None:
            return None
        return board_entry["area_id"], board_entry["board_id"]

    @classmethod
    def _get_default_board_entry(cls, ctx: Mapping[str, Any]) -> dict[str, str] | None:
        board_id = cls._normalize_string(ctx.get("default_board_id"))
        location = ctx.get("location")
        area_id = cls._normalize_string(location.get("area_id")) if isinstance(location, Mapping) else ""
        area_boards = ctx.get("area_boards")
        if board_id is not None:
            if isinstance(area_boards, list):
                for item in area_boards:
                    if not isinstance(item, Mapping):
                        continue
                    candidate_id = cls._normalize_string(item.get("id"))
                    if candidate_id != board_id:
                        continue
                    raw_sub_location = cls._normalize_string(item.get("sub_location"))
                    return {
                        "area_id": area_id or "",
                        "board_id": board_id,
                        "sub_location": raw_sub_location or "",
                    }
            return {"area_id": area_id or "", "board_id": board_id, "sub_location": ""}

        if isinstance(area_boards, list):
            for item in area_boards:
                if not isinstance(item, Mapping):
                    continue
                candidate_id = cls._normalize_string(item.get("id"))
                if candidate_id is None:
                    continue
                raw_sub_location = cls._normalize_string(item.get("sub_location"))
                return {
                    "area_id": area_id or "",
                    "board_id": candidate_id,
                    "sub_location": raw_sub_location or "",
                }

        resolved = cls._resolve_quest_board(ctx)
        if resolved is None:
            return None
        area_id, board_id = resolved
        return {
            "area_id": area_id,
            "board_id": board_id,
            "sub_location": "",
        }

    @classmethod
    def _resolve_quest_board(
        cls,
        ctx: Mapping[str, Any],
    ) -> tuple[str, str] | None:
        raw_maps = ctx.get("maps")
        if raw_maps is None:
            return None
        location = ctx.get("location", {})
        if not isinstance(location, Mapping):
            return None
        area_id = cls._normalize_string(location.get("area_id"))
        if area_id is None:
            return None
        if not hasattr(raw_maps, "get"):
            return None
        area_template = raw_maps.get(area_id)
        if area_template is None:
            return None

        if isinstance(area_template, Mapping):
            raw_sub_locations = area_template.get("sub_locations")
        else:
            raw_sub_locations = getattr(area_template, "sub_locations", None)
        if not isinstance(raw_sub_locations, Mapping):
            return None

        for raw_sub_location in raw_sub_locations.values():
            if isinstance(raw_sub_location, Mapping):
                interactables = raw_sub_location.get("interactables")
            else:
                interactables = getattr(raw_sub_location, "interactables", None)
            if not isinstance(interactables, list):
                continue
            for raw_interactable in interactables:
                if isinstance(raw_interactable, Mapping):
                    tags = cls._normalize_interactable_tags(raw_interactable.get("tags", []))
                    board_id = str(raw_interactable.get("id", "")).strip()
                else:
                    tags = cls._normalize_interactable_tags(getattr(raw_interactable, "tags", []))
                    board_id = str(getattr(raw_interactable, "id", "")).strip()
                if "quest_source" not in tags:
                    continue
                if board_id:
                    return area_id, board_id
        return None

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
