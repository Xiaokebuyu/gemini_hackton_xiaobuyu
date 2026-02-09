import asyncio
import types
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.models.admin_protocol import AgenticResult, AgenticToolCall, FlashOperation, FlashResponse
from app.models.admin_protocol import CoordinatorResponse
from app.services.admin.admin_coordinator import AdminCoordinator
from app.services.admin.agentic_enforcement import AgenticToolExecutionRequiredError


class _MemoryResult:
    def __init__(self, marker: str = "player") -> None:
        self.marker = marker
        self.activated_nodes = {f"node_{marker}": 0.9}

    def model_dump(self):
        return {
            "activated_nodes": self.activated_nodes,
            "used_subgraph": True,
        }


class _HistoryStub:
    def __init__(self) -> None:
        self.rounds = []
        self.teammate_records = []

    def get_recent_history(self, max_tokens: int = 0) -> str:
        return ""

    def get_last_teammate_responses(self):
        return []

    def record_round(self, player_input: str, gm_response: str, metadata: dict):
        self.rounds.append(
            {
                "player_input": player_input,
                "gm_response": gm_response,
                "metadata": metadata,
            }
        )
        return {
            "should_graphize": False,
            "message_count": 2,
            "total_tokens": 32,
            "usage_ratio": 0.1,
        }

    def record_teammate_response(self, character_id: str, name: str, response: str):
        self.teammate_records.append(
            {
                "character_id": character_id,
                "name": name,
                "response": response,
            }
        )


class _StateManagerStub:
    def __init__(self) -> None:
        self.state = types.SimpleNamespace(
            chapter_id="ch_1",
            area_id="town_square",
            player_location="town_square",
        )

    async def get_state(self, world_id: str, session_id: str):
        return self.state

    async def set_state(self, world_id: str, session_id: str, state):
        self.state = state


class _PartyStub:
    def __init__(self, members, share_events: bool = False) -> None:
        self._members = members
        self.share_events = share_events
        self.world_id = "w"

    def get_active_members(self):
        return list(self._members)


class _NarrativeServiceStub:
    def __init__(self, progress, transition_result=None) -> None:
        self._progress = progress
        self._transition_result = transition_result or {}
        self.saved_progress = None
        self.transition_calls = []

    async def load_narrative_data(self, world_id: str, force_reload: bool = False):
        return None

    async def get_progress(self, world_id: str, session_id: str):
        return self._progress

    async def save_progress(self, world_id: str, session_id: str, progress):
        self.saved_progress = progress

    async def transition_to_chapter(
        self,
        world_id: str,
        session_id: str,
        target_chapter_id: str,
        transition_type: str,
    ):
        self.transition_calls.append(
            {
                "target_chapter_id": target_chapter_id,
                "transition_type": transition_type,
            }
        )
        return dict(self._transition_result)

    async def trigger_event(self, world_id: str, session_id: str, event_id: str, skip_advance: bool = True):
        return {"success": True, "event_id": event_id}


class _TeammateServiceStub:
    def __init__(self, responses=None) -> None:
        self.calls = []
        self._responses = responses or []

    async def process_round(self, party, player_input: str, gm_response: str, context: dict):
        self.calls.append(
            {
                "player_input": player_input,
                "gm_response": gm_response,
                "context": dict(context),
            }
        )
        return types.SimpleNamespace(responses=self._responses)


def _build_directive(*, fired_events=None, chapter_transition=None):
    return types.SimpleNamespace(
        fired_events=list(fired_events or []),
        side_effects=[],
        narrative_injections=[],
        chapter_transition=chapter_transition,
        pacing_action=None,
    )


