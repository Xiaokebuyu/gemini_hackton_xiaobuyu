"""
游戏操作选项模型。

选项式交互：系统为玩家提供"当前场景可做的操作"。
参考战斗系统的 ActionOption 模型。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ActionCategory(str, Enum):
    """操作类别"""
    MOVEMENT = "movement"           # 导航、进入子地点
    INTERACTION = "interaction"     # 与 NPC 交互
    OBSERVATION = "observation"     # 观察、查看
    COMBAT = "combat"               # 战斗相关
    PARTY = "party"                 # 队伍相关
    INVENTORY = "inventory"         # 物品相关
    SYSTEM = "system"               # 系统操作


class GameAction(BaseModel):
    """通用游戏操作选项"""

    action_id: str                              # 如 "go_forest", "talk_marcus"
    category: ActionCategory
    display_name: str                           # "前往森林", "与 Marcus 交谈"
    description: str = ""                       # 详细描述
    enabled: bool = True                        # 是否可用
    requires: Optional[str] = None              # 前置条件描述
    hotkey: Optional[str] = None                # 快捷键（如 "1", "2"）

    # 执行参数
    parameters: Dict[str, Any] = Field(default_factory=dict)

    # 显示属性
    icon: Optional[str] = None                  # 图标名称
    priority: int = 0                           # 显示优先级（越高越前）


class ActionGroup(BaseModel):
    """操作分组（用于 UI 展示）"""

    category: ActionCategory
    display_name: str                           # "导航", "交互"
    actions: List[GameAction] = Field(default_factory=list)


class AvailableActions(BaseModel):
    """当前可用的所有操作"""

    context_description: str = ""               # 当前情境描述
    groups: List[ActionGroup] = Field(default_factory=list)
    quick_actions: List[GameAction] = Field(default_factory=list)  # 快速操作（无需分组）

    def get_all_actions(self) -> List[GameAction]:
        """获取所有操作的平铺列表"""
        actions = list(self.quick_actions)
        for group in self.groups:
            actions.extend(group.actions)
        return actions

    def find_action(self, action_id: str) -> Optional[GameAction]:
        """根据 ID 查找操作"""
        for action in self.get_all_actions():
            if action.action_id == action_id:
                return action
        return None

    def find_by_hotkey(self, hotkey: str) -> Optional[GameAction]:
        """根据快捷键查找操作"""
        for action in self.get_all_actions():
            if action.hotkey == hotkey:
                return action
        return None
