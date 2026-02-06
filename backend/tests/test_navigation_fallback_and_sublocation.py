import inspect
import uuid

import pytest

from app.models.admin_protocol import FlashOperation, FlashRequest
from app.models.state_delta import GameState
from app.services.admin.flash_cpu_service import FlashCPUService
from app.services.admin.state_manager import StateManager
from app.services.admin.world_runtime import AdminWorldRuntime
from app.services.area_navigator import AreaNavigator


def test_area_navigator_fallback_to_firestore_when_local_missing(monkeypatch):
    """本地 maps.json 不存在时，应回退到 Firestore 加载。"""
    called = {"count": 0}

    def fake_load_from_firestore(self):
        called["count"] += 1
        self._load_maps(
            {
                "maps": [
                    {
                        "id": "frontier_town",
                        "name": "边境城镇",
                        "connections": [],
                        "sub_locations": [{"id": "frontier_tavern", "name": "酒馆"}],
                    }
                ]
            }
        )

    monkeypatch.setattr(AreaNavigator, "_load_maps_from_firestore", fake_load_from_firestore)

    nav = AreaNavigator(world_id=f"missing_world_{uuid.uuid4().hex}")

    assert called["count"] == 1
    assert nav.get_area("frontier_town") is not None


def test_world_runtime_reloads_empty_navigator_cache(monkeypatch):
    """首次缓存为空地图时，应触发一次重建。"""

    class _FakeNavigator:
        calls = 0

        def __init__(self, world_id: str):
            _FakeNavigator.calls += 1
            self.world_id = world_id
            # 第一次返回空地图，第二次返回有效地图
            self.maps = {} if _FakeNavigator.calls == 1 else {"frontier_town": object()}

    monkeypatch.setattr("app.services.admin.world_runtime.AreaNavigator", _FakeNavigator)

    runtime = AdminWorldRuntime(state_manager=StateManager())
    runtime._get_navigator.cache_clear()

    nav = runtime._get_navigator_ready("test_world")

    assert _FakeNavigator.calls == 2
    assert nav.maps


class _DummySessionState:
    def __init__(self, session_id: str):
        self.session_id = session_id


class _DummySessionStore:
    def __init__(self) -> None:
        self.created = False
        self.updated = []

    async def create_session(self, world_id: str, session_id=None, participants=None):
        self.created = True
        return _DummySessionState(session_id or "sess_test")

    async def get_session(self, world_id: str, session_id: str):
        return None

    async def update_session(self, world_id: str, session_id: str, updates: dict):
        self.updated.append((world_id, session_id, updates))


class _DummyProgress:
    current_chapter = "chapter_1"

    def to_dict(self):
        return {"current_chapter": self.current_chapter}


class _DummyNarrativeService:
    async def get_progress(self, world_id: str, session_id: str):
        return _DummyProgress()


@pytest.mark.asyncio
async def test_world_runtime_start_session_fails_when_maps_missing(monkeypatch):
    """世界无地图时应明确报错，且不创建会话。"""
    store = _DummySessionStore()
    runtime = AdminWorldRuntime(
        state_manager=StateManager(),
        session_store=store,
        narrative_service=_DummyNarrativeService(),
    )

    class _EmptyNavigator:
        maps = {}

    monkeypatch.setattr(runtime, "_get_navigator_ready", lambda _wid: _EmptyNavigator())

    with pytest.raises(ValueError, match="未初始化地图数据"):
        await runtime.start_session(world_id="missing_world")

    assert store.created is False


@pytest.mark.asyncio
async def test_world_runtime_get_current_location_self_heals_invalid_location(monkeypatch):
    """旧会话位置失效时应自动回退到可用地图并持久化。"""
    store = _DummySessionStore()
    state_manager = StateManager()
    runtime = AdminWorldRuntime(
        state_manager=state_manager,
        session_store=store,
        narrative_service=_DummyNarrativeService(),
    )

    navigator = AreaNavigator(
        world_id="test_world",
        maps_data={
            "maps": [
                {
                    "id": "frontier_town",
                    "name": "边境城镇",
                    "description": "测试城镇",
                    "atmosphere": "平静",
                    "danger_level": "low",
                    "connections": [],
                }
            ]
        },
    )
    monkeypatch.setattr(runtime, "_get_navigator_ready", lambda _wid: navigator)

    await state_manager.set_state(
        "test_world",
        "sess_1",
        GameState(
            world_id="test_world",
            session_id="sess_1",
            player_location="missing_area",
        ),
    )

    location = await runtime.get_current_location("test_world", "sess_1")
    assert location["location_id"] == "frontier_town"
    assert location["location_name"] == "边境城镇"

    healed = await state_manager.get_state("test_world", "sess_1")
    assert healed is not None
    assert healed.player_location == "frontier_town"
    assert any("metadata.admin_state" in updates for _, _, updates in store.updated)


class _DummyState:
    player_location = "frontier_town"


