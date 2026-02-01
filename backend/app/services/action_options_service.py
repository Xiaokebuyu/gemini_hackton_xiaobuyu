"""
选项生成服务。

为玩家生成当前场景可用的操作选项。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models.game_action import (
    ActionCategory,
    ActionGroup,
    AvailableActions,
    GameAction,
)
from app.models.party import Party


class ActionOptionsService:
    """选项生成服务"""

    async def get_available_actions(
        self,
        location: Dict[str, Any],
        party: Optional[Party] = None,
        state: str = "exploring",
        active_npc: Optional[str] = None,
    ) -> AvailableActions:
        """
        根据当前上下文生成所有可用操作。

        Args:
            location: 当前位置信息
            party: 队伍信息（可选）
            state: 当前状态（exploring/in_dialogue/combat）
            active_npc: 当前对话 NPC（如有）

        Returns:
            AvailableActions: 可用操作集合
        """
        groups = []
        quick_actions = []
        hotkey_counter = 1

        # 1. 导航选项
        movement_actions = self._build_navigation_actions(location, hotkey_counter)
        if movement_actions:
            hotkey_counter += len(movement_actions)
            groups.append(ActionGroup(
                category=ActionCategory.MOVEMENT,
                display_name="导航",
                actions=movement_actions,
            ))

        # 2. NPC 交互选项
        interaction_actions = self._build_npc_actions(
            location, party, active_npc, hotkey_counter
        )
        if interaction_actions:
            hotkey_counter += len(interaction_actions)
            groups.append(ActionGroup(
                category=ActionCategory.INTERACTION,
                display_name="交互",
                actions=interaction_actions,
            ))

        # 3. 观察选项
        observation_actions = self._build_observation_actions(location, hotkey_counter)
        if observation_actions:
            hotkey_counter += len(observation_actions)
            groups.append(ActionGroup(
                category=ActionCategory.OBSERVATION,
                display_name="观察",
                actions=observation_actions,
            ))

        # 4. 队伍选项
        if party:
            party_actions = self._build_party_actions(party, hotkey_counter)
            if party_actions:
                hotkey_counter += len(party_actions)
                groups.append(ActionGroup(
                    category=ActionCategory.PARTY,
                    display_name="队伍",
                    actions=party_actions,
                ))

        # 5. 如果在对话中，添加结束对话选项
        if state == "in_dialogue" and active_npc:
            quick_actions.append(GameAction(
                action_id="end_dialogue",
                category=ActionCategory.INTERACTION,
                display_name="结束对话",
                description=f"与 {active_npc} 告别",
                hotkey="0",
                priority=100,
            ))

        # 构建上下文描述
        location_name = location.get("location_name", "未知地点")
        context_description = f"你在 {location_name}"
        if state == "in_dialogue" and active_npc:
            context_description = f"你正在与 {active_npc} 交谈"

        return AvailableActions(
            context_description=context_description,
            groups=groups,
            quick_actions=quick_actions,
        )

    def _build_navigation_actions(
        self,
        location: Dict[str, Any],
        start_hotkey: int,
    ) -> List[GameAction]:
        """构建导航选项"""
        actions = []
        hotkey = start_hotkey

        # 主地点导航
        for dest in location.get("available_destinations", []):
            if isinstance(dest, dict):
                dest_id = dest.get("id", dest.get("name", "unknown"))
                dest_name = dest.get("name", dest.get("id", "未知"))
                description = dest.get("description", "")
            else:
                dest_id = str(dest)
                dest_name = str(dest)
                description = ""

            actions.append(GameAction(
                action_id=f"go_{dest_id}",
                category=ActionCategory.MOVEMENT,
                display_name=f"前往 {dest_name}",
                description=description,
                hotkey=str(hotkey) if hotkey <= 9 else None,
                parameters={"destination": dest_id},
            ))
            hotkey += 1

        # 子地点进入
        for sub_loc in location.get("sub_locations", []):
            if isinstance(sub_loc, dict):
                sub_id = sub_loc.get("id", sub_loc.get("name", "unknown"))
                sub_name = sub_loc.get("name", sub_loc.get("id", "未知"))
                description = sub_loc.get("description", "")
            else:
                sub_id = str(sub_loc)
                sub_name = str(sub_loc)
                description = ""

            actions.append(GameAction(
                action_id=f"enter_{sub_id}",
                category=ActionCategory.MOVEMENT,
                display_name=f"进入 {sub_name}",
                description=description,
                hotkey=str(hotkey) if hotkey <= 9 else None,
                parameters={"sub_location_id": sub_id},
            ))
            hotkey += 1

        # 如果在子地点，添加离开选项
        if location.get("sub_location"):
            actions.append(GameAction(
                action_id="leave_sub_location",
                category=ActionCategory.MOVEMENT,
                display_name="离开当前区域",
                description=f"返回 {location.get('location_name', '主区域')}",
                hotkey=str(hotkey) if hotkey <= 9 else None,
            ))

        return actions

    def _build_npc_actions(
        self,
        location: Dict[str, Any],
        party: Optional[Party],
        active_npc: Optional[str],
        start_hotkey: int,
    ) -> List[GameAction]:
        """构建 NPC 交互选项"""
        actions = []
        hotkey = start_hotkey

        # 场景中的 NPC
        for npc_id in location.get("npcs_present", []):
            # 如果正在与该 NPC 对话，跳过
            if npc_id == active_npc:
                continue

            actions.append(GameAction(
                action_id=f"talk_{npc_id}",
                category=ActionCategory.INTERACTION,
                display_name=f"与 {npc_id} 交谈",
                description="开始对话",
                hotkey=str(hotkey) if hotkey <= 9 else None,
                parameters={"npc_id": npc_id},
            ))
            hotkey += 1

        # 队友交互
        if party:
            for member in party.get_active_members():
                actions.append(GameAction(
                    action_id=f"talk_teammate_{member.character_id}",
                    category=ActionCategory.INTERACTION,
                    display_name=f"与 {member.name} 交谈",
                    description=f"与队友深入交流（{member.role.value}）",
                    hotkey=str(hotkey) if hotkey <= 9 else None,
                    parameters={"teammate_id": member.character_id},
                    icon="party",
                ))
                hotkey += 1

        return actions

    def _build_observation_actions(
        self,
        location: Dict[str, Any],
        start_hotkey: int,
    ) -> List[GameAction]:
        """构建观察选项"""
        actions = []
        hotkey = start_hotkey

        # 通用观察
        actions.append(GameAction(
            action_id="look_around",
            category=ActionCategory.OBSERVATION,
            display_name="观察周围",
            description="仔细观察当前环境",
            hotkey=str(hotkey) if hotkey <= 9 else None,
        ))
        hotkey += 1

        # 如果有特定可观察物
        for obj in location.get("observable_objects", []):
            if isinstance(obj, dict):
                obj_id = obj.get("id", obj.get("name", "unknown"))
                obj_name = obj.get("name", obj.get("id", "未知"))
            else:
                obj_id = str(obj)
                obj_name = str(obj)

            actions.append(GameAction(
                action_id=f"examine_{obj_id}",
                category=ActionCategory.OBSERVATION,
                display_name=f"查看 {obj_name}",
                description="仔细检查",
                hotkey=str(hotkey) if hotkey <= 9 else None,
                parameters={"object_id": obj_id},
            ))
            hotkey += 1

        return actions

    def _build_party_actions(
        self,
        party: Party,
        start_hotkey: int,
    ) -> List[GameAction]:
        """构建队伍选项"""
        actions = []

        # 查看队伍状态
        actions.append(GameAction(
            action_id="party_status",
            category=ActionCategory.PARTY,
            display_name="查看队伍",
            description="查看队友状态",
        ))

        return actions

    def format_actions_for_display(
        self,
        available_actions: AvailableActions,
    ) -> str:
        """格式化操作列表用于文本显示"""
        lines = [f"【{available_actions.context_description}】", ""]

        # 快速操作
        for action in available_actions.quick_actions:
            hotkey_str = f"[{action.hotkey}] " if action.hotkey else ""
            lines.append(f"  {hotkey_str}{action.display_name}")

        # 分组操作
        for group in available_actions.groups:
            if not group.actions:
                continue
            lines.append(f"\n{group.display_name}:")
            for action in group.actions:
                hotkey_str = f"[{action.hotkey}] " if action.hotkey else "    "
                enabled_str = "" if action.enabled else " (不可用)"
                lines.append(f"  {hotkey_str}{action.display_name}{enabled_str}")
                if action.description:
                    lines.append(f"      {action.description}")

        return "\n".join(lines)