def _make_base_coordinator(
    monkeypatch: pytest.MonkeyPatch,
    *,
    party_members,
    party_share_events: bool,
    final_directive,
    agentic_result: AgenticResult,
    transition_result=None,
    strict_tools: bool = False,
):
    monkeypatch.setattr(settings, "use_agentic_mode", True)
    monkeypatch.setattr(settings, "admin_agentic_strict_tools", strict_tools)
    monkeypatch.setattr(settings, "fixed_world_id", "final-world")

    coordinator = AdminCoordinator.__new__(AdminCoordinator)
    history = _HistoryStub()
    state_manager = _StateManagerStub()
    chapter = types.SimpleNamespace(
        id="ch_1",
        events=[
            types.SimpleNamespace(id="ev_tool"),
            types.SimpleNamespace(id="ev_other"),
        ],
    )
    progress = types.SimpleNamespace(
        current_chapter="ch_1",
        events_triggered=[],
        rounds_in_chapter=0,
        rounds_since_last_progress=0,
        npc_interactions={},
        event_cooldowns={},
    )

    party = _PartyStub(
        members=[types.SimpleNamespace(character_id=m[0], name=m[1]) for m in party_members],
        share_events=party_share_events,
    )

    teammate_service = _TeammateServiceStub()
    narrative_service = _NarrativeServiceStub(progress=progress, transition_result=transition_result)

    coordinator.character_store = types.SimpleNamespace(
        get_character=AsyncMock(return_value=types.SimpleNamespace(character_id="player", name="玩家"))
    )
    coordinator._world_runtime = types.SimpleNamespace(
        get_current_location=AsyncMock(
            return_value={
                "location_id": "town_square",
                "location_name": "城镇广场",
            }
        ),
        _get_navigator_ready=lambda world_id: None,
    )
    coordinator.party_service = types.SimpleNamespace(
        get_party=AsyncMock(return_value=party),
        sync_locations=AsyncMock(),
    )
    coordinator.session_history_manager = types.SimpleNamespace(
        get_or_create=lambda world_id, session_id: history,
    )
    coordinator.narrative_service = narrative_service
    coordinator.story_director = types.SimpleNamespace(
        pre_evaluate=lambda *args, **kwargs: types.SimpleNamespace(
            auto_fired_events=[],
            pending_flash_conditions=[],
            narrative_injections=[],
        ),
        pre_evaluate_multi=lambda *args, **kwargs: types.SimpleNamespace(
            auto_fired_events=[],
            pending_flash_conditions=[],
            narrative_injections=[],
        ),
        post_evaluate=lambda *args, **kwargs: final_directive,
        post_evaluate_multi=lambda *args, **kwargs: final_directive,
    )
    coordinator._state_manager = state_manager
    coordinator.flash_cpu = types.SimpleNamespace(
        agentic_process=AsyncMock(return_value=agentic_result),
        run_required_tool_repair=AsyncMock(
            return_value=AgenticResult(
                narration="",
                tool_calls=[],
                flash_results=[],
                story_condition_results={},
            )
        ),
    )
    coordinator.teammate_response_service = teammate_service

    coordinator._build_context = AsyncMock(
        return_value={
            "world_id": "w",
            "state": "exploring",
            "location": {
                "location_id": "town_square",
                "location_name": "城镇广场",
                "atmosphere": "平静",
                "npcs_present": ["酒馆老板"],
            },
            "time": {"day": 1, "hour": 8, "minute": 0, "formatted": "第1天 08:00"},
            "chapter_info": {
                "chapter": {"id": "ch_1", "name": "第一章"},
                "goals": [],
            },
        }
    )
    coordinator._resolve_story_chapters = lambda world_id, progress_obj: [chapter]
    coordinator._build_game_context = lambda context, progress_obj, session_id, player_input="": {}
    coordinator._execute_side_effects = AsyncMock()
    coordinator._assemble_context = AsyncMock(
        side_effect=lambda base_context, memory_result, flash_results, world_id, session_id: dict(base_context)
    )
    coordinator._run_curation_pipeline = AsyncMock(return_value=(None, {}, {}))
    coordinator._summarize_memory = lambda memory_result: "记忆摘要"
    coordinator._build_effective_seeds = lambda analysis_memory_seeds, base_context, character_id="player": [
        f"seed_{character_id}"
    ]
    coordinator._recall_memory = AsyncMock(return_value=_MemoryResult("player"))
    coordinator._reevaluate_transition_after_progress = lambda **kwargs: None
    coordinator._sync_story_director_graph = AsyncMock()
    coordinator._refresh_chapter_context = AsyncMock()
    coordinator._distribute_event_to_party = AsyncMock()
    coordinator._get_available_actions = AsyncMock(return_value=[])
    coordinator._build_chapter_response_payload = lambda **kwargs: None
    coordinator._build_story_director_metadata = lambda **kwargs: {}
    coordinator._merge_state_deltas = lambda flash_results: None
    coordinator._detect_output_anomalies = lambda narration: {
        "output_anomalies": [],
        "output_anomaly_excerpt": None,
    }
    coordinator._run_graphization = AsyncMock()
    coordinator._generate_chapter_transition = AsyncMock(return_value="")
    coordinator._build_execution_summary = lambda flash_results: "EXEC_SUMMARY"

    return coordinator, progress, narrative_service, teammate_service


