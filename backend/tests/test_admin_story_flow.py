import types

import pytest

from app.services.admin.admin_coordinator import AdminCoordinator


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


class _StateManagerStub:
    async def get_state(self, world_id: str, session_id: str):
        return types.SimpleNamespace(
            game_time=types.SimpleNamespace(
                model_dump=lambda: {"formatted": "第1天 08:00"}
            )
        )


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
    coordinator._state_manager = _StateManagerStub()
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
