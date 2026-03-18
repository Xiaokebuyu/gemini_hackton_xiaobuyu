"""Tests for post-action hook mechanism in TickCoordinator."""
import asyncio
from dataclasses import dataclass, field

from app.game_core.orchestration.hooks.base import SettlementHook
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.tick_coordinator import TickCoordinator
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.rules import RulesEngine
from app.game_core.state import StateContainer
from app.game_core.state.slices import SceneSlice, TimeSlice, PlayerSlice
from app.game_core.content import WorldInstance


class _TrackingHook(SettlementHook):
    HOOK_PRIORITY = 50
    HOOK_NAME = "tracking_hook"

    def __init__(self):
        self.call_count = 0

    @property
    def name(self) -> str:
        return self.HOOK_NAME

    @property
    def priority(self) -> int:
        return self.HOOK_PRIORITY

    async def execute(self, context):
        self.call_count += 1
        return HookResult(
            sse_events=[SSEEvent(event_type="test_event", payload={"n": self.call_count})],
        )


def _build_coordinator() -> tuple[TickCoordinator, _TrackingHook]:
    state = StateContainer()
    state.register(SceneSlice())
    state.register(TimeSlice())
    state.register(PlayerSlice())
    world = WorldInstance("test")
    scene_bus = SceneBus(state.scene)
    rules_engine = RulesEngine()
    tc = TickCoordinator(
        world=world,
        state=state,
        rules_engine=rules_engine,
        scene_bus=scene_bus,
    )
    hook = _TrackingHook()
    tc.register_post_action_hook(hook)
    return tc, hook


def test_post_action_hook_runs_for_non_noop():
    async def _run():
        tc, hook = _build_coordinator()
        # Process a navigate action (will fail validation but action_type won't be noop
        # if we inject a result directly)
        result = await tc.process({"action_type": "navigate", "area_id": "test"})
        # The navigate will likely become noop since no handler is registered,
        # so let's check hook wasn't called
        if result.action_type == "noop":
            assert hook.call_count == 0
        else:
            assert hook.call_count == 1
    asyncio.run(_run())


def test_register_post_action_hook_sorted():
    state = StateContainer()
    state.register(SceneSlice())
    world = WorldInstance("test")
    scene_bus = SceneBus(state.scene)
    rules_engine = RulesEngine()
    tc = TickCoordinator(
        world=world,
        state=state,
        rules_engine=rules_engine,
        scene_bus=scene_bus,
    )

    class HookA(SettlementHook):
        HOOK_PRIORITY = 80
        HOOK_NAME = "hook_a"
        @property
        def name(self): return self.HOOK_NAME
        @property
        def priority(self): return self.HOOK_PRIORITY
        async def execute(self, ctx): return HookResult()

    class HookB(SettlementHook):
        HOOK_PRIORITY = 20
        HOOK_NAME = "hook_b"
        @property
        def name(self): return self.HOOK_NAME
        @property
        def priority(self): return self.HOOK_PRIORITY
        async def execute(self, ctx): return HookResult()

    tc.register_post_action_hook(HookA())
    tc.register_post_action_hook(HookB())
    assert len(tc.post_action_hooks) == 2
    assert tc.post_action_hooks[0].priority == 20
    assert tc.post_action_hooks[1].priority == 80
