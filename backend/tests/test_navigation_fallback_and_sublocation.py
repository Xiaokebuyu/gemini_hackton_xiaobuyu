import uuid
import types

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
    async def load_narrative_data(self, world_id: str, force_reload: bool = False):
        return None

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


class _DummyRuntime:
    def __init__(self):
        self.entered_sub_location_id = None

    async def get_state(self, world_id: str, session_id: str):
        class _State:
            player_location = "frontier_town"
        return _State()

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
        return {
            "time": {"day": 1, "hour": 8, "minute": minutes},
            "events": [],
        }

    async def get_game_time(self, world_id: str, session_id: str):
        return {"day": 1, "hour": 8, "minute": 0, "formatted": "第1天 08:00"}

    async def navigate(
        self,
        world_id: str,
        session_id: str,
        destination: str = None,
        direction: str = None,
        generate_narration: bool = True,
    ):
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


@pytest.mark.asyncio
async def test_update_time_calls_runtime_directly():
    """时间更新操作应直接调用 world_runtime.advance_time。"""
    runtime = _DummyRuntime()
    service = FlashCPUService(world_runtime=runtime)

    result = await service.execute_request(
        world_id="test_world",
        session_id="test_session",
        request=FlashRequest(
            operation=FlashOperation.UPDATE_TIME,
            parameters={"minutes": 15},
        ),
    )

    assert result.success is True
    assert "time" in result.result


class _DummyCharacter:
    def __init__(self):
        self.character_id = "player"
        self.name = "玩家"
        self.level = 1
        self.xp = 100
        self.gold = 12
        self.current_hp = 20
        self.max_hp = 30
        self.inventory = []
        self.updated_at = None

    def add_item(self, item_id, item_name, quantity=1, properties=None):
        for item in self.inventory:
            if item.get("item_id") == item_id:
                item["quantity"] = item.get("quantity", 1) + quantity
                return item
        item = {"item_id": item_id, "name": item_name, "quantity": quantity}
        if properties:
            item["properties"] = properties
        self.inventory.append(item)
        return item

    def remove_item(self, item_id, quantity=1):
        for idx, item in enumerate(self.inventory):
            if item.get("item_id") == item_id:
                current = int(item.get("quantity", 1))
                if current <= quantity:
                    self.inventory.pop(idx)
                else:
                    item["quantity"] = current - quantity
                return True
        return False


class _DummyCharacterStore:
    def __init__(self, character: _DummyCharacter):
        self.character = character

    async def get_character(self, world_id: str, session_id: str):
        return self.character

    async def save_character(self, world_id: str, session_id: str, character):
        self.character = character


class _DummyCharacterService:
    def __init__(self, store: _DummyCharacterStore):
        self.store = store

    async def add_xp(self, world_id: str, session_id: str, amount: int):
        character = await self.store.get_character(world_id, session_id)
        character.xp += amount
        leveled = False
        if character.xp >= 300:
            character.level = 2
            leveled = True
        return {
            "xp_gained": amount,
            "new_xp": character.xp,
            "leveled_up": leveled,
            "new_level": character.level,
        }


class _DummyParty:
    party_id = "party_test"
    share_events = True

    def get_active_members(self):
        return [
            types.SimpleNamespace(
                character_id="ally_1",
                name="队友一",
                role=types.SimpleNamespace(value="support"),
                current_mood="focused",
            )
        ]


class _DummyPartyService:
    async def get_party(self, world_id: str, session_id: str):
        return _DummyParty()


@pytest.mark.asyncio
async def test_character_and_inventory_ops_return_state_delta():
    runtime = _DummyRuntime()
    character = _DummyCharacter()
    store = _DummyCharacterStore(character)
    service = FlashCPUService(
        world_runtime=runtime,
        character_store=store,
        character_service=_DummyCharacterService(store),
    )

    add_item_result = await service.execute_request(
        world_id="test_world",
        session_id="test_session",
        request=FlashRequest(
            operation=FlashOperation.ADD_ITEM,
            parameters={"item_id": "potion", "item_name": "治疗药水", "quantity": 2},
        ),
    )
    assert add_item_result.success is True
    assert add_item_result.state_delta is not None
    assert add_item_result.state_delta.changes.get("inventory_item_count") == 1

    heal_result = await service.execute_request(
        world_id="test_world",
        session_id="test_session",
        request=FlashRequest(
            operation=FlashOperation.HEAL_PLAYER,
            parameters={"amount": 5},
        ),
    )
    assert heal_result.success is True
    assert heal_result.state_delta is not None
    hp_change = heal_result.state_delta.changes.get("player_hp") or {}
    assert hp_change.get("current") == 25

    xp_result = await service.execute_request(
        world_id="test_world",
        session_id="test_session",
        request=FlashRequest(
            operation=FlashOperation.ADD_XP,
            parameters={"amount": 210},
        ),
    )
    assert xp_result.success is True
    assert xp_result.state_delta is not None
    xp_change = xp_result.state_delta.changes.get("xp") or {}
    assert xp_change.get("new_xp") == 310
    assert xp_change.get("leveled_up") is True