class _DummyRuntime:
    def __init__(self):
        self.entered_sub_location_id = None
        self.advance_time_calls = 0
        self.navigate_calls = 0

    async def get_state(self, world_id: str, session_id: str):
        return _DummyState()

    async def get_current_location(self, world_id: str, session_id: str):
        return {
            "location_id": "frontier_town",
            "available_sub_locations": [
                {"id": "frontier_tavern", "name": "酒馆"},
                {"id": "blacksmith_shop", "name": "铁匠铺"},
            ],
        }

    async def enter_sub_location(self, world_id: str, session_id: str, sub_location_id: str):
        self.entered_sub_location_id = sub_location_id
        return {"success": True, "sub_location": {"id": sub_location_id}}

    async def refresh_state(self, world_id: str, session_id: str):
        return None

    async def advance_time(self, world_id: str, session_id: str, minutes: int):
        self.advance_time_calls += 1
        return {
            "time": {"day": 1, "hour": 8, "minute": minutes},
            "events": [],
        }

    async def navigate(
        self,
        world_id: str,
        session_id: str,
        destination: str = None,
        direction: str = None,
        generate_narration: bool = True,
    ):
        self.navigate_calls += 1
        return {
            "success": True,
            "new_location": {"location_id": destination or "frontier_town"},
            "narration": "",
        }

    def _get_navigator(self, world_id: str):
        return AreaNavigator(
            world_id,
            maps_data={
                "maps": [
                    {
                        "id": "frontier_town",
                        "name": "边境城镇",
                        "connections": [],
                        "sub_locations": [
                            {"id": "frontier_tavern", "name": "酒馆"},
                            {"id": "blacksmith_shop", "name": "铁匠铺"},
                        ],
                    }
                ]
            },
        )


async def _fallback_only_call(tool_name, arguments, fallback):
    result = fallback()
    if inspect.isawaitable(result):
        return await result
    return result


@pytest.mark.asyncio
async def test_enter_sublocation_accepts_sub_location_name():
    runtime = _DummyRuntime()
    service = FlashCPUService(world_runtime=runtime)
    service._call_tool_with_fallback = _fallback_only_call

    result = await service.execute_request(
        world_id="test_world",
        session_id="test_session",
        request=FlashRequest(
            operation=FlashOperation.ENTER_SUBLOCATION,
            parameters={"sub_location": "酒馆"},
        ),
    )

    assert result.success is True
    assert runtime.entered_sub_location_id == "frontier_tavern"


@pytest.mark.asyncio
async def test_enter_sublocation_keeps_sub_location_id_priority():
    runtime = _DummyRuntime()
    service = FlashCPUService(world_runtime=runtime)
    service._call_tool_with_fallback = _fallback_only_call

    result = await service.execute_request(
        world_id="test_world",
        session_id="test_session",
        request=FlashRequest(
            operation=FlashOperation.ENTER_SUBLOCATION,
            parameters={"sub_location_id": "blacksmith_shop", "sub_location": "酒馆"},
        ),
    )

    assert result.success is True
    assert runtime.entered_sub_location_id == "blacksmith_shop"


@pytest.mark.asyncio
async def test_enter_sublocation_accepts_destination_alias():
    runtime = _DummyRuntime()
    service = FlashCPUService(world_runtime=runtime)
    service._call_tool_with_fallback = _fallback_only_call

    result = await service.execute_request(
        world_id="test_world",
        session_id="test_session",
        request=FlashRequest(
            operation=FlashOperation.ENTER_SUBLOCATION,
            parameters={"destination": "酒馆"},
        ),
    )

    assert result.success is True
    assert runtime.entered_sub_location_id == "frontier_tavern"


@pytest.mark.asyncio
async def test_enter_sublocation_accepts_location_alias():
    runtime = _DummyRuntime()
    service = FlashCPUService(world_runtime=runtime)
    service._call_tool_with_fallback = _fallback_only_call

    result = await service.execute_request(
        world_id="test_world",
        session_id="test_session",
        request=FlashRequest(
            operation=FlashOperation.ENTER_SUBLOCATION,
            parameters={"location": "酒馆"},
        ),
    )

    assert result.success is True
    assert runtime.entered_sub_location_id == "frontier_tavern"


@pytest.mark.asyncio
async def test_enter_sublocation_fallbacks_when_mcp_returns_failure():
    runtime = _DummyRuntime()
    service = FlashCPUService(world_runtime=runtime)

    async def _mcp_semantic_failure(tool_name, arguments, fallback):
        return {"success": False, "error": "子地点不存在"}

    service._call_tool_with_fallback = _mcp_semantic_failure

    result = await service.execute_request(
        world_id="test_world",
        session_id="test_session",
        request=FlashRequest(
            operation=FlashOperation.ENTER_SUBLOCATION,
            parameters={"sub_location_id": "tavern"},
        ),
    )

    assert result.success is True
    assert runtime.entered_sub_location_id == "tavern"


@pytest.mark.asyncio
async def test_update_time_fallbacks_when_mcp_returns_failure():
    runtime = _DummyRuntime()
    service = FlashCPUService(world_runtime=runtime)

    async def _mcp_semantic_failure(tool_name, arguments, fallback):
        return {"error": "mcp error"}

    service._call_tool_with_fallback = _mcp_semantic_failure

    result = await service.execute_request(
        world_id="test_world",
        session_id="test_session",
        request=FlashRequest(
            operation=FlashOperation.UPDATE_TIME,
            parameters={"minutes": 15},
        ),
    )

    assert result.success is True
    assert runtime.advance_time_calls == 1
    assert "time" in result.result


@pytest.mark.asyncio
async def test_navigate_fallbacks_when_mcp_returns_failure():
    runtime = _DummyRuntime()
    service = FlashCPUService(world_runtime=runtime)

    async def _mcp_semantic_failure(tool_name, arguments, fallback):
        return {"success": False, "error": "位置不存在"}

    service._call_tool_with_fallback = _mcp_semantic_failure

    result = await service.execute_request(
        world_id="test_world",
        session_id="test_session",
        request=FlashRequest(
            operation=FlashOperation.NAVIGATE,
            parameters={"destination": "frontier_town"},
        ),
    )

    assert result.success is True
    assert runtime.navigate_calls == 1
