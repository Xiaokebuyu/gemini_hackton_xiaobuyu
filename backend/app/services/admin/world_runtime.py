"""
Admin world runtime service (navigation/time/state).
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

from app.models.state_delta import GameState, GameTimeState
from app.services.admin.state_manager import StateManager
from app.services.admin.event_service import AdminEventService
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
    ) -> None:
        self.state_manager = state_manager
        self.session_store = session_store or GameSessionStore()
        self.narrative_service = narrative_service or NarrativeService(self.session_store)
        self.event_service = event_service or AdminEventService()

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
        await self.narrative_service.load_narrative_data(world_id, force_reload=True)
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

        # 优先从当前章节的 available_areas 确定起始位置
        if not current_location:
            try:
                progress = await self.narrative_service.get_progress(world_id, session_id)
                chapter_id = getattr(progress, "current_chapter", None)
                if chapter_id:
                    chapter_info = self.narrative_service.get_chapter_info(world_id, chapter_id)
                    if chapter_info:
                        areas = chapter_info.get("available_maps") or []
                        for area in areas:
                            if area in navigator.maps:
                                current_location = area
                                break
            except Exception:
                pass  # 回退到原有逻辑

        if not current_location:
            for area_id, area in navigator.maps.items():
                if area.danger_level == "low":
                    current_location = area_id
                    break
            if not current_location:
                current_location = list(navigator.maps.keys())[0]

        state = await self.session_store.create_session(world_id, session_id, participants)

        progress = await self.narrative_service.get_progress(world_id, state.session_id)

        # 选择起始子地点（取第一个，area 无子地点时保持 None）
        starting_sub = None
        sub_locations = navigator.get_sub_locations(current_location)
        if sub_locations:
            starting_sub = sub_locations[0].id

        admin_state = GameState(
            session_id=state.session_id,
            world_id=world_id,
            player_location=current_location,
            area_id=current_location,
            chapter_id=progress.current_chapter,
            sub_location=starting_sub,
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
        await self.narrative_service.save_progress(world_id, state.session_id, progress)
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

    async def enter_sub_location(self, world_id: str, session_id: str, sub_location_id: str) -> Dict[str, Any]:
        state = await self.get_state(world_id, session_id)
        if not state.player_location:
            return {"success": False, "error": "当前位置未知"}

        navigator = self._get_navigator_ready(world_id)
        sub_loc = navigator.get_sub_location(state.player_location, sub_location_id)
        if not sub_loc:
            # 列出可用子地点帮助 LLM 自我纠正
            available = navigator.get_sub_locations(state.player_location)
            available_text = ", ".join(
                f"{s.name}({s.id})" for s in available
            ) if available else "无"
            return {
                "success": False,
                "error": f"子地点不存在: {sub_location_id}",
                "available_sub_locations": [
                    {"id": s.id, "name": s.name} for s in available
                ],
                "hint": f"可用子地点: {available_text}",
            }

        # 营业时间检查：商店类子地点在非营业时间不可进入
        from app.services.area_navigator import InteractionType
        if hasattr(sub_loc, "interaction_type") and sub_loc.interaction_type == InteractionType.SHOP:
            time_manager = self._time_manager_from_state(state)
            if not time_manager.is_shop_open():
                hour = state.game_time.hour if state.game_time else 0
                return {
                    "success": False,
                    "error": f"{sub_loc.name}已打烊，营业时间: 08:00-20:00（当前: {hour:02d}:00）",
                    "time_blocked": True,
                }

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
