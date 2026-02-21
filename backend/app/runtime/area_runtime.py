"""AreaRuntime — 区域生命周期管理。

C8: 事件逻辑已迁移到 BehaviorEngine/WorldGraph。
保留：区域上下文、子地点、访问记录、记忆图谱、NPC上下文。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from google.cloud import firestore

from app.config import settings
from app.models.narrative import (
    Condition,
    ConditionGroup,
    ConditionType,
)
from app.runtime.models.area_state import (
    AreaDefinition,
    AreaState,
    VisitSummary,
)

logger = logging.getLogger(__name__)


def _clean_npc_for_context(char_data: Dict[str, Any]) -> Dict[str, Any]:
    """从 Firestore 原始角色文档中提取 LLM 需要的字段，扁平化 + 去 null。"""
    profile = char_data.get("profile", {})
    if not isinstance(profile, dict):
        profile = {}
    metadata = profile.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    tier = metadata.get("tier", "secondary")

    npc: Dict[str, Any] = {
        "id": char_data.get("id", ""),
        "name": profile.get("name") or char_data.get("name", ""),
        "tier": tier,
    }

    # 按设计文档选取字段，跳过 null
    for field in ("occupation", "age", "personality", "speech_pattern"):
        val = profile.get(field)
        if val is not None:
            npc[field] = val

    # 仅 main 层级注入 example_dialogue
    if tier == "main":
        val = profile.get("example_dialogue")
        if val is not None:
            npc["example_dialogue"] = val

    # metadata 中的非 null 字段
    for field in ("appearance", "backstory", "importance"):
        val = metadata.get(field)
        if val is not None:
            npc[field] = val

    # relationships — 仅非空时注入
    rels = metadata.get("relationships")
    if rels:
        npc["relationships"] = rels

    # tags / aliases — 仅非空列表时注入
    for field in ("tags", "aliases"):
        val = metadata.get(field)
        if val:
            npc[field] = val

    return npc


class AreaRuntime:
    """区域运行时 — 管理区域上下文、子地点、访问记录。

    C8: 事件状态机已迁移到 BehaviorEngine。保留 _summarize_* 工具方法。
    """

    def __init__(self, area_id: str, definition: AreaDefinition) -> None:
        self.area_id = area_id
        self.definition = definition
        self.state: AreaState = AreaState(area_id=area_id)
        self.events: List[AreaEvent] = []
        self.area_graph: Optional[Any] = None        # 区域级动态记忆图谱 (MemoryGraph)
        self.npc_contexts: Dict[str, Any] = {}       # NPC 上下文窗口快照
        self.visit_summaries: List[VisitSummary] = []
        self.current_visit_log: List[str] = []
        self._world_id: Optional[str] = None
        self._session_id: Optional[str] = None
        self._db: Optional[firestore.Client] = None
        self._graph_store: Optional[Any] = None      # GraphStore 引用
        self._chapter_id: Optional[str] = None        # 章节 ID（GraphScope 需要）

    def _get_db(self) -> firestore.Client:
        """获取或创建 Firestore 客户端。"""
        if self._db is None:
            self._db = firestore.Client(database=settings.firestore_database)
        return self._db

    def _area_ref(self, world_id: str) -> firestore.DocumentReference:
        """获取区域根文档引用。"""
        return (
            self._get_db()
            .collection("worlds")
            .document(world_id)
            .collection("areas")
            .document(self.area_id)
        )

    # =========================================================================
    # 生命周期 — load / unload
    # =========================================================================

    async def load(
        self,
        world_id: str,
        session_id: str,
        *,
        chapter_id: Optional[str] = None,
        graph_store: Optional[Any] = None,
    ) -> None:
        """从 Firestore 加载区域状态（5 路并行）。

        并行加载：state + events + visits + area_graph + npc_contexts，
        完成后更新访问计数。
        """
        self._world_id = world_id
        self._session_id = session_id
        self._chapter_id = chapter_id
        self._graph_store = graph_store
        area_ref = self._area_ref(world_id)

        # 并行加载 4 个数据源（C8: 事件由 WorldGraph 管理，不再从 Firestore 加载）
        results = await asyncio.gather(
            self._load_state(area_ref),
            self._load_visits(area_ref, session_id),
            self._load_area_graph(world_id, chapter_id, graph_store),
            self._load_npc_contexts(area_ref),
            return_exceptions=True,
        )
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("AreaRuntime '%s' 加载任务 %d 异常: %s", self.area_id, i, result)

        # 更新访问计数
        self.state.visit_count += 1
        self.state.updated_at = datetime.utcnow()

        # 重置本次访问日志
        self.current_visit_log = []

        logger.info(
            "AreaRuntime '%s' 已加载: %d 历史访问, graph=%s, npc_ctx=%d",
            self.area_id, len(self.visit_summaries),
            self.area_graph is not None, len(self.npc_contexts),
        )

    async def _load_state(self, area_ref: firestore.DocumentReference) -> None:
        """加载区域状态文档。"""
        state_doc = area_ref.collection("state").document("current").get()
        if state_doc.exists:
            data = state_doc.to_dict() or {}
            data.setdefault("area_id", self.area_id)
            self.state = AreaState(**data)
        else:
            self.state = AreaState(area_id=self.area_id)

    async def _load_visits(
        self, area_ref: firestore.DocumentReference, session_id: str,
    ) -> None:
        """加载访问摘要（最近 10 条）。"""
        self.visit_summaries = []
        visits_ref = area_ref.collection("visits")
        for doc in visits_ref.order_by(
            "entered_at", direction=firestore.Query.DESCENDING
        ).limit(10).stream():
            data = doc.to_dict()
            if not data:
                continue
            data.setdefault("visit_id", doc.id)
            data.setdefault("area_id", self.area_id)
            data.setdefault("session_id", session_id)
            try:
                self.visit_summaries.append(VisitSummary(**data))
            except Exception as e:
                logger.warning("跳过无效访问摘要 %s: %s", doc.id, e)

    async def _load_area_graph(
        self, world_id: str, chapter_id: Optional[str], graph_store: Optional[Any],
    ) -> None:
        """加载区域级动态记忆图谱。"""
        if not chapter_id or not graph_store:
            return
        from app.models.graph_scope import GraphScope
        scope = GraphScope.area(chapter_id, self.area_id)
        graph_data = await graph_store.load_graph_v2(world_id, scope)
        if graph_data and (graph_data.nodes or graph_data.edges):
            from app.services.memory_graph import MemoryGraph
            self.area_graph = MemoryGraph.from_graph_data(graph_data)
            logger.debug(
                "AreaRuntime '%s' 加载 area_graph: %d nodes, %d edges",
                self.area_id,
                len(graph_data.nodes),
                len(graph_data.edges),
            )

    async def _load_npc_contexts(
        self, area_ref: firestore.DocumentReference,
    ) -> None:
        """加载 NPC 上下文窗口快照。"""
        self.npc_contexts = {}
        ctx_ref = area_ref.collection("npc_contexts")
        for doc in ctx_ref.stream():
            data = doc.to_dict()
            if data:
                self.npc_contexts[doc.id] = data
        if self.npc_contexts:
            logger.debug(
                "AreaRuntime '%s' 加载 npc_contexts: %d NPCs",
                self.area_id, len(self.npc_contexts),
            )

    async def persist_state(self) -> None:
        """每轮增量持久化：保存 state + events + area_graph + npc_contexts。"""
        world_id = self._world_id
        if not world_id:
            return
        area_ref = self._area_ref(world_id)

        # 1. 持久化区域状态
        self.state.updated_at = datetime.utcnow()
        area_ref.collection("state").document("current").set(
            self.state.model_dump(), merge=True
        )

        # 2. 持久化区域级记忆图谱（merge=True 保证不覆盖 B 阶段写入的节点）
        if self.area_graph and self._graph_store and self._chapter_id:
            from app.models.graph_scope import GraphScope
            scope = GraphScope.area(self._chapter_id, self.area_id)
            await self._graph_store.save_graph_v2(
                world_id, scope, self.area_graph, merge=True
            )

        # 3. 持久化 NPC 上下文快照
        if self.npc_contexts:
            for npc_id, snapshot in self.npc_contexts.items():
                area_ref.collection("npc_contexts").document(npc_id).set(
                    snapshot, merge=True
                )

        logger.debug("AreaRuntime '%s' 增量持久化完成", self.area_id)

    async def unload(self, session: Any) -> Optional[VisitSummary]:
        """生成访问摘要 + 持久化 + 释放资源。

        Args:
            session: SessionRuntime 实例，提供 session_id 等信息。

        Returns:
            本次访问的 VisitSummary，无操作时返回 None。
        """
        world_id = self._world_id
        session_id = self._session_id
        if not world_id:
            logger.warning("AreaRuntime '%s' 未加载就尝试 unload", self.area_id)
            return None

        area_ref = self._area_ref(world_id)

        # 1. 生成 VisitSummary
        visit_summary: Optional[VisitSummary] = None
        if self.current_visit_log:
            visit_id = str(uuid.uuid4())[:8]
            game_day = 1
            if session is not None and hasattr(session, "time") and session.time:
                game_day = getattr(session.time, "day", 1) or 1

            # C8: 从 WorldGraph 获取已完成事件（self.events 不再维护）
            triggered_events = []
            if session and hasattr(session, "world_graph") and session.world_graph:
                for eid in session.world_graph.find_events_in_scope(self.area_id):
                    node = session.world_graph.get_node(eid)
                    if node and node.state.get("status") == "completed":
                        triggered_events.append(eid)
            interacted_npcs = list(self.npc_contexts.keys())

            visit_summary = VisitSummary(
                visit_id=visit_id,
                area_id=self.area_id,
                session_id=session_id or "",
                entered_at=self.state.updated_at or datetime.utcnow(),
                left_at=datetime.utcnow(),
                actions_taken=list(self.current_visit_log),
                events_triggered=triggered_events,
                npcs_interacted=interacted_npcs,
                game_day=game_day,
            )
            # 保存访问摘要
            area_ref.collection("visits").document(visit_id).set(
                visit_summary.model_dump()
            )

        # 2+3. 持久化 state + events（委托）
        await self.persist_state()

        # 4. 清理临时数据
        self.current_visit_log = []
        self.npc_contexts = {}
        self.area_graph = None

        logger.info("AreaRuntime '%s' 已卸载并持久化", self.area_id)
        return visit_summary

    # =========================================================================
    # 上下文生成
    # =========================================================================

    def record_action(self, action: str) -> None:
        """记录本次访问的行动。"""
        self.current_visit_log.append(action)

    def get_area_context(
        self, world: Any, session: Any
    ) -> Dict[str, Any]:
        """生成 Layer 2 区域上下文。

        包含：
        - 区域基本信息（name, description, danger_level）
        - 子地点列表（已发现的）
        - 区域内 NPC
        - 区域事件状态（available + active 的事件概要）
        - 环境描述
        """
        defn = self.definition

        # 子地点列表（过滤已发现的）
        discovered = set(self.state.discovered_sub_locations)
        sub_locations = []
        for sl in defn.sub_locations:
            sub_locations.append({
                "id": sl.id,
                "name": sl.name,
                "description": sl.description if sl.id in discovered else "",
                "discovered": sl.id in discovered,
                "interaction_type": sl.interaction_type,
                "resident_npcs": sl.resident_npcs,
                "available_actions": sl.available_actions,
            })

        # 区域内 NPC — 子地点过滤 + 字段清洗
        area_npcs = []
        sub_location = getattr(session, "sub_location", None) if session else None

        if world is not None and hasattr(world, "get_characters_in_area"):
            try:
                if sub_location and hasattr(world, "get_characters_at_sublocation"):
                    raw_npcs = world.get_characters_at_sublocation(self.area_id, sub_location)
                else:
                    raw_npcs = world.get_characters_in_area(self.area_id)
                area_npcs = [_clean_npc_for_context(c) for c in raw_npcs]
            except NotImplementedError:
                for npc_id in defn.resident_npcs:
                    char = world.get_character(npc_id) if hasattr(world, "get_character") else None
                    area_npcs.append(
                        _clean_npc_for_context(char) if char else {"id": npc_id, "name": npc_id}
                    )

        # 事件概要（C8: 从 WorldGraph 获取）
        event_summaries = []
        if session and hasattr(session, "get_event_summaries_from_graph"):
            event_summaries = session.get_event_summaries_from_graph(self.area_id)

        # ------ 战斗实体参考（数据包③）------
        monsters = []
        available_skills = []
        item_reference = []

        if world is not None:
            # 怪物 — 按区域危险等级过滤
            raw_monsters = (
                world.get_monsters_for_danger(defn.danger_level)
                if hasattr(world, "get_monsters_for_danger")
                else []
            )
            for m in raw_monsters[:10]:
                stats = m.get("stats") or {}
                monsters.append({
                    "id": m.get("id", ""),
                    "name": m.get("name", ""),
                    "type": m.get("type", ""),
                    "challenge_rating": m.get("challenge_rating", ""),
                    "description": (m.get("description") or "")[:200],
                    "hp": stats.get("hp", 0),
                    "ac": stats.get("ac", 0),
                    "special_abilities": m.get("special_abilities", []),
                })

            # 技能 — 按玩家职业过滤
            player = getattr(session, "player", None) if session else None
            player_classes = []
            if player and hasattr(player, "character_class"):
                cls = player.character_class
                player_classes = [cls.value if hasattr(cls, "value") else str(cls)]
            raw_skills = (
                world.get_skills_for_classes(player_classes)
                if hasattr(world, "get_skills_for_classes")
                else []
            )
            for s in raw_skills[:15]:
                available_skills.append({
                    "id": s.get("id", ""),
                    "name": s.get("name", ""),
                    "source": s.get("source", ""),
                    "description": (s.get("description") or "")[:150],
                })

            # 物品 — 全量参考（精简字段）
            item_reg = getattr(world, "item_registry", {}) or {}
            for item in list(item_reg.values())[:25]:
                item_reference.append({
                    "id": item.get("id", ""),
                    "name": item.get("name", ""),
                    "type": item.get("type", ""),
                    "description": (item.get("description") or "")[:150],
                    "rarity": item.get("rarity", ""),
                })

        return {
            "area_id": self.area_id,
            "name": defn.name,
            "description": defn.description,
            "danger_level": defn.danger_level,
            "area_type": defn.area_type,
            "tags": defn.tags,
            "key_features": defn.key_features,
            "available_actions": defn.available_actions,
            "ambient_description": defn.ambient_description,
            "sub_locations": sub_locations,
            "npcs": area_npcs,
            "events": event_summaries,
            "monsters": monsters,
            "available_skills": available_skills,
            "item_reference": item_reference,
            "visit_count": self.state.visit_count,
            "connections": [
                {
                    "target": c.target_area_id,
                    "type": c.connection_type,
                    "travel_time": c.travel_time,
                    "description": c.description,
                    "requirements": c.requirements if c.requirements else None,
                }
                for c in defn.connections
            ],
        }

    def get_location_context(self, sub_id: str) -> Dict[str, Any]:
        """生成 Layer 3 子地点上下文。

        包含子地点详情、驻留 NPC、可用交互。
        """
        sub_loc = self.definition.get_sub_location(sub_id)
        if sub_loc is None:
            return {"error": f"子地点 '{sub_id}' 不存在"}

        # 标记为已发现
        if sub_id not in self.state.discovered_sub_locations:
            self.state.discovered_sub_locations.append(sub_id)

        return {
            "id": sub_loc.id,
            "name": sub_loc.name,
            "description": sub_loc.description,
            "interaction_type": sub_loc.interaction_type,
            "resident_npcs": sub_loc.resident_npcs,
            "requirements": sub_loc.requirements,
            "metadata": sub_loc.metadata,
        }

    # =========================================================================
    # 条件摘要工具方法（供 SessionRuntime / V4AgenticTools 调用）
    # =========================================================================

    @staticmethod
    def _parse_condition_group(
        raw: Any,
    ) -> Optional[ConditionGroup]:
        """将 dict 或 ConditionGroup 统一为 ConditionGroup。"""
        if isinstance(raw, ConditionGroup):
            return raw
        if isinstance(raw, dict):
            if not raw:
                return None
            try:
                return ConditionGroup(**raw)
            except Exception:
                return None
        return None

    @staticmethod
    def _summarize_completion_conditions(raw: Any) -> str:
        """将 completion_conditions 转为中文摘要，供 LLM 参考。"""
        group = AreaRuntime._parse_condition_group(raw)
        if group is None or not group.conditions:
            return ""
        return AreaRuntime._summarize_group(group)

    @staticmethod
    def _summarize_group(group: ConditionGroup) -> str:
        """递归摘要条件组。"""
        parts: List[str] = []
        for cond in group.conditions:
            if isinstance(cond, ConditionGroup):
                sub = AreaRuntime._summarize_group(cond)
                if sub:
                    parts.append(f"({sub})")
            elif isinstance(cond, Condition):
                text = AreaRuntime._summarize_single_condition(cond)
                if text:
                    parts.append(text)
            elif isinstance(cond, dict):
                try:
                    c = Condition(**cond)
                    text = AreaRuntime._summarize_single_condition(c)
                    if text:
                        parts.append(text)
                except Exception as exc:
                    logger.debug("[area_runtime] 条件解析跳过: %s", exc)
        if not parts:
            return ""
        joiner = "且" if group.operator == "and" else "或"
        return f" {joiner} ".join(parts)

    @staticmethod
    def _summarize_single_condition(cond: Condition) -> str:
        """将单个 Condition 转为中文短语。"""
        p = cond.params
        if cond.type == ConditionType.NPC_INTERACTED:
            npc = p.get("npc_id", "?")
            n = p.get("min_interactions", 1)
            return f"与 {npc} 交谈至少 {n} 次"
        if cond.type == ConditionType.LOCATION:
            sub = p.get("sub_location")
            area = p.get("area_id")
            if sub:
                return f"前往 {sub}"
            if area:
                return f"前往 {area}"
            return ""
        if cond.type == ConditionType.EVENT_TRIGGERED:
            return f"完成事件 {p.get('event_id', '?')}"
        if cond.type == ConditionType.PARTY_CONTAINS:
            return f"队伍中需有 {p.get('character_id', '?')}"
        if cond.type == ConditionType.OBJECTIVE_COMPLETED:
            return f"完成目标 {p.get('objective_id', '?')}"
        if cond.type == ConditionType.FLASH_EVALUATE:
            return p.get("description") or p.get("prompt", "")
        if cond.type == ConditionType.TIME_PASSED:
            day = p.get("min_day")
            if day:
                return f"等待至第 {day} 天"
            return ""
        if cond.type == ConditionType.EVENT_STATE:
            event_id = p.get("event_id", "?")
            key = p.get("key", "?")
            value = p.get("value", "?")
            return f"事件 {event_id} 的 {key} 需要为 {value}"
        if cond.type == ConditionType.ROUNDS_ELAPSED:
            # P11: 为玩家可见的回合类条件提供摘要
            min_r = p.get("min_rounds")
            max_r = p.get("max_rounds")
            if min_r is not None and max_r is not None and max_r != float("inf"):
                return f"经过 {min_r}~{int(max_r)} 回合"
            if min_r is not None:
                return f"经过至少 {min_r} 回合"
            return ""
        # GAME_STATE / EVENT_ROUNDS_ELAPSED 等内部机制，不暴露给 LLM
        return ""
