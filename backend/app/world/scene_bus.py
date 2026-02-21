"""SceneBus — 回合级场景总线容器。

作用域 = area_id + sub_location 组合。
瞬时层：persist 前 clear()，崩溃丢失当轮数据是可接受的。

Direction A.1 实现。
"""

from __future__ import annotations

import logging
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class BusEntryType(str, Enum):
    SPEECH = "speech"
    ACTION = "action"
    ENGINE_RESULT = "engine_result"
    NARRATIVE = "narrative"
    REACTION = "reaction"
    SYSTEM = "system"


class BusEntry(BaseModel):
    """总线条目。"""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    actor: str  # "player" | npc_id | teammate_id | "engine" | "gm"
    actor_name: str = ""
    type: BusEntryType
    content: str  # 人类可读
    data: Dict[str, Any] = Field(default_factory=dict)
    round: int = 0
    game_time: str = ""
    responds_to: Optional[str] = None
    topics: List[str] = Field(default_factory=list)
    visibility: str = "public"  # "public" | "private:{target_id}"


class SceneBus:
    """回合级总线容器 — 作用域 = area_id + sub_location 组合。"""

    def __init__(
        self,
        area_id: str,
        sub_location: Optional[str] = None,
        round_number: int = 0,
        permanent_members: Optional[Set[str]] = None,
    ) -> None:
        self.area_id = area_id
        self.sub_location = sub_location
        self.round_number = round_number
        self._entries: List[BusEntry] = []
        # Phase 4a: 成员追踪
        self.permanent_members: Set[str] = set(permanent_members or [])
        self.active_members: Set[str] = set()

    @property
    def entries(self) -> List[BusEntry]:
        return list(self._entries)

    # ── 成员管理 (Phase 4a) ──

    def contact(self, npc_id: str) -> None:
        """玩家开始与 NPC 交互 → NPC 加入总线。"""
        self.active_members.add(npc_id)

    def end_contact(self, npc_id: str) -> None:
        """玩家结束与 NPC 交互 → NPC 离开总线。"""
        self.active_members.discard(npc_id)

    def is_member(self, entity_id: str) -> bool:
        """检查实体是否为总线成员（常驻或临时）。"""
        return entity_id in self.permanent_members or entity_id in self.active_members

    def get_members(self) -> Set[str]:
        """返回所有成员（常驻 + 临时）。"""
        return self.permanent_members | self.active_members

    def reset_scene(self, new_area_id: str) -> None:
        """区域切换 → 清临时成员 + 条目，保留常驻成员。"""
        self.area_id = new_area_id
        self.sub_location = None
        self.active_members.clear()
        self._entries.clear()

    # ── 消息发布 ──

    def publish(
        self,
        entry: BusEntry,
        event_queue: Optional[Any] = None,
    ) -> None:
        """追加条目 + 可选 SSE 推送。"""
        if not entry.round:
            entry.round = self.round_number
        self._entries.append(entry)
        if event_queue is not None:
            import asyncio
            try:
                event_queue.put_nowait({
                    "type": "bus_entry",
                    "entry": entry.model_dump(mode="json"),
                })
            except (asyncio.QueueFull, Exception):
                pass  # 非阻塞，丢失可接受

    def get_entries(
        self,
        actor: Optional[str] = None,
        entry_type: Optional[BusEntryType] = None,
        visibility: Optional[str] = None,
    ) -> List[BusEntry]:
        """过滤查询。"""
        result = self._entries
        if actor is not None:
            result = [e for e in result if e.actor == actor]
        if entry_type is not None:
            result = [e for e in result if e.type == entry_type]
        if visibility is not None:
            result = [e for e in result if e.visibility == visibility]
        return result

    _SYSTEM_ACTORS = frozenset({"player", "gm", "engine"})

    def get_visible_entries(self, viewer_id: Optional[str] = None) -> List[BusEntry]:
        """按 visibility 过滤，返回 viewer 有权看到的条目。

        public 条目对所有人可见。
        private:{target_id} 条目仅对 actor 和 target_id 可见。
        非成员（且非系统 actor）返回空列表。
        """
        has_members = bool(self.permanent_members or self.active_members)
        if has_members and viewer_id and viewer_id not in self._SYSTEM_ACTORS and not self.is_member(viewer_id):
            return []
        result: List[BusEntry] = []
        for entry in self._entries:
            if entry.visibility == "public":
                result.append(entry)
            elif viewer_id and entry.visibility.startswith("private:"):
                target = entry.visibility.split(":", 1)[1]
                if viewer_id == target or viewer_id == entry.actor:
                    result.append(entry)
        return result

    def get_round_summary(
        self,
        viewer_id: Optional[str] = None,
        max_length: int = 2000,
        exclude_actors: Optional[set] = None,
    ) -> str:
        """生成本轮摘要文本（respect visibility）。"""
        entries = (
            self.get_visible_entries(viewer_id) if viewer_id
            else [e for e in self._entries if e.visibility == "public"]
        )
        if exclude_actors:
            entries = [e for e in entries if e.actor not in exclude_actors]
        if not entries:
            return ""
        lines: List[str] = []
        for e in entries:
            actor_label = e.actor_name or e.actor
            prefix = f"[{e.type.value}] {actor_label}"
            lines.append(f"{prefix}: {e.content}")
        summary = "\n".join(lines)
        if len(summary) > max_length:
            summary = summary[:max_length] + "\n..."
        return summary

    def clear(self) -> None:
        """回合结束清空。"""
        self._entries.clear()

    def to_serializable(self) -> Dict[str, Any]:
        """快照兼容。"""
        return {
            "area_id": self.area_id,
            "sub_location": self.sub_location,
            "round_number": self.round_number,
            "entries": [e.model_dump(mode="json") for e in self._entries],
            "permanent_members": sorted(self.permanent_members),
            "active_members": sorted(self.active_members),
        }

    @classmethod
    def from_serializable(cls, data: Dict[str, Any]) -> "SceneBus":
        """从快照恢复。"""
        bus = cls(
            area_id=data.get("area_id", ""),
            sub_location=data.get("sub_location"),
            round_number=data.get("round_number", 0),
            permanent_members=set(data.get("permanent_members", [])),
        )
        bus.active_members = set(data.get("active_members", []))
        for entry_data in data.get("entries", []):
            bus._entries.append(BusEntry(**entry_data))
        return bus
