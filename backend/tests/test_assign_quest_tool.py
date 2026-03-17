"""Tests for Phase D — AssignQuestTool, _planner_source_gate, and bootstrap enrichment.

Covers:
1. test_assign_quest_creates_quest         — normal creation, verifies command type/source/params
2. test_assign_quest_rejects_receptionist  — receptionist NPC rejected with status="wrong_tool"
3. test_assign_quest_missing_params        — missing required field returns ok=False
4. test_source_gate_accepts_npc            — _planner_source_gate(source="npc") → ok=True
5. test_source_gate_rejects_unknown        — _planner_source_gate(source="player") → ok=False
6. test_bootstrap_quest_board_delivery     — bootstrap directive uses delivery_method: "board"
7. test_bootstrap_enrichment_adds_objectives — post-processing enriches objectives/rewards
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.content.registries.quests import (
    MilestoneCondition,
    MilestoneTemplate,
    QuestRegistry,
)
from app.game_core.narrative.character_tools import AssignQuestTool
from app.game_core.narrative.context import AgentContext
from app.game_core.planning.opening_bootstrap import OpeningBootstrapQuestAgent
from app.game_core.rules.handlers.planner import _planner_source_gate
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, QuestSlice
from app.game_core.state.slices.narrative_plan import NarrativePlanSlice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _recording_command_executor() -> tuple[list[Command], Any]:
    """Return (log, executor) that records commands and returns executed=True."""
    log: list[Command] = []

    def _run(cmd: Command) -> ExecuteResult:
        log.append(cmd)
        return ExecuteResult(executed=True)

    return log, _run


def _make_ctx(
    *,
    character_id: str = "village_elder",
    npc_tags: list[str] | None = None,
    execute_command: Any = None,
) -> AgentContext:
    """Build a minimal AgentContext for AssignQuestTool tests."""
    state = StateContainer()
    metadata: dict[str, Any] = {"character_id": character_id}
    if npc_tags is not None:
        metadata["npc_tags"] = npc_tags
    return AgentContext(
        role="npc",
        world=WorldInstance("test"),
        state=state,
        metadata=metadata,
        execute_command=execute_command,
    )


# ---------------------------------------------------------------------------
# 1. Normal creation path
# ---------------------------------------------------------------------------


def test_assign_quest_signals_intent() -> None:
    """AssignQuestTool.execute() writes quest_intent to blackboard, no command."""

    async def _run() -> None:
        log, executor = _recording_command_executor()
        ctx = _make_ctx(
            character_id="village_elder",
            npc_tags=["quest_giver"],
            execute_command=executor,
        )
        params = {
            "title": "Find the Healing Herb",
            "summary": "The elder needs a rare herb from the forest.",
        }
        result = await AssignQuestTool().execute(params, ctx)

        assert result.ok is True, f"Expected ok=True, got: {result.message}"
        assert len(log) == 0, "AssignQuestTool should not execute commands"
        assert result.metadata.get("event_type") == "quest_intent"
        intent = result.metadata.get("intent", {})
        assert intent["title"] == "Find the Healing Herb"
        assert intent["giver_npc"] == "village_elder"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 2. Receptionist rejection
# ---------------------------------------------------------------------------


def test_assign_quest_rejects_receptionist() -> None:
    """AssignQuestTool refuses receptionist NPCs and returns status='wrong_tool'."""

    async def _run() -> None:
        log, executor = _recording_command_executor()
        ctx = _make_ctx(
            character_id="guild_girl",
            npc_tags=["receptionist", "quest_giver"],
            execute_command=executor,
        )
        params = {
            "quest_id": "dq_goblin_hunt",
            "title": "Goblin Hunt",
            "summary": "Clear the goblin nest.",
        }
        result = await AssignQuestTool().execute(params, ctx)

        assert result.ok is False
        assert result.metadata.get("status") == "wrong_tool"
        assert len(log) == 0, "No command should be executed for receptionist NPCs"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 3. Missing required parameters
# ---------------------------------------------------------------------------


def test_assign_quest_missing_params() -> None:
    """AssignQuestTool returns ok=False when a required field is absent."""

    async def _run() -> None:
        log, executor = _recording_command_executor()
        ctx = _make_ctx(
            character_id="blacksmith",
            npc_tags=[],
            execute_command=executor,
        )
        # Missing 'title' required field
        params = {
            "quest_id": "dq_repair_armor",
            "summary": "Bring the broken breastplate for repair.",
        }
        result = await AssignQuestTool().execute(params, ctx)

        assert result.ok is False
        assert len(log) == 0, "No command should be fired on validation failure"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 4. _planner_source_gate accepts "npc"
# ---------------------------------------------------------------------------


def test_source_gate_accepts_npc() -> None:
    """_planner_source_gate returns ok=True when source='npc'."""
    cmd = Command(type="planner_create_quest", source="npc", params={})
    result = _planner_source_gate(cmd)
    assert result.ok is True


# ---------------------------------------------------------------------------
# 5. _planner_source_gate rejects unknown sources
# ---------------------------------------------------------------------------


def test_source_gate_rejects_unknown() -> None:
    """_planner_source_gate returns ok=False when source='player'."""
    cmd = Command(type="planner_create_quest", source="player", params={})
    result = _planner_source_gate(cmd)
    assert result.ok is False
    assert result.reason is not None and len(result.reason) > 0


# ---------------------------------------------------------------------------
# 6. Bootstrap directive uses delivery_method: "board"
# ---------------------------------------------------------------------------


def test_bootstrap_quest_board_delivery() -> None:
    """OpeningBootstrapQuestAgent emits create_quest with delivery_method='board'."""

    async def _run() -> None:
        agent = OpeningBootstrapQuestAgent()
        context = {
            "current_event": {"kind": "bootstrap"},
            "quests": {
                "available_milestones": ["ms_arrival"],
                "active_milestones": [],
                "dynamic_quests": {},
            },
        }
        result = await agent.evaluate(context)

        directives = result.get("directives", [])
        assert len(directives) >= 1, "Expected at least one directive"

        create_directive = next(
            (d for d in directives if d.get("kind") == "create_quest"),
            None,
        )
        assert create_directive is not None, "Expected a create_quest directive"
        payload = create_directive.get("payload", {})
        assert payload.get("delivery_method") == "board", (
            f"Expected delivery_method='board', got '{payload.get('delivery_method')}'"
        )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 7. Bootstrap enrichment adds objectives and rewards
# ---------------------------------------------------------------------------


def _build_world_with_milestone(world_id: str) -> WorldInstance:
    """Build a minimal WorldInstance with one quest milestone having conditions and rewards."""
    world = WorldInstance(world_id)
    registry = QuestRegistry()
    registry.load(
        {
            "milestones": {
                "ms_test_arrival": {
                    "id": "ms_test_arrival",
                    "title": "初到边境",
                    "description": "前往冒险者公会完成登记。",
                    "success_conditions": [
                        {"type": "npc_talked", "params": {"npc_id": "guild_girl"}},
                    ],
                    "rewards": {"xp": 100, "gold": 50},
                }
            }
        }
    )
    world.register(registry)
    return world


def _build_state_with_seeded_quest() -> StateContainer:
    """Build a state container with a pre-seeded dynamic quest referencing ms_test_arrival."""
    state = StateContainer()

    quest_slice = QuestSlice()
    quest_slice.restore(
        {
            "dynamic_quests": {
                "dq_ms_test_arrival": {
                    "quest_id": "dq_ms_test_arrival",
                    "title": "Lead: Ms Test Arrival",
                    "summary": "Follow the new lead tied to ms_test_arrival.",
                    "status": "available",
                    "delivery_method": "board",
                    "metadata": {
                        "source_milestone": "ms_test_arrival",
                    },
                }
            }
        }
    )
    state.register(quest_slice)

    # Areas with a board bulletin for dq_ms_test_arrival so _sync_bulletin_title can update it
    area_slice = AreaSlice()
    area_slice.restore(
        {
            "areas": {
                "frontier_town": {
                    "danger_level": 1.0,
                    "board_bulletins": {
                        "quest_board": [
                            {
                                "quest_id": "dq_ms_test_arrival",
                                "title": "Lead: Ms Test Arrival",
                                "content": "old content",
                                "status": "available",
                            }
                        ]
                    },
                }
            }
        }
    )
    state.register(area_slice)

    # NarrativePlanSlice is needed by bootstrap_opening_planner logging
    np_slice = NarrativePlanSlice()
    np_slice.restore({})
    state.register(np_slice)

    return state


def test_bootstrap_enrichment_adds_objectives() -> None:
    """bootstrap_opening_planner enriches seeded quests with objectives and rewards."""

    async def _run() -> None:
        from app.game_core.bootstrap import build_runtime_for_world
        from app.game_core.orchestration.models import HookResult
        from app.game_core.runtime import GameRuntime, ManagedSession

        world = _build_world_with_milestone("test_world_enrich")
        runtime_obj = build_runtime_for_world(world)
        # Replace state with our pre-seeded state
        runtime_obj.state = _build_state_with_seeded_quest()

        session = ManagedSession(
            world_id="test_world_enrich",
            session_id="sess_enrich_test",
            runtime=runtime_obj,
            phase="opening_ready",
        )

        # Use a stub hook that returns the minimal metadata bootstrap_opening_planner needs
        class _NoopBootstrapHook:
            async def bootstrap(self, context: Any) -> HookResult:
                return HookResult(
                    sse_events=[],
                    metadata={"applied_count": 0, "story_fact_count": 0},
                )

        gr = GameRuntime()
        # Patch _resolve_bootstrap_planner_hook to return our no-op hook so the
        # test exercises only the enrichment post-processing logic.
        gr._resolve_bootstrap_planner_hook = lambda _session: _NoopBootstrapHook()  # type: ignore[method-assign]

        await gr.bootstrap_opening_planner(session)

        # --- Verify objectives were populated ---
        dq = runtime_obj.state.quests.get_dynamic_quest("dq_ms_test_arrival")
        assert dq is not None, "Dynamic quest must still exist after bootstrap"

        objectives = dq.get("objectives", [])
        assert len(objectives) >= 1, (
            f"Expected at least one objective, got: {objectives}"
        )
        assert objectives[0].get("completed") is False
        # The condition binding must be present so quest tracking can pick it up
        assert "condition" in objectives[0], "Objective must carry a condition binding"
        assert objectives[0]["condition"]["type"] == "npc_talked"

        # --- Verify rewards were populated ---
        rewards = dq.get("rewards", {})
        assert rewards.get("xp") == 100, f"Expected xp=100, got: {rewards}"
        assert rewards.get("gold") == 50, f"Expected gold=50, got: {rewards}"

        # --- Verify title was enriched from milestone template ---
        assert dq.get("title") == "初到边境", (
            f"Expected title from milestone template, got: {dq.get('title')}"
        )

        # --- Verify bulletin title was synced ---
        area_state = runtime_obj.state.areas.get_area("frontier_town")
        bulletins = getattr(area_state, "board_bulletins", {})
        board_entries = bulletins.get("quest_board", [])
        matching = [e for e in board_entries if e.get("quest_id") == "dq_ms_test_arrival"]
        assert matching, "Bulletin entry for dq_ms_test_arrival must exist"
        assert matching[0].get("title") == "初到边境", (
            f"Bulletin title should be updated to '初到边境', got: {matching[0].get('title')}"
        )

    asyncio.run(_run())