@pytest.mark.asyncio
async def test_get_status_includes_party_and_player_snapshot():
    runtime = _DummyRuntime()
    character = _DummyCharacter()
    character.inventory = [{"item_id": "coin", "name": "金币", "quantity": 1}]
    store = _DummyCharacterStore(character)
    service = FlashCPUService(
        world_runtime=runtime,
        character_store=store,
        party_service=_DummyPartyService(),
    )

    status_result = await service.execute_request(
        world_id="test_world",
        session_id="test_session",
        request=FlashRequest(
            operation=FlashOperation.GET_STATUS,
            parameters={},
        ),
    )

    assert status_result.success is True
    party = status_result.result.get("party") or {}
    player = status_result.result.get("player") or {}
    assert party.get("has_party") is True
    assert len(party.get("members", [])) == 1
    assert player.get("inventory_item_count") == 1


class _DummyPartyOps:
    def __init__(self) -> None:
        self.party_id = "party_ops"
        self.members = []

    def is_full(self):
        return False

    def get_member(self, character_id: str):
        for member in self.members:
            if member.character_id == character_id:
                return member
        return None

    def get_active_members(self):
        return list(self.members)


class _DummyPartyOpsService:
    def __init__(self) -> None:
        self.party = _DummyPartyOps()

    async def get_or_create_party(self, world_id: str, session_id: str):
        if self.party is None:
            self.party = _DummyPartyOps()
        return self.party

    async def get_party(self, world_id: str, session_id: str):
        return self.party

    async def add_member(
        self,
        world_id: str,
        session_id: str,
        character_id: str,
        name: str,
        role,
        personality: str = "",
        response_tendency: float = 0.5,
    ):
        member = types.SimpleNamespace(
            character_id=character_id,
            name=name,
            role=role,
            personality=personality,
            response_tendency=response_tendency,
            current_mood="neutral",
        )
        self.party.members.append(member)
        return member

    async def remove_member(self, world_id: str, session_id: str, character_id: str):
        if self.party is None:
            return False
        for idx, member in enumerate(self.party.members):
            if member.character_id == character_id:
                self.party.members.pop(idx)
                return True
        return False

    async def disband_party(self, world_id: str, session_id: str):
        existed = self.party is not None
        self.party = None
        return existed


class _DummyNarrativeEventService:
    async def trigger_event(self, world_id: str, session_id: str, event_id: str, skip_advance: bool = True):
        return {"success": True, "event_id": event_id}


@pytest.mark.asyncio
async def test_party_and_story_event_ops_return_state_delta():
    runtime = _DummyRuntime()
    party_service = _DummyPartyOpsService()
    service = FlashCPUService(
        world_runtime=runtime,
        party_service=party_service,
        narrative_service=_DummyNarrativeEventService(),
    )

    add_result = await service.execute_request(
        world_id="test_world",
        session_id="test_session",
        request=FlashRequest(
            operation=FlashOperation.ADD_TEAMMATE,
            parameters={"character_id": "ally_2", "name": "队友二", "role": "support"},
        ),
    )
    assert add_result.success is True
    assert add_result.state_delta is not None
    assert add_result.state_delta.changes.get("party_member_count") == 1
    assert add_result.state_delta.changes.get("party_update", {}).get("action") == "add_member"

    remove_result = await service.execute_request(
        world_id="test_world",
        session_id="test_session",
        request=FlashRequest(
            operation=FlashOperation.REMOVE_TEAMMATE,
            parameters={"character_id": "ally_2", "reason": "剧情分队"},
        ),
    )
    assert remove_result.success is True
    assert remove_result.state_delta is not None
    assert remove_result.state_delta.changes.get("party_member_count") == 0
    assert remove_result.state_delta.changes.get("party_update", {}).get("action") == "remove_member"

    add_again_result = await service.execute_request(
        world_id="test_world",
        session_id="test_session",
        request=FlashRequest(
            operation=FlashOperation.ADD_TEAMMATE,
            parameters={"character_id": "ally_3", "name": "队友三"},
        ),
    )
    assert add_again_result.success is True

    disband_result = await service.execute_request(
        world_id="test_world",
        session_id="test_session",
        request=FlashRequest(
            operation=FlashOperation.DISBAND_PARTY,
            parameters={"reason": "任务结束"},
        ),
    )
    assert disband_result.success is True
    assert disband_result.state_delta is not None
    assert disband_result.state_delta.changes.get("has_party") is False
    assert disband_result.state_delta.changes.get("party_member_count") == 0
    assert disband_result.state_delta.changes.get("party_update", {}).get("action") == "disband"

    trigger_result = await service.execute_request(
        world_id="test_world",
        session_id="test_session",
        request=FlashRequest(
            operation=FlashOperation.TRIGGER_NARRATIVE_EVENT,
            parameters={"event_id": "ev_test"},
        ),
    )
    assert trigger_result.success is True
    assert trigger_result.state_delta is not None
    assert trigger_result.state_delta.changes.get("story_events") == ["ev_test"]
    story_update = trigger_result.state_delta.changes.get("story_event_update", {})
    assert story_update.get("event_id") == "ev_test"
