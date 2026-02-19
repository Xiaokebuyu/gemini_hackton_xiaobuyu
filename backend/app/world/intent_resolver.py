"""IntentResolver — 纯图结构意图匹配（不调 LLM）。

Direction A.2 实现。覆盖 MOVE / TALK / LEAVE / REST / EXAMINE / USE_ITEM。
无匹配 → return None（GM 全权处理）。
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# 导航关键词（中英文）
_MOVE_KEYWORDS = (
    "去", "走", "前往", "到", "回", "进入", "出发",
    "go to", "go", "move to", "travel to", "head to", "walk to",
    "navigate", "enter",
)

# 对话关键词
_TALK_KEYWORDS = (
    "跟", "和", "与", "对", "找", "问",
    "说", "聊", "谈", "交谈", "对话", "搭话",
    "talk to", "speak to", "chat with", "ask",
)

# 对话后缀（中文：跟X说话、找X聊聊）
_TALK_SUFFIXES = ("说话", "聊聊", "聊天", "谈谈", "交谈", "对话", "搭话")

# 离开关键词
_LEAVE_KEYWORDS = (
    "离开", "出去", "退出", "走出", "返回",
    "leave", "exit", "go back", "return",
)

# 休息关键词
_REST_KEYWORDS = (
    "休息", "睡觉", "扎营", "恢复", "养伤", "歇息",
    "rest", "sleep", "camp", "take a break",
)

# 检查关键词
_EXAMINE_KEYWORDS = (
    "查看", "检查", "观察", "看看", "审视", "查阅", "打量", "端详",
    "examine", "inspect", "look at", "observe", "check",
)

# 使用物品关键词
_USE_ITEM_KEYWORDS = (
    "使用", "用", "喝", "吃", "服用", "取出", "拿出", "装备",
    "use", "drink", "eat", "consume", "equip", "apply",
)


class IntentType(str, Enum):
    MOVE = "move"
    TALK = "talk"
    LEAVE = "leave"
    REST = "rest"
    EXAMINE = "examine"
    USE_ITEM = "use_item"


class ResolvedIntent(BaseModel):
    type: IntentType
    confidence: float = 1.0
    target: str  # area_id / npc_id
    target_name: str = ""
    params: Dict[str, Any] = Field(default_factory=dict)
    raw_input: str = ""


class IntentResolver:
    """纯图结构匹配。"""

    def __init__(self, world_graph: Any, session: Any) -> None:
        self.wg = world_graph
        self.session = session

    def resolve(self, player_input: str) -> Optional[ResolvedIntent]:
        """匹配玩家输入到图结构中的实体。

        匹配优先级:
        1. LEAVE（需要在子地点中）
        2. CONNECTS 边目标 name/id → MOVE
        3. CONTAINS 子地点 name/id → MOVE (sublocation)
        4. HOSTS NPC name/id → TALK
        5. EXAMINE（子地点/NPC/事件）
        6. USE_ITEM（背包物品）
        7. REST（无特定目标）

        无匹配 → return None
        """
        if not self.wg or not player_input:
            return None

        text = player_input.strip()

        # 尝试 LEAVE 匹配（优先级最高，避免与 MOVE 冲突）
        leave = self._try_leave(text)
        if leave:
            return leave

        # 尝试 MOVE 匹配
        move = self._try_move(text)
        if move:
            return move

        # 尝试 TALK 匹配
        talk = self._try_talk(text)
        if talk:
            return talk

        # 尝试 EXAMINE 匹配
        examine = self._try_examine(text)
        if examine:
            return examine

        # 尝试 USE_ITEM 匹配
        use_item = self._try_use_item(text)
        if use_item:
            return use_item

        # 尝试 REST 匹配（优先级最低）
        rest = self._try_rest(text)
        if rest:
            return rest

        return None

    def _try_move(self, text: str) -> Optional[ResolvedIntent]:
        """尝试匹配导航意图。"""
        from app.world.models import WorldEdgeType

        current = self.session.player_location
        if not current:
            return None

        text_lower = text.lower()

        # 检查是否有移动关键词
        has_move_keyword = any(kw in text_lower for kw in _MOVE_KEYWORDS)
        if not has_move_keyword:
            return None

        # 收集候选目标：CONNECTS 邻居
        area_candidates: List[Tuple[str, str]] = []  # (id, name)
        for neighbor_id, _ in self.wg.get_neighbors(current, WorldEdgeType.CONNECTS.value):
            node = self.wg.get_node(neighbor_id)
            name = node.name if node else neighbor_id
            area_candidates.append((neighbor_id, name))

        # 收集子地点候选
        sublocation_candidates: List[Tuple[str, str]] = []
        from app.world.models import WorldEdgeType as WET, WorldNodeType
        for child_id in self.wg.get_children(current, WorldNodeType.LOCATION.value):
            node = self.wg.get_node(child_id)
            name = node.name if node else child_id
            sublocation_candidates.append((child_id, name))

        # 尝试在文本中匹配（精确 ID 或 name 子串）
        match = self._find_best_match(text, area_candidates)
        if match:
            return ResolvedIntent(
                type=IntentType.MOVE,
                target=match[0],
                target_name=match[1],
                raw_input=text,
                params={"is_sublocation": False},
            )

        match = self._find_best_match(text, sublocation_candidates)
        if match:
            return ResolvedIntent(
                type=IntentType.MOVE,
                target=match[0],
                target_name=match[1],
                raw_input=text,
                params={"is_sublocation": True},
            )

        return None

    def _try_talk(self, text: str) -> Optional[ResolvedIntent]:
        """尝试匹配对话意图。"""
        from app.world.models import WorldEdgeType, WorldNodeType

        current = self.session.player_location
        if not current:
            return None

        text_lower = text.lower()

        # 检查是否有对话关键词
        has_talk_keyword = any(kw in text_lower for kw in _TALK_KEYWORDS)
        has_talk_suffix = any(text.endswith(sf) for sf in _TALK_SUFFIXES)
        if not has_talk_keyword and not has_talk_suffix:
            return None

        # 收集在场 NPC
        npc_candidates: List[Tuple[str, str]] = []

        # 从 HOSTS 边收集区域/子地点中的 NPC
        scope_nodes = [current]
        sub = self.session.sub_location
        if sub:
            scope_nodes.append(sub)
        # 也包括区域下的所有子地点
        for child_id in self.wg.get_children(current, WorldNodeType.LOCATION.value):
            scope_nodes.append(child_id)

        seen = set()
        for scope_id in scope_nodes:
            for entity_id in self.wg.get_entities_at(scope_id):
                if entity_id in seen:
                    continue
                seen.add(entity_id)
                node = self.wg.get_node(entity_id)
                if node and node.type == WorldNodeType.NPC.value:
                    npc_candidates.append((entity_id, node.name))

        match = self._find_best_match(text, npc_candidates)
        if match:
            return ResolvedIntent(
                type=IntentType.TALK,
                target=match[0],
                target_name=match[1],
                raw_input=text,
            )

        return None

    def _try_leave(self, text: str) -> Optional[ResolvedIntent]:
        """尝试匹配离开子地点意图。前提：玩家当前在子地点中。"""
        if not self.session.sub_location:
            return None

        text_lower = text.lower()
        if not any(kw in text_lower for kw in _LEAVE_KEYWORDS):
            return None

        return ResolvedIntent(
            type=IntentType.LEAVE,
            target=self.session.sub_location,
            target_name="",
            raw_input=text,
        )

    def _try_rest(self, text: str) -> Optional[ResolvedIntent]:
        """尝试匹配休息意图。无特定目标。"""
        text_lower = text.lower()
        if not any(kw in text_lower for kw in _REST_KEYWORDS):
            return None

        return ResolvedIntent(
            type=IntentType.REST,
            target="rest",
            target_name="",
            raw_input=text,
        )

    def _try_examine(self, text: str) -> Optional[ResolvedIntent]:
        """尝试匹配检查/查看意图。

        候选目标优先级：子地点 > NPC > 区域事件。
        """
        from app.world.models import WorldNodeType

        text_lower = text.lower()
        if not any(kw in text_lower for kw in _EXAMINE_KEYWORDS):
            return None

        current = self.session.player_location
        if not current:
            return None

        wg = getattr(self, "wg", None)
        if not wg:
            return None

        candidates: List[Tuple[str, str]] = []

        # 子地点候选
        for child_id in wg.get_children(current, WorldNodeType.LOCATION.value):
            node = wg.get_node(child_id)
            name = node.name if node else child_id
            candidates.append((child_id, name))

        # 在场 NPC 候选
        sub = self.session.sub_location
        scope_nodes = [current]
        if sub:
            scope_nodes.append(sub)
        seen: set = set()
        for scope_id in scope_nodes:
            for entity_id in wg.get_entities_at(scope_id):
                if entity_id in seen:
                    continue
                seen.add(entity_id)
                node = wg.get_node(entity_id)
                if node and node.type == WorldNodeType.NPC.value:
                    candidates.append((entity_id, node.name))

        # 区域事件候选（若会话支持）
        if hasattr(self.session, "get_event_summaries_from_graph"):
            try:
                events = self.session.get_event_summaries_from_graph(current) or []
                for event in events:
                    if not isinstance(event, dict):
                        continue
                    event_id = str(event.get("id", "")).strip()
                    event_name = str(event.get("name", "")).strip() or event_id
                    if event_id:
                        candidates.append((event_id, event_name))
            except Exception:
                pass

        match = self._find_best_match(text, candidates)
        if match:
            return ResolvedIntent(
                type=IntentType.EXAMINE,
                target=match[0],
                target_name=match[1],
                raw_input=text,
            )

        return None

    def _try_use_item(self, text: str) -> Optional[ResolvedIntent]:
        """尝试匹配使用物品意图。

        从玩家背包中匹配物品 name/id。
        """
        text_lower = text.lower()
        if not any(kw in text_lower for kw in _USE_ITEM_KEYWORDS):
            return None

        inventory = self._extract_inventory()

        if not inventory:
            return None

        candidates: List[Tuple[str, str]] = []
        for item in inventory:
            if isinstance(item, dict):
                item_id = item.get("item_id") or item.get("id") or ""
                item_name = item.get("item_name") or item.get("name") or item_id
            else:
                item_id = getattr(item, "item_id", "") or getattr(item, "id", "")
                item_name = getattr(item, "item_name", "") or getattr(item, "name", item_id)
            if item_id or item_name:
                # 兼容旧数据仅有 name 的情况，target 至少可回传一个稳定值
                target_id = str(item_id or item_name)
                candidates.append((target_id, str(item_name or item_id)))

        match = self._find_best_match(text, candidates)
        if match:
            return ResolvedIntent(
                type=IntentType.USE_ITEM,
                target=match[0],
                target_name=match[1],
                raw_input=text,
            )

        return None

    def _extract_inventory(self) -> List[Any]:
        """统一读取玩家背包。

        真实 SessionRuntime 走 session.player（PlayerNodeView / PlayerCharacter）。
        测试或旧调用链兼容 session.state.player_character。
        """
        # 1) 首选 SessionRuntime.player
        try:
            player = getattr(self.session, "player", None)
            if player is not None:
                if isinstance(player, dict):
                    inv = player.get("inventory", []) or []
                else:
                    inv = getattr(player, "inventory", []) or []
                if isinstance(inv, list):
                    return inv
        except Exception:
            pass

        # 2) 兼容旧路径：session.state.player_character
        try:
            state = getattr(self.session, "state", None)
            player = getattr(state, "player_character", None) if state is not None else None
            if player is not None:
                if isinstance(player, dict):
                    inv = player.get("inventory", []) or []
                else:
                    inv = getattr(player, "inventory", []) or []
                if isinstance(inv, list):
                    return inv
        except Exception:
            pass

        return []

    @staticmethod
    def _find_best_match(
        text: str,
        candidates: List[Tuple[str, str]],
    ) -> Optional[Tuple[str, str]]:
        """在 text 中查找最佳匹配的候选。

        优先精确 ID 匹配 > name 子串匹配。
        多候选时取最长 name 匹配（更精确）。
        """
        text_lower = text.lower()

        # Pass 1: 精确 ID 匹配
        for cid, name in candidates:
            if cid.lower() in text_lower:
                return (cid, name)

        # Pass 2: name 子串匹配（取最长匹配）
        best: Optional[Tuple[str, str]] = None
        best_len = 0
        for cid, name in candidates:
            if not name:
                continue
            if name.lower() in text_lower or name in text:
                if len(name) > best_len:
                    best = (cid, name)
                    best_len = len(name)
        return best