@pytest.mark.asyncio
async def test_v3_passes_prefill_memory_and_teammate_tasks_to_curation(monkeypatch: pytest.MonkeyPatch):
    flash_results = [
        FlashResponse(success=True, operation=FlashOperation.NPC_DIALOGUE, result={"summary": "ok"})
    ]
    agentic_result = AgenticResult(
        narration="叙述",
        tool_calls=[],
        flash_results=flash_results,
        story_condition_results={},
    )
    final_directive = _build_directive()
    coordinator, _, _, _ = _make_base_coordinator(
        monkeypatch,
        party_members=[("ally_1", "队友一"), ("ally_2", "队友二")],
        party_share_events=False,
        final_directive=final_directive,
        agentic_result=agentic_result,
    )

    player_memory = _MemoryResult("player")

    async def _recall_memory(
        world_id: str,
        character_id: str,
        seed_nodes,
        intent_type=None,
        chapter_id=None,
        area_id=None,
    ):
        if character_id == "player":
            return player_memory
        await asyncio.sleep(0.05)
        return _MemoryResult(character_id)

    captured = {}

    async def _curation_pipeline(**kwargs):
        captured["memory_result"] = kwargs["memory_result"]
        captured["teammate_task_keys"] = sorted(kwargs["teammate_recall_tasks"].keys())
        return None, {}, {}

    coordinator._recall_memory = _recall_memory
    coordinator._run_curation_pipeline = _curation_pipeline

    await AdminCoordinator.process_player_input_v3(
        coordinator,
        world_id="final-world",
        session_id="s",
        player_input="和队友商量下一步",
    )

    assert captured["memory_result"] is player_memory
    assert captured["teammate_task_keys"] == ["ally_1", "ally_2"]


@pytest.mark.asyncio
async def test_v3_records_trigger_event_from_tool_calls(monkeypatch: pytest.MonkeyPatch):
    flash_results = [
        FlashResponse(success=True, operation=FlashOperation.NPC_DIALOGUE, result={"summary": "ok"})
    ]
    tool_calls = [
        AgenticToolCall(
            name="trigger_narrative_event",
            args={"event_id": "ev_tool"},
            success=True,
            result={"success": True},
        )
    ]
    agentic_result = AgenticResult(
        narration="你触发了关键事件。",
        tool_calls=tool_calls,
        flash_results=flash_results,
        story_condition_results={},
    )
    final_directive = _build_directive()
    coordinator, progress, _, _ = _make_base_coordinator(
        monkeypatch,
        party_members=[],
        party_share_events=False,
        final_directive=final_directive,
        agentic_result=agentic_result,
    )

    response = await AdminCoordinator.process_player_input_v3(
        coordinator,
        world_id="final-world",
        session_id="s",
        player_input="推进剧情",
    )

    assert "ev_tool" in response.story_events
    assert "ev_tool" in progress.events_triggered
    assert response.metadata["event_accounting_mode"] == "tool_call_first"


@pytest.mark.asyncio
async def test_v3_records_story_events_from_curation_fallback(monkeypatch: pytest.MonkeyPatch):
    flash_results = [
        FlashResponse(success=True, operation=FlashOperation.NPC_DIALOGUE, result={"summary": "ok"})
    ]
    agentic_result = AgenticResult(
        narration="你发现了关键线索。",
        tool_calls=[],
        flash_results=flash_results,
        story_condition_results={},
    )
    final_directive = _build_directive()
    coordinator, progress, _, _ = _make_base_coordinator(
        monkeypatch,
        party_members=[],
        party_share_events=False,
        final_directive=final_directive,
        agentic_result=agentic_result,
    )
    coordinator._recall_memory = AsyncMock(return_value=_MemoryResult("player"))
    coordinator._run_curation_pipeline = AsyncMock(
        return_value=(
            {"story_progression": {"story_events": ["ev_other"]}},
            {},
            {},
        )
    )

    response = await AdminCoordinator.process_player_input_v3(
        coordinator,
        world_id="final-world",
        session_id="s",
        player_input="推进剧情",
    )

    assert "ev_other" not in response.story_events
    assert "ev_other" not in progress.events_triggered
    assert response.metadata["event_accounting_mode"] == "tool_call_first"


