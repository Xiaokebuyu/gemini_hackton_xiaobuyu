"""GameRuntime — 全局单例，管理 WorldInstance 生命周期。"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class GameRuntime:
    """V4 游戏运行时全局单例。

    持有所有已加载的 WorldInstance，提供统一的世界访问入口。
    """

    _instance: Optional["GameRuntime"] = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __init__(self) -> None:
        from app.runtime.world_instance import WorldInstance
        self._worlds: Dict[str, WorldInstance] = {}

    @classmethod
    async def get_instance(cls) -> "GameRuntime":
        """获取或创建全局单例（双重检查锁）。"""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
                    logger.info("GameRuntime 单例已创建")
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（仅测试用）。"""
        cls._instance = None

    async def get_world(self, world_id: str) -> "WorldInstance":
        """获取 WorldInstance，首次访问时自动初始化。"""
        from app.runtime.world_instance import WorldInstance

        if world_id not in self._worlds:
            world = WorldInstance(world_id=world_id)
            await world.initialize()
            self._worlds[world_id] = world
            logger.info(f"WorldInstance '{world_id}' 已初始化")
        return self._worlds[world_id]

    def get_world_cached(self, world_id: str) -> Optional["WorldInstance"]:
        """获取已缓存的 WorldInstance（同步，无初始化）。"""
        return self._worlds.get(world_id)

    async def unload_world(self, world_id: str) -> None:
        """卸载 WorldInstance。"""
        if world_id in self._worlds:
            del self._worlds[world_id]
            logger.info(f"WorldInstance '{world_id}' 已卸载")

    @property
    def loaded_worlds(self) -> list[str]:
        """已加载的世界 ID 列表。"""
        return list(self._worlds.keys())
