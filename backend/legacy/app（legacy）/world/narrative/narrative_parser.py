"""narrative_parser.py — 叙事数据解析函数（纯函数，无状态依赖）。"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.models.narrative import (
    Chapter,
    ChapterObjective,
    Condition,
    ConditionGroup,
    ConditionType,
    Mainline,
    PacingConfig,
    StoryEvent,
    ChapterTransition,
)

logger = logging.getLogger(__name__)


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def merge_unique_items(items: List[str]) -> List[str]:
    """保持顺序去重。"""
    seen = set()
    result: List[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def normalize_condition_dict(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return dict(raw)


def normalize_completion_conditions(raw: Any) -> Dict[str, Any]:
    """统一 completion_conditions 字段，确保 events_required 为字符串数组。"""
    result = normalize_condition_dict(raw)

    events_required = result.get("events_required")
    if isinstance(events_required, list):
        result["events_required"] = [
            str(event_id).strip()
            for event_id in events_required
            if str(event_id).strip()
        ]
        return result

    event_id = result.get("event_id")
    if isinstance(event_id, str) and event_id.strip():
        result["events_required"] = [event_id.strip()]
        return result

    event_ids = result.get("event_ids")
    if isinstance(event_ids, list):
        result["events_required"] = [
            str(item).strip() for item in event_ids if str(item).strip()
        ]
        return result

    return result


def parse_objectives(chapter_id: str, raw_objectives: Any) -> List[ChapterObjective]:
    """兼容 objectives 的两种格式：[{id,description}] 或 [\"text\", ...]。"""
    if not isinstance(raw_objectives, list):
        return []

    objectives: List[ChapterObjective] = []
    for idx, raw_obj in enumerate(raw_objectives, start=1):
        obj_id = f"{chapter_id}_obj_{idx}"
        description = ""
        completed = False
        completed_at = None

        if isinstance(raw_obj, str):
            description = raw_obj.strip()
        elif isinstance(raw_obj, dict):
            obj_id = str(raw_obj.get("id") or obj_id)
            description = str(raw_obj.get("description") or raw_obj.get("text") or "").strip()
            completed = bool(raw_obj.get("completed", False))
            raw_completed_at = raw_obj.get("completed_at")
            if isinstance(raw_completed_at, str) and raw_completed_at.strip():
                try:
                    completed_at = datetime.fromisoformat(raw_completed_at)
                except ValueError:
                    completed_at = None

        if not description:
            continue

        objectives.append(
            ChapterObjective(
                id=obj_id,
                description=description,
                completed=completed,
                completed_at=completed_at,
            )
        )

    return objectives


def parse_condition(raw: Any) -> Optional[Condition]:
    """解析单个条件。"""
    if not isinstance(raw, dict):
        return None
    ctype = raw.get("type", "")
    try:
        cond_type = ConditionType(ctype)
    except ValueError:
        return None
    return Condition(type=cond_type, params=raw.get("params", {}))


def parse_condition_group(raw: Any) -> Optional[ConditionGroup]:
    """解析条件组（递归）。"""
    if not isinstance(raw, dict):
        return None
    operator = raw.get("operator", "and")
    if operator not in ("and", "or", "not"):
        operator = "and"
    conditions = []
    for item in raw.get("conditions", []):
        if not isinstance(item, dict):
            continue
        if "operator" in item and "conditions" in item:
            nested = parse_condition_group(item)
            if nested:
                conditions.append(nested)
        else:
            cond = parse_condition(item)
            if cond:
                conditions.append(cond)
    return ConditionGroup(operator=operator, conditions=conditions)


def parse_story_events(chapter_id: str, raw_events: Any) -> List[StoryEvent]:
    """解析 StoryEvent 列表。"""
    if not isinstance(raw_events, list):
        return []
    events: List[StoryEvent] = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        event_id = str(raw.get("id", "")).strip()
        if not event_id:
            continue
        trigger_cg = parse_condition_group(raw.get("trigger_conditions"))
        completion_cg = parse_condition_group(raw.get("completion_conditions"))
        on_complete_raw = raw.get("on_complete")
        on_complete = on_complete_raw if isinstance(on_complete_raw, dict) else None

        # on_complete fallback: 从 side_effects 映射
        if on_complete is None and isinstance(raw.get("side_effects"), list) and raw["side_effects"]:
            mapped: Dict[str, Any] = {}
            for effect in raw["side_effects"]:
                if not isinstance(effect, dict):
                    continue
                etype = effect.get("type", "")
                if etype == "unlock_event" and "event_id" in effect:
                    mapped.setdefault("unlock_events", []).append(effect["event_id"])
                elif etype == "add_item":
                    mapped.setdefault("add_items", []).append({
                        "item_id": effect.get("item_id", ""),
                        "quantity": effect.get("quantity", 1),
                    })
                elif etype == "add_xp":
                    mapped["add_xp"] = mapped.get("add_xp", 0) + int(effect.get("amount", 0))
            if mapped:
                on_complete = mapped

        events.append(StoryEvent(
            id=event_id,
            name=str(raw.get("name", event_id)),
            description=str(raw.get("description", "")),
            trigger_conditions=trigger_cg or ConditionGroup(),
            completion_conditions=completion_cg,
            on_complete=on_complete,
            is_required=bool(raw.get("is_required", False)),
            is_repeatable=bool(raw.get("is_repeatable", False)),
            cooldown_rounds=int(raw.get("cooldown_rounds", 0)),
            narrative_directive=str(raw.get("narrative_directive", "")),
            side_effects=raw.get("side_effects", []) if isinstance(raw.get("side_effects"), list) else [],
        ))
    return events


def parse_chapter_transitions(raw_transitions: Any) -> List[ChapterTransition]:
    """解析 ChapterTransition 列表。"""
    if not isinstance(raw_transitions, list):
        return []
    transitions: List[ChapterTransition] = []
    for raw in raw_transitions:
        if not isinstance(raw, dict):
            continue
        target = str(raw.get("target_chapter_id", "")).strip()
        if not target:
            continue
        cg = parse_condition_group(raw.get("conditions"))
        ttype = raw.get("transition_type", "normal")
        if ttype not in ("normal", "branch", "failure", "skip"):
            ttype = "normal"
        transitions.append(ChapterTransition(
            target_chapter_id=target,
            conditions=cg or ConditionGroup(),
            priority=int(raw.get("priority", 0)),
            transition_type=ttype,
            narrative_hint=str(raw.get("narrative_hint", "")),
        ))
    return transitions


def parse_pacing_config(raw: Any) -> PacingConfig:
    """解析 PacingConfig。"""
    if not isinstance(raw, dict):
        return PacingConfig()
    return PacingConfig(
        min_rounds=int(raw.get("min_rounds", 3)),
        ideal_rounds=int(raw.get("ideal_rounds", 10)),
        max_rounds=int(raw.get("max_rounds", 30)),
        stall_threshold=int(raw.get("stall_threshold", 5)),
        hint_escalation=raw.get("hint_escalation", [
            "subtle_environmental", "npc_reminder", "direct_prompt", "forced_event",
        ]),
    )


def parse_chapter(raw: Any) -> Optional[Chapter]:
    if not isinstance(raw, dict):
        return None

    chapter_id = str(raw.get("id") or "").strip()
    if not chapter_id:
        return None

    # 章节类型过滤：跳过 metadata 和 volume_index 类型
    chapter_type = str(raw.get("type") or "story").strip().lower()
    if chapter_type in ("metadata", "volume_index"):
        return None

    mainline_id = str(raw.get("mainline_id") or "default").strip() or "default"
    name = str(raw.get("name") or chapter_id).strip()
    description = str(raw.get("description") or "").strip()
    available_maps_raw = raw.get("available_maps") or []
    available_maps = merge_unique_items(
        [str(map_id) for map_id in available_maps_raw if str(map_id).strip()]
    )

    # v2 新增字段
    raw_events = raw.get("events", [])
    events = parse_story_events(chapter_id, raw_events)
    transitions = parse_chapter_transitions(raw.get("transitions", []))
    pacing = parse_pacing_config(raw.get("pacing"))
    entry_conditions = parse_condition_group(raw.get("entry_conditions"))
    tags = [str(t) for t in raw.get("tags", []) if isinstance(t, str)]

    completion_conditions = normalize_completion_conditions(raw.get("completion_conditions"))

    # strict-v2：story 章节必须显式携带 v2 events
    if chapter_type == "story" and not events:
        raise ValueError(
            f"章节 '{chapter_id}' 缺少有效 v2 events，"
            "strict-v2 模式下禁止 legacy fallback"
        )

    return Chapter(
        id=chapter_id,
        mainline_id=mainline_id,
        name=name,
        description=description,
        type=chapter_type,
        objectives=parse_objectives(chapter_id, raw.get("objectives", [])),
        available_maps=available_maps,
        trigger_conditions=normalize_condition_dict(raw.get("trigger_conditions")),
        completion_conditions=completion_conditions,
        events=events,
        transitions=transitions,
        pacing=pacing,
        entry_conditions=entry_conditions,
        tags=tags,
    )


def merge_chapter(existing: Chapter, incoming: Chapter) -> Chapter:
    """当 chapter id 冲突时，合并两份数据。"""
    description = existing.description
    if len(incoming.description) > len(description):
        description = incoming.description

    objectives = existing.objectives
    if len(incoming.objectives) > len(objectives):
        objectives = incoming.objectives

    available_maps = merge_unique_items(existing.available_maps + incoming.available_maps)
    trigger_conditions = {**existing.trigger_conditions, **incoming.trigger_conditions}
    completion_conditions = {**existing.completion_conditions, **incoming.completion_conditions}

    # v2 字段：取更丰富的一方
    events = incoming.events if len(incoming.events) >= len(existing.events) else existing.events
    transitions = incoming.transitions if len(incoming.transitions) >= len(existing.transitions) else existing.transitions
    pacing = incoming.pacing if incoming.pacing != PacingConfig() else existing.pacing
    entry_conditions = incoming.entry_conditions or existing.entry_conditions
    tags = merge_unique_items(existing.tags + incoming.tags)

    return Chapter(
        id=existing.id,
        mainline_id=incoming.mainline_id or existing.mainline_id,
        name=incoming.name or existing.name,
        description=description,
        objectives=objectives,
        available_maps=available_maps,
        trigger_conditions=trigger_conditions,
        completion_conditions=completion_conditions,
        events=events,
        transitions=transitions,
        pacing=pacing,
        entry_conditions=entry_conditions,
        tags=tags,
    )


def parse_mainline(
    raw: Any,
    chapters: Dict[str, Chapter],
) -> Optional[Mainline]:
    if not isinstance(raw, dict):
        return None

    mainline_id = str(raw.get("id") or "").strip()
    if not mainline_id:
        return None

    name = str(raw.get("name") or mainline_id).strip()
    description = str(raw.get("description") or "").strip()

    chapter_refs = raw.get("chapters") if isinstance(raw.get("chapters"), list) else []
    chapter_ids: List[str] = []
    for ref in chapter_refs:
        if not isinstance(ref, str):
            continue
        chapter_id = ref.strip()
        if chapter_id and chapter_id in chapters:
            chapter_ids.append(chapter_id)

    if not chapter_ids:
        # 兜底：按章节声明的 mainline_id 聚合
        for chapter in chapters.values():
            if chapter.mainline_id == mainline_id:
                chapter_ids.append(chapter.id)

    # v2: 解析 chapter_graph（DAG 结构）
    chapter_graph: Dict[str, List[str]] = {}
    raw_graph = raw.get("chapter_graph")
    if isinstance(raw_graph, dict):
        for src_id, targets in raw_graph.items():
            src_id = str(src_id).strip()
            if not src_id:
                continue
            if isinstance(targets, list):
                valid_targets = [
                    str(t).strip() for t in targets
                    if isinstance(t, str) and str(t).strip() in chapters
                ]
                if valid_targets:
                    chapter_graph[src_id] = valid_targets

    return Mainline(
        id=mainline_id,
        name=name,
        description=description,
        chapters=merge_unique_items(chapter_ids),
        chapter_graph=chapter_graph,
    )
