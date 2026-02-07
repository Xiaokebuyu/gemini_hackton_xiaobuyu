"""
Admin world runtime service (navigation/time/state).
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

from app.models.event import Event, EventContent, EventType, GMEventIngestRequest
from app.models.state_delta import GameState, GameTimeState, StateDelta
from app.services.admin.state_manager import StateManager
from app.services.event_generator import EventGenerator
from app.services.admin.event_service import AdminEventService
from app.services.llm_service import LLMService
from app.services.narrative_service import NarrativeService
from app.services.game_session_store import GameSessionStore
from app.services.area_navigator import AreaNavigator
from app.services.time_manager import GameTime, TimeManager


class AdminWorldRuntime:
    """Runtime logic for navigation/time/state without GameMasterService."""

    def __init__(
        self,
        state_manager: StateManager,
        session_store: Optional[GameSessionStore] = None,
        narrative_service: Optional[NarrativeService] = None,
        event_service: Optional[AdminEventService] = None,
        llm_service: Optional[LLMService] = None,
    ) -> None:
        self.state_manager = state_manager
        self.session_store = session_store or GameSessionStore()
        self.narrative_service = narrative_service or NarrativeService(self.session_store)
        self.event_service = event_service or AdminEventService()
        self.llm_service = llm_service or LLMService()

    @lru_cache(maxsize=10)
    def _get_navigator(self, world_id: str) -> AreaNavigator:
        """获取导航器（LRU 缓存，最多 10 个世界）"""
        return AreaNavigator(world_id)

    def _get_navigator_ready(self, world_id: str) -> AreaNavigator:
        """
        获取可用导航器。

        若某世界在“数据尚未初始化”时被缓存为空地图，后续补数据后需要重建一次。
        """
        navigator = self._get_navigator(world_id)
        if navigator.maps:
            return navigator

        # 清理 LRU 缓存并重建，避免长期持有空导航器。
        self._get_navigator.cache_clear()
        return self._get_navigator(world_id)

    @lru_cache(maxsize=10)
    def _get_event_generator(self, world_id: str) -> EventGenerator:
        """获取事件生成器（LRU 缓存）"""
        return EventGenerator(world_id)

    async def get_state(self, world_id: str, session_id: str) -> GameState:
        cached = await self.state_manager.get_state(world_id, session_id)
        if cached:
            return cached

        session = await self.session_store.get_session(world_id, session_id)
        if session and session.metadata.get("admin_state"):
            state = GameState(**session.metadata.get("admin_state"))
        else:
            state = GameState(world_id=world_id, session_id=session_id)

        await self.state_manager.set_state(world_id, session_id, state)
        return state

    async def refresh_state(self, world_id: str, session_id: str) -> GameState:
        session = await self.session_store.get_session(world_id, session_id)
        if session and session.metadata.get("admin_state"):
            state = GameState(**session.metadata.get("admin_state"))
        else:
            state = GameState(world_id=world_id, session_id=session_id)
        await self.state_manager.set_state(world_id, session_id, state)
        return state

    async def persist_state(self, state: GameState) -> None:
        await self.session_store.update_session(
            state.world_id,
            state.session_id,
            {"metadata.admin_state": state.model_dump()},
        )

    def _time_manager_from_state(self, state: GameState) -> TimeManager:
        return TimeManager.from_dict(state.game_time.model_dump())

    def _update_time_state(self, state: GameState, time_manager: TimeManager) -> None:
        time_dict = time_manager.to_dict()
        state.game_time = GameTimeState(**time_dict)

    async def start_session(
        self,
        world_id: str,
        session_id: Optional[str] = None,
        participants: Optional[list] = None,
        known_characters: Optional[list] = None,
        character_locations: Optional[dict] = None,
        starting_location: Optional[str] = None,
        starting_time: Optional[dict] = None,
    ) -> GameState:
        logger.info("会话启动: world=%s, session=%s", world_id, session_id)
        navigator = self._get_navigator_ready(world_id)
        if not navigator.maps:
            raise ValueError(
                f"世界 '{world_id}' 未初始化地图数据，请先准备 data/{world_id}/structured/maps.json"
            )

        initial_time = None
        if starting_time:
            initial_time = GameTime(
                day=starting_time.get("day", 1),
                hour=starting_time.get("hour", 8),
                minute=starting_time.get("minute", 0),
            )
        time_manager = TimeManager(initial_time=initial_time)

        current_location = None
        if starting_location:
            current_location = navigator.resolve_location_name(starting_location)
            if not current_location:
                raise ValueError(f"无效起始地点: {starting_location}")

        if not current_location:
            for area_id, area in navigator.maps.items():
                if area.danger_level == "low":
                    current_location = area_id
                    break
            if not current_location:
                current_location = list(navigator.maps.keys())[0]

        state = await self.session_store.create_session(world_id, session_id, participants)

        progress = await self.narrative_service.get_progress(world_id, state.session_id)

        admin_state = GameState(
            session_id=state.session_id,
            world_id=world_id,
            player_location=current_location,
            area_id=current_location,
            chapter_id=progress.current_chapter,
            sub_location=None,
            game_time=GameTimeState(**time_manager.to_dict()),
            chat_mode="think",
            narrative_progress=progress.to_dict(),
            metadata={
                "known_characters": known_characters or [],
                "character_locations": character_locations or {},
            },
        )

        await self.state_manager.set_state(world_id, state.session_id, admin_state)
        await self.persist_state(admin_state)
        logger.info("会话启动完成: session=%s, location=%s", admin_state.session_id, current_location)
        return admin_state

    async def get_current_location(self, world_id: str, session_id: str) -> Dict[str, Any]:
        state = await self.get_state(world_id, session_id)
        navigator = self._get_navigator_ready(world_id)

        if not navigator.maps:
            return {
                "error": (
                    f"世界 '{world_id}' 未初始化地图数据，请先准备 "
                    f"data/{world_id}/structured/maps.json"
                )
            }

        location_id = state.player_location
        if not location_id and navigator.maps:
            location_id = list(navigator.maps.keys())[0]
            state.player_location = location_id
            state.area_id = location_id
            await self.persist_state(state)

        if not location_id:
            return {"error": "当前位置未知"}

        area = navigator.get_area(location_id)
        if not area:
            fallback_id = list(navigator.maps.keys())[0]
            area = navigator.get_area(fallback_id)
            if area is None:
                return {"error": f"位置不存在: {location_id}"}
            location_id = fallback_id
            state.player_location = fallback_id
            state.area_id = fallback_id
            state.sub_location = None
            await self.persist_state(state)

        destinations = navigator.get_available_destinations(location_id)
        time_info = state.game_time.model_dump()

        npcs_present = list(area.resident_npcs) if area.resident_npcs else []
        sub_location_id = state.sub_location
        sub_location_name = None
        available_sub_locations = navigator.get_available_sub_locations(location_id)

        if sub_location_id:
            sub_loc = navigator.get_sub_location(location_id, sub_location_id)
            if sub_loc:
                sub_location_name = sub_loc.name
                if sub_loc.resident_npcs:
                    npcs_present = list(sub_loc.resident_npcs)

        return {
            "location_id": location_id,
            "location_name": area.name,
            "description": area.description,
            "atmosphere": area.atmosphere,
            "danger_level": area.danger_level,
            "available_destinations": destinations,
            "npcs_present": npcs_present,
            "available_actions": area.available_actions,
            "time": time_info,
            "sub_location_id": sub_location_id,
            "sub_location_name": sub_location_name,
            "available_sub_locations": available_sub_locations,
        }

    async def navigate(
        self,
        world_id: str,
        session_id: str,
        destination: Optional[str] = None,
        direction: Optional[str] = None,
        generate_narration: bool = True,
    ) -> Dict[str, Any]:
        state = await self.get_state(world_id, session_id)
        if not state.player_location:
            return {"success": False, "error": "当前位置未知"}

        logger.info("导航开始: from=%s, destination=%s, direction=%s", state.player_location, destination, direction)
        navigator = self._get_navigator_ready(world_id)
        event_generator = self._get_event_generator(world_id)

        target_id = None
        if destination:
            target_id = navigator.resolve_location_name(destination)
            if not target_id:
                return {"success": False, "error": f"未知的目的地: {destination}"}
        elif direction:
            return {"success": False, "error": "方向导航尚未实现，请使用目的地名称"}
        else:
            return {"success": False, "error": "请指定目的地或方向"}

        is_available = await self.narrative_service.is_map_available(world_id, session_id, target_id)
        if not is_available:
            available_maps = await self.narrative_service.get_available_maps(world_id, session_id)
            return {
                "success": False,
                "error": "该地区尚未解锁",
                "hint": "完成当前章节目标后解锁新地区",
                "available_maps": available_maps,
            }

        travel_result = navigator.calculate_travel(state.player_location, target_id)
        if not travel_result.success:
            return {"success": False, "error": travel_result.error}

        time_manager = self._time_manager_from_state(state)
        segments = []
        all_events = []
        narration_parts = []

        for segment in travel_result.segments:
            current_time = time_manager.time
            random_event = event_generator.check_travel_event(
                from_location=segment["from_id"],
                to_location=segment["to_id"],
                danger_level=segment["danger_level"],
                time=current_time,
            )

            if generate_narration:
                narration = await self._generate_travel_narration(
                    from_area=navigator.get_area(segment["from_id"]),
                    to_area=navigator.get_area(segment["to_id"]),
                    travel_time=segment["travel_time"],
                    time_manager=time_manager,
                    random_event=random_event,
                )
            else:
                narration = ""

            time_events = time_manager.tick(segment["time_minutes"])

            segment_data = {
                "from_id": segment["from_id"],
                "from_name": segment["from_name"],
                "to_id": segment["to_id"],
                "to_name": segment["to_name"],
                "travel_time": segment["travel_time"],
                "time_minutes": segment["time_minutes"],
                "danger_level": segment["danger_level"],
                "narration": narration,
                "event": random_event.to_dict() if random_event else None,
            }
            segments.append(segment_data)
            if narration:
                narration_parts.append(narration)

            if random_event:
                all_events.append(random_event.to_dict())
            for te in time_events:
                all_events.append({
                    "event_type": te.event_type,
                    "description": te.description,
                    "data": te.data,
                })

        state.player_location = target_id
        state.area_id = target_id
        state.sub_location = None
        progress = await self.narrative_service.get_progress(world_id, session_id)
        state.chapter_id = progress.current_chapter
        state.narrative_progress = progress.to_dict()
        self._update_time_state(state, time_manager)

        await self.state_manager.set_state(world_id, session_id, state)
        await self.persist_state(state)

        new_location = await self.get_current_location(world_id, session_id)
        logger.info("导航完成: to=%s, elapsed_minutes=%d", target_id, travel_result.total_time_minutes)

        await self._record_movement_event(
            world_id=world_id,
            session_id=session_id,
            from_id=travel_result.path[0] if travel_result.path else "",
            to_id=target_id,
            path=travel_result.path,
            known_characters=state.metadata.get("known_characters", []),
            character_locations=state.metadata.get("character_locations", {}),
        )

        return {
            "success": True,
            "narration": "\n\n".join(narration_parts),
            "segments": segments,
            "new_location": new_location,
            "time_elapsed_minutes": travel_result.total_time_minutes,
            "events": all_events,
            "time": time_manager.to_dict(),
        }

    async def _generate_travel_narration(
        self,
        from_area,
        to_area,
        travel_time: str,
        time_manager: TimeManager,
        random_event=None,
    ) -> str:
        time_info = time_manager.time.format()
        time_period = time_manager.time.get_period().value
        event_desc = ""
        if random_event:
            event_desc = f"\n事件: {random_event.title} - {random_event.description}"

        prompt = (
            "你是TRPG主持人，正在描述玩家的旅途。\n"
            f"起点: {from_area.name if from_area else '未知'}\n"
            f"终点: {to_area.name if to_area else '未知'}\n"
            f"时间: {time_info} ({time_period})\n"
            f"用时: {travel_time}\n"
            f"终点危险等级: {to_area.danger_level if to_area else 'low'}\n"
            f"终点描述: {to_area.description if to_area else ''}\n"
            f"{event_desc}\n"
            "输出2-3段简洁叙述，不要OOC。"
        )

        response = await self.llm_service.generate_response(prompt, "生成旅途叙述", thinking_level="low")
        return response.text

    async def _record_movement_event(
        self,
        world_id: str,
        session_id: str,
        from_id: str,
        to_id: str,
        path: list,
        known_characters: list,
        character_locations: dict,
    ) -> None:
        navigator = self._get_navigator_ready(world_id)
        from_area = navigator.get_area(from_id)
        to_area = navigator.get_area(to_id)

        event = Event(
            type=EventType.ACTION,
            game_day=None,
            location=to_id,
            participants=["player"],
            content=EventContent(
                raw=f"玩家从{from_area.name if from_area else from_id}前往{to_area.name if to_area else to_id}",
                structured={"action": "travel", "from": from_id, "to": to_id, "path": path},
            ),
        )

        request = GMEventIngestRequest(
            event=event,
            distribute=True,
            known_characters=known_characters,
            character_locations=character_locations,
        )

        await self.event_service.ingest_event(world_id, request)

    async def get_game_time(self, world_id: str, session_id: str) -> Dict[str, Any]:
        state = await self.get_state(world_id, session_id)
        return state.game_time.model_dump()

    async def advance_time(self, world_id: str, session_id: str, minutes: int) -> Dict[str, Any]:
        state = await self.get_state(world_id, session_id)
        time_manager = self._time_manager_from_state(state)
        events = time_manager.tick(minutes)
        self._update_time_state(state, time_manager)

        await self.state_manager.set_state(world_id, session_id, state)
        await self.persist_state(state)

        return {
            "time": time_manager.to_dict(),
            "events": [
                {"event_type": e.event_type, "description": e.description, "data": e.data}
                for e in events
            ],
        }

    async def advance_day(self, world_id: str, session_id: str) -> Dict[str, Any]:
        state = await self.get_state(world_id, session_id)
        state.game_time.day += 1
        await self.state_manager.set_state(world_id, session_id, state)
        await self.persist_state(state)
        return {
            "type": "system",
            "response": f"新的一天开始了。现在是第 {state.game_time.day} 天。",
            "game_day": state.game_time.day,
        }

    async def enter_sub_location(self, world_id: str, session_id: str, sub_location_id: str) -> Dict[str, Any]:
        state = await self.get_state(world_id, session_id)
        if not state.player_location:
            return {"success": False, "error": "当前位置未知"}

        navigator = self._get_navigator_ready(world_id)
        sub_loc = navigator.get_sub_location(state.player_location, sub_location_id)
        if not sub_loc:
            return {"success": False, "error": f"子地点不存在: {sub_location_id}"}

        state.sub_location = sub_location_id
        await self.state_manager.set_state(world_id, session_id, state)
        await self.persist_state(state)

        return {
            "success": True,
            "sub_location": sub_loc.to_dict(),
            "description": sub_loc.description or f"你进入了{sub_loc.name}",
        }

    async def leave_sub_location(self, world_id: str, session_id: str) -> Dict[str, Any]:
        state = await self.get_state(world_id, session_id)
        if not state.sub_location:
            return {"success": False, "error": "当前不在子地点"}

        state.sub_location = None
        await self.state_manager.set_state(world_id, session_id, state)
        await self.persist_state(state)

        return {"success": True}
