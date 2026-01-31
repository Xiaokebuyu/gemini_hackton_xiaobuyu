"""
Narrative Service - 主线叙事管理服务

管理 主线 -> 章节 -> 地图 -> 地点 四层结构。
进度存储在 GameSessionState.metadata.narrative
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.models.narrative import (
    Chapter,
    ChapterObjective,
    Mainline,
    NarrativeData,
    NarrativeProgress,
)
from app.services.game_session_store import GameSessionStore


class NarrativeService:
    """
    主线叙事管理服务

    职责：
    - 加载主线/章节数据
    - 管理叙事进度
    - 检查地图解锁状态
    - 处理章节完成和推进
    """

    def __init__(self, session_store: Optional[GameSessionStore] = None):
        """
        初始化叙事服务

        Args:
            session_store: 会话存储服务
        """
        self._session_store = session_store or GameSessionStore()
        self._mainlines: Dict[str, Mainline] = {}
        self._chapters: Dict[str, Chapter] = {}
        self._loaded_worlds: set = set()

    async def load_narrative_data(self, world_id: str) -> None:
        """
        加载主线数据

        从 data/{world_id}/structured/mainlines.json 加载

        Args:
            world_id: 世界ID
        """
        if world_id in self._loaded_worlds:
            return

        mainlines_path = Path(f"data/{world_id}/structured/mainlines.json")
        if not mainlines_path.exists():
            # 没有主线数据，使用默认值
            self._create_default_narrative(world_id)
            self._loaded_worlds.add(world_id)
            return

        with open(mainlines_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 解析主线
        for ml_data in data.get("mainlines", []):
            mainline = Mainline(
                id=ml_data["id"],
                name=ml_data["name"],
                description=ml_data.get("description", ""),
                chapters=ml_data.get("chapters", []),
            )
            self._mainlines[mainline.id] = mainline

        # 解析章节
        for ch_data in data.get("chapters", []):
            objectives = [
                ChapterObjective(
                    id=obj["id"],
                    description=obj["description"],
                )
                for obj in ch_data.get("objectives", [])
            ]

            chapter = Chapter(
                id=ch_data["id"],
                mainline_id=ch_data["mainline_id"],
                name=ch_data["name"],
                description=ch_data.get("description", ""),
                objectives=objectives,
                available_maps=ch_data.get("available_maps", []),
                trigger_conditions=ch_data.get("trigger_conditions", {}),
                completion_conditions=ch_data.get("completion_conditions", {}),
            )
            self._chapters[chapter.id] = chapter

        self._loaded_worlds.add(world_id)

    def _create_default_narrative(self, world_id: str) -> None:
        """创建默认叙事（当没有 mainlines.json 时）"""
        # 默认主线
        default_mainline = Mainline(
            id="default",
            name="冒险之旅",
            description="默认主线剧情",
            chapters=["ch_intro"],
        )
        self._mainlines["default"] = default_mainline

        # 默认章节（解锁所有地图）
        default_chapter = Chapter(
            id="ch_intro",
            mainline_id="default",
            name="序章",
            description="冒险的开始",
            objectives=[],
            available_maps=["*"],  # * 表示解锁所有地图
            trigger_conditions={},
            completion_conditions={},
        )
        self._chapters["ch_intro"] = default_chapter

    async def get_progress(
        self,
        world_id: str,
        session_id: str,
    ) -> NarrativeProgress:
        """
        获取当前进度

        Args:
            world_id: 世界ID
            session_id: 会话ID

        Returns:
            叙事进度
        """
        # 确保数据已加载
        await self.load_narrative_data(world_id)

        session = await self._session_store.get_session(world_id, session_id)
        if not session:
            # 返回默认进度
            return self._get_default_progress()

        narrative_data = session.metadata.get("narrative", {})

        if not narrative_data:
            # 初始化进度
            return self._get_default_progress()

        return NarrativeProgress.from_dict(narrative_data)

    def _get_default_progress(self) -> NarrativeProgress:
        """获取默认进度"""
        # 获取第一个主线和章节
        first_mainline = "default"
        first_chapter = "ch_intro"

        if self._mainlines:
            first_mainline = list(self._mainlines.keys())[0]
            mainline = self._mainlines[first_mainline]
            if mainline.chapters:
                first_chapter = mainline.chapters[0]

        return NarrativeProgress(
            current_mainline=first_mainline,
            current_chapter=first_chapter,
            chapter_started_at=datetime.now(),
        )

    async def save_progress(
        self,
        world_id: str,
        session_id: str,
        progress: NarrativeProgress,
    ) -> None:
        """
        保存进度到 session.metadata.narrative

        Args:
            world_id: 世界ID
            session_id: 会话ID
            progress: 叙事进度
        """
        await self._session_store.update_session(
            world_id,
            session_id,
            {"metadata.narrative": progress.to_dict()},
        )

    async def get_available_maps(
        self,
        world_id: str,
        session_id: str,
    ) -> List[str]:
        """
        获取当前章节可用的地图

        Args:
            world_id: 世界ID
            session_id: 会话ID

        Returns:
            可用地图ID列表，["*"] 表示所有地图都可用
        """
        progress = await self.get_progress(world_id, session_id)
        chapter = self._chapters.get(progress.current_chapter)

        if not chapter:
            return ["*"]  # 默认解锁所有

        return chapter.available_maps

    async def is_map_available(
        self,
        world_id: str,
        session_id: str,
        map_id: str,
    ) -> bool:
        """
        检查地图是否解锁

        Args:
            world_id: 世界ID
            session_id: 会话ID
            map_id: 地图ID

        Returns:
            是否解锁
        """
        available_maps = await self.get_available_maps(world_id, session_id)

        # "*" 表示所有地图都可用
        if "*" in available_maps:
            return True

        return map_id in available_maps

    async def trigger_event(
        self,
        world_id: str,
        session_id: str,
        event_id: str,
    ) -> Dict[str, Any]:
        """
        触发叙事事件，检查章节完成

        Args:
            world_id: 世界ID
            session_id: 会话ID
            event_id: 事件ID

        Returns:
            {
                "event_recorded": bool,
                "chapter_completed": bool,
                "new_chapter": str | None,
                "new_maps_unlocked": List[str]
            }
        """
        progress = await self.get_progress(world_id, session_id)

        # 记录事件
        event_recorded = False
        if event_id not in progress.events_triggered:
            progress.events_triggered.append(event_id)
            event_recorded = True

        # 检查章节完成条件
        chapter = self._chapters.get(progress.current_chapter)
        if chapter and self._check_completion(chapter, progress):
            result = await self._advance_chapter(world_id, session_id, progress)
            return {
                "event_recorded": event_recorded,
                "chapter_completed": True,
                "new_chapter": result.get("new_chapter"),
                "new_maps_unlocked": result.get("new_maps_unlocked", []),
            }

        # 保存进度
        await self.save_progress(world_id, session_id, progress)

        return {
            "event_recorded": event_recorded,
            "chapter_completed": False,
            "new_chapter": None,
            "new_maps_unlocked": [],
        }

    def _check_completion(
        self,
        chapter: Chapter,
        progress: NarrativeProgress,
    ) -> bool:
        """检查章节完成条件"""
        required_events = chapter.completion_conditions.get("events_required", [])
        if not required_events:
            return False  # 没有完成条件

        return all(ev in progress.events_triggered for ev in required_events)

    async def _advance_chapter(
        self,
        world_id: str,
        session_id: str,
        progress: NarrativeProgress,
    ) -> Dict[str, Any]:
        """
        推进到下一章节

        Returns:
            {
                "new_chapter": str | None,
                "new_maps_unlocked": List[str]
            }
        """
        mainline = self._mainlines.get(progress.current_mainline)
        if not mainline:
            return {"new_chapter": None, "new_maps_unlocked": []}

        try:
            current_idx = mainline.chapters.index(progress.current_chapter)
        except ValueError:
            return {"new_chapter": None, "new_maps_unlocked": []}

        if current_idx + 1 >= len(mainline.chapters):
            return {"new_chapter": None, "new_maps_unlocked": []}  # 已完成所有章节

        # 记录完成的章节
        progress.chapters_completed.append(progress.current_chapter)

        # 推进到下一章节
        next_chapter_id = mainline.chapters[current_idx + 1]
        progress.current_chapter = next_chapter_id
        progress.events_triggered = []  # 重置事件
        progress.chapter_started_at = datetime.now()

        # 获取新章节解锁的地图
        next_chapter = self._chapters.get(next_chapter_id)
        new_maps = next_chapter.available_maps if next_chapter else []

        # 保存进度
        await self.save_progress(world_id, session_id, progress)

        return {
            "new_chapter": next_chapter_id,
            "new_maps_unlocked": new_maps,
        }

    async def complete_objective(
        self,
        world_id: str,
        session_id: str,
        objective_id: str,
    ) -> bool:
        """
        完成章节目标

        Args:
            world_id: 世界ID
            session_id: 会话ID
            objective_id: 目标ID

        Returns:
            是否成功标记
        """
        progress = await self.get_progress(world_id, session_id)

        if objective_id not in progress.objectives_completed:
            progress.objectives_completed.append(objective_id)
            await self.save_progress(world_id, session_id, progress)
            return True

        return False

    def get_chapter_info(self, chapter_id: str) -> Optional[Dict[str, Any]]:
        """获取章节信息"""
        chapter = self._chapters.get(chapter_id)
        if not chapter:
            return None

        return {
            "id": chapter.id,
            "name": chapter.name,
            "description": chapter.description,
            "objectives": [
                {"id": obj.id, "description": obj.description}
                for obj in chapter.objectives
            ],
            "available_maps": chapter.available_maps,
        }

    def get_mainline_info(self, mainline_id: str) -> Optional[Dict[str, Any]]:
        """获取主线信息"""
        mainline = self._mainlines.get(mainline_id)
        if not mainline:
            return None

        return {
            "id": mainline.id,
            "name": mainline.name,
            "description": mainline.description,
            "chapters": mainline.chapters,
        }
