"""
队伍管理服务。

负责：
- 队伍创建/解散
- 成员加入/离开
- 位置同步
- 事件分发到队友图谱
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.models.graph import MemoryNode
from app.models.party import (
    Party,
    PartyMember,
    TeammateRole,
)
from app.services.graph_store import GraphStore


class PartyService:
    """队伍管理服务"""

    def __init__(
        self,
        graph_store: Optional[GraphStore] = None,
    ) -> None:
        self.graph_store = graph_store or GraphStore()
        # 内存缓存：session_id -> Party
        self._parties: Dict[str, Party] = {}

    # =========================================================================
    # 队伍生命周期
    # =========================================================================

    async def create_party(
        self,
        world_id: str,
        session_id: str,
        leader_id: str,
    ) -> Party:
        """创建队伍"""
        party_id = f"party_{uuid.uuid4().hex[:8]}"
        party = Party(
            party_id=party_id,
            world_id=world_id,
            session_id=session_id,
            leader_id=leader_id,
            formed_at=datetime.utcnow(),
        )
        self._parties[session_id] = party
        return party

    async def get_party(
        self,
        world_id: str,
        session_id: str,
    ) -> Optional[Party]:
        """获取队伍"""
        return self._parties.get(session_id)

    async def get_or_create_party(
        self,
        world_id: str,
        session_id: str,
        leader_id: str = "player",
    ) -> Party:
        """获取或创建队伍"""
        party = await self.get_party(world_id, session_id)
        if not party:
            party = await self.create_party(world_id, session_id, leader_id)
        return party

    async def disband_party(
        self,
        world_id: str,
        session_id: str,
    ) -> bool:
        """解散队伍"""
        if session_id in self._parties:
            del self._parties[session_id]
            return True
        return False

    # =========================================================================
    # 成员管理
    # =========================================================================

    async def add_member(
        self,
        world_id: str,
        session_id: str,
        character_id: str,
        name: str,
        role: TeammateRole = TeammateRole.SUPPORT,
        personality: str = "",
        response_tendency: float = 0.5,
    ) -> Optional[PartyMember]:
        """添加队友"""
        party = await self.get_party(world_id, session_id)
        if not party:
            return None

        if party.is_full():
            return None

        # 检查是否已存在
        if party.get_member(character_id):
            return party.get_member(character_id)

        # 确保角色图谱存在
        await self._ensure_character_graph(world_id, character_id, name)

        member = PartyMember(
            character_id=character_id,
            name=name,
            role=role,
            personality=personality,
            response_tendency=response_tendency,
            graph_ref=f"worlds/{world_id}/characters/{character_id}/",
        )
        party.members.append(member)
        return member

    async def remove_member(
        self,
        world_id: str,
        session_id: str,
        character_id: str,
    ) -> bool:
        """移除队友"""
        party = await self.get_party(world_id, session_id)
        if not party:
            return False

        for i, member in enumerate(party.members):
            if member.character_id == character_id:
                party.members.pop(i)
                return True
        return False

    async def set_member_active(
        self,
        world_id: str,
        session_id: str,
        character_id: str,
        is_active: bool,
    ) -> bool:
        """设置队友活跃状态"""
        party = await self.get_party(world_id, session_id)
        if not party:
            return False

        member = party.get_member(character_id)
        if member:
            member.is_active = is_active
            return True
        return False

    # =========================================================================
    # 位置同步
    # =========================================================================

    async def sync_locations(
        self,
        world_id: str,
        session_id: str,
        new_location: str,
        new_sub_location: Optional[str] = None,
    ) -> None:
        """同步队伍位置（玩家移动时调用）"""
        party = await self.get_party(world_id, session_id)
        if not party:
            return

        if not party.auto_follow:
            return

        party.current_location = new_location
        party.current_sub_location = new_sub_location

    # =========================================================================
    # 预定义角色加载
    # =========================================================================

    async def load_predefined_teammates(
        self,
        world_id: str,
        session_id: str,
        teammate_configs: List[Dict[str, Any]],
    ) -> List[PartyMember]:
        """从配置加载预定义队友"""
        members = []
        for config in teammate_configs:
            member = await self.add_member(
                world_id=world_id,
                session_id=session_id,
                character_id=config.get("character_id", config.get("id", "")),
                name=config.get("name", ""),
                role=TeammateRole(config.get("role", "support")),
                personality=config.get("personality", ""),
                response_tendency=config.get("response_tendency", 0.5),
            )
            if member:
                members.append(member)
        return members

    # =========================================================================
    # 辅助方法
    # =========================================================================

    async def _ensure_character_graph(
        self,
        world_id: str,
        character_id: str,
        name: str,
    ) -> None:
        """确保角色图谱存在"""
        # 检查 identity 节点是否存在
        identity_path = f"worlds/{world_id}/characters/{character_id}/nodes/identity"
        try:
            existing = await self.graph_store.get_node(
                world_id=world_id,
                graph_type="character",
                node_id="identity",
                character_id=character_id,
            )
            if existing:
                return
        except Exception:
            pass

        # 创建 identity 节点
        identity_node = MemoryNode(
            id="identity",
            type="identity",
            name=name,
            properties={
                "character_id": character_id,
                "is_teammate": True,
            },
        )
        await self.graph_store.upsert_node(
            world_id=world_id,
            graph_type="character",
            node=identity_node,
            character_id=character_id,
        )

    def get_party_member_ids(
        self,
        world_id: str,
        session_id: str,
    ) -> List[str]:
        """获取队友 ID 列表（同步方法）"""
        party = self._parties.get(session_id)
        if not party:
            return []
        return [m.character_id for m in party.get_active_members()]
