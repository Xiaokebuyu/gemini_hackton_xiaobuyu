"""event_behaviors.py — StoryEvent → Behavior 列表映射。

从 GraphBuilder 拆出的纯转换逻辑，将叙事事件定义转换为可执行的 Behavior 列表。
仅被 builder._build_events() 调用。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.models.narrative import (
    Condition,
    ConditionGroup,
    ConditionType,
    StoryEvent,
)
from app.world.graph.models import (
    Action,
    ActionType,
    Behavior,
    EventStatus,
    TriggerType,
)

logger = logging.getLogger(__name__)


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
