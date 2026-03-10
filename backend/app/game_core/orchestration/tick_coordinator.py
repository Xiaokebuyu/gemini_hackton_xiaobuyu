"""TickCoordinator skeleton."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Mapping

logger = logging.getLogger(__name__)

from app.game_core.content import WorldInstance
from app.game_core.narrative.companion_runtime import CompanionRuntimeManager, TickRecord
from app.game_core.orchestration.companion_manager import CompanionManager
from app.game_core.orchestration.models import PipelineResult, SSEEvent
from app.game_core.orchestration.hooks.rest_phase import build_rest_phase
from app.game_core.orchestration.pipeline import AfterEngineCallback, PipelineOrchestrator
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.orchestration.shared_context import SharedContext
from app.game_core.orchestration.hooks.base import SettlementHook
from app.game_core.rules import RulesEngine
from app.game_core.rules.models import ExecuteResult
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.orchestration.event_engine import run_inline_event_check
from app.game_core.orchestration.combat_sse import extract_combat_sse


_SEMANTIC_TAGS: dict[str, list[str]] = {
    "rest_long":     ["REST", "LONG_REST"],
    "rest_short":    ["REST", "SHORT_REST"],
    "dialogue_turn": ["DIALOGUE", "NPC_INTERACTION"],
    "public_utterance_turn": ["DIALOGUE", "PUBLIC_UTTERANCE"],
    "party_chat_turn": ["DIALOGUE", "PARTY_CHAT"],
    "free_chat_turn": ["DIALOGUE", "PARTY_CHAT"],
    "private_chat_turn": ["DIALOGUE", "PRIVATE_CHAT"],
    "start_combat":  ["COMBAT"],
    "end_combat":    ["COMBAT_END"],
    "navigate":      ["NAVIGATION"],
    "skill_check":   ["SKILL_CHECK"],
    "advance_quest": ["QUEST_PROGRESS"],
}


class TickCoordinator:
    """Own the session tick lifecycle."""

    def __init__(
        self,
        world: WorldInstance,
        state: StateContainer,
        rules_engine: RulesEngine,
        scene_bus: SceneBus,
        pipeline: PipelineOrchestrator | None = None,
        companion_manager: CompanionRuntimeManager | None = None,
    ) -> None:
        self.world = world
        self.state = state
        self.rules_engine = rules_engine
        self.scene_bus = scene_bus
        self.pipeline = pipeline or PipelineOrchestrator()
        self.companion_manager = companion_manager
        self.knowledge_graph: Any | None = None
        self.change_log: list[StateChange] = []
        self._pending_action_records: list[dict[str, Any]] = []
        self.settlement_hooks: list[SettlementHook] = []

    @property
    def action_log(self) -> list[dict[str, Any]]:
        """Current settlement-window action semantics exposed to hooks/tests."""
        return self._peek_pending_action_window()

    def register_settlement_hook(self, hook: SettlementHook) -> None:
        self.settlement_hooks.append(hook)
        self.settlement_hooks.sort(key=lambda item: item.priority)

    async def process(
        self,
        input_payload: Any,
        event_sink: Callable[[SSEEvent], Awaitable[None]] | None = None,
        after_engine: AfterEngineCallback | None = None,
    ) -> PipelineResult:
        shared = SharedContext(
            world=self.world,
            state=self.state,
            rules_engine=self.rules_engine,
            scene_bus=self.scene_bus,
            companion_manager=self.companion_manager,
        )
        result = await self.pipeline.process(
            input_payload,
            shared,
            apply_delta=self._apply_delta,
            change_log=self.change_log,
            action_log=self.action_log,
            event_sink=event_sink,
            after_engine=after_engine,
        )
        appended = self._append_pipeline_action(result)
        if appended is not None:
            self._emit_action_tags_for_record(appended)
        if (
            self.state.has_slice("narrative_plan")
            and result.action_type != "noop"
            and self.state.has_slice("time")
        ):
            location = (
                self.state.player.current_location
                if self.state.has_slice("player")
                else None
            )
            self.state.narrative_plan.record_behavior(
                {
                    "tick": self.state.time.absolute_tick(),
                    "action_type": result.action_type,
                    "location": location,
                }
            )
        self._dispatch_companion_events(result)   # Phase C3
        self._sync_party_to_player()
        # Phase 9: immediate combat SSE events
        combat_events = extract_combat_sse(result.action_type, result.metadata)
        result.sse_events.extend(combat_events)
        self.accumulate(result.time_cost)
        while self.check_settlement():
            before_accumulated = self.state.time.accumulated
            settlement_events = await self._tick_settlement(event_sink)
            result.sse_events.extend(settlement_events)
            if (
                self.state.has_slice("time")
                and self.state.time.accumulated >= 1.0
                and self.state.time.accumulated >= before_accumulated
            ):
                raise RuntimeError("settlement made no progress")
            self._consume_pending_action_window()
        return result

    def accumulate(self, time_cost: float) -> None:
        if time_cost <= 0:
            return
        if not self.state.has_slice("time"):
            return
        self.state.time.add_action(time_cost)

    def check_settlement(self) -> bool:
        if not self.state.has_slice("time"):
            return False
        return self.state.time.accumulated >= 1.0

    async def _tick_settlement(
        self,
        event_sink: Callable[[SSEEvent], Awaitable[None]] | None = None,
    ) -> list[SSEEvent]:
        """Run settlement hooks. Returns collected SSE events."""
        collected_events: list[SSEEvent] = []
        action_log = self._peek_pending_action_window()
        rest_phase = build_rest_phase(
            action_log=action_log,
            current_time=self.state.time if self.state.has_slice("time") else None,
        )
        context = SettlementContext(
            change_log=self.change_log,
            state=self.state,
            world=self.world,
            scene_bus=self.scene_bus,
            _rules_engine=self.rules_engine,
            _apply_delta=self._apply_delta,
            action_log=action_log,
            rest_phase=rest_phase,
            companion_manager=self.companion_manager,
            knowledge_graph=self.knowledge_graph,
        )
        for hook in self.settlement_hooks:
            if hook.should_skip(self.change_log, action_log=context.action_log):
                continue
            hook_name = getattr(hook, "HOOK_NAME", type(hook).__name__)
            try:
                result = await hook.execute(context)
            except Exception as exc:
                logger.exception("settlement hook failed: %s", hook_name)
                error_event = SSEEvent(
                    event_type="hook_error",
                    payload={
                        "hook": hook_name,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
                collected_events.append(error_event)
                if event_sink is not None:
                    await event_sink(error_event)
                continue
            for command in result.commands:
                try:
                    context.execute_command(command)
                except Exception as exc:
                    logger.exception("hook command failed: %s", hook_name)
                    cmd_error = SSEEvent(
                        event_type="command_error",
                        payload={
                            "hook": hook_name,
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        },
                    )
                    collected_events.append(cmd_error)
                    if event_sink is not None:
                        await event_sink(cmd_error)
            for event in result.sse_events:
                collected_events.append(event)
                if event_sink is not None:
                    await event_sink(event)
        return collected_events

    def apply_external_result(self, result: ExecuteResult) -> None:
        """Record a rules-engine result produced outside PipelineOrchestrator.

        Dialogue/private-chat agent tools execute intra-turn micro-commands
        directly through the rules engine. Their deltas still need to enter
        TickCoordinator's change_log so settlement hooks can observe them.
        """
        if result.executed and result.delta is not None:
            self._apply_delta(result.delta)

    async def finalize_external_turn(
        self,
        time_cost: float,
        event_sink: Callable[[SSEEvent], Awaitable[None]] | None = None,
        turn_action_record: Mapping[str, Any] | None = None,
    ) -> list[SSEEvent]:
        """Finalize one non-pipeline turn by consuming time and settlement.

        Used by the dedicated interaction/private-chat routes, which have
        their own agentic execution path but still belong to the same tick
        lifecycle as normal player actions.
        """
        collected_events: list[SSEEvent] = []
        for payload in run_inline_event_check(
            state=self.state,
            world=self.world,
            rules_engine=self.rules_engine,
            apply_delta=self._apply_delta,
            change_log=self.change_log,
            scene_bus=self.scene_bus,
            label="post_external",
            sse_collector=collected_events,
        ):
            event = SSEEvent(event_type="event_state_changed", payload=payload)
            collected_events.append(event)
            if event_sink is not None:
                await event_sink(event)
        if turn_action_record is not None:
            appended = self._append_external_action(turn_action_record, time_cost=time_cost)
            if appended is not None:
                self._emit_action_tags_for_record(appended)
        self._sync_party_to_player()
        self.accumulate(time_cost)
        while self.check_settlement():
            before_accumulated = self.state.time.accumulated
            settlement_events = await self._tick_settlement(event_sink)
            collected_events.extend(settlement_events)
            if (
                self.state.has_slice("time")
                and self.state.time.accumulated >= 1.0
                and self.state.time.accumulated >= before_accumulated
            ):
                raise RuntimeError("settlement made no progress")
            self._consume_pending_action_window()
        return collected_events

    def export_dirty(self) -> dict[str, dict[str, Any]]:
        """Return serialized dirty-slice data for the outer persistence boundary.

        This does NOT write to storage.  The caller (SaveStore) is
        responsible for actual persistence — see D-O22 in orchestration.md.
        """
        return self.state.export_dirty()

    def _emit_action_tags_for_record(self, action_record: Mapping[str, Any]) -> None:
        """Write semantic ENGINE tags to SceneBus for downstream hook consumption.

        Phase 0 prerequisite for Phase 3 (SharedExperience) and Phase 4 (Teammate).
        """
        action_type = str(action_record.get("type", "")).strip()
        tags = _SEMANTIC_TAGS.get(action_type, [])
        if not tags:
            return
        content = f"[{action_type}]"
        raw_hints = action_record.get("narrative_hints", [])
        if isinstance(raw_hints, list) and raw_hints:
            first_hint = raw_hints[0]
            if isinstance(first_hint, str) and first_hint:
                content = f"{content} {first_hint}"
        self.scene_bus.add_entry({
            "source": "ENGINE",
            "content": content,
            "visibility": "system",
            "tags": tags,
        })

    def _emit_action_tags(self, result: PipelineResult) -> None:
        """Backward-compatible wrapper used by existing tests/helpers."""
        action_record = self._public_action_record(
            {
                "type": result.action_type,
                "time_cost": result.time_cost,
                "narrative_hints": list(result.narrative_hints),
            }
        )
        self._emit_action_tags_for_record(action_record)

    def _dispatch_companion_events(self, result: PipelineResult) -> None:
        """C3: Dispatch one structured tick observation to active companions."""
        if self.companion_manager is None:
            return
        if not result.executed:
            return
        if result.action_type == "noop":
            return
        if not self.state.has_slice("party"):
            return
        members = self.state.party.get_members()
        if not members:
            return

        tick = self.state.time.absolute_tick() if self.state.has_slice("time") else 0
        self.companion_manager.sync_members(
            list(members.keys()),
            current_tick=tick,
        )

        tags: list[str] = []
        involved: list[str] = []
        for entry in self.scene_bus.snapshot().get("entries", []):
            if not isinstance(entry, dict):
                continue
            source = entry.get("source", "")
            if source == "ENGINE":
                entry_tags = entry.get("tags", [])
                if isinstance(entry_tags, list):
                    tags.extend(entry_tags)
            elif source.startswith("NPC:") or source.startswith("npc:"):
                npc_id = source.split(":", 1)[1]
                if npc_id and npc_id not in involved:
                    involved.append(npc_id)

        transitions = [
            e.payload.get("event_id", "")
            for e in result.sse_events
            if e.event_type == "event_state_changed"
            and e.payload.get("event_id")
        ]
        summary = (
            result.narrative_hints[0]
            if result.narrative_hints
            else result.action_type
        )
        record = TickRecord(
            tick=tick,
            action_type=result.action_type,
            executed=result.executed,
            summary=summary,
            tags=list(dict.fromkeys(tags)),
            involved_npcs=involved,
            has_rolls=bool(result.rolls),
            event_transitions=transitions,
        )
        self.companion_manager.dispatch_tick(record)

    def _sync_party_to_player(self) -> None:
        """Sync all party members to the player's current position after each action."""
        if not self.state.has_slice("party") or not self.state.party.members:
            return
        mgr = CompanionManager(self.state)
        mgr.sync_to_player()

    def _append_pipeline_action(self, result: PipelineResult) -> dict[str, Any] | None:
        if result.action_type == "noop":
            return None
        command = result.commands[0] if result.commands else None
        record: dict[str, Any] = {
            "type": result.action_type,
            "actor": command.source if command else "system",
            "params": dict(command.params) if command else {},
            "executed": result.executed,
            "time_cost": result.time_cost,
        }
        self._inject_action_metadata(record, result.metadata)
        if result.narrative_hints:
            record["narrative_hints"] = list(result.narrative_hints)
        self._append_pending_action_record(record)
        return self._public_action_record(record)

    def _append_external_action(
        self,
        raw_record: Mapping[str, Any],
        *,
        time_cost: float,
    ) -> dict[str, Any] | None:
        action_type = str(raw_record.get("type", "")).strip()
        if not action_type:
            return None
        params = raw_record.get("params", {})
        record: dict[str, Any] = {
            "type": action_type,
            "actor": str(raw_record.get("actor", "player")),
            "params": dict(params) if isinstance(params, Mapping) else {},
            "executed": bool(raw_record.get("executed", True)),
            "time_cost": float(raw_record.get("time_cost", time_cost)),
            "source": str(raw_record.get("source", "external_turn")),
        }
        self._inject_action_metadata(record, raw_record.get("metadata"))
        raw_hints = raw_record.get("narrative_hints", [])
        if isinstance(raw_hints, list):
            record["narrative_hints"] = [hint for hint in raw_hints if isinstance(hint, str)]
        self._append_pending_action_record(record)
        return self._public_action_record(record)

    @staticmethod
    def _inject_action_metadata(
        record: dict[str, Any],
        raw_metadata: Any,
    ) -> None:
        if not isinstance(raw_metadata, Mapping):
            return
        camp_type = raw_metadata.get("camp_type")
        if isinstance(camp_type, str) and camp_type.strip():
            record["camp_type"] = camp_type.strip()
        night_watch_required = raw_metadata.get("night_watch_required")
        if isinstance(night_watch_required, bool):
            record["night_watch_required"] = night_watch_required
        status = raw_metadata.get("status")
        if isinstance(status, str) and status.strip():
            record["status"] = status.strip()
        executed = raw_metadata.get("executed")
        if isinstance(executed, bool):
            record["executed"] = executed
        outcome = raw_metadata.get("outcome")
        if isinstance(outcome, Mapping):
            record["outcome"] = dict(outcome)

    def _append_pending_action_record(self, record: Mapping[str, Any]) -> None:
        normalized = self._public_action_record(record)
        normalized["_remaining_time_cost"] = max(
            0.0,
            self._coerce_time_cost(normalized.get("time_cost")),
        )
        self._pending_action_records.append(normalized)

    def _peek_pending_action_window(self) -> list[dict[str, Any]]:
        if not self._pending_action_records:
            return []
        remaining = 1.0
        snapshot: list[dict[str, Any]] = []
        for raw_record in self._pending_action_records:
            record = self._public_action_record(raw_record)
            action_cost = self._coerce_time_cost(raw_record.get("_remaining_time_cost"))
            if action_cost <= 0.0:
                snapshot.append(record)
                continue
            contributed = min(action_cost, remaining)
            record["time_cost"] = contributed
            snapshot.append(record)
            remaining -= contributed
            if remaining <= 0.0:
                break
        return snapshot

    def _consume_pending_action_window(self) -> None:
        if not self._pending_action_records:
            return
        remaining = 1.0
        next_pending: list[dict[str, Any]] = []
        for raw_record in self._pending_action_records:
            record = dict(raw_record)
            action_cost = self._coerce_time_cost(record.get("_remaining_time_cost"))
            if action_cost <= 0.0:
                if remaining > 0.0:
                    continue
                next_pending.append(record)
                continue
            if remaining <= 0.0:
                next_pending.append(record)
                continue
            if action_cost <= remaining:
                remaining -= action_cost
                continue
            record["_remaining_time_cost"] = action_cost - remaining
            remaining = 0.0
            next_pending.append(record)
        self._pending_action_records = next_pending

    @staticmethod
    def _coerce_time_cost(value: Any) -> float:
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _public_action_record(record: Mapping[str, Any]) -> dict[str, Any]:
        public = {str(key): value for key, value in record.items() if not str(key).startswith("_")}
        params = public.get("params", {})
        public["params"] = dict(params) if isinstance(params, Mapping) else {}
        raw_hints = public.get("narrative_hints", [])
        if isinstance(raw_hints, list):
            hints = [hint for hint in raw_hints if isinstance(hint, str)]
            if hints:
                public["narrative_hints"] = hints
            else:
                public.pop("narrative_hints", None)
        else:
            public.pop("narrative_hints", None)
        return public

    def _apply_delta(self, delta: StateDelta | None) -> None:
        if delta is None:
            return
        self.state.apply(delta)
        self.change_log.extend(delta.changes)
        for change in delta.changes:
            self.scene_bus.record_state_change(change)
