import types

import pytest

from app.models.game import GamePhase
from app.services.admin.admin_coordinator import AdminCoordinator
from app.services.admin.narrative_coordinator import NarrativeCoordinator


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

    nc = NarrativeCoordinator.__new__(NarrativeCoordinator)
    nc._world_runtime = _RuntimeStub()
    nc._state_manager = _StateManagerStub()
    nc.narrative_service = _NarrativeStub()
    nc.party_service = _PartyServiceStub()
    nc.character_store = _CharacterStoreStub()
    nc.llm_service = llm
    nc._world_background_cache = {}
    nc._character_roster_cache = {}

    async def _world_background(world_id: str, session_id: str | None = None):
        return "这是一个危机四伏却仍保有希望的世界。"

    nc._get_world_background = _world_background

    narration = await nc.generate_opening_narration("world_x", "session_x")

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
async def test_list_recoverable_sessions_detects_character_from_metadata():
    """has_character 标记由 SessionRuntime.persist() 写入 metadata。"""
    session_with_char = types.SimpleNamespace(
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
            },
            "has_character": True,
        },
    )
    session_no_char = types.SimpleNamespace(
        session_id="sess_no_pc",
        world_id="w",
        status="idle",
        updated_at="2026-02-09T00:00:00Z",
        participants=["u1"],
        metadata={
            "admin_state": {
                "player_location": "frontier_town",
                "chapter_id": "ch_1_1",
                "sub_location": None,
            },
        },
    )

    class _SessionStoreStub:
        async def list_sessions(self, world_id: str, user_id: str, limit: int = 20):
            return [session_with_char, session_no_char]

    class _PartyStoreStub:
        async def get_party(self, world_id: str, session_id: str):
            return None

    coordinator = AdminCoordinator.__new__(AdminCoordinator)
    coordinator._session_store = _SessionStoreStub()
    coordinator.party_service = _PartyStoreStub()

    rows = await AdminCoordinator.list_recoverable_sessions(
        coordinator,
        world_id="w",
        user_id="u1",
        limit=20,
    )

    assert len(rows) == 2
    assert rows[0]["session_id"] == "sess_has_pc"
    assert rows[0]["needs_character_creation"] is False
    assert rows[1]["session_id"] == "sess_no_pc"
    assert rows[1]["needs_character_creation"] is True


@pytest.mark.asyncio
async def test_get_context_async_prefers_session_runtime_player(monkeypatch):
    class _StateManagerCtxStub:
        async def get_state(self, world_id: str, session_id: str):
            return types.SimpleNamespace(
                metadata={},
                combat_id=None,
                active_dialogue_npc=None,
                game_time=types.SimpleNamespace(day=1),
            )

    class _RuntimeSessionStub:
        player = types.SimpleNamespace(name="测试角色")

    from app.runtime.session_runtime import SessionRuntime

    async def _fake_get_or_restore(cls, world_id: str, session_id: str):
        return _RuntimeSessionStub()

    monkeypatch.setattr(
        SessionRuntime,
        "get_or_restore",
        classmethod(_fake_get_or_restore),
    )

    coordinator = AdminCoordinator.__new__(AdminCoordinator)
    coordinator._state_manager = _StateManagerCtxStub()

    ctx = await AdminCoordinator.get_context_async(coordinator, "w", "s")

    assert ctx is not None
    assert ctx.phase == GamePhase.IDLE
