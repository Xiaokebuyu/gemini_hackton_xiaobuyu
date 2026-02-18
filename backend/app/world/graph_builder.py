"""
GraphBuilder -- Step C3

从 WorldInstance（Firestore 静态数据）构建填充完毕的 WorldGraph。
纯内存构图（同步），不负责快照恢复（由调用方处理）。

设计文档: 架构与设计/世界底层重构与战斗系统设计专项/世界活图详细设计.md §5

构建步骤:
  Step 1: world_root — 全局唯一根节点
  Step 2: chapters — 叙事章节 + GATE 边 + 解锁 Behavior
  Step 3: regions + areas + locations — 地理层级
  Step 4: events — 事件定义 + HAS_EVENT 边 + Behavior 映射
  Step 5: characters (NPCs) — NPC 实体 + HOSTS 边 + RELATES_TO 边
  Step 6: party (camp + teammates) — 营地 + 队友 MEMBER_OF 边
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional

from app.models.narrative import (
    Chapter,
    ChapterTransition,
    Condition,
    ConditionGroup,
    ConditionType,
    StoryEvent,
)
from app.runtime.models.area_state import AreaDefinition
from app.runtime.session_runtime import SessionRuntime
from app.runtime.world_instance import WorldInstance
from app.world.constants import default_npc_state, default_player_state
from app.world.models import (
    Action,
    ActionType,
    Behavior,
    ChapterStatus,
    EventStatus,
    TriggerType,
    WorldEdgeType,
    WorldNode,
    WorldNodeType,
)
from app.world.world_graph import WorldGraph

logger = logging.getLogger(__name__)


# =============================================================================
# Helper Functions
# =============================================================================


def _sanitize_region_id(region_name: str) -> str:
    """中文 region 名 → 合法节点 ID。

    如 '边境地区' → 'region_边境地区'
    空白字符替换为下划线，保留中文字符。
    """
    sanitized = re.sub(r"\s+", "_", region_name.strip())
    return f"region_{sanitized}"


def _extract_char_field(
    char_data: Dict[str, Any], field: str, default: Any = None
) -> Any:
    """兼容 Firestore 嵌套格式和 JSON 扁平格式的字段提取。

    查找顺序:
      1. char_data["profile"]["metadata"][field]
      2. char_data[field]
      3. default
    """
    profile = char_data.get("profile")
    if isinstance(profile, dict):
        metadata = profile.get("metadata")
        if isinstance(metadata, dict):
            val = metadata.get(field)
            if val is not None:
                return val
    val = char_data.get(field)
    if val is not None:
        return val
    return default


def _build_complete_actions(
    event_data: StoryEvent, event_node_id: str,
) -> List[Action]:
    """从 StoryEvent.on_complete 构建 EMIT_EVENT actions（副作用）。

    抽取为独立函数，供 stage 路径和非 stage 路径共用。
    """
    on_complete = event_data.on_complete or {}
    actions: List[Action] = []

    # unlock_events → EMIT_EVENT
    for unlock_eid in (on_complete.get("unlock_events") or []):
        actions.append(
            Action(
                type=ActionType.EMIT_EVENT,
                target="self",
                params={
                    "event_type": "event_unlocked",
                    "data": {"event_id": unlock_eid},
                    "visibility": "scope",
                },
            )
        )

    # add_xp → EMIT_EVENT
    xp = on_complete.get("add_xp")
    if xp:
        actions.append(
            Action(
                type=ActionType.EMIT_EVENT,
                target="self",
                params={
                    "event_type": "xp_awarded",
                    "data": {"amount": xp},
                    "visibility": "global",
                },
            )
        )

    # add_items → EMIT_EVENT
    for item in (on_complete.get("add_items") or []):
        actions.append(
            Action(
                type=ActionType.EMIT_EVENT,
                target="self",
                params={
                    "event_type": "item_granted",
                    "data": item,
                    "visibility": "global",
                },
            )
        )

    # add_gold → EMIT_EVENT
    gold = on_complete.get("add_gold")
    if gold:
        actions.append(
            Action(
                type=ActionType.EMIT_EVENT,
                target="self",
                params={
                    "event_type": "gold_awarded",
                    "data": {"amount": gold},
                    "visibility": "global",
                },
            )
        )

    # reputation_changes → EMIT_EVENT (每个阵营一个事件)
    for faction, delta in (on_complete.get("reputation_changes") or {}).items():
        actions.append(
            Action(
                type=ActionType.EMIT_EVENT,
                target="self",
                params={
                    "event_type": "reputation_changed",
                    "data": {"faction": faction, "delta": delta},
                    "visibility": "global",
                },
            )
        )

    # world_flags → EMIT_EVENT (每个标记一个事件)
    for key, value in (on_complete.get("world_flags") or {}).items():
        actions.append(
            Action(
                type=ActionType.EMIT_EVENT,
                target="self",
                params={
                    "event_type": "world_flag_set",
                    "data": {"key": key, "value": value},
                    "visibility": "global",
                },
            )
        )

    return actions


def _build_outcome_reward_actions(
    outcome: Dict[str, Any], event_node_id: str,
) -> List[Action]:
    """从 EventOutcome dict 构建奖励 EMIT_EVENT actions。"""
    actions: List[Action] = []

    rewards = outcome.get("rewards", {})
    if rewards.get("xp"):
        actions.append(Action(
            type=ActionType.EMIT_EVENT, target="self",
            params={"event_type": "xp_awarded", "data": {"amount": rewards["xp"]}, "visibility": "global"},
        ))
    if rewards.get("gold"):
        actions.append(Action(
            type=ActionType.EMIT_EVENT, target="self",
            params={"event_type": "gold_awarded", "data": {"amount": rewards["gold"]}, "visibility": "global"},
        ))
    for item in (rewards.get("items") or []):
        actions.append(Action(
            type=ActionType.EMIT_EVENT, target="self",
            params={"event_type": "item_granted", "data": {"item_id": item}, "visibility": "global"},
        ))

    for faction, delta in (outcome.get("reputation_changes") or {}).items():
        actions.append(Action(
            type=ActionType.EMIT_EVENT, target="self",
            params={"event_type": "reputation_changed", "data": {"faction": faction, "delta": delta}, "visibility": "global"},
        ))

    for key, value in (outcome.get("world_flags") or {}).items():
        actions.append(Action(
            type=ActionType.EMIT_EVENT, target="self",
            params={"event_type": "world_flag_set", "data": {"key": key, "value": value}, "visibility": "global"},
        ))

    for unlock_eid in (outcome.get("unlock_events") or []):
        actions.append(Action(
            type=ActionType.EMIT_EVENT, target="self",
            params={"event_type": "event_unlocked", "data": {"event_id": unlock_eid}, "visibility": "scope"},
        ))

    return actions


def _wrap_with_guards(
    guards: List[Any], original_group: Optional[ConditionGroup]
) -> ConditionGroup:
    """将守卫条件与原始条件组合并，保留原始 operator 语义。

    如果原始组是 and，可以安全展平；否则嵌套保留 or/not 语义。
    """
    if not original_group or not original_group.conditions:
        return ConditionGroup(operator="and", conditions=guards)

    if original_group.operator == "and":
        # 安全展平
        return ConditionGroup(
            operator="and",
            conditions=guards + list(original_group.conditions),
        )
    else:
        # 嵌套保留 or/not 语义
        return ConditionGroup(
            operator="and",
            conditions=guards + [original_group],
        )


def _event_to_behaviors(
    event_data: StoryEvent, event_node_id: str, chapter_id: str
) -> List[Behavior]:
    """StoryEvent → Behavior 列表映射。

    映射规则:
      - trigger_conditions → unlock behavior (ON_TICK, once=True) + 状态守卫 (6a)
        - 空 conditions → conditions=None（永真）
      - 有 stages → 为每个有 completion_conditions 的 stage 生成推进 behavior (U4)
      - 无 stages → completion_conditions 非 None → complete behavior (U4 原有)
      - outcomes 有 conditions → 自动判定 behavior (U5)
    """
    behaviors: List[Behavior] = []

    # --- Unlock behavior (6a: 补状态守卫 + U8: activation_type 分支) ---
    unlock_guard = Condition(
        type=ConditionType.EVENT_STATE,
        params={"event_id": event_node_id, "key": "status", "value": EventStatus.LOCKED},
    )

    activation_type = getattr(event_data, "activation_type", None) or "event_driven"

    if activation_type == "npc_given":
        # 纯手动，LLM 调 activate_event，不生成 unlock behavior
        pass
    elif activation_type in ("auto_enter", "discovery"):
        # ON_ENTER trigger，无条件进入即解锁 (conditions = 仅 unlock_guard)
        unlock_actions: List[Action] = [
            Action(
                type=ActionType.CHANGE_STATE,
                target="self",
                params={"updates": {"status": EventStatus.AVAILABLE}, "merge": True},
            )
        ]
        if activation_type == "discovery":
            # 额外 NARRATIVE_HINT action：感知检定提示
            discovery_check = getattr(event_data, "discovery_check", None) or {}
            if isinstance(discovery_check, dict):
                skill = discovery_check.get("skill", "感知")
                dc = discovery_check.get("dc", 15)
            else:
                skill = "感知"
                dc = 15
            hint_text = f"[{skill}检定 DC {dc}] 附近似乎有什么隐藏的东西..."
            unlock_actions.append(
                Action(
                    type=ActionType.NARRATIVE_HINT,
                    target="self",
                    params={"text": hint_text},
                )
            )
        behaviors.append(
            Behavior(
                id=f"bh_unlock_{event_node_id}",
                trigger=TriggerType.ON_ENTER,
                conditions=ConditionGroup(operator="and", conditions=[unlock_guard]),
                actions=unlock_actions,
                once=True,
                priority=10,
            )
        )
    else:
        # event_driven / None / 其他 → 保持 ON_TICK（临时策略，待 ON_EVENT 双轨）
        unlock_conditions = _wrap_with_guards(
            [unlock_guard],
            event_data.trigger_conditions if event_data.trigger_conditions and event_data.trigger_conditions.conditions else None,
        )
        behaviors.append(
            Behavior(
                id=f"bh_unlock_{event_node_id}",
                trigger=TriggerType.ON_TICK,
                conditions=unlock_conditions,
                actions=[
                    Action(
                        type=ActionType.CHANGE_STATE,
                        target="self",
                        params={"updates": {"status": EventStatus.AVAILABLE}, "merge": True},
                    )
                ],
                once=True,
                priority=10,
            )
        )

    if event_data.stages:
        # === 有 stages 的事件 (U4) ===
        stages = event_data.stages
        for i, stage in enumerate(stages):
            next_stage_id = stages[i + 1].id if i + 1 < len(stages) else None
            is_last = (next_stage_id is None)

            if not stage.completion_conditions:
                continue  # 无条件的 stage → 靠 advance_stage 工具手动推进

            # 阶段推进守卫: 事件 ACTIVE + 当前 stage 匹配
            stage_guards: List[Any] = [
                Condition(
                    type=ConditionType.EVENT_STATE,
                    params={"event_id": event_node_id, "key": "status", "value": EventStatus.ACTIVE},
                ),
                Condition(
                    type=ConditionType.EVENT_STATE,
                    params={"event_id": event_node_id, "key": "current_stage", "value": stage.id},
                ),
            ]
            all_conditions = _wrap_with_guards(stage_guards, stage.completion_conditions)

            if is_last:
                # 最后一个 stage 完成 → 事件完成 + 副作用
                complete_actions = _build_complete_actions(event_data, event_node_id)
                behaviors.append(Behavior(
                    id=f"bh_stage_{event_node_id}_{stage.id}",
                    trigger=TriggerType.ON_TICK,
                    conditions=all_conditions,
                    actions=[
                        Action(
                            type=ActionType.CHANGE_STATE,
                            target="self",
                            params={"updates": {
                                "current_stage": "__completed__",
                                "status": EventStatus.COMPLETED,
                            }, "merge": True},
                        ),
                    ] + complete_actions,
                    once=True,
                    priority=6,
                ))
            else:
                # 推进到下一阶段
                behaviors.append(Behavior(
                    id=f"bh_stage_{event_node_id}_{stage.id}",
                    trigger=TriggerType.ON_TICK,
                    conditions=all_conditions,
                    actions=[
                        Action(
                            type=ActionType.CHANGE_STATE,
                            target="self",
                            params={"updates": {
                                "current_stage": next_stage_id,
                            }, "merge": True},
                        ),
                    ],
                    once=True,
                    priority=6,
                ))
    else:
        # === 无 stages 的事件（6a: 补状态守卫） ===
        if event_data.completion_conditions is not None:
            # 守卫: 只在 ACTIVE 状态触发
            complete_guard = Condition(
                type=ConditionType.EVENT_STATE,
                params={"event_id": event_node_id, "key": "status", "value": EventStatus.ACTIVE},
            )
            guarded_conditions = _wrap_with_guards(
                [complete_guard], event_data.completion_conditions,
            )

            complete_actions = [
                Action(
                    type=ActionType.CHANGE_STATE,
                    target="self",
                    params={"updates": {"status": EventStatus.COMPLETED}, "merge": True},
                ),
            ] + _build_complete_actions(event_data, event_node_id)

            behaviors.append(
                Behavior(
                    id=f"bh_complete_{event_node_id}",
                    trigger=TriggerType.ON_TICK,
                    conditions=guarded_conditions,
                    actions=complete_actions,
                    once=True,
                    priority=5,
                )
            )

    # === Outcome 自动判定行为 (U5) ===
    if event_data.outcomes:
        for outcome_key, outcome in event_data.outcomes.items():
            if not outcome.conditions:
                continue  # 无条件的 outcome → fallback / LLM 选择
            outcome_guards = [
                Condition(
                    type=ConditionType.EVENT_STATE,
                    params={"event_id": event_node_id, "key": "status", "value": EventStatus.COMPLETED},
                ),
                Condition(
                    type=ConditionType.EVENT_STATE,
                    params={"event_id": event_node_id, "key": "outcome", "value": None},
                ),
            ]
            outcome_conditions = _wrap_with_guards(outcome_guards, outcome.conditions)
            outcome_dict = outcome.model_dump()
            behaviors.append(Behavior(
                id=f"bh_outcome_{event_node_id}_{outcome_key}",
                trigger=TriggerType.ON_TICK,
                conditions=outcome_conditions,
                actions=[
                    Action(
                        type=ActionType.CHANGE_STATE,
                        target="self",
                        params={"updates": {"outcome": outcome_key}, "merge": True},
                    ),
                ] + _build_outcome_reward_actions(outcome_dict, event_node_id),
                once=True,
                priority=4,
            ))

    # === 超时行为 (E4/U9): 有 time_limit 时生成 ===
    if event_data.time_limit:
        behaviors.append(Behavior(
            id=f"bh_timeout_{event_node_id}",
            trigger=TriggerType.ON_TICK,
            conditions=ConditionGroup(operator="and", conditions=[
                Condition(
                    type=ConditionType.EVENT_STATE,
                    params={"event_id": event_node_id, "key": "status", "value": EventStatus.ACTIVE},
                ),
                Condition(
                    type=ConditionType.EVENT_ROUNDS_ELAPSED,
                    params={"event_id": event_node_id, "min_rounds": event_data.time_limit},
                ),
            ]),
            actions=[
                Action(
                    type=ActionType.CHANGE_STATE,
                    target="self",
                    params={"updates": {"status": EventStatus.FAILED, "failure_reason": "timeout"}, "merge": True},
                )
            ],
            once=True,
            priority=3,  # 低于 complete(5) 和 stage(6)，确保完成优先于超时
        ))

    # === 冷却重置行为 (E4/U9): is_repeatable + cooldown_rounds > 0 时生成 ===
    if event_data.is_repeatable and (event_data.cooldown_rounds or 0) > 0:
        behaviors.append(Behavior(
            id=f"bh_cooldown_{event_node_id}",
            trigger=TriggerType.ON_TICK,
            conditions=ConditionGroup(operator="and", conditions=[
                Condition(
                    type=ConditionType.EVENT_STATE,
                    params={"event_id": event_node_id, "key": "status", "value": EventStatus.COOLDOWN},
                ),
                Condition(
                    type=ConditionType.EVENT_ROUNDS_ELAPSED,
                    params={"event_id": event_node_id, "min_rounds": event_data.cooldown_rounds},
                ),
            ]),
            actions=[
                Action(
                    type=ActionType.CHANGE_STATE,
                    target="self",
                    params={"updates": {
                        "status": EventStatus.AVAILABLE,
                        "current_stage": None,
                        "stage_progress": {},
                        "objective_progress": {},
                        "outcome": None,
                        "failure_reason": None,
                    }, "merge": True},
                )
            ],
            # once=False：有 status==cooldown 守卫，转为 available 后自然不再触发
            priority=2,
        ))

    return behaviors


# =============================================================================
# GraphBuilder
# =============================================================================


class GraphBuilder:
    """从 WorldInstance 构建 WorldGraph。

    职责: 纯内存构图（同步）。不负责快照恢复（由调用方处理）。

    Usage::

        wg = GraphBuilder.build(world_instance, session_runtime)
        # 调用方可随后: wg.restore_snapshot(saved_states)
    """

    @staticmethod
    def build(
        world: WorldInstance,
        session: SessionRuntime,
        use_canonical_relationships: bool = True,
    ) -> WorldGraph:
        """7 步构建流程（Step 7 快照恢复不在此处）。

        Args:
            world: 已初始化的 WorldInstance（Firestore 静态数据）。
            session: 已 restore 的 SessionRuntime（队伍数据）。
            use_canonical_relationships: 是否构建 NPC RELATES_TO 边。

        Returns:
            填充完毕的 WorldGraph。
        """
        wg = WorldGraph()

        _build_world_root(wg, world)
        _build_chapters(wg, world)
        _build_geography(wg, world)
        _build_events(wg, world)
        _build_characters(wg, world, use_canonical_relationships)
        _build_party(wg, session)

        wg.seal()

        stats = wg.stats()
        logger.info(
            "[GraphBuilder] 构建完成: %d nodes, %d edges, types=%s",
            stats["node_count"],
            stats["edge_count"],
            stats["type_distribution"],
        )

        return wg


# =============================================================================
# Step 1: world_root
# =============================================================================


def _build_world_root(wg: WorldGraph, world: WorldInstance) -> None:
    """创建全局唯一的世界根节点。"""
    wc = world.world_constants
    props: Dict[str, Any] = {}
    if wc:
        props["description"] = wc.description
        props["setting"] = wc.setting
        props["tone"] = wc.tone
        props["background"] = wc.background
        if wc.geography:
            props["geography"] = wc.geography.model_dump(exclude_none=True)

    wg.add_node(WorldNode(
        id="world_root",
        type=WorldNodeType.WORLD,
        name=wc.name if wc else world.world_id,
        properties=props,
    ))


# =============================================================================
# Step 2: chapters
# =============================================================================


def _build_chapters(wg: WorldGraph, world: WorldInstance) -> None:
    """遍历 chapter_registry 构建章节节点、GATE 边和解锁 Behavior。"""
    for ch_id, ch_data in world.chapter_registry.items():
        # 解析为 Pydantic 模型
        try:
            chapter = Chapter(**ch_data) if isinstance(ch_data, dict) else ch_data
        except Exception as exc:
            logger.warning(
                "[GraphBuilder] 跳过无法解析的 chapter '%s': %s", ch_id, exc
            )
            continue

        # 创建 chapter 节点
        ch_props: Dict[str, Any] = {
            "name": chapter.name,
            "description": chapter.description,
            "available_maps": chapter.available_maps,
            "mainline_id": chapter.mainline_id,
            "tags": chapter.tags,
        }
        if chapter.objectives:
            ch_props["objectives"] = [
                obj.model_dump() for obj in chapter.objectives
            ]
        if chapter.pacing:
            ch_props["pacing"] = chapter.pacing.model_dump()

        if isinstance(ch_data, dict):
            status = ch_data.get("status", ChapterStatus.LOCKED)
        else:
            status = getattr(ch_data, "status", ChapterStatus.LOCKED) or ChapterStatus.LOCKED

        wg.add_node(WorldNode(
            id=ch_id,
            type=WorldNodeType.CHAPTER,
            name=chapter.name,
            properties=ch_props,
            state={"status": status},
        ))

        # CONTAINS: world_root → chapter
        wg.add_edge("world_root", ch_id, WorldEdgeType.CONTAINS.value)

    # GATE 边 + 解锁 Behavior（需要所有 chapter 节点都创建完毕）
    for ch_id, ch_data in world.chapter_registry.items():
        try:
            chapter = Chapter(**ch_data) if isinstance(ch_data, dict) else ch_data
        except Exception as exc:
            logger.warning(
                "[GraphBuilder] GATE 阶段跳过无法解析的 chapter '%s': %s",
                ch_id, exc,
            )
            continue

        for transition in chapter.transitions:
            target_ch = transition.target_chapter_id
            if not wg.has_node(target_ch):
                logger.warning(
                    "[GraphBuilder] GATE 目标 chapter '%s' 不存在，跳过",
                    target_ch,
                )
                continue

            # GATE 边: source → target
            wg.add_edge(
                ch_id,
                target_ch,
                WorldEdgeType.GATE.value,
                key=f"gate_{ch_id}_{target_ch}",
                transition_type=transition.transition_type,
                narrative_hint=transition.narrative_hint,
                priority=transition.priority,
            )

            # 目标 chapter 上挂解锁 Behavior
            target_node = wg.get_node(target_ch)
            if target_node:
                unlock_conditions: Optional[ConditionGroup] = None
                if transition.conditions and transition.conditions.conditions:
                    unlock_conditions = transition.conditions

                bh = Behavior(
                    id=f"bh_gate_{ch_id}_{target_ch}",
                    trigger=TriggerType.ON_TICK,
                    conditions=unlock_conditions,
                    actions=[
                        Action(
                            type=ActionType.CHANGE_STATE,
                            target="self",
                            params={
                                "updates": {"status": ChapterStatus.ACTIVE},
                                "merge": True,
                            },
                        )
                    ],
                    once=True,
                    priority=20,
                )
                target_node.behaviors.append(bh)


# =============================================================================
# Step 3: regions + areas + locations
# =============================================================================


def _build_geography(wg: WorldGraph, world: WorldInstance) -> None:
    """构建地理层级: region → area → location。"""

    # 收集 region 分组
    region_areas: Dict[str, List[str]] = defaultdict(list)
    for area_id, area_def in world.area_registry.items():
        region_name = area_def.region or "unknown"
        region_areas[region_name].append(area_id)

    # 创建 region 节点
    # TODO: region 节点当前仅从 area.region 字段自动聚合，properties 数据待后期补充
    for region_name in region_areas:
        region_id = _sanitize_region_id(region_name)
        wg.add_node(WorldNode(
            id=region_id,
            type=WorldNodeType.REGION,
            name=region_name,
            properties={},
        ))
        # CONTAINS: world_root → region
        wg.add_edge("world_root", region_id, WorldEdgeType.CONTAINS.value)

    # 创建 area 节点
    for area_id, area_def in world.area_registry.items():
        region_name = area_def.region or "unknown"
        region_id = _sanitize_region_id(region_name)

        area_props: Dict[str, Any] = {
            "description": area_def.description,
            "danger_level": area_def.danger_level,
            "area_type": area_def.area_type,
            "atmosphere": area_def.ambient_description,
            "key_features": area_def.key_features,
            "tags": area_def.tags,
            "available_actions": area_def.available_actions,
        }

        wg.add_node(WorldNode(
            id=area_id,
            type=WorldNodeType.AREA,
            name=area_def.name,
            properties=area_props,
            state={"visited": False, "visit_count": 0},
        ))

        # CONTAINS: region → area
        wg.add_edge(region_id, area_id, WorldEdgeType.CONTAINS.value)

        # Sub-locations
        for sub_loc in area_def.sub_locations:
            loc_id = f"loc_{area_id}_{sub_loc.id}"
            loc_props: Dict[str, Any] = {
                "description": sub_loc.description,
                "interaction_type": sub_loc.interaction_type,
                "available_actions": sub_loc.available_actions,
                "passerby_spawn_rate": sub_loc.passerby_spawn_rate,
            }

            wg.add_node(WorldNode(
                id=loc_id,
                type=WorldNodeType.LOCATION,
                name=sub_loc.name,
                properties=loc_props,
            ))

            # CONTAINS: area → location
            wg.add_edge(area_id, loc_id, WorldEdgeType.CONTAINS.value)

    # CONNECTS 边: area ↔ area
    for area_id, area_def in world.area_registry.items():
        for conn in area_def.connections:
            target_area = conn.target_area_id
            if not wg.has_node(target_area):
                logger.debug(
                    "[GraphBuilder] CONNECTS 目标 area '%s' 不存在，跳过",
                    target_area,
                )
                continue

            # add_edge 会自动加反向边，所以需要同时检查正向和反向 key
            edge_key = f"conn_{area_id}_{target_area}"
            reverse_key = f"conn_{target_area}_{area_id}"
            # 检查是否已有此连接（避免重复：对向 area 也会尝试添加）
            if (wg.get_edge(area_id, target_area, edge_key)
                    or wg.get_edge(area_id, target_area, reverse_key)
                    or wg.get_edge(target_area, area_id, reverse_key)
                    or wg.get_edge(target_area, area_id, edge_key)):
                continue

            edge_kwargs: Dict[str, Any] = {
                "key": edge_key,
                "connection_type": conn.connection_type,
                "travel_time": conn.travel_time,
                "requirements": conn.requirements,
            }
            # P8: 补传 description（构图时遗漏的字段）
            if getattr(conn, "description", None):
                edge_kwargs["description"] = conn.description
            wg.add_edge(
                area_id,
                target_area,
                WorldEdgeType.CONNECTS.value,
                **edge_kwargs,
            )


# =============================================================================
# Step 4: events
# =============================================================================


def _build_events(wg: WorldGraph, world: WorldInstance) -> None:
    """构建事件节点 + HAS_EVENT 边 + Behavior 映射。"""
    for ch_id, ch_data in world.chapter_registry.items():
        try:
            chapter = Chapter(**ch_data) if isinstance(ch_data, dict) else ch_data
        except Exception:
            continue

        for event in chapter.events:
            event_node_id = event.id

            # U21: 属性透传（sparse，None/空值不写入）
            evt_props: Dict[str, Any] = {
                "chapter_id": ch_id,
                "narrative_directive": event.narrative_directive,
                "is_required": event.is_required,
                "is_repeatable": event.is_repeatable,
                # TODO(pipeline): is_required → main 的耦合是过渡方案。
                # is_required 是机械约束（章节推进依赖），importance 是叙事呈现力度，
                # 两者概念不同不应耦合。待管线提取 importance 字段后（当前覆盖率 0%），
                # 改为直接使用 event.importance，不再自动升级。
                "importance": event.importance if event.importance != "side" else (
                    "main" if event.is_required else "side"
                ),
            }
            # 可选字段: 非空才写入
            for key in (
                "description", "on_complete", "activation_type", "visibility",
                "quest_giver", "time_limit", "discovery_check", "recommended_level",
                "cooldown_rounds",  # E4: 补丁，之前漏传
            ):
                val = getattr(event, key, None)
                if val is not None and val != "" and val != []:
                    evt_props[key] = val
            # completion_conditions 序列化
            if event.completion_conditions:
                evt_props["completion_conditions"] = event.completion_conditions.model_dump()
            # stages 序列化 (U4)
            if event.stages:
                evt_props["stages"] = [s.model_dump() for s in event.stages]
            # outcomes 序列化 (U5)
            if event.outcomes:
                evt_props["outcomes"] = {k: v.model_dump() for k, v in event.outcomes.items()}

            # U22: 状态初始化
            evt_state: Dict[str, Any] = {
                "status": EventStatus.LOCKED,
                "current_stage": None,
                "stage_progress": {},
                "objective_progress": {},
                "outcome": None,
                "activated_at_round": None,
                "rounds_elapsed": 0,
                "failure_reason": None,  # E4: 失败原因（超时/手动）
            }

            # Behavior 映射
            behaviors = _event_to_behaviors(event, event_node_id, ch_id)

            wg.add_node(WorldNode(
                id=event_node_id,
                type=WorldNodeType.EVENT_DEF,
                name=event.name,
                properties=evt_props,
                state=evt_state,
                behaviors=behaviors,
            ))

            # HAS_EVENT 地理锚点：挂载到章节的所有 available_maps
            # 确保玩家在章节任意区域都能通过 find_events_in_scope 看到该事件
            if chapter.available_maps:
                anchored = False
                for anchor_area in chapter.available_maps:
                    if wg.has_node(anchor_area):
                        wg.add_edge(
                            anchor_area,
                            event_node_id,
                            WorldEdgeType.HAS_EVENT.value,
                            key=f"has_event_{event_node_id}_{anchor_area}",
                        )
                        anchored = True
                    else:
                        logger.debug(
                            "[GraphBuilder] 事件 '%s' 锚点 area '%s' 不存在，跳过",
                            event_node_id,
                            anchor_area,
                        )
                if not anchored:
                    logger.warning(
                        "[GraphBuilder] 事件 '%s' 无可用锚点（available_maps=%s 均不存在）",
                        event_node_id,
                        chapter.available_maps,
                    )
            # else: 无地理锚点的事件，通过 properties.chapter_id 关联
            # TODO: 无地理锚点的事件，后续可通过数据补充 area 映射


# =============================================================================
# Step 5: characters (NPCs)
# =============================================================================


def _build_characters(
    wg: WorldGraph,
    world: WorldInstance,
    use_canonical_relationships: bool,
) -> None:
    """构建 NPC 节点 + HOSTS 边。"""
    for char_id, char_data in world.character_registry.items():
        default_map = _extract_char_field(char_data, "default_map")
        default_sub = _extract_char_field(char_data, "default_sub_location")
        tier = _extract_char_field(char_data, "tier", "secondary")

        # 提取 profile 字段
        profile = char_data.get("profile", {})
        if not isinstance(profile, dict):
            profile = {}

        char_props: Dict[str, Any] = {
            "tier": tier,
        }
        # 从 profile 提取常用字段
        for field in (
            "personality", "backstory", "occupation",
            "speech_pattern", "appearance", "aliases", "tags",
        ):
            val = profile.get(field)
            if val:
                char_props[field] = val

        # NPC 默认 state + 位置
        npc_state = default_npc_state()
        if default_map:
            if default_sub:
                npc_state["current_location"] = f"loc_{default_map}_{default_sub}"
            else:
                npc_state["current_location"] = default_map

        npc_name = (
            profile.get("name")
            or char_data.get("name")
            or char_id
        )

        wg.add_node(WorldNode(
            id=char_id,
            type=WorldNodeType.NPC,
            name=npc_name,
            properties=char_props,
            state=npc_state,
        ))

        # HOSTS 边: location/area → npc
        if default_map:
            if default_sub:
                host_id = f"loc_{default_map}_{default_sub}"
                if wg.has_node(host_id):
                    wg.add_edge(
                        host_id,
                        char_id,
                        WorldEdgeType.HOSTS.value,
                        key=f"hosts_{char_id}",
                    )
                elif wg.has_node(default_map):
                    # fallback: 子地点不存在，挂到 area + 修正 state
                    wg.add_edge(
                        default_map,
                        char_id,
                        WorldEdgeType.HOSTS.value,
                        key=f"hosts_{char_id}",
                    )
                    npc_node = wg.get_node(char_id)
                    if npc_node:
                        npc_node.state["current_location"] = default_map
                    logger.debug(
                        "[GraphBuilder] NPC '%s' 的子地点 '%s' 不存在，"
                        "回退到 area '%s'",
                        char_id, host_id, default_map,
                    )
                else:
                    logger.warning(
                        "[GraphBuilder] NPC '%s' 的 default_map '%s' 不存在",
                        char_id, default_map,
                    )
            else:
                if wg.has_node(default_map):
                    wg.add_edge(
                        default_map,
                        char_id,
                        WorldEdgeType.HOSTS.value,
                        key=f"hosts_{char_id}",
                    )
                else:
                    logger.warning(
                        "[GraphBuilder] NPC '%s' 的 default_map '%s' 不存在",
                        char_id, default_map,
                    )
        else:
            logger.debug(
                "[GraphBuilder] NPC '%s' 无 default_map，不创建 HOSTS 边",
                char_id,
            )

    # RELATES_TO 边（条件构建）
    # TODO: 当前 relationships 数据混合了世界观背景和小说剧情关系，
    #       后期需在源数据中区分。use_canonical_relationships 参数控制是否构建。
    if use_canonical_relationships:
        _build_npc_relationships(wg, world.character_registry)


def _build_npc_relationships(
    wg: WorldGraph,
    character_registry: Dict[str, Dict[str, Any]],
) -> None:
    """构建 NPC 间的 RELATES_TO 边。"""
    for char_id, char_data in character_registry.items():
        if not wg.has_node(char_id):
            continue

        # relationships 可能在 profile 或顶层
        profile = char_data.get("profile", {})
        if not isinstance(profile, dict):
            profile = {}

        relationships = profile.get("relationships") or char_data.get("relationships")
        if not relationships:
            continue

        if isinstance(relationships, list):
            for rel in relationships:
                if not isinstance(rel, dict):
                    continue
                target_id = rel.get("character_id") or rel.get("target")
                if not target_id or not wg.has_node(target_id):
                    continue
                rel_type = rel.get("type", "knows")
                wg.add_edge(
                    char_id,
                    target_id,
                    WorldEdgeType.RELATES_TO.value,
                    key=f"rel_{char_id}_{target_id}_{rel_type}",
                    relationship_type=rel_type,
                    description=rel.get("description", ""),
                )
        elif isinstance(relationships, dict):
            for target_id, rel_info in relationships.items():
                if not wg.has_node(target_id):
                    continue
                if isinstance(rel_info, str):
                    wg.add_edge(
                        char_id,
                        target_id,
                        WorldEdgeType.RELATES_TO.value,
                        key=f"rel_{char_id}_{target_id}",
                        relationship_type=rel_info,
                    )
                elif isinstance(rel_info, dict):
                    rel_type = rel_info.get("type", "knows")
                    wg.add_edge(
                        char_id,
                        target_id,
                        WorldEdgeType.RELATES_TO.value,
                        key=f"rel_{char_id}_{target_id}_{rel_type}",
                        relationship_type=rel_type,
                        description=rel_info.get("description", ""),
                    )


# =============================================================================
# Step 6: party (camp + teammates)
# =============================================================================


def _build_party(wg: WorldGraph, session: SessionRuntime) -> None:
    """构建营地节点 + 玩家节点 + 队友 MEMBER_OF 边。"""
    # Camp 节点
    wg.add_node(WorldNode(
        id="camp",
        type=WorldNodeType.CAMP,
        name="Camp",
        properties={},
    ))
    wg.add_edge("world_root", "camp", WorldEdgeType.CONTAINS.value)

    # Player 节点（U2: 图为唯一运行时真理源）
    _build_player_node(wg, session)

    # 队友 MEMBER_OF 边
    if not session.party:
        return

    for member in session.party.get_active_members():
        if wg.has_node(member.character_id):
            wg.add_edge(
                member.character_id,
                "camp",
                WorldEdgeType.MEMBER_OF.value,
                key=f"member_{member.character_id}",
            )
        else:
            logger.warning(
                "[GraphBuilder] 队友 '%s' (%s) 的 NPC 节点不存在，"
                "跳过 MEMBER_OF 边",
                member.name,
                member.character_id,
            )


def _build_player_node(wg: WorldGraph, session: SessionRuntime) -> None:
    """构建玩家图节点 + MEMBER_OF(player→camp) + HOSTS(location→player) 边。"""
    pc = getattr(session, "_player_character", None)
    if pc is None:
        logger.debug("[GraphBuilder] 无 PlayerCharacter，跳过 player 节点")
        return

    from app.world.player_node import translate_character_to_node

    state, props = translate_character_to_node(pc)

    # 设置当前位置
    player_location = session.player_location
    if player_location:
        state["current_location"] = player_location

    wg.add_node(WorldNode(
        id="player",
        type=WorldNodeType.PLAYER,
        name=pc.name,
        properties=props,
        state=state,
    ))

    # MEMBER_OF: player → camp
    wg.add_edge(
        "player",
        "camp",
        WorldEdgeType.MEMBER_OF.value,
        key="member_player",
    )

    # HOSTS: location/area → player
    if player_location:
        sub_location = session.sub_location
        host_id = f"loc_{player_location}_{sub_location}" if sub_location else player_location
        if wg.has_node(host_id):
            wg.add_edge(
                host_id,
                "player",
                WorldEdgeType.HOSTS.value,
                key="hosts_player",
            )
        elif player_location and wg.has_node(player_location):
            wg.add_edge(
                player_location,
                "player",
                WorldEdgeType.HOSTS.value,
                key="hosts_player",
            )
        else:
            logger.debug(
                "[GraphBuilder] Player 位置 '%s' 不存在，跳过 HOSTS 边",
                host_id,
            )
