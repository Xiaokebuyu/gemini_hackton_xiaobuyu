"""
队友信息可见性管理。

GM 层管理队友"应该知道什么/不应该知道什么"。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models.party import Party, PartyMember


class TeammateVisibilityManager:
    """管理队友的信息可见性"""

    def filter_context_for_teammate(
        self,
        teammate: PartyMember,
        full_context: Dict[str, Any],
        event: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        过滤上下文，只保留队友应该知道的信息。

        规则：
        1. 队友知道：玩家说的话（SAY 模式）
        2. 队友知道：GM 的公开叙述
        3. 队友知道：自己图谱中已有的信息
        4. 队友不知道：其他 NPC 的私下对话（除非在场）
        5. 队友不知道：玩家的系统操作细节
        6. 私密模式下非目标队友不知道玩家说了什么

        Args:
            teammate: 队友信息
            full_context: 完整上下文
            event: 可选的事件信息

        Returns:
            过滤后的上下文
        """
        filtered = {}

        is_private = full_context.get("is_private", False)
        private_target = full_context.get("private_target")
        is_target = (private_target == teammate.character_id) if private_target else False

        # 位置信息（队友总是知道当前位置）
        if "location" in full_context:
            filtered["location"] = self._filter_location_info(
                full_context["location"]
            )

        # 时间信息
        if "time" in full_context:
            filtered["time"] = full_context["time"]

        # 玩家输入
        if "player_input" in full_context:
            if is_private and not is_target:
                # 私密模式下非目标队友：不知道玩家说了什么
                pass
            elif is_private and is_target:
                # 私密模式下目标队友：知道玩家私下说了什么
                filtered["player_said_privately"] = full_context["player_input"]
                filtered["player_said"] = full_context["player_input"]
                filtered["is_private_to_me"] = True
            else:
                # 公开模式
                filtered["player_said"] = full_context["player_input"]

        # GM 叙述（公开部分）
        if "gm_response" in full_context:
            filtered["gm_narration"] = full_context["gm_response"]

        # 其他队友信息
        if "party" in full_context:
            filtered["party_members"] = self._get_visible_party_info(
                full_context["party"],
                teammate.character_id,
            )

        # 事件信息（如果有）
        if event:
            filtered["event"] = self._filter_event_for_teammate(
                event, teammate
            )

        return filtered

    def should_teammate_know(
        self,
        teammate: PartyMember,
        event: Dict[str, Any],
        party: Optional[Party] = None,
    ) -> bool:
        """
        判断队友是否应该知道这个事件。

        Args:
            teammate: 队友信息
            event: 事件信息
            party: 队伍信息

        Returns:
            是否应该知道
        """
        event_type = event.get("event_type", "")
        visibility = event.get("visibility", "public")
        participants = event.get("participants", [])
        witnesses = event.get("witnesses", [])

        # 如果队友是参与者或目击者，总是知道
        if teammate.character_id in participants:
            return True
        if teammate.character_id in witnesses:
            return True

        # 公开事件，队友知道
        if visibility == "public":
            return True

        # 队伍事件，队友知道
        if visibility == "party":
            return True

        # 对话事件，检查是否在场
        if event_type == "dialogue":
            # 如果是与队友的对话
            npc_id = event.get("npc_id")
            if npc_id == teammate.character_id:
                return True
            # 其他 NPC 对话，队友不知道
            return False

        # 私密事件，队友不知道
        if visibility == "private":
            return False

        # 默认：队友知道（公开原则）
        return True

    def _filter_location_info(
        self,
        location: Dict[str, Any],
    ) -> Dict[str, Any]:
        """过滤位置信息（队友也知道能去哪里、有哪些子地点）"""
        return {
            "location_id": location.get("location_id"),
            "location_name": location.get("location_name"),
            "atmosphere": location.get("atmosphere"),
            "npcs_present": location.get("npcs_present", []),
            "available_destinations": location.get("available_destinations", []),
            "sub_locations": location.get("available_sub_locations", location.get("sub_locations", [])),
        }

    def _get_visible_party_info(
        self,
        party: Party,
        exclude_character_id: str,
    ) -> List[Dict[str, Any]]:
        """获取对队友可见的队伍信息"""
        visible_members = []
        for member in party.get_active_members():
            if member.character_id == exclude_character_id:
                continue
            visible_members.append({
                "character_id": member.character_id,
                "name": member.name,
                "role": member.role.value,
                "current_mood": member.current_mood,
            })
        return visible_members

    def _filter_event_for_teammate(
        self,
        event: Dict[str, Any],
        teammate: PartyMember,
    ) -> Dict[str, Any]:
        """为队友过滤事件信息"""
        filtered = {
            "event_type": event.get("event_type"),
            "description": event.get("description"),
            "timestamp": event.get("timestamp"),
        }

        # 如果是目击者视角，添加观察信息
        if teammate.character_id in event.get("witnesses", []):
            filtered["perspective"] = "witness"
        elif teammate.character_id in event.get("participants", []):
            filtered["perspective"] = "participant"
        else:
            filtered["perspective"] = "heard_about"

        return filtered
