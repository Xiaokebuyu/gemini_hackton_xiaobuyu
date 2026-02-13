"""AreaRuntime — 区域生命周期管理（Phase 2B 实现）。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from google.cloud import firestore

from app.config import settings
from app.models.narrative import (
    Chapter,
    ChapterTransition,
    Condition,
    ConditionGroup,
    ConditionType,
    StoryEvent,
)
from app.runtime.models.area_state import (
    AreaDefinition,
    AreaEvent,
    AreaState,
    EventUpdate,
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
    """区域运行时 — 管理区域状态和事件生命周期。

    Phase 2B 完整实现。
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

        # 并行加载 5 个数据源
        results = await asyncio.gather(
            self._load_state(area_ref),
            self._load_events(area_ref),
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
            "AreaRuntime '%s' 已加载: %d 事件, %d 历史访问, graph=%s, npc_ctx=%d",
            self.area_id, len(self.events), len(self.visit_summaries),
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

    async def _load_events(self, area_ref: firestore.DocumentReference) -> None:
        """加载事件状态集合。"""
        self.events = []
        events_ref = area_ref.collection("events")
        for doc in events_ref.stream():
            data = doc.to_dict()
            if not data:
                continue
            data.setdefault("id", doc.id)
            data.setdefault("area_id", self.area_id)
            try:
                self.events.append(AreaEvent(**data))
            except Exception as e:
                logger.warning("跳过无效事件文档 %s: %s", doc.id, e)

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

    def initialize_events_from_chapter(
        self, chapter: Chapter, chapter_id: str
    ) -> None:
        """从章节 StoryEvent 初始化区域事件（Firestore 无数据时使用）。

        仅添加尚不存在的事件。
        """
        existing_ids = {e.id for e in self.events}
        for se in chapter.events:
            if se.id in existing_ids:
                continue
            area_event = AreaEvent(
                id=se.id,
                area_id=self.area_id,
                chapter_id=chapter_id,
                name=se.name,
                description=se.description,
                importance="main" if se.is_required else "side",
                status="locked",
                trigger_conditions=se.trigger_conditions.model_dump()
                    if se.trigger_conditions.conditions else {},
                completion_conditions=se.completion_conditions.model_dump()
                    if se.completion_conditions and se.completion_conditions.conditions
                    else None,
                on_complete=se.on_complete,
                narrative_directive=se.narrative_directive,
                cooldown_rounds=se.cooldown_rounds,
                is_repeatable=se.is_repeatable,
            )
            self.events.append(area_event)

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

        # 2. 持久化事件状态
        for event in self.events:
            area_ref.collection("events").document(event.id).set(
                event.model_dump(), merge=True
            )

        # 3. 持久化区域级记忆图谱（merge=True 保证不覆盖 B 阶段写入的节点）
        if self.area_graph and self._graph_store and self._chapter_id:
            from app.models.graph_scope import GraphScope
            scope = GraphScope.area(self._chapter_id, self.area_id)
            await self._graph_store.save_graph_v2(
                world_id, scope, self.area_graph, merge=True
            )

        # 4. 持久化 NPC 上下文快照
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

            triggered_events = [
                e.id for e in self.events if e.status == "completed"
            ]
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
    # 事件状态机 — check_events
    # =========================================================================

    def check_events(self, session: Any) -> List[EventUpdate]:
        """机械条件检查 — 驱动事件状态机。

        对每个事件按状态执行转换：
        - locked → available: trigger_conditions 满足
        - active → completed: completion_conditions 满足

        Args:
            session: SessionRuntime 实例，提供条件评估所需的上下文。

        Returns:
            本次检查产生的所有状态变更列表。
        """
        updates: List[EventUpdate] = []

        for event in self.events:
            if event.status == "completed":
                continue

            if event.status == "locked":
                if not event.trigger_conditions:
                    # 无触发条件 → 直接变为 available
                    event.status = "available"
                    updates.append(EventUpdate(
                        event=event,
                        transition="locked→available",
                        details={"reason": "no_trigger_conditions"},
                    ))
                    logger.info("事件 '%s' 状态: locked → available (无触发条件)", event.id)
                    continue
                cond_group = self._parse_condition_group(
                    event.trigger_conditions
                )
                if cond_group is None or not cond_group.conditions:
                    # 条件组为空 → 直接变为 available
                    event.status = "available"
                    updates.append(EventUpdate(
                        event=event,
                        transition="locked→available",
                        details={"reason": "empty_condition_group"},
                    ))
                    logger.info("事件 '%s' 状态: locked → available (空条件组)", event.id)
                    continue
                result = self._evaluate_conditions(cond_group, session)
                if result["satisfied"] and not result["pending_flash"]:
                    event.status = "available"
                    update = EventUpdate(
                        event=event,
                        transition="locked→available",
                        details=result.get("details", {}),
                    )
                    updates.append(update)
                    logger.info(
                        "事件 '%s' 状态: locked → available", event.id
                    )

            if event.status == "active":
                if not event.completion_conditions:
                    continue
                cond_group = self._parse_condition_group(
                    event.completion_conditions
                )
                if cond_group is None or not cond_group.conditions:
                    continue
                result = self._evaluate_conditions(cond_group, session)
                if result["satisfied"] and not result["pending_flash"]:
                    event.status = "completed"
                    self._apply_on_complete(
                        event.on_complete, session, completed_event=event,
                    )
                    update = EventUpdate(
                        event=event,
                        transition="active→completed",
                        details=result.get("details", {}),
                    )
                    updates.append(update)
                    logger.info(
                        "事件 '%s' 状态: active → completed", event.id
                    )

        return updates

    # =========================================================================
    # 章节转换检查
    # =========================================================================

    def check_chapter_transition(
        self, session: Any
    ) -> Optional[Dict[str, Any]]:
        """检查章节转换条件。

        读取当前章节的 transitions，评估每个 ChapterTransition 的 conditions，
        返回最高优先级的就绪转换。

        Args:
            session: SessionRuntime 实例。

        Returns:
            转换信息字典，或 None。
        """
        if session is None or not hasattr(session, "world") or session.world is None:
            return None

        # 获取当前章节
        narrative = getattr(session, "narrative", None)
        if narrative is None:
            return None
        current_chapter_id = getattr(narrative, "current_chapter", None)
        if not current_chapter_id:
            return None

        chapter_data = session.world.chapter_registry.get(current_chapter_id)
        if not chapter_data:
            return None

        # 将 dict 转为 Chapter（如果需要）
        if isinstance(chapter_data, dict):
            try:
                chapter = Chapter(**chapter_data)
            except Exception:
                return None
        else:
            chapter = chapter_data

        if not chapter.transitions:
            return None

        # 评估转换条件，收集候选
        candidates: List[tuple] = []
        for trans in chapter.transitions:
            if not trans.conditions.conditions:
                # 无条件转换
                candidates.append((trans.priority, trans))
                continue

            result = self._evaluate_conditions(trans.conditions, session)
            if result["satisfied"] and not result["pending_flash"]:
                candidates.append((trans.priority, trans))

        if not candidates:
            return None

        # 按优先级降序排列，取最高
        candidates.sort(key=lambda x: x[0], reverse=True)
        best = candidates[0][1]

        return {
            "target_chapter_id": best.target_chapter_id,
            "transition_type": best.transition_type,
            "priority": best.priority,
            "narrative_hint": best.narrative_hint,
        }

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

        # 事件概要（仅 available + active）
        event_summaries = []
        for event in self.events:
            if event.status in ("available", "active"):
                entry: Dict[str, Any] = {
                    "id": event.id,
                    "name": event.name,
                    "description": event.description,
                    "status": event.status,
                    "importance": event.importance,
                }
                if event.narrative_directive:
                    entry["narrative_directive"] = event.narrative_directive
                if event.status == "active" and event.completion_conditions:
                    hint = self._summarize_completion_conditions(
                        event.completion_conditions
                    )
                    if hint:
                        entry["completion_hint"] = hint
                event_summaries.append(entry)

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
    # 条件评估（内联复用 ConditionEngine 逻辑）
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

    def _evaluate_conditions(
        self,
        group: ConditionGroup,
        session: Any,
    ) -> Dict[str, Any]:
        """递归评估条件组，返回 {satisfied, pending_flash, details}。

        内联复用 ConditionEngine 的 8 种条件逻辑。
        """
        if not group.conditions:
            return {"satisfied": True, "pending_flash": [], "details": {}}

        if group.operator == "not":
            inner = self._eval_single(group.conditions[0], session)
            return {
                "satisfied": not inner["satisfied"]
                    if not inner["pending_flash"] else True,
                "pending_flash": inner["pending_flash"],
                "details": inner["details"],
            }

        results = [
            self._eval_single(cond, session) for cond in group.conditions
        ]

        all_pending: List[Any] = []
        all_details: Dict[str, bool] = {}
        structural: List[bool] = []

        for r in results:
            all_pending.extend(r["pending_flash"])
            all_details.update(r["details"])
            structural.append(r["satisfied"])

        if group.operator == "and":
            satisfied = all(structural)
        else:  # "or"
            satisfied = any(structural)

        return {
            "satisfied": satisfied,
            "pending_flash": all_pending,
            "details": all_details,
        }

    def _eval_single(
        self,
        cond: Any,
        session: Any,
    ) -> Dict[str, Any]:
        """评估单个条件或嵌套条件组。"""
        if isinstance(cond, ConditionGroup):
            return self._evaluate_conditions(cond, session)

        if not isinstance(cond, Condition):
            if isinstance(cond, dict):
                try:
                    cond = Condition(**cond)
                except Exception:
                    return {"satisfied": True, "pending_flash": [], "details": {}}
            else:
                return {"satisfied": True, "pending_flash": [], "details": {}}

        handler = self._CONDITION_HANDLERS.get(cond.type)
        if handler is None:
            logger.warning("未知条件类型: %s", cond.type)
            return {"satisfied": True, "pending_flash": [], "details": {}}
        return handler(self, cond, session)

    # ----- 条件处理器 -----

    def _eval_location(
        self, cond: Condition, session: Any
    ) -> Dict[str, Any]:
        params = cond.params
        area_match = True
        sub_match = True
        if "area_id" in params:
            area_match = self.area_id == params["area_id"]
        if "sub_location" in params:
            current_sub = None
            if session and hasattr(session, "player") and session.player:
                current_sub = getattr(session.player, "sub_location", None)
            sub_match = current_sub == params["sub_location"]
        satisfied = area_match and sub_match
        return {
            "satisfied": satisfied,
            "pending_flash": [],
            "details": {"location": satisfied},
        }

    def _eval_npc_interacted(
        self, cond: Condition, session: Any
    ) -> Dict[str, Any]:
        npc_id = cond.params.get("npc_id", "")
        min_interactions = cond.params.get("min_interactions", 1)
        actual = 0
        if session and hasattr(session, "narrative") and session.narrative:
            interactions = getattr(session.narrative, "npc_interactions", {})
            actual = interactions.get(npc_id, 0)
        satisfied = actual >= min_interactions
        return {
            "satisfied": satisfied,
            "pending_flash": [],
            "details": {f"npc_interacted:{npc_id}": satisfied},
        }

    def _eval_time_passed(
        self, cond: Condition, session: Any
    ) -> Dict[str, Any]:
        min_day = cond.params.get("min_day", 0)
        min_hour = cond.params.get("min_hour", 0)
        game_day = 0
        game_hour = 0
        if session and hasattr(session, "time") and session.time:
            game_day = getattr(session.time, "day", 0) or 0
            game_hour = getattr(session.time, "hour", 0) or 0
        satisfied = (game_day > min_day) or (
            game_day == min_day and game_hour >= min_hour
        )
        return {
            "satisfied": satisfied,
            "pending_flash": [],
            "details": {"time_passed": satisfied},
        }

    def _eval_rounds_elapsed(
        self, cond: Condition, session: Any
    ) -> Dict[str, Any]:
        min_rounds = cond.params.get("min_rounds", 0)
        max_rounds = cond.params.get("max_rounds", float("inf"))
        rounds = 0
        if session and hasattr(session, "narrative") and session.narrative:
            rounds = getattr(session.narrative, "rounds_in_chapter", 0)
        satisfied = min_rounds <= rounds <= max_rounds
        return {
            "satisfied": satisfied,
            "pending_flash": [],
            "details": {"rounds_elapsed": satisfied},
        }

    def _eval_party_contains(
        self, cond: Condition, session: Any
    ) -> Dict[str, Any]:
        character_id = cond.params.get("character_id", "")
        party_ids: List[str] = []
        if session and hasattr(session, "party") and session.party:
            party_ids = getattr(session.party, "member_ids", []) or []
        satisfied = character_id in party_ids
        return {
            "satisfied": satisfied,
            "pending_flash": [],
            "details": {f"party_contains:{character_id}": satisfied},
        }

    def _eval_event_triggered(
        self, cond: Condition, session: Any
    ) -> Dict[str, Any]:
        event_id = cond.params.get("event_id", "")
        triggered: List[str] = []
        if session and hasattr(session, "narrative") and session.narrative:
            triggered = getattr(
                session.narrative, "events_triggered", []
            ) or []
        satisfied = event_id in triggered
        return {
            "satisfied": satisfied,
            "pending_flash": [],
            "details": {f"event_triggered:{event_id}": satisfied},
        }

    def _eval_objective_completed(
        self, cond: Condition, session: Any
    ) -> Dict[str, Any]:
        objective_id = cond.params.get("objective_id", "")
        completed: List[str] = []
        if session and hasattr(session, "narrative") and session.narrative:
            completed = getattr(
                session.narrative, "objectives_completed", []
            ) or []
        satisfied = objective_id in completed
        return {
            "satisfied": satisfied,
            "pending_flash": [],
            "details": {f"objective_completed:{objective_id}": satisfied},
        }

    def _eval_game_state(
        self, cond: Condition, session: Any
    ) -> Dict[str, Any]:
        required_state = cond.params.get("state", "")
        current_state = ""
        if session and hasattr(session, "player") and session.player:
            current_state = getattr(session.player, "game_state", "") or ""
        satisfied = current_state == required_state
        return {
            "satisfied": satisfied,
            "pending_flash": [],
            "details": {f"game_state:{required_state}": satisfied},
        }

    def _eval_flash_evaluate(
        self, cond: Condition, session: Any
    ) -> Dict[str, Any]:
        """语义条件标记为 pending，不在此处理。"""
        return {
            "satisfied": True,
            "pending_flash": [cond],
            "details": {"flash_evaluate": True},
        }

    # ----- 完成条件摘要（供 LLM 上下文注入） -----

    def _summarize_completion_conditions(
        self, raw: Any
    ) -> str:
        """将 completion_conditions 转为中文摘要，供 LLM 参考。"""
        group = self._parse_condition_group(raw)
        if group is None or not group.conditions:
            return ""
        return self._summarize_group(group)

    def _summarize_group(self, group: ConditionGroup) -> str:
        """递归摘要条件组。"""
        parts: List[str] = []
        for cond in group.conditions:
            if isinstance(cond, ConditionGroup):
                sub = self._summarize_group(cond)
                if sub:
                    parts.append(f"({sub})")
            elif isinstance(cond, Condition):
                text = self._summarize_single_condition(cond)
                if text:
                    parts.append(text)
            elif isinstance(cond, dict):
                try:
                    c = Condition(**cond)
                    text = self._summarize_single_condition(c)
                    if text:
                        parts.append(text)
                except Exception:
                    pass
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
        # ROUNDS_ELAPSED / GAME_STATE — 内部机制，不暴露给 LLM
        return ""

    _CONDITION_HANDLERS = {
        ConditionType.LOCATION: _eval_location,
        ConditionType.NPC_INTERACTED: _eval_npc_interacted,
        ConditionType.TIME_PASSED: _eval_time_passed,
        ConditionType.ROUNDS_ELAPSED: _eval_rounds_elapsed,
        ConditionType.PARTY_CONTAINS: _eval_party_contains,
        ConditionType.EVENT_TRIGGERED: _eval_event_triggered,
        ConditionType.OBJECTIVE_COMPLETED: _eval_objective_completed,
        ConditionType.GAME_STATE: _eval_game_state,
        ConditionType.FLASH_EVALUATE: _eval_flash_evaluate,
    }

    # =========================================================================
    # 事件完成副作用
    # =========================================================================

    def _apply_on_complete(
        self, on_complete: Optional[Dict[str, Any]], session: Any,
        completed_event: Optional[AreaEvent] = None,
    ) -> None:
        """处理事件完成后的副作用。

        支持的副作用类型：
        - unlock_events: 将指定事件从 locked 改为 available
        - add_items: 向玩家背包添加物品（需 session 支持）
        - add_xp: 向玩家添加经验（需 session 支持）
        - [Phase 5C] 自动分发 CompactEvent 到同伴
        """
        if not on_complete:
            # 即使没有 on_complete 副作用，仍需分发事件到同伴
            if completed_event:
                self._dispatch_event_to_companions(completed_event, session)
            return

        # 解锁关联事件
        unlock_ids = on_complete.get("unlock_events", [])
        if unlock_ids:
            events_by_id = {e.id: e for e in self.events}
            for eid in unlock_ids:
                target = events_by_id.get(eid)
                if target and target.status == "locked":
                    target.status = "available"
                    logger.info(
                        "副作用: 事件 '%s' 解锁 (locked → available)", eid
                    )

        # 添加物品（如果 session 支持）
        add_items = on_complete.get("add_items", [])
        if add_items and session and hasattr(session, "player") and session.player:
            inventory = getattr(session.player, "inventory", None)
            if inventory is not None and hasattr(inventory, "append"):
                for item in add_items:
                    inventory.append(item)
                    logger.info("副作用: 添加物品 %s", item)

        # 添加经验
        add_xp = on_complete.get("add_xp", 0)
        if add_xp and session and hasattr(session, "player") and session.player:
            current_xp = getattr(session.player, "xp", 0) or 0
            if hasattr(session.player, "xp"):
                session.player.xp = current_xp + add_xp
                logger.info("副作用: 添加 %d XP", add_xp)

        # Phase 5C: 分发事件到同伴
        if completed_event:
            self._dispatch_event_to_companions(completed_event, session)

    def _dispatch_event_to_companions(
        self, event: AreaEvent, session: Any,
    ) -> None:
        """将完成的事件分发到所有同伴实例（结构化，无 LLM）。"""
        companions = getattr(session, "companions", None)
        if not companions:
            return

        from app.runtime.models.companion_state import CompactEvent

        game_day = 1
        if hasattr(session, "time") and session.time:
            game_day = getattr(session.time, "day", 1) or 1

        compact = CompactEvent(
            event_id=event.id,
            event_name=event.name,
            summary=event.description or event.name,
            area_id=self.area_id,
            game_day=game_day,
            importance=event.importance or "side",
        )

        for companion in companions.values():
            if hasattr(companion, "add_event"):
                companion.add_event(compact)
                logger.debug(
                    "事件 '%s' 已分发到同伴 '%s'",
                    event.id,
                    getattr(companion, "character_id", "?"),
                )