@pytest.mark.asyncio
async def test_v3_appends_chapter_transition_text_to_narration(monkeypatch: pytest.MonkeyPatch):
    flash_results = [
        FlashResponse(success=True, operation=FlashOperation.NPC_DIALOGUE, result={"summary": "ok"})
    ]
    chapter_transition = types.SimpleNamespace(
        target_chapter_id="ch_2",
        transition_type="normal",
        narrative_hint=None,
    )
    final_directive = _build_directive(chapter_transition=chapter_transition)
    agentic_result = AgenticResult(
        narration="原始叙述",
        tool_calls=[],
        flash_results=flash_results,
        story_condition_results={},
    )
    coordinator, _, narrative_service, _ = _make_base_coordinator(
        monkeypatch,
        party_members=[],
        party_share_events=False,
        final_directive=final_directive,
        agentic_result=agentic_result,
        transition_result={"new_chapter": "ch_2", "new_maps_unlocked": []},
    )
    coordinator._generate_chapter_transition = AsyncMock(return_value="【章节过渡】")
    coordinator._recall_memory = AsyncMock(return_value=_MemoryResult("player"))

    response = await AdminCoordinator.process_player_input_v3(
        coordinator,
        world_id="final-world",
        session_id="s",
        player_input="继续前进",
    )

    assert response.narration == "原始叙述\n\n【章节过渡】"
    assert narrative_service.transition_calls
    coordinator._generate_chapter_transition.assert_awaited_once()


@pytest.mark.asyncio
async def test_v3_teammate_uses_dual_signal_inputs(monkeypatch: pytest.MonkeyPatch):
    flash_results = [
        FlashResponse(success=True, operation=FlashOperation.NPC_DIALOGUE, result={"summary": "ok"})
    ]
    agentic_result = AgenticResult(
        narration="完整GM叙述",
        tool_calls=[],
        flash_results=flash_results,
        story_condition_results={},
    )
    final_directive = _build_directive()
    teammate_responses = [
        types.SimpleNamespace(
            character_id="ally_1",
            name="队友一",
            response="我赞同。",
            reaction="点头",
            model_used="flash",
            thinking_level="low",
            latency_ms=12,
        )
    ]
    coordinator, _, _, teammate_service = _make_base_coordinator(
        monkeypatch,
        party_members=[("ally_1", "队友一")],
        party_share_events=False,
        final_directive=final_directive,
        agentic_result=agentic_result,
    )
    teammate_service._responses = teammate_responses
    coordinator._recall_memory = AsyncMock(return_value=_MemoryResult("player"))

    response = await AdminCoordinator.process_player_input_v3(
        coordinator,
        world_id="final-world",
        session_id="s",
        player_input="你怎么看？",
    )

    assert teammate_service.calls
    call = teammate_service.calls[0]
    assert call["gm_response"] == "EXEC_SUMMARY"
    assert call["context"]["gm_narration_full"] == "完整GM叙述"
    assert response.metadata["teammate_signal_mode"] == "execution_summary+gm_narration"
    assert response.metadata["player_curation_applied"] is False


@pytest.mark.asyncio
async def test_v3_metadata_contains_agentic_trace_payload(monkeypatch: pytest.MonkeyPatch):
    flash_results = [
        FlashResponse(success=True, operation=FlashOperation.NPC_DIALOGUE, result={"summary": "ok"})
    ]
    tool_calls = [
        AgenticToolCall(
            name="recall_memory",
            args={"seeds": ["酒馆老板"]},
            success=True,
            duration_ms=23,
            result={"success": True, "activated_nodes": {"npc_tavern_owner": 0.88}},
        )
    ]
    agentic_result = AgenticResult(
        narration="你想起了酒馆老板的往事。",
        thinking_summary="先检索记忆，再组织叙述。",
        tool_calls=tool_calls,
        flash_results=flash_results,
        story_condition_results={},
        usage={
            "tool_calls": 1,
            "thoughts_token_count": 12,
            "output_token_count": 34,
            "total_token_count": 46,
        },
        finish_reason="STOP",
    )
    final_directive = _build_directive()
    coordinator, _, _, _ = _make_base_coordinator(
        monkeypatch,
        party_members=[],
        party_share_events=False,
        final_directive=final_directive,
        agentic_result=agentic_result,
    )
    coordinator._recall_memory = AsyncMock(return_value=_MemoryResult("player"))

    response = await AdminCoordinator.process_player_input_v3(
        coordinator,
        world_id="final-world",
        session_id="s",
        player_input="你记得那个老板吗？",
    )

    trace = response.metadata.get("agentic_trace")
    assert isinstance(trace, dict)
    assert trace["stats"]["count"] == 1
    assert trace["tool_calls"][0]["name"] == "recall_memory"
    assert trace["thinking"]["summary"] == "先检索记忆，再组织叙述。"
    assert trace["thinking"]["finish_reason"] == "STOP"


