import types

import pytest

from app.services.admin.admin_coordinator import AdminCoordinator
from app.services.admin.flash_cpu_service import FlashCPUService


class _RuntimeStub:
    async def get_current_location(self, world_id: str, session_id: str):
        return {
            "location_id": "frontier_town",
            "location_name": "边境小镇",
            "atmosphere": "清晨薄雾",
            "npcs_present": ["柜台老板", "路过冒险者"],
        }

    async def get_game_time(self, world_id: str, session_id: str):
        return {"formatted": "第1天 08:00"}


class _NarrativeStub:
    async def get_current_chapter_plan(self, world_id: str, session_id: str):
        return {
            "chapter": {
                "id": "ch_1",
                "name": "第一章",
                "description": "抵达边境小镇并调查近期异动。",
            },
            "goals": ["打听哥布林踪迹", "确认委托人身份"],
        }


class _PartyStub:
    def get_active_members(self):
        return [types.SimpleNamespace(name="女神官")]


class _PartyServiceStub:
    async def get_party(self, world_id: str, session_id: str):
        return _PartyStub()


class _CharacterStoreStub:
    async def get_character(self, world_id: str, session_id: str):
        return None


class _LLMStub:
    def __init__(self):
        self.last_prompt = ""

    async def generate_simple(self, prompt: str, **kwargs):
        self.last_prompt = prompt
        return "晨雾笼罩着边境小镇，新的冒险就此开始。"


@pytest.mark.asyncio
async def test_generate_opening_narration_formats_prompt_without_missing_keys():
    llm = _LLMStub()

    coordinator = AdminCoordinator.__new__(AdminCoordinator)
    coordinator._world_runtime = _RuntimeStub()
    coordinator.narrative_service = _NarrativeStub()
    coordinator.party_service = _PartyServiceStub()
    coordinator.character_store = _CharacterStoreStub()
    coordinator.flash_cpu = types.SimpleNamespace(llm_service=llm)

    async def _world_background(world_id: str, session_id: str | None = None):
        return "这是一个危机四伏却仍保有希望的世界。"

    coordinator._get_world_background = _world_background

    narration = await AdminCoordinator.generate_opening_narration(
        coordinator, "world_x", "session_x"
    )

    assert narration
    assert "第一章" in llm.last_prompt
    assert "打听哥布林踪迹" in llm.last_prompt
    assert "清晨薄雾" in llm.last_prompt
    assert "女神官" in llm.last_prompt


def test_parse_analysis_result_extracts_top_level_story_progression():
    service = FlashCPUService()
    parsed = {
        "intent_type": "roleplay",
        "confidence": 0.9,
        "operations": [],
        "memory_seeds": [],
        "story_progression": {
            "story_events": ["ev_1", " ", None],
            "progress_note": "玩家完成了关键线索确认。",
        },
    }

    plan = service._parse_analysis_result(parsed, "我去调查线索", {})

    assert plan.story_progression == {
        "story_events": ["ev_1"],
        "progress_note": "玩家完成了关键线索确认。",
    }


def test_parse_analysis_result_extracts_nested_story_progression():
    service = FlashCPUService()
    parsed = {
        "intent_type": "roleplay",
        "confidence": 0.9,
        "operations": [],
        "memory_seeds": [],
        "context_package": {
            "story_progression": {
                "story_events": "ev_a，ev_b、ev_c",
                "progress_note": "推进成功",
            }
        },
    }

    plan = service._parse_analysis_result(parsed, "继续推进剧情", {})

    assert plan.story_progression == {
        "story_events": ["ev_a", "ev_b", "ev_c"],
        "progress_note": "推进成功",
    }


