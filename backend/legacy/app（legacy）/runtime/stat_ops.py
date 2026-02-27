"""StatOps — 玩家数值操作（从 SessionRuntime 提取，P7 瘦身）。"""
from __future__ import annotations

import logging
from typing import Any, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from app.runtime.session_runtime import SessionRuntime

logger = logging.getLogger(__name__)


class StatOps:
    """玩家 HP / XP / 金币 / 物品的纯内存操作，persist() 统一持久化。"""

    def __init__(self, session: 'SessionRuntime') -> None:
        self._s = session

    def heal(self, amount: int) -> Dict[str, Any]:
        """回复玩家 HP。"""
        player = self._s.player
        if not player:
            return {"success": False, "error": "player not loaded"}
        from app.world.player import stats as stats_manager
        result = stats_manager.add_hp(player, int(amount))
        self._s.mark_player_dirty()
        return {"success": True, **result}

    def damage(self, amount: int) -> Dict[str, Any]:
        """扣除玩家 HP。"""
        player = self._s.player
        if not player:
            return {"success": False, "error": "player not loaded"}
        from app.world.player import stats as stats_manager
        result = stats_manager.remove_hp(player, int(amount))
        self._s.mark_player_dirty()
        return {"success": True, **result}

    def add_xp(self, amount: int) -> Dict[str, Any]:
        """增加玩家经验值（自动处理升级）。"""
        player = self._s.player
        if not player:
            return {"success": False, "error": "player not loaded"}
        from app.world.player import stats as stats_manager
        result = stats_manager.add_xp(player, int(amount))
        self._s.mark_player_dirty()
        return {"success": True, **result}

    def add_gold(self, amount: int) -> Dict[str, Any]:
        """增加玩家金币。"""
        player = self._s.player
        if not player:
            return {"success": False, "error": "player not loaded"}
        from app.world.player import stats as stats_manager
        result = stats_manager.add_gold(player, int(amount))
        self._s.mark_player_dirty()
        return {"success": True, **result}

    def add_item(self, item_id: str, item_name: str, quantity: int = 1) -> Dict[str, Any]:
        """添加物品到玩家背包。"""
        player = self._s.player
        if not player:
            return {"success": False, "error": "player not loaded"}
        item = player.add_item(item_id, item_name, int(quantity))
        self._s.mark_player_dirty()
        return {"success": True, "item": item}

    def remove_item(self, item_id: str, quantity: int = 1) -> Dict[str, Any]:
        """从玩家背包移除物品。"""
        player = self._s.player
        if not player:
            return {"success": False, "error": "player not loaded"}
        removed = player.remove_item(item_id, int(quantity))
        if not removed:
            return {"success": False, "error": f"item not found: {item_id}"}
        self._s.mark_player_dirty()
        return {"success": True, "removed": item_id, "quantity": int(quantity)}
