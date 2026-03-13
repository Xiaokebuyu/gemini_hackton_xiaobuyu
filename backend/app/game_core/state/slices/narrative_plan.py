"""NarrativePlanSlice implementation."""

from __future__ import annotations

from typing import Any, ClassVar, Mapping

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
        self.quest_history: list[dict[str, Any]] = []
        self.escalation_level = 0
        self.ticks_since_milestone_progress = 0
        self.strategy_notes = ""
        self.last_run_tick = 0
        self.next_scheduled_tick: int | None = None
        self.pacing_frozen = False
        self.play_style_tags: list[str] = []
        self.behavior_window: list[dict[str, Any]] = []
        self.temporary_npcs: dict[str, dict[str, Any]] = {}
        self.story_facts: list[dict[str, Any]] = []
        self.actor_knowledge: dict[str, Any] = {}
        self.context_windows_data: dict[str, Any] = {}
        self.last_planner_replay_trace: dict[str, Any] = {}
        self.npc_capabilities: dict[str, list[dict[str, Any]]] = {}
        self.milestone_outline: dict[str, Any] = {}

    def restore(self, payload: Mapping[str, Any]) -> None:
        self.current_chapter = str(payload.get("current_chapter", ""))
        raw_target = payload.get("current_target_milestone")
        self.current_target_milestone = str(raw_target) if raw_target is not None else None
        self.chapter_completion = float(payload.get("chapter_completion", 0.0))
        self.npc_directives = [
            dict(item) for item in payload.get("npc_directives", [])
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
        raw_temporary_npcs = payload.get("temporary_npcs")
        self.temporary_npcs = {}
        if isinstance(raw_temporary_npcs, Mapping):
            for npc_id, raw_profile in raw_temporary_npcs.items():
                if isinstance(npc_id, str) and isinstance(raw_profile, Mapping):
                    self.temporary_npcs[npc_id] = dict(raw_profile)
        self.behavior_window = [
            dict(item) for item in payload.get("behavior_window", [])
            if isinstance(item, Mapping)
        ]
        self.story_facts = [
            dict(item) for item in payload.get("story_facts", [])
            if isinstance(item, Mapping)
        ]
        raw_ak = payload.get("actor_knowledge")
        self.actor_knowledge = dict(raw_ak) if isinstance(raw_ak, Mapping) else {}
        raw_cw = payload.get("context_windows_data")
        self.context_windows_data = dict(raw_cw) if isinstance(raw_cw, Mapping) else {}
        raw_trace = payload.get("last_planner_replay_trace")
        self.last_planner_replay_trace = (
            dict(raw_trace) if isinstance(raw_trace, Mapping) else {}
        )
        raw_caps = payload.get("npc_capabilities")
        self.npc_capabilities = {}
        if isinstance(raw_caps, Mapping):
            for npc_id, raw_list in raw_caps.items():
                if isinstance(npc_id, str) and isinstance(raw_list, list):
                    self.npc_capabilities[npc_id] = [
                        dict(item) for item in raw_list
                        if isinstance(item, Mapping)
                    ]
        raw_outline = payload.get("milestone_outline")
        self.milestone_outline = dict(raw_outline) if isinstance(raw_outline, Mapping) else {}
        self.clear_dirty()

    def serialize(self) -> dict[str, Any]:
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "current_chapter": self.current_chapter,
            "current_target_milestone": self.current_target_milestone,
            "chapter_completion": self.chapter_completion,
            "npc_directives": [dict(item) for item in self.npc_directives],
            "quest_history": [dict(item) for item in self.quest_history],
            "escalation_level": self.escalation_level,
            "ticks_since_milestone_progress": self.ticks_since_milestone_progress,
            "strategy_notes": self.strategy_notes,
            "last_run_tick": self.last_run_tick,
            "next_scheduled_tick": self.next_scheduled_tick,
            "pacing_frozen": self.pacing_frozen,
            "play_style_tags": list(self.play_style_tags),
            "temporary_npcs": {
                npc_id: dict(profile)
                for npc_id, profile in self.temporary_npcs.items()
            },
            "behavior_window": [dict(item) for item in self.behavior_window],
            "story_facts": [dict(f) for f in self.story_facts],
            "actor_knowledge": dict(self.actor_knowledge),
            "context_windows_data": dict(self.context_windows_data),
            "last_planner_replay_trace": dict(self.last_planner_replay_trace),
            "npc_capabilities": {
                npc_id: [dict(cap) for cap in caps]
                for npc_id, caps in self.npc_capabilities.items()
            },
            "milestone_outline": self._snapshot_milestone_outline(),
        }

    def record_behavior(self, entry: dict[str, Any]) -> None:
        self.behavior_window.append(dict(entry))
        self.behavior_window = self.behavior_window[-24:]
        self._dirty = True

    def add_story_facts(self, facts: list[dict[str, Any]]) -> None:
        """Append new story facts (planner-produced world knowledge triples)."""
        self.story_facts.extend(dict(f) for f in facts)
        self._dirty = True

    def set_actor_knowledge(self, data: dict[str, Any]) -> None:
        """Update actor knowledge blob (from WKG export). Marks dirty."""
        self.actor_knowledge = dict(data) if data else {}
        self._dirty = True

    def set_context_windows_data(self, data: dict[str, Any]) -> None:
        """Update serialized context windows blob. Marks dirty."""
        self.context_windows_data = dict(data) if data else {}
        self._dirty = True

    def set_last_planner_replay_trace(self, trace: dict[str, Any]) -> None:
        """Persist the latest planner replay summary."""
        self.last_planner_replay_trace = dict(trace) if trace else {}
        self._dirty = True

    def add_temporary_npc(self, npc_id: str, profile: dict[str, Any]) -> None:
        npc_id = str(npc_id)
        self.temporary_npcs[npc_id] = dict(profile)
        self._dirty = True

    def remove_temporary_npc(self, npc_id: str) -> None:
        raw_id = str(npc_id)
        if raw_id in self.temporary_npcs:
            self.temporary_npcs.pop(raw_id, None)
            self._dirty = True

    def get_temporary_npc(self, npc_id: str) -> dict[str, Any] | None:
        profile = self.temporary_npcs.get(str(npc_id))
        return dict(profile) if isinstance(profile, Mapping) else None

    def set_target_milestone(self, milestone_id: str | None) -> None:
        self.current_target_milestone = milestone_id
        self._dirty = True

    def add_directive(self, directive: dict[str, Any]) -> dict[str, Any]:
        stored = dict(directive)
        npc_id = stored.get("npc_id")
        if npc_id is not None:
            # Remove any pending (unconsumed) directive for the same NPC before
            # appending the new one, ensuring at most one pending per NPC.
            # Already-consumed directives are retained for GC / audit.
            self.npc_directives = [
                d for d in self.npc_directives
                if d.get("npc_id") != npc_id or d.get("consumed", False)
            ]
        self.npc_directives.append(stored)
        self._dirty = True
        return stored

    def add_history(self, summary: dict[str, Any]) -> None:
        self.quest_history.append(dict(summary))
        self._dirty = True

    def set_strategy(self, notes: str) -> None:
        self.strategy_notes = notes
        self._dirty = True

    def schedule_next(self, tick: int | None) -> None:
        self.next_scheduled_tick = tick
        self._dirty = True

    def prune_consumed_and_expired(self, current_tick: int) -> int:
        """Remove consumed or expired directives. Returns count of removed items."""
        before = len(self.npc_directives)
        self.npc_directives = [
            d for d in self.npc_directives
            if not d.get("consumed", False)
            and d.get("expires_at_tick", current_tick + 1) >= current_tick
        ]
        pruned = before - len(self.npc_directives)
        if pruned > 0:
            self._dirty = True
        return pruned

    def adjust_escalation(self, delta: int) -> None:
        self.escalation_level = max(0, self.escalation_level + delta)
        self._dirty = True

    def set_pacing_frozen(self, frozen: bool) -> None:
        self.pacing_frozen = frozen
        self._dirty = True

    # ------------------------------------------------------------------
    # NPC capability management
    # ------------------------------------------------------------------

    def assign_capability(self, npc_id: str, cap_dict: dict[str, Any]) -> None:
        """Assign (or replace by capability_id) a capability for an NPC."""
        npc_id = str(npc_id)
        cap_id = str(cap_dict.get("capability_id", ""))
        existing = self.npc_capabilities.get(npc_id, [])
        # Replace existing entry with the same capability_id
        filtered = [c for c in existing if str(c.get("capability_id", "")) != cap_id]
        filtered.append(dict(cap_dict))
        self.npc_capabilities[npc_id] = filtered
        self._dirty = True

    def revoke_capability(self, npc_id: str, capability_id: str) -> bool:
        """Remove a capability from an NPC. Returns True if something was removed."""
        npc_id = str(npc_id)
        capability_id = str(capability_id)
        existing = self.npc_capabilities.get(npc_id, [])
        filtered = [c for c in existing if str(c.get("capability_id", "")) != capability_id]
        if len(filtered) == len(existing):
            return False
        if filtered:
            self.npc_capabilities[npc_id] = filtered
        else:
            self.npc_capabilities.pop(npc_id, None)
        self._dirty = True
        return True

    def get_capabilities(self, npc_id: str) -> list[dict[str, Any]]:
        """Return defensive copy of capabilities for npc_id."""
        caps = self.npc_capabilities.get(str(npc_id), [])
        return [dict(c) for c in caps]

    def prune_expired_capabilities(self, current_tick: int) -> int:
        """Remove expired capability entries. Returns count of removed capabilities."""
        pruned = 0
        to_remove: list[str] = []
        for npc_id, caps in self.npc_capabilities.items():
            before_count = len(caps)
            active = [
                c for c in caps
                if int(c.get("expiry_tick", 0)) == 0
                or int(c.get("expiry_tick", 0)) > current_tick
            ]
            pruned += before_count - len(active)
            if active:
                self.npc_capabilities[npc_id] = active
            else:
                to_remove.append(npc_id)
        for npc_id in to_remove:
            self.npc_capabilities.pop(npc_id, None)
        if pruned > 0:
            self._dirty = True
        return pruned

    # ------------------------------------------------------------------
    # Milestone outline management
    # ------------------------------------------------------------------

    def _snapshot_milestone_outline(self) -> dict[str, Any]:
        """Return a deep defensive copy of milestone_outline."""
        if not self.milestone_outline:
            return {}
        result: dict[str, Any] = {}
        for k, v in self.milestone_outline.items():
            if k == "steps" and isinstance(v, list):
                result[k] = [dict(step) for step in v if isinstance(step, dict)]
            else:
                result[k] = v
        return result

    def set_milestone_outline(self, outline: dict[str, Any]) -> None:
        """Replace the current milestone outline.

        Expected structure::

            {
                "target_milestone_id": str,
                "chapter_id": str,
                "computed_at_tick": int,
                "steps": [
                    {
                        "index": int,
                        "description": str,
                        "type": str,
                        "condition": dict | None,
                        "related_npcs": list[str],
                        "related_locations": list[str],
                        "completed": bool,
                        "quest_id": str | None,
                    },
                    ...
                ]
            }
        """
        if not isinstance(outline, Mapping):
            return
        stored: dict[str, Any] = {}
        for k, v in outline.items():
            if k == "steps" and isinstance(v, list):
                stored[k] = [dict(step) for step in v if isinstance(step, dict)]
            else:
                stored[k] = v
        self.milestone_outline = stored
        self._dirty = True

    def mark_outline_step_completed(self, step_index: int) -> None:
        """Mark the step at *step_index* as completed.

        Silently ignores out-of-range indices or missing steps list.
        """
        steps = self.milestone_outline.get("steps")
        if not isinstance(steps, list):
            return
        for step in steps:
            if isinstance(step, dict) and step.get("index") == step_index:
                step["completed"] = True
                self._dirty = True
                return

    def set_outline_step_quest_id(self, step_index: int, quest_id: str) -> None:
        """Write quest_id back to the outline step at *step_index*.

        Silently ignores out-of-range indices or missing steps list.
        """
        steps = self.milestone_outline.get("steps")
        if not isinstance(steps, list):
            return
        for step in steps:
            if isinstance(step, dict) and step.get("index") == step_index:
                step["quest_id"] = str(quest_id)
                self._dirty = True
                return

    def get_current_outline_step(self) -> dict[str, Any] | None:
        """Return a defensive copy of the first incomplete step, or None."""
        steps = self.milestone_outline.get("steps")
        if not isinstance(steps, list):
            return None
        for step in steps:
            if isinstance(step, dict) and not step.get("completed", False):
                return dict(step)
        return None

    def validate(self) -> list[str]:
        issues: list[str] = []
        if not isinstance(self.chapter_completion, (int, float)):
            issues.append("chapter_completion must be numeric")
        elif not 0.0 <= self.chapter_completion <= 1.0:
            issues.append("chapter_completion must be between 0.0 and 1.0")
        if not isinstance(self.escalation_level, int) or self.escalation_level < 0:
            issues.append("escalation_level must be an integer >= 0")
        if not isinstance(self.ticks_since_milestone_progress, int) or self.ticks_since_milestone_progress < 0:
            issues.append("ticks_since_milestone_progress must be an integer >= 0")
        if not isinstance(self.last_run_tick, int) or self.last_run_tick < 0:
            issues.append("last_run_tick must be an integer >= 0")
        if self.next_scheduled_tick is not None:
            if not isinstance(self.next_scheduled_tick, int) or self.next_scheduled_tick < 0:
                issues.append("next_scheduled_tick must be None or an integer >= 0")
        if not isinstance(self.behavior_window, list):
            issues.append("behavior_window must be a list")
        else:
            if len(self.behavior_window) > 24:
                issues.append("behavior_window must not exceed 24 entries")
            for i, entry in enumerate(self.behavior_window):
                if not isinstance(entry, dict):
                    issues.append(f"behavior_window[{i}] must be a dict")
        for field_name in ("npc_directives", "quest_history"):
            field_value = getattr(self, field_name)
            if not isinstance(field_value, list):
                issues.append(f"{field_name} must be a list")
            else:
                for i, item in enumerate(field_value):
                    if not isinstance(item, dict):
                        issues.append(f"{field_name}[{i}] must be a dict")
        if not isinstance(self.play_style_tags, list):
            issues.append("play_style_tags must be a list")
        if not isinstance(self.temporary_npcs, dict):
            issues.append("temporary_npcs must be a dict")
        else:
            for npc_id, profile in self.temporary_npcs.items():
                if not isinstance(npc_id, str) or not npc_id:
                    issues.append("temporary_npcs keys must be non-empty strings")
                if not isinstance(profile, Mapping):
                    issues.append(f"temporary_npcs[{npc_id}] must be a dict")
        if not isinstance(self.npc_capabilities, dict):
            issues.append("npc_capabilities must be a dict")
        else:
            for npc_id, caps in self.npc_capabilities.items():
                if not isinstance(npc_id, str) or not npc_id:
                    issues.append("npc_capabilities keys must be non-empty strings")
                if not isinstance(caps, list):
                    issues.append(f"npc_capabilities[{npc_id}] must be a list")
                else:
                    for i, cap in enumerate(caps):
                        if not isinstance(cap, dict):
                            issues.append(f"npc_capabilities[{npc_id}][{i}] must be a dict")
        return issues

    def apply_state_change(self, change: StateChange) -> None:
        if change.path == "npc_directives":
            if isinstance(change.value, Mapping):
                self.add_directive(dict(change.value))
                return
            if change.operation in {"set", "modify"} and isinstance(change.value, list):
                self.npc_directives = [
                    dict(item) for item in change.value
                    if isinstance(item, Mapping)
                ]
                self._dirty = True
                return
        if change.path == "quest_history":
            if isinstance(change.value, Mapping):
                self.add_history(dict(change.value))
                return
            if change.operation in {"set", "modify"} and isinstance(change.value, list):
                self.quest_history = [
                    dict(item) for item in change.value
                    if isinstance(item, Mapping)
                ]
                self._dirty = True
                return
        if change.path == "behavior_window":
            if isinstance(change.value, Mapping):
                self.record_behavior(dict(change.value))
                return
            if change.operation in {"set", "modify"} and isinstance(change.value, list):
                self.behavior_window = [
                    dict(item) for item in change.value
                    if isinstance(item, Mapping)
                ][-24:]
                self._dirty = True
                return
        if change.path == "story_facts":
            if change.operation in {"add", "append"}:
                if isinstance(change.value, Mapping):
                    self.add_story_facts([dict(change.value)])
                    return
                if isinstance(change.value, list):
                    self.add_story_facts([
                        dict(item) for item in change.value
                        if isinstance(item, Mapping)
                    ])
                    return
            if change.operation in {"set", "modify"} and isinstance(change.value, list):
                self.story_facts = [
                    dict(item) for item in change.value
                    if isinstance(item, Mapping)
                ]
                self._dirty = True
                return
        if change.path == "escalation_level":
            if change.operation == "add":
                self.adjust_escalation(int(change.value))
            else:
                self.escalation_level = max(0, int(change.value))
                self._dirty = True
            return
        if change.path == "current_target_milestone":
            self.set_target_milestone(
                str(change.value) if change.value is not None else None
            )
            return
        if change.path == "next_scheduled_tick":
            self.schedule_next(
                int(change.value) if change.value is not None else None
            )
            return
        if change.path == "play_style_tags" and isinstance(change.value, list):
            self.play_style_tags = [str(item) for item in change.value]
            self._dirty = True
            return
        if change.path == "temporary_npcs" and isinstance(change.value, Mapping):
            self.temporary_npcs = {
                str(npc_id): dict(profile)
                for npc_id, profile in change.value.items()
                if isinstance(npc_id, str) and isinstance(profile, Mapping)
            }
            self._dirty = True
            return
        if change.path.startswith("temporary_npcs."):
            _, npc_id = change.path.split(".", 1)
            if change.operation == "remove":
                self.remove_temporary_npc(npc_id)
                return
            if isinstance(change.value, Mapping):
                self.temporary_npcs[str(npc_id)] = dict(change.value)
                self._dirty = True
                return
        if change.path == "last_planner_replay_trace" and isinstance(change.value, Mapping):
            self.set_last_planner_replay_trace(dict(change.value))
            return
        if change.path == "npc_capabilities" and change.operation in {"set", "modify"} and isinstance(change.value, Mapping):
            self.npc_capabilities = {
                str(npc_id): [dict(c) for c in caps if isinstance(c, Mapping)]
                for npc_id, caps in change.value.items()
                if isinstance(npc_id, str) and isinstance(caps, list)
            }
            self._dirty = True
            return
        if change.path == "npc_capabilities.assign" and isinstance(change.value, Mapping):
            self.assign_capability(
                str(change.value.get("npc_id", "")),
                dict(change.value),
            )
            return
        if change.path == "npc_capabilities.revoke" and change.operation == "remove" and isinstance(change.value, Mapping):
            self.revoke_capability(
                str(change.value.get("npc_id", "")),
                str(change.value.get("capability_id", "")),
            )
            return
        if change.path == "milestone_outline" and isinstance(change.value, Mapping):
            self.set_milestone_outline(dict(change.value))
            return
        if change.path == "milestone_outline.step_completed":
            self.mark_outline_step_completed(int(change.value))
            return
        if change.path == "milestone_outline.step_quest_id" and isinstance(change.value, Mapping):
            step_index = change.value.get("step_index")
            quest_id = change.value.get("quest_id")
            if step_index is not None and quest_id is not None:
                self.set_outline_step_quest_id(int(step_index), str(quest_id))
            return
        if change.operation in {"set", "modify"} and change.path in self._SIMPLE_FIELDS:
            coerce = self._SIMPLE_FIELDS[change.path]
            setattr(self, change.path, coerce(change.value))
            self._dirty = True
            return
        raise ValueError(
            f"unsupported narrative plan change: {change.operation} {change.path}"
        )

    _SIMPLE_FIELDS: ClassVar[dict[str, type]] = {
        "current_chapter": str,
        "chapter_completion": float,
        "ticks_since_milestone_progress": int,
        "strategy_notes": str,
        "last_run_tick": int,
        "pacing_frozen": bool,
    }
