"""AreaRuntime — 区域生命周期管理（Phase 2B 实现）。"""

from __future__ import annotations

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


class AreaRuntime:
    """区域运行时 — 管理区域状态和事件生命周期。

    Phase 2B 完整实现。
    """

    def __init__(self, area_id: str, definition: AreaDefinition) -> None:
        self.area_id = area_id
        self.definition = definition
        self.state: AreaState = AreaState(area_id=area_id)
        self.events: List[AreaEvent] = []
        self.area_graph: Optional[Any] = None        # MemoryGraph
        self.npc_contexts: Dict[str, Any] = {}
        self.visit_summaries: List[VisitSummary] = []
        self.current_visit_log: List[str] = []
        self._world_id: Optional[str] = None
        self._session_id: Optional[str] = None
        self._db: Optional[firestore.Client] = None

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

    async def load(self, world_id: str, session_id: str) -> None:
        """从 Firestore 加载区域状态。

        加载顺序：
        1. 区域状态 (state)
        2. 事件状态 (events/)
        3. 访问摘要 (visits/)
        """
        self._world_id = world_id
        self._session_id = session_id
        area_ref = self._area_ref(world_id)

        # 1. 加载区域状态
        state_doc = area_ref.collection("state").document("current").get()
        if state_doc.exists:
            data = state_doc.to_dict() or {}
            data.setdefault("area_id", self.area_id)
            self.state = AreaState(**data)
        else:
            self.state = AreaState(area_id=self.area_id)

        # 2. 加载事件状态
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

        # 3. 加载访问摘要（最近 10 条）
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

        # 更新访问计数
        self.state.visit_count += 1
        self.state.updated_at = datetime.utcnow()

        # 重置本次访问日志
        self.current_visit_log = []

        logger.info(
            "AreaRuntime '%s' 已加载: %d 事件, %d 历史访问",
            self.area_id, len(self.events), len(self.visit_summaries),
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
                completion_conditions=None,
                on_complete=None,
                narrative_directive=se.narrative_directive,
                cooldown_rounds=se.cooldown_rounds,
                is_repeatable=se.is_repeatable,
            )
            self.events.append(area_event)

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

        # 2. 持久化区域状态
        self.state.updated_at = datetime.utcnow()
        area_ref.collection("state").document("current").set(
            self.state.model_dump(), merge=True
        )

        # 3. 持久化事件状态
        for event in self.events:
            area_ref.collection("events").document(event.id).set(
                event.model_dump(), merge=True
            )

        # 4. 清理临时数据
        self.current_visit_log = []
        self.npc_contexts = {}

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
                    continue
                cond_group = self._parse_condition_group(
                    event.trigger_conditions
                )
                if cond_group is None or not cond_group.conditions:
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
            })

        # 区域内 NPC
        area_npcs = []
        if world is not None and hasattr(world, "get_characters_in_area"):
            try:
                area_npcs = world.get_characters_in_area(self.area_id)
            except NotImplementedError:
                # WorldInstance 尚未实现时使用 resident_npcs
                for npc_id in defn.resident_npcs:
                    char = None
                    if hasattr(world, "get_character"):
                        char = world.get_character(npc_id)
                    area_npcs.append(
                        char if char else {"id": npc_id, "name": npc_id}
                    )

        # 事件概要（仅 available + active）
        event_summaries = []
        for event in self.events:
            if event.status in ("available", "active"):
                event_summaries.append({
                    "id": event.id,
                    "name": event.name,
                    "description": event.description,
                    "status": event.status,
                    "importance": event.importance,
                })

        return {
            "area_id": self.area_id,
            "name": defn.name,
            "description": defn.description,
            "danger_level": defn.danger_level,
            "area_type": defn.area_type,
            "tags": defn.tags,
            "ambient_description": defn.ambient_description,
            "sub_locations": sub_locations,
            "npcs": area_npcs,
            "events": event_summaries,
            "visit_count": self.state.visit_count,
            "connections": [
                {
                    "target": c.target_area_id,
                    "type": c.connection_type,
                    "travel_time": c.travel_time,
                    "description": c.description,
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
