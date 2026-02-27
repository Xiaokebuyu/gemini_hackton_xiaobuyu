"""EventMachine — 事件状态机 + 奖励 + Tick 编排（从 SessionRuntime 提取，P5 核心）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from app.runtime.session_runtime import SessionRuntime

logger = logging.getLogger(__name__)


@dataclass
class CompactEvent:
    """同伴事件分发用的轻量事件摘要。"""
    event_id: str
    event_name: str
    summary: str = ""
    area_id: str = ""
    game_day: int = 1
    importance: str = "side"
    player_role: str = ""


class EventMachine:
    """事件生命周期管理：状态转换 / 奖励发放 / Tick 同步 / 同伴分发。"""

    def __init__(self, session: 'SessionRuntime') -> None:
        self._s = session

    # =========================================================================
    # TickContext 组装 + BehaviorEngine tick
    # =========================================================================

    def build_tick_context(self, phase: str = "pre") -> Optional[Any]:
        """从当前会话状态构建 TickContext。无 WorldGraph 时返回 None。"""
        if not self._s.world_graph:
            return None
        from app.world.graph.models import TickContext
        # 从 world_root 节点读取 world_flags 和 faction_reputations，供条件评估使用
        world_flags: Dict[str, Any] = {}
        faction_reputations: Dict[str, int] = {}
        world_root = self._s.world_graph.get_node("world_root")
        if world_root:
            world_flags = dict(world_root.state.get("world_flags", {}))
            faction_reputations = dict(world_root.state.get("faction_reputations", {}))
        return TickContext(
            session=self._s,
            phase=phase,
            player_location=self._s.player_location or "",
            player_sub_location=self._s.sub_location or "",
            game_day=self._s.time.day if self._s.time else 1,
            game_hour=self._s.time.hour if self._s.time else 8,
            active_chapter=self._s.chapter_id or "",
            party_members=[m.character_id for m in self._s.party.get_active_members()] if self._s.party else [],
            events_triggered=list(self._s.narrative.events_triggered) if self._s.narrative else [],
            objectives_completed=list(getattr(self._s.narrative, "objectives_completed", []) or []) if self._s.narrative else [],
            round_count=getattr(self._s.narrative, "rounds_in_chapter", 0) if self._s.narrative else 0,
            npc_interactions=dict(getattr(self._s.narrative, "npc_interactions", {}) or {}) if self._s.narrative else {},
            game_state="",
            world_flags=world_flags,
            faction_reputations=faction_reputations,
            flash_results=dict(self._s.flash_results),
        )

    def run_behavior_tick(self, phase: str = "pre") -> Optional[Any]:
        """BehaviorEngine.tick() + narrative 同步 + 副作用。返回 TickResult 或 None。"""
        if not self._s._behavior_engine:
            return None
        ctx = self.build_tick_context(phase)
        if ctx is None:
            return None
        try:
            tick_result = self._s._behavior_engine.tick(ctx)
            logger.info(
                "[SessionRuntime] tick(%s): %d fired, %d hints, %d events",
                phase, len(tick_result.results), len(tick_result.narrative_hints),
                len(tick_result.all_events),
            )
            self._sync_tick_to_narrative(tick_result)
            self._apply_tick_side_effects(tick_result)
            return tick_result
        except (KeyError, ValueError) as exc:
            logger.error("[SessionRuntime] tick(%s) failed: %s", phase, exc, exc_info=True)
            return None

    # =========================================================================
    # Tick → Narrative 同步 + 副作用
    # =========================================================================

    def _sync_tick_to_narrative(self, tick_result: Any) -> None:
        """将 BehaviorEngine 的事件完成同步到 narrative.events_triggered。"""
        if not self._s.narrative or not self._s.world_graph:
            return
        from app.world.graph.models import EventStatus
        for nid, changes in tick_result.state_changes.items():
            if changes.get("status") != EventStatus.COMPLETED:
                continue
            node = self._s.world_graph.get_node(nid)
            if not node or node.type != "event_def":
                continue
            if nid not in self._s.narrative.events_triggered:
                self._s.narrative.events_triggered.append(nid)
                self._s.mark_narrative_dirty()
                logger.info("[SessionRuntime] 同步事件完成: %s", nid)

            # E4: is_repeatable 事件完成 → COOLDOWN
            cooldown_key = f"cooldown:{nid}"
            if node.properties.get("is_repeatable") and cooldown_key not in self._s._applied_side_effect_events:
                cooldown_rounds = node.properties.get("cooldown_rounds", 0)
                current_round = getattr(self._s.narrative, "rounds_in_chapter", 0)
                if cooldown_rounds > 0:
                    self._s.world_graph.merge_state(nid, {
                        "status": EventStatus.COOLDOWN,
                        "activated_at_round": current_round,
                    })
                    self._s.world_graph.reset_behaviors(nid)
                    logger.info("[SessionRuntime] 事件进入冷却: %s (%d 回合)", nid, cooldown_rounds)
                else:
                    self._s.world_graph.merge_state(nid, {
                        "status": EventStatus.AVAILABLE,
                        "activated_at_round": None,
                    })
                    self._s.world_graph.reset_behaviors(nid)
                    logger.info("[SessionRuntime] 事件直接回 AVAILABLE（cooldown=0）: %s", nid)
                self._s._applied_side_effect_events.add(cooldown_key)

    def _apply_tick_side_effects(self, tick_result: Any) -> None:
        """从 tick 产出的 WorldEvent 中应用 XP/物品等副作用。

        使用 _applied_side_effect_events 去重，防止工具手动 apply 后
        pipeline post-tick 再次重复发放。
        """
        for event in tick_result.all_events:
            dedup_key = event.event_id
            blanket_key = f"{event.event_type}:{event.origin_node}"
            if dedup_key in self._s._applied_side_effect_events or blanket_key in self._s._applied_side_effect_events:
                continue
            if event.event_type == "xp_awarded":
                amount = event.data.get("amount", 0)
                if amount and self._s.player and hasattr(self._s.player, "xp"):
                    from app.world.player import stats as _sm
                    _sm.add_xp(self._s.player, amount)
                    self._s.mark_player_dirty()
                    self._s._applied_side_effect_events.add(dedup_key)
                    logger.info("[SessionRuntime] 副作用: +%d XP", amount)
            elif event.event_type == "item_granted":
                if self._s.player:
                    inventory = getattr(self._s.player, "inventory", None)
                    if inventory is not None and hasattr(inventory, "append"):
                        inventory.append(event.data)
                        self._s.mark_player_dirty()
                        self._s._applied_side_effect_events.add(dedup_key)
                        logger.info("[SessionRuntime] 副作用: +物品 %s", event.data)
            elif event.event_type == "gold_awarded":
                amount = event.data.get("amount", 0)
                if amount and self._s.player and hasattr(self._s.player, "gold"):
                    from app.world.player import stats as _sm2
                    _sm2.add_gold(self._s.player, amount)
                    self._s.mark_player_dirty()
                    self._s._applied_side_effect_events.add(dedup_key)
                    logger.info("[SessionRuntime] 副作用: +%d 金币", amount)
            elif event.event_type == "reputation_changed":
                faction = event.data.get("faction", "")
                delta = event.data.get("delta", 0)
                if faction and delta and self._s.world_graph and self._s.world_graph.has_node("world_root"):
                    root = self._s.world_graph.get_node("world_root")
                    reps = dict(root.state.get("faction_reputations", {})) if root else {}
                    reps[faction] = reps.get(faction, 0) + delta
                    self._s.world_graph.merge_state("world_root", {"faction_reputations": reps})
                    self._s._applied_side_effect_events.add(dedup_key)
                    logger.info("[SessionRuntime] 副作用: 声望 %s %+d", faction, delta)
            elif event.event_type == "world_flag_set":
                key = event.data.get("key", "")
                value = event.data.get("value")
                if key and self._s.world_graph and self._s.world_graph.has_node("world_root"):
                    root = self._s.world_graph.get_node("world_root")
                    flags = dict(root.state.get("world_flags", {})) if root else {}
                    flags[key] = value
                    self._s.world_graph.merge_state("world_root", {"world_flags": flags})
                    self._s._applied_side_effect_events.add(dedup_key)
                    logger.info("[SessionRuntime] 副作用: 世界标记 %s = %s", key, value)
        # 同伴分发
        self._dispatch_completed_events_to_companions(tick_result)

    def _dispatch_completed_events_to_companions(self, tick_result: Any) -> None:
        """将完成的事件分发到同伴实例。去重防止工具已分发的事件再次发放。"""
        if not self._s.companions or not self._s.world_graph:
            return
        # CompactEvent 定义在本模块顶部
        game_day = self._s.time.day if self._s.time else 1
        area_id = self._s.player_location or ""
        from app.world.graph.models import EventStatus
        for nid, changes in tick_result.state_changes.items():
            if changes.get("status") != EventStatus.COMPLETED:
                continue
            dedup_key = f"companion_dispatch:{nid}"
            if dedup_key in self._s._applied_side_effect_events:
                continue
            node = self._s.world_graph.get_node(nid)
            if not node or node.type != "event_def":
                continue
            compact = CompactEvent(
                event_id=nid,
                event_name=node.name,
                summary=node.properties.get("description", node.name),
                area_id=area_id,
                game_day=game_day,
                importance=node.properties.get("importance", "side"),
            )
            for companion in self._s.companions.values():
                if hasattr(companion, "add_event"):
                    companion.add_event(compact)
            self._s._applied_side_effect_events.add(dedup_key)

    # =========================================================================
    # 章节转换 + 事件概要
    # =========================================================================

    def check_chapter_transitions(self) -> Optional[Dict[str, Any]]:
        """从 WorldGraph GATE 边评估章节转换。"""
        if not self._s.world_graph or not self._s.narrative:
            return None
        from app.world.graph.models import WorldNodeType
        current_chapter = self._s.narrative.current_chapter

        candidates: List[Dict[str, Any]] = []
        for ch_id in self._s.world_graph.get_by_type(WorldNodeType.CHAPTER.value):
            if ch_id == current_chapter:
                continue
            node = self._s.world_graph.get_node(ch_id)
            if not node or node.state.get("status") != "active":
                continue
            edges = self._s.world_graph.get_edges_between(current_chapter, ch_id)
            for key, edge_data in edges:
                if edge_data.get("relation") == "gate":
                    candidates.append({
                        "target_chapter_id": ch_id,
                        "transition_type": edge_data.get("transition_type", "normal"),
                        "priority": edge_data.get("priority", 0),
                        "narrative_hint": edge_data.get("narrative_hint", ""),
                    })
                    break

        if not candidates:
            return None
        candidates.sort(key=lambda c: c["priority"], reverse=True)
        return candidates[0]

    def get_event_summaries_from_graph(self, area_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """从 WorldGraph 获取事件概要。"""
        if not self._s.world_graph:
            return []
        target = area_id or self._s.player_location
        if not target:
            return []
        summaries: List[Dict[str, Any]] = []
        for eid in self._s.world_graph.find_events_in_scope(target):
            node = self._s.world_graph.get_node(eid)
            if not node:
                continue
            from app.world.graph.models import EventStatus as _ES
            status = node.state.get("status", _ES.LOCKED)
            if status not in (_ES.AVAILABLE, _ES.ACTIVE):
                continue
            entry: Dict[str, Any] = {
                "id": node.id,
                "name": node.name,
                "description": node.properties.get("description", ""),
                "status": status,
                "importance": node.properties.get("importance", "side"),
            }
            if node.properties.get("narrative_directive"):
                entry["narrative_directive"] = node.properties["narrative_directive"]
            if status == _ES.ACTIVE:
                stages_raw = node.properties.get("stages", [])
                current_stage_id = node.state.get("current_stage")
                if stages_raw and current_stage_id:
                    current_stage = next(
                        (s for s in stages_raw
                         if (s.get("id") if isinstance(s, dict) else getattr(s, "id", None)) == current_stage_id),
                        None,
                    )
                    if current_stage:
                        cs = current_stage if isinstance(current_stage, dict) else current_stage.model_dump()
                        obj_progress = node.state.get("objective_progress", {})
                        entry["current_stage"] = {
                            "id": cs["id"],
                            "name": cs.get("name", ""),
                            "narrative_directive": cs.get("narrative_directive", ""),
                            "objectives": [
                                {
                                    "id": obj["id"],
                                    "text": obj.get("text", ""),
                                    "required": obj.get("required", True),
                                    "completed": obj_progress.get(obj["id"], False),
                                }
                                for obj in cs.get("objectives", [])
                            ],
                        }
                    entry["stage_progress"] = node.state.get("stage_progress", {})

                outcomes_raw = node.properties.get("outcomes", {})
                if outcomes_raw:
                    entry["available_outcomes"] = [
                        {
                            "key": k,
                            "description": v.get("description", "") if isinstance(v, dict) else getattr(v, "description", ""),
                        }
                        for k, v in outcomes_raw.items()
                    ]
            summaries.append(entry)
        return summaries

    # =========================================================================
    # 事件状态机 — activate / complete / fail / advance_stage / complete_objective
    # =========================================================================

    def activate_event(self, event_id: str) -> Dict[str, Any]:
        """激活可用事件 (available → active)。"""
        from app.world.graph.models import EventStatus

        wg = self._s.world_graph
        engine = self._s._behavior_engine
        if not wg:
            return {"success": False, "error": "WorldGraph not available"}

        node = wg.get_node(event_id)
        if not node or node.type != "event_def":
            available = [
                eid for eid in wg.find_events_in_scope(self._s.player_location or "")
                if (n := wg.get_node(eid)) and n.state.get("status") == EventStatus.AVAILABLE
            ]
            return {"success": False, "error": f"event not found: {event_id}", "available_events": available}

        current_status = node.state.get("status", EventStatus.LOCKED)

        # 补偿同轮时序：如果事件仍 locked，先 tick 刷新条件
        if current_status == EventStatus.LOCKED and engine:
            ctx = self.build_tick_context("post")
            if ctx:
                try:
                    engine.tick(ctx)
                except (KeyError, ValueError) as exc:
                    logger.warning("[session] pre-activate tick failed: %s", exc)
            node = wg.get_node(event_id)
            current_status = node.state.get("status", EventStatus.LOCKED) if node else EventStatus.LOCKED

        if current_status == EventStatus.LOCKED:
            return {
                "success": False,
                "event_id": event_id,
                "current_status": EventStatus.LOCKED,
                "error": f"事件 '{node.name}' 尚未解锁",
                "available_events": [
                    eid for eid in wg.find_events_in_scope(self._s.player_location or "")
                    if (n := wg.get_node(eid)) and n.state.get("status") == EventStatus.AVAILABLE
                ],
            }

        if current_status != EventStatus.AVAILABLE:
            return {
                "success": False,
                "event_id": event_id,
                "current_status": current_status,
                "error": f"事件 '{node.name}' 当前状态为 '{current_status}'，需要 'available'",
            }

        # 激活事件
        current_round = getattr(self._s.narrative, "rounds_in_chapter", 0) if self._s.narrative else 0
        wg.merge_state(event_id, {"status": EventStatus.ACTIVE, "activated_at_round": current_round})

        # 初始化 stages
        stages = node.properties.get("stages", [])
        if stages:
            first_stage_id = stages[0]["id"] if isinstance(stages[0], dict) else stages[0].id
            wg.merge_state(event_id, {"current_stage": first_stage_id})

        # 传播事件
        if engine:
            try:
                from app.world.graph.models import WorldEvent
                ctx = self.build_tick_context("post")
                if ctx:
                    evt = WorldEvent(
                        event_type="event_activated",
                        origin_node=event_id,
                        actor="player",
                        game_day=ctx.game_day,
                        game_hour=ctx.game_hour,
                        data={"event_id": event_id},
                        visibility="scope",
                    )
                    engine.handle_event(evt, ctx)
            except (KeyError, ValueError) as exc:
                logger.warning("[session] 事件传播失败 '%s': %s", event_id, exc)

        return {
            "success": True,
            "event_id": event_id,
            "event_name": node.name,
            "new_status": EventStatus.ACTIVE,
            "narrative_directive": node.properties.get("narrative_directive", ""),
        }

    def complete_event(self, event_id: str, outcome_key: str = "") -> Dict[str, Any]:
        """完成活跃事件 (active → completed)，应用奖励和级联解锁。"""
        from app.world.graph.models import EventStatus

        wg = self._s.world_graph
        engine = self._s._behavior_engine
        if not wg:
            return {"success": False, "error": "WorldGraph not available"}

        node = wg.get_node(event_id)
        if not node or node.type != "event_def":
            active_events = [
                eid for eid in wg.find_events_in_scope(self._s.player_location or "")
                if (n := wg.get_node(eid)) and n.state.get("status") == EventStatus.ACTIVE
            ]
            return {"success": False, "error": f"event not found: {event_id}", "active_events": active_events}

        current_status = node.state.get("status", EventStatus.LOCKED)
        if current_status != EventStatus.ACTIVE:
            return {"success": False, "error": f"event '{event_id}' status is '{current_status}', expected 'active'"}

        # 标记完成
        wg.merge_state(event_id, {"status": EventStatus.COMPLETED})

        # outcome 处理
        outcome_applied = False
        if outcome_key:
            outcomes = node.properties.get("outcomes", {})
            outcome = outcomes.get(outcome_key)
            if not outcome:
                wg.merge_state(event_id, {"status": EventStatus.ACTIVE})  # 回滚
                return {"success": False, "error": f"Unknown outcome: {outcome_key}", "available_outcomes": list(outcomes.keys())}

            # 验证 outcome 条件
            outcome_conditions = outcome.get("conditions") if isinstance(outcome, dict) else getattr(outcome, "conditions", None)
            if outcome_conditions:
                from app.models.narrative import ConditionGroup as CG
                ctx = self.build_tick_context("post")
                if ctx:
                    from app.world.events.behavior_engine import ConditionEvaluator
                    eval_result = ConditionEvaluator().evaluate(
                        CG(**outcome_conditions) if isinstance(outcome_conditions, dict) else outcome_conditions,
                        ctx,
                    )
                    if not eval_result.satisfied:
                        wg.merge_state(event_id, {"status": EventStatus.ACTIVE})
                        return {"success": False, "error": f"Outcome conditions not met: {outcome_key}"}

            wg.merge_state(event_id, {"outcome": outcome_key})
            outcome_dict = outcome if isinstance(outcome, dict) else outcome.model_dump()
            self._apply_outcome_rewards(outcome_dict, event_id, node)
            outcome_applied = True
        else:
            on_complete = node.properties.get("on_complete")
            self._apply_on_complete_from_graph(on_complete, event_id, node)

        # 同步到 narrative
        if self._s.narrative:
            triggered = self._s.narrative.events_triggered
            if event_id not in triggered:
                triggered.append(event_id)
                self._s.mark_narrative_dirty()

        # 级联解锁
        newly_available: List[str] = []
        if engine:
            try:
                from app.world.graph.models import WorldEvent
                ctx = self.build_tick_context("post")
                if ctx:
                    evt = WorldEvent(
                        event_type="event_completed",
                        origin_node=event_id,
                        actor="player",
                        game_day=ctx.game_day,
                        game_hour=ctx.game_hour,
                        data={"event_id": event_id, "outcome": outcome_key or None,
                              "source": "manual" if outcome_key else "tool"},
                        visibility="scope",
                    )
                    cascade_result = engine.handle_event(evt, ctx)
                    self._sync_tick_to_narrative(cascade_result)

                    tick_result = engine.tick(ctx)
                    self._sync_tick_to_narrative(tick_result)

                    for nid, changes in tick_result.state_changes.items():
                        if changes.get("status") in ("available", "active"):
                            newly_available.append(nid)
                    for nid, changes in cascade_result.state_changes.items():
                        if changes.get("status") in ("available", "active") and nid not in newly_available:
                            newly_available.append(nid)
            except (KeyError, ValueError) as exc:
                logger.warning("[session] 级联解锁失败 '%s': %s", event_id, exc)

        # 分发到同伴
        self._dispatch_event_to_companions_from_graph(event_id, node)

        payload: Dict[str, Any] = {
            "success": True,
            "event_id": event_id,
            "event_name": node.name,
            "new_status": "completed",
            "newly_available_events": newly_available,
        }
        if outcome_key:
            payload["outcome"] = outcome_key
            payload["outcome_applied"] = outcome_applied
        else:
            payload["on_complete_applied"] = bool(node.properties.get("on_complete"))
        return payload

    def fail_event(self, event_id: str, reason: str = "") -> Dict[str, Any]:
        """标记事件失败 (active → failed)。"""
        from app.world.graph.models import EventStatus

        wg = self._s.world_graph
        engine = self._s._behavior_engine
        if not wg:
            return {"success": False, "error": "WorldGraph 不可用"}

        node = wg.get_node(event_id)
        if not node or node.type != "event_def":
            return {"success": False, "error": f"事件节点不存在: {event_id}"}

        current_status = node.state.get("status")
        if current_status != EventStatus.ACTIVE:
            return {"success": False, "error": f"事件 {event_id} 不处于 ACTIVE 状态（当前: {current_status}）"}

        wg.merge_state(event_id, {"status": EventStatus.FAILED, "failure_reason": reason or "manual_fail"})

        if engine:
            try:
                from app.world.graph.models import WorldEvent
                ctx = self.build_tick_context("post")
                if ctx:
                    evt = WorldEvent(
                        event_type="event_failed",
                        origin_node=event_id,
                        actor="player",
                        game_day=ctx.game_day,
                        game_hour=ctx.game_hour,
                        data={"event_id": event_id, "reason": reason},
                        visibility="scope",
                    )
                    fail_result = engine.handle_event(evt, ctx)
                    self._sync_tick_to_narrative(fail_result)
                    self._apply_tick_side_effects(fail_result)
            except (KeyError, ValueError) as exc:
                logger.warning("[session] fail_event 事件传播失败 '%s': %s", event_id, exc)

        return {"success": True, "event_id": event_id, "status": "failed", "reason": reason or "manual_fail"}

    def advance_stage(self, event_id: str, stage_id: str = "") -> Dict[str, Any]:
        """推进事件到下一阶段。"""
        from app.world.graph.models import EventStatus

        wg = self._s.world_graph
        engine = self._s._behavior_engine
        if not wg:
            return {"success": False, "error": "WorldGraph not available"}

        node = wg.get_node(event_id)
        if not node or node.type != "event_def":
            return {"success": False, "error": f"event not found: {event_id}"}

        if node.state.get("status") != EventStatus.ACTIVE:
            return {"success": False, "error": f"event '{event_id}' is not active"}

        stages_raw = node.properties.get("stages", [])
        if not stages_raw:
            return {"success": False, "error": f"event '{event_id}' has no stages"}

        current_stage_id = node.state.get("current_stage")

        # 找当前 stage 索引
        current_idx = -1
        for i, s in enumerate(stages_raw):
            sid = s["id"] if isinstance(s, dict) else s.id
            if sid == current_stage_id:
                current_idx = i
                break

        if current_idx < 0:
            return {"success": False, "error": f"current_stage '{current_stage_id}' not found in stages"}

        # 校验 required objectives 完成
        current_stage = stages_raw[current_idx]
        cs = current_stage if isinstance(current_stage, dict) else current_stage.model_dump()
        obj_progress = node.state.get("objective_progress", {})
        for obj in cs.get("objectives", []):
            if obj.get("required", True) and not obj_progress.get(obj["id"], False):
                return {
                    "success": False,
                    "error": f"Required objective '{obj['id']}' not completed",
                    "incomplete_objectives": [
                        o["id"] for o in cs.get("objectives", [])
                        if o.get("required", True) and not obj_progress.get(o["id"], False)
                    ],
                }

        # 确定目标 stage
        if stage_id:
            target_idx = -1
            for i, s in enumerate(stages_raw):
                sid = s["id"] if isinstance(s, dict) else s.id
                if sid == stage_id:
                    target_idx = i
                    break
            if target_idx < 0:
                return {"success": False, "error": f"target stage '{stage_id}' not found"}
        else:
            target_idx = current_idx + 1

        is_last = target_idx >= len(stages_raw)

        if is_last:
            result = self.complete_event(event_id)
            result["advanced_from_stage"] = current_stage_id
            result["auto_completed"] = True
            return result

        target_stage = stages_raw[target_idx]
        target_stage_id = target_stage["id"] if isinstance(target_stage, dict) else target_stage.id
        target_stage_name = target_stage.get("name", "") if isinstance(target_stage, dict) else getattr(target_stage, "name", "")

        # 更新 current_stage + stage_progress
        progress = dict(node.state.get("stage_progress", {}))
        progress[current_stage_id] = True
        wg.merge_state(event_id, {"current_stage": target_stage_id, "stage_progress": progress})

        # 补偿 tick
        if engine:
            try:
                ctx = self.build_tick_context("post")
                if ctx:
                    tick_result = engine.tick(ctx)
                    self._sync_tick_to_narrative(tick_result)
            except (KeyError, ValueError) as exc:
                logger.warning("[session] advance_stage 补偿 tick 失败: %s", exc)

        ts = target_stage if isinstance(target_stage, dict) else target_stage.model_dump()
        return {
            "success": True,
            "event_id": event_id,
            "previous_stage": current_stage_id,
            "new_stage": target_stage_id,
            "stage_name": target_stage_name,
            "narrative_directive": ts.get("narrative_directive", ""),
        }

    def complete_event_objective(self, event_id: str, objective_id: str) -> Dict[str, Any]:
        """标记事件目标完成。"""
        from app.world.graph.models import EventStatus

        wg = self._s.world_graph
        engine = self._s._behavior_engine
        if not wg:
            return {"success": False, "error": "WorldGraph not available"}

        node = wg.get_node(event_id)
        if not node or node.type != "event_def":
            return {"success": False, "error": f"event not found: {event_id}"}

        if node.state.get("status") != EventStatus.ACTIVE:
            return {"success": False, "error": f"event '{event_id}' is not active"}

        # 验证 objective 在当前 stage 中
        stages_raw = node.properties.get("stages", [])
        current_stage_id = node.state.get("current_stage")
        objective_found = False

        if stages_raw and current_stage_id:
            for s in stages_raw:
                sid = s["id"] if isinstance(s, dict) else s.id
                if sid == current_stage_id:
                    objectives = s.get("objectives", []) if isinstance(s, dict) else getattr(s, "objectives", [])
                    for obj in objectives:
                        oid = obj["id"] if isinstance(obj, dict) else obj.id
                        if oid == objective_id:
                            objective_found = True
                            break
                    break

        if not objective_found:
            return {"success": False, "error": f"objective '{objective_id}' not found in current stage '{current_stage_id}'"}

        obj_progress = dict(node.state.get("objective_progress", {}))
        if obj_progress.get(objective_id, False):
            return {"success": False, "error": f"objective '{objective_id}' already completed"}

        obj_progress[objective_id] = True
        wg.merge_state(event_id, {"objective_progress": obj_progress})

        # 计算剩余
        remaining = []
        if stages_raw and current_stage_id:
            for s in stages_raw:
                sid = s["id"] if isinstance(s, dict) else s.id
                if sid == current_stage_id:
                    objectives = s.get("objectives", []) if isinstance(s, dict) else getattr(s, "objectives", [])
                    for obj in objectives:
                        oid = obj["id"] if isinstance(obj, dict) else obj.id
                        if not obj_progress.get(oid, False):
                            remaining.append(oid)
                    break

        # 补偿 tick
        if engine:
            try:
                ctx = self.build_tick_context("post")
                if ctx:
                    tick_result = engine.tick(ctx)
                    self._sync_tick_to_narrative(tick_result)
            except (KeyError, ValueError) as exc:
                logger.warning("[session] complete_event_objective 补偿 tick 失败: %s", exc)

        return {"success": True, "event_id": event_id, "objective_id": objective_id, "remaining_objectives": remaining}

    # =========================================================================
    # Event helpers — 奖励 & 同伴分发
    # =========================================================================

    def _apply_rewards(
        self,
        *,
        xp: int = 0,
        gold: int = 0,
        items: Optional[list] = None,
        reputation_changes: Optional[Dict[str, Any]] = None,
        world_flags: Optional[Dict[str, Any]] = None,
        event_id: str,
        label: str = "副作用",
    ) -> None:
        """通用奖励应用：XP/Gold/Items/Reputation/WorldFlags。"""
        from app.world.player import stats as stats_manager

        player = self._s.player

        if xp and player and hasattr(player, "xp"):
            stats_manager.add_xp(player, xp)
            self._s.mark_player_dirty()
            self._s._applied_side_effect_events.add(f"xp_awarded:{event_id}")
            logger.info("[session] %s: +%d XP (event=%s)", label, xp, event_id)

        if gold and player and hasattr(player, "gold"):
            stats_manager.add_gold(player, gold)
            self._s.mark_player_dirty()
            self._s._applied_side_effect_events.add(f"gold_awarded:{event_id}")
            logger.info("[session] %s: +%d 金币 (event=%s)", label, gold, event_id)

        if items and player:
            inventory = getattr(player, "inventory", None)
            if inventory is not None and hasattr(inventory, "append"):
                for item in items:
                    inventory.append(item if isinstance(item, dict) else {"item_id": item})
                self._s.mark_player_dirty()
                self._s._applied_side_effect_events.add(f"item_granted:{event_id}")

        wg = self._s.world_graph

        if reputation_changes and wg and wg.has_node("world_root"):
            root = wg.get_node("world_root")
            reps = dict(root.state.get("faction_reputations", {})) if root else {}
            for faction, delta in reputation_changes.items():
                reps[faction] = reps.get(faction, 0) + delta
                logger.info("[session] %s: 声望 %s %+d (event=%s)", label, faction, delta, event_id)
            wg.merge_state("world_root", {"faction_reputations": reps})
            self._s._applied_side_effect_events.add(f"reputation_changed:{event_id}")

        if world_flags and wg and wg.has_node("world_root"):
            root = wg.get_node("world_root")
            flags = dict(root.state.get("world_flags", {})) if root else {}
            for key, value in world_flags.items():
                flags[key] = value
                logger.info("[session] %s: 世界标记 %s = %s (event=%s)", label, key, value, event_id)
            wg.merge_state("world_root", {"world_flags": flags})
            self._s._applied_side_effect_events.add(f"world_flag_set:{event_id}")

    def _apply_on_complete_from_graph(
        self, on_complete: Optional[Dict[str, Any]], event_id: str, node: Any,
    ) -> None:
        """从 WorldGraph 节点的 on_complete 属性应用副作用。"""
        if not on_complete:
            return
        self._apply_rewards(
            xp=on_complete.get("add_xp", 0),
            gold=on_complete.get("add_gold", 0),
            items=on_complete.get("add_items"),
            reputation_changes=on_complete.get("reputation_changes"),
            world_flags=on_complete.get("world_flags"),
            event_id=event_id,
            label="副作用",
        )

    def _apply_outcome_rewards(
        self, outcome: Dict[str, Any], event_id: str, node: Any,
    ) -> None:
        """应用 EventOutcome 的特定奖励。"""
        from app.world.graph.models import EventStatus

        rewards = outcome.get("rewards", {})
        if not rewards and not outcome.get("reputation_changes") and not outcome.get("world_flags"):
            return
        self._apply_rewards(
            xp=rewards.get("xp", 0),
            gold=rewards.get("gold", 0),
            items=rewards.get("items"),
            reputation_changes=outcome.get("reputation_changes"),
            world_flags=outcome.get("world_flags"),
            event_id=event_id,
            label="outcome 奖励",
        )
        # unlock_events
        unlock_events = outcome.get("unlock_events") or []
        if unlock_events:
            wg = self._s.world_graph
            if wg:
                for unlock_eid in unlock_events:
                    unlock_node = wg.get_node(unlock_eid)
                    if unlock_node and unlock_node.state.get("status") == EventStatus.LOCKED:
                        wg.merge_state(unlock_eid, {"status": EventStatus.AVAILABLE})
                        self._s._applied_side_effect_events.add(f"event_unlocked:{unlock_eid}")

    def _dispatch_event_to_companions_from_graph(
        self, event_id: str, node: Any,
    ) -> None:
        """将完成的事件分发到同伴。"""
        companions = self._s.companions
        if not companions:
            return
        # CompactEvent 定义在本模块顶部
        game_day = self._s.time.day if self._s.time else 1
        area_id = self._s.player_location or ""
        compact = CompactEvent(
            event_id=event_id,
            event_name=node.name,
            summary=node.properties.get("description", node.name),
            area_id=area_id,
            game_day=game_day,
            importance=node.properties.get("importance", "side"),
        )
        for companion in companions.values():
            if hasattr(companion, "add_event"):
                companion.add_event(compact)
        self._s._applied_side_effect_events.add(f"companion_dispatch:{event_id}")