def test_build_chapter_response_payload_includes_extended_fields():
    context = {
        "chapter_info": {
            "chapter": {
                "id": "ch_1_1",
                "name": "第一章",
                "description": "章节描述",
            },
            "goals": ["推进主线"],
            "required_events": ["ev_1", "ev_2"],
            "pending_required_events": ["ev_2"],
            "events_triggered": ["ev_1"],
            "event_total": 2,
            "event_completed": 1,
            "event_completion_pct": 50.0,
            "all_required_events_completed": False,
            "waiting_transition": False,
            "current_event": {
                "id": "ev_2",
                "name": "突袭",
                "description": "哥布林发动突袭",
            },
            "next_chapter": {"id": "ch_1_2", "name": "第二章"},
        }
    }
    progress = types.SimpleNamespace(current_chapter="ch_1_1")
    final_directive = types.SimpleNamespace(
        pacing_action="hint",
        chapter_transition=types.SimpleNamespace(
            target_chapter_id="ch_1_2",
        ),
    )

    payload = AdminCoordinator._build_chapter_response_payload(
        context=context,
        progress=progress,
        final_directive=final_directive,
    )

    assert payload is not None
    assert payload["id"] == "ch_1_1"
    assert payload["name"] == "第一章"
    assert payload["description"] == "章节描述"
    assert payload["goals"] == ["推进主线"]
    assert payload["required_events"] == ["ev_1", "ev_2"]
    assert payload["pending_required_events"] == ["ev_2"]
    assert payload["events_triggered"] == ["ev_1"]
    assert payload["event_total"] == 2
    assert payload["event_completed"] == 1
    assert payload["event_completion_pct"] == 50.0
    assert payload["all_required_events_completed"] is False
    assert payload["waiting_transition"] is False
    assert payload["current_event"]["id"] == "ev_2"
    assert payload["transition"] == "ch_1_2"
    assert payload["pacing_action"] == "hint"


def test_build_story_director_metadata_contains_summary_fields():
    pre_directive = types.SimpleNamespace(
        auto_fired_events=[types.SimpleNamespace(id="ev_pre")],
        narrative_injections=["hint-a"],
    )
    final_directive = types.SimpleNamespace(
        fired_events=[types.SimpleNamespace(id="ev_post")],
        chapter_transition=types.SimpleNamespace(
            target_chapter_id="ch_2",
            transition_type="normal",
        ),
        pacing_action="accelerate",
        narrative_injections=["hint-b"],
    )
    turn_story_events = [types.SimpleNamespace(id="ev_pre"), types.SimpleNamespace(id="ev_post")]

    meta = AdminCoordinator._build_story_director_metadata(
        pre_directive=pre_directive,
        final_directive=final_directive,
        turn_story_events=turn_story_events,
    )

    assert meta["pre_auto_fired"] == ["ev_pre"]
    assert meta["post_fired"] == ["ev_post"]
    assert meta["turn_story_events"] == ["ev_pre", "ev_post"]
    assert meta["pacing_action"] == "accelerate"
    assert meta["transition_target"] == "ch_2"
    assert meta["transition_type"] == "normal"
    assert meta["narrative_directive_count"] == 2


def test_build_chapter_guidance_contains_event_focus_and_pending_events():
    service = FlashCPUService()
    guidance = service._build_chapter_guidance(
        {
            "chapter_info": {
                "chapter": {"name": "第一章"},
                "goals": ["调查哥布林巢穴"],
                "current_event": {
                    "id": "ev_5",
                    "name": "绝望的伏击",
                    "description": "队伍陷入混乱",
                },
                "required_events": ["ev_1", "ev_2", "ev_5"],
                "pending_required_events": ["ev_5"],
                "event_directives": ["[绝望的伏击] 引导玩家感知危险逼近。"],
            }
        }
    )

    assert "当前事件焦点" in guidance
    assert "绝望的伏击" in guidance
    assert "待触发关键事件" in guidance
    assert "ev_5" in guidance