@pytest.mark.asyncio
async def test_v3_stream_emits_agentic_trace_event(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "use_agentic_mode", True)
    coordinator = AdminCoordinator.__new__(AdminCoordinator)
    coordinator.process_player_input_v3 = AsyncMock(
        return_value=CoordinatorResponse(
            narration="GM叙述",
            speaker="GM",
            teammate_responses=[],
            available_actions=[],
            state_delta=None,
            metadata={
                "agentic_trace": {
                    "thinking": {"summary": "思考摘要"},
                    "tool_calls": [{"index": 1, "name": "get_status"}],
                    "stats": {"count": 1, "failed": 0, "success": 1},
                }
            },
            story_events=[],
            pacing_action=None,
            chapter_info=None,
            image_data=None,
        )
    )

    events = []
    async for event in AdminCoordinator.process_player_input_v3_stream(
        coordinator,
        world_id="final-world",
        session_id="s",
        player_input="测试流式",
    ):
        events.append(event)

    trace_events = [event for event in events if event.get("type") == "agentic_trace"]
    assert len(trace_events) == 1
    assert trace_events[0]["agentic_trace"]["stats"]["count"] == 1

    complete_events = [event for event in events if event.get("type") == "complete"]
    assert len(complete_events) == 1
    assert complete_events[0]["agentic_trace"]["tool_calls"][0]["name"] == "get_status"


@pytest.mark.asyncio
async def test_v3_strict_mode_runs_repair_and_passes(monkeypatch: pytest.MonkeyPatch):
    initial_result = AgenticResult(
        narration="初始叙述",
        tool_calls=[],
        flash_results=[],
        story_condition_results={},
    )
    final_directive = _build_directive()
    coordinator, _, _, _ = _make_base_coordinator(
        monkeypatch,
        party_members=[],
        party_share_events=False,
        final_directive=final_directive,
        agentic_result=initial_result,
        strict_tools=True,
    )
    repair_result = AgenticResult(
        narration="修复后的叙述",
        tool_calls=[
            AgenticToolCall(
                name="add_teammate",
                args={"character_id": "ally_1", "name": "艾拉"},
                success=True,
                result={"success": True},
            )
        ],
        flash_results=[
            FlashResponse(
                success=True,
                operation=FlashOperation.ADD_TEAMMATE,
                result={"summary": "艾拉加入了队伍"},
            )
        ],
        story_condition_results={},
    )
    coordinator.flash_cpu.run_required_tool_repair = AsyncMock(return_value=repair_result)

    response = await AdminCoordinator.process_player_input_v3(
        coordinator,
        world_id="final-world",
        session_id="s",
        player_input="让艾拉加入队伍",
    )

    coordinator.flash_cpu.run_required_tool_repair.assert_awaited_once()
    enforcement_meta = response.metadata["agentic_enforcement"]
    assert enforcement_meta["passed"] is True
    assert enforcement_meta["repair"]["attempted"] is True
    assert enforcement_meta["repair"]["status"] == "repaired"
    assert enforcement_meta["repair"]["narration_replaced"] is True
    assert response.narration == "修复后的叙述"


@pytest.mark.asyncio
async def test_v3_strict_mode_raises_when_repair_still_fails(monkeypatch: pytest.MonkeyPatch):
    initial_result = AgenticResult(
        narration="初始叙述",
        tool_calls=[],
        flash_results=[],
        story_condition_results={},
    )
    final_directive = _build_directive()
    coordinator, _, _, _ = _make_base_coordinator(
        monkeypatch,
        party_members=[],
        party_share_events=False,
        final_directive=final_directive,
        agentic_result=initial_result,
        strict_tools=True,
    )
    coordinator.flash_cpu.run_required_tool_repair = AsyncMock(
        return_value=AgenticResult(
            narration="",
            tool_calls=[],
            flash_results=[],
            story_condition_results={},
        )
    )

    with pytest.raises(AgenticToolExecutionRequiredError) as exc_info:
        await AdminCoordinator.process_player_input_v3(
            coordinator,
            world_id="final-world",
            session_id="s",
            player_input="让艾拉加入队伍",
        )

    coordinator.flash_cpu.run_required_tool_repair.assert_awaited_once()
    assert "add_teammate" in exc_info.value.missing_requirements
