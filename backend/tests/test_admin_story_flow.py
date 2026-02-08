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