@pytest.mark.asyncio
async def test_refresh_chapter_context_computes_event_progress_fields():
    class _NarrativeProgressStub:
        events_triggered = ["ev_1", "ev_2"]

    class _NarrativeContextStub:
        async def get_current_chapter_plan(self, world_id: str, session_id: str):
            return {
                "chapter": {"id": "ch_1", "name": "第一章"},
                "required_events": ["ev_1", "ev_2", "ev_3"],
                "current_event": {"id": "ev_3", "name": "收束事件"},
            }

        async def get_progress(self, world_id: str, session_id: str):
            return _NarrativeProgressStub()

    coordinator = AdminCoordinator.__new__(AdminCoordinator)
    coordinator.narrative_service = _NarrativeContextStub()
    context = {}

    await AdminCoordinator._refresh_chapter_context(coordinator, "w", "s", context)

    chapter_info = context["chapter_info"]
    assert chapter_info["event_total"] == 3
    assert chapter_info["event_completed"] == 2
    assert chapter_info["event_completion_pct"] == pytest.approx(66.67, rel=1e-3)
    assert chapter_info["all_required_events_completed"] is False
    assert chapter_info["waiting_transition"] is False


def test_detect_output_anomalies_marks_thought_leak():
    sample = (
        "thought\n"
        "*   Player Character (PC): 测试角色\n"
        "*   Current Scenario: cave\n"
        "*   Draft 1: ...\n"
        "*Self-Correction:* ...\n"
    )
    result = AdminCoordinator._detect_output_anomalies(sample)
    assert result["output_anomalies"] == ["thought_leak_suspected"]
    assert result["output_anomaly_excerpt"]


def test_reevaluate_transition_after_progress_returns_transition():
    class _StoryDirectorStub:
        def post_evaluate(self, *args, **kwargs):
            return types.SimpleNamespace(
                chapter_transition=types.SimpleNamespace(
                    target_chapter_id="ch_2",
                    transition_type="normal",
                )
            )

        def post_evaluate_multi(self, *args, **kwargs):
            return types.SimpleNamespace(chapter_transition=None)

    coordinator = AdminCoordinator.__new__(AdminCoordinator)
    coordinator.story_director = _StoryDirectorStub()
    coordinator._build_game_context = (
        lambda context, progress, session_id, player_input="": {"session_id": session_id}
    )

    transition = AdminCoordinator._reevaluate_transition_after_progress(
        coordinator,
        context={},
        progress=types.SimpleNamespace(),
        session_id="sess_x",
        player_input="推进剧情",
        chapters=[types.SimpleNamespace(id="ch_1")],
        flash_condition_results={},
        pre_auto_fired_ids=[],
    )

    assert transition is not None
    assert transition.target_chapter_id == "ch_2"
    assert transition.transition_type == "normal"


@pytest.mark.asyncio
async def test_list_recoverable_sessions_uses_character_store_for_creation_flag():
    session_obj = types.SimpleNamespace(
        session_id="sess_has_pc",
        world_id="w",
        status="idle",
        updated_at="2026-02-09T00:00:00Z",
        participants=["u1"],
        metadata={
            "admin_state": {
                "player_location": "frontier_town",
                "chapter_id": "ch_1_1",
                "sub_location": None,
            }
        },
    )

    class _SessionStoreStub:
        async def list_sessions(self, world_id: str, user_id: str, limit: int = 20):
            return [session_obj]

    class _PartyStoreStub:
        async def get_party(self, world_id: str, session_id: str):
            return None

    class _CharacterStoreForRecoverStub:
        async def get_character(self, world_id: str, session_id: str):
            if session_id == "sess_has_pc":
                return types.SimpleNamespace(name="测试角色")
            return None

    coordinator = AdminCoordinator.__new__(AdminCoordinator)
    coordinator._session_store = _SessionStoreStub()
    coordinator.party_store = _PartyStoreStub()
    coordinator.character_store = _CharacterStoreForRecoverStub()

    rows = await AdminCoordinator.list_recoverable_sessions(
        coordinator,
        world_id="w",
        user_id="u1",
        limit=20,
    )

    assert len(rows) == 1
    assert rows[0]["session_id"] == "sess_has_pc"
    assert rows[0]["needs_character_creation"] is False
