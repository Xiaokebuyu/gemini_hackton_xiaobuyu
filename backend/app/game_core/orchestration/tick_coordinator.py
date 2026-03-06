"""TickCoordinator skeleton."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

MAX_ACTION_LOG = 100  # 每个 session 保留的最近动作条数上限（防止无界增长污染 LLM 上下文）

from app.game_core.content import WorldInstance
from app.game_core.narrative.companion_runtime import CompanionRuntimeManager, TickRecord
from app.game_core.orchestration.models import PipelineResult, SSEEvent
from app.game_core.orchestration.pipeline import AfterEngineCallback, PipelineOrchestrator
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.orchestration.shared_context import SharedContext
from app.game_core.orchestration.hooks.base import SettlementHook
from app.game_core.rules import RulesEngine
from app.game_core.rules.models import ExecuteResult
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.orchestration.event_engine import run_inline_event_check


_SEMANTIC_TAGS: dict[str, list[str]] = {
    "rest_long":     ["REST", "LONG_REST"],
    "rest_short":    ["REST", "SHORT_REST"],
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
        self.change_log: list[StateChange] = []
        self.action_log: list[dict[str, Any]] = []
        self.settlement_hooks: list[SettlementHook] = []

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
        self._record_action(result)
        self._emit_action_tags(result)    # Phase 0
        self._dispatch_companion_events(result)   # Phase C3
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
        context = SettlementContext(
            change_log=self.change_log,
            state=self.state,
            world=self.world,
            scene_bus=self.scene_bus,
            _rules_engine=self.rules_engine,
            _apply_delta=self._apply_delta,
            action_log=list(self.action_log),
            companion_manager=self.companion_manager,
        )
        for hook in self.settlement_hooks:
            if hook.should_skip(self.change_log):
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
        if result.success and result.delta is not None:
            self._apply_delta(result.delta)

    async def finalize_external_turn(
        self,
        time_cost: float,
        event_sink: Callable[[SSEEvent], Awaitable[None]] | None = None,
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
        ):
            event = SSEEvent(event_type="event_state_changed", payload=payload)
            collected_events.append(event)
            if event_sink is not None:
                await event_sink(event)
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
        return collected_events

    def export_dirty(self) -> dict[str, dict[str, Any]]:
        """Return serialized dirty-slice data for the outer persistence boundary.

        This does NOT write to storage.  The caller (SaveStore) is
        responsible for actual persistence — see D-O22 in orchestration.md.
        """
        return self.state.export_dirty()

    def _emit_action_tags(self, result: PipelineResult) -> None:
        """Write semantic ENGINE tags to SceneBus for downstream hook consumption.

        Phase 0 prerequisite for Phase 3 (SharedExperience) and Phase 4 (Teammate).
        """
        tags = _SEMANTIC_TAGS.get(result.action_type, [])
        if not tags:
            return
        content = f"[{result.action_type}]"
        if result.narrative_hints:
            content = f"{content} {result.narrative_hints[0]}"
        self.scene_bus.add_entry({
            "source": "ENGINE",
            "content": content,
            "visibility": "system",
            "tags": tags,
        })

    def _dispatch_companion_events(self, result: PipelineResult) -> None:
        """C3: Dispatch one structured tick observation to active companions."""
        if self.companion_manager is None:
            return
        if not result.success:
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
            success=result.success,
            summary=summary,
            tags=list(dict.fromkeys(tags)),
            involved_npcs=involved,
            has_rolls=bool(result.rolls),
            event_transitions=transitions,
        )
        self.companion_manager.dispatch_tick(record)

    def _record_action(self, result: PipelineResult) -> None:
        if result.action_type == "noop":
            return
        command = result.commands[0] if result.commands else None
        record: dict[str, Any] = {
            "type": result.action_type,
            "actor": command.source if command else "system",
            "params": dict(command.params) if command else {},
            "success": result.success,
            "time_cost": result.time_cost,
        }
        if result.narrative_hints:
            record["narrative_hints"] = list(result.narrative_hints)
        self.action_log.append(record)
        if len(self.action_log) > MAX_ACTION_LOG:
            self.action_log = self.action_log[-MAX_ACTION_LOG:]

    def _apply_delta(self, delta: StateDelta | None) -> None:
        if delta is None:
            return
        self.state.apply(delta)
        self.change_log.extend(delta.changes)
        for change in delta.changes:
            self.scene_bus.record_state_change(change)
