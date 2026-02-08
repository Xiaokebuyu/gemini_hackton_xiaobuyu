"""
Narrative Service - 主线叙事管理服务

管理 主线 -> 章节 -> 地图 -> 地点 四层结构。
进度存储在 GameSessionState.metadata.narrative
"""
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from google.cloud import firestore

from app.config import settings
from app.models.narrative import (
    Chapter,
    ChapterObjective,
    Condition,
    ConditionGroup,
    ConditionType,
    Mainline,
    NarrativeProgress,
    PacingConfig,
    StoryEvent,
    ChapterTransition,
)
from app.services.game_session_store import GameSessionStore

logger = logging.getLogger(__name__)


class NarrativeService:
    """
    主线叙事管理服务

    职责：
    - 加载主线/章节数据
    - 管理叙事进度
    - 检查地图解锁状态
    - 处理章节完成和推进
    """

    def __init__(self, session_store: Optional[GameSessionStore] = None):
        """
        初始化叙事服务

        Args:
            session_store: 会话存储服务
        """
        self._session_store = session_store or GameSessionStore()
        self._db = getattr(
            self._session_store,
            "db",
            firestore.Client(database=settings.firestore_database),
        )
        self._mainlines_by_world: Dict[str, Dict[str, Mainline]] = {}
        self._chapters_by_world: Dict[str, Dict[str, Chapter]] = {}
        self._loaded_worlds: set = set()

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _load_narrative_payload_from_firestore(
        self,
        world_id: str,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        world_ref = self._db.collection("worlds").document(world_id)

        chapters_raw: List[Dict[str, Any]] = []
        for chapter_doc in world_ref.collection("chapters").stream():
            payload = chapter_doc.to_dict() or {}
            if not isinstance(payload, dict):
                continue

            chapter_data = dict(payload)
            chapter_data["id"] = str(chapter_data.get("id") or chapter_doc.id).strip()
            if not chapter_data["id"]:
                continue

            if "available_maps" not in chapter_data and isinstance(
                chapter_data.get("available_areas"), list
            ):
                chapter_data["available_maps"] = chapter_data.get("available_areas", [])

            mainline_id = str(chapter_data.get("mainline_id") or "").strip()
            chapter_data["mainline_id"] = mainline_id or "default"
            chapters_raw.append(chapter_data)

        if not chapters_raw:
            raise ValueError(
                f"世界 '{world_id}' 缺少章节叙事数据（Firestore: worlds/{world_id}/chapters）"
            )

        mainlines_raw: List[Dict[str, Any]] = []
        for mainline_doc in world_ref.collection("mainlines").stream():
            payload = mainline_doc.to_dict() or {}
            if not isinstance(payload, dict):
                continue
            mainline_data = dict(payload)
            mainline_data["id"] = str(mainline_data.get("id") or mainline_doc.id).strip()
            if not mainline_data["id"]:
                continue
            mainlines_raw.append(mainline_data)

        if mainlines_raw:
            return mainlines_raw, chapters_raw

        grouped: Dict[str, Dict[str, Any]] = {}
        for chapter_data in chapters_raw:
            chapter_id = str(chapter_data.get("id") or "").strip()
            if not chapter_id:
                continue
            mainline_id = str(chapter_data.get("mainline_id") or "default").strip() or "default"
            group = grouped.setdefault(
                mainline_id,
                {
                    "id": mainline_id,
                    "name": str(chapter_data.get("mainline_name") or mainline_id),
                    "description": "",
                    "_chapters": [],
                },
            )
            group["_chapters"].append(
                (
                    self._safe_int(chapter_data.get("order"), 0),
                    chapter_id,
                )
            )

        if not grouped:
            raise ValueError(
                f"世界 '{world_id}' 缺少主线叙事数据（Firestore: worlds/{world_id}/mainlines）"
            )

        for group in grouped.values():
            ordered = sorted(group.pop("_chapters"), key=lambda item: (item[0], item[1]))
            group["chapters"] = [chapter_id for _, chapter_id in ordered]
            mainlines_raw.append(group)

        return mainlines_raw, chapters_raw

    async def load_narrative_data(self, world_id: str) -> None:
        """
        加载主线数据

        从 Firestore 加载（worlds/{world_id}/chapters 与 worlds/{world_id}/mainlines）

        Args:
            world_id: 世界ID
        """
        if world_id in self._loaded_worlds:
            return

        mainlines: Dict[str, Mainline] = {}
        chapters: Dict[str, Chapter] = {}
        mainlines_raw, chapters_raw = self._load_narrative_payload_from_firestore(world_id)

        # 先解析章节，确保主线的 chapter 引用可以校验
        for ch_data in chapters_raw:
            chapter = self._parse_chapter(ch_data)
            if not chapter:
                continue
            if chapter.id in chapters:
                chapters[chapter.id] = self._merge_chapter(chapters[chapter.id], chapter)
            else:
                chapters[chapter.id] = chapter

        # 解析主线并做去重/过滤
        for ml_data in mainlines_raw:
            mainline = self._parse_mainline(ml_data, chapters)
            if not mainline:
                continue
            mainlines[mainline.id] = mainline

        # 兜底：如果未提供 mainlines，但有 chapters，则按 chapter.mainline_id 聚合
        if not mainlines and chapters:
            grouped: Dict[str, List[str]] = {}
            for chapter in chapters.values():
                grouped.setdefault(chapter.mainline_id or "default", []).append(chapter.id)
            for mainline_id, chapter_ids in grouped.items():
                mainlines[mainline_id] = Mainline(
                    id=mainline_id,
                    name=mainline_id,
                    description="",
                    chapters=self._merge_unique_items(chapter_ids),
                )

        if not chapters:
            raise ValueError(
                f"世界 '{world_id}' 没有可用 story 章节数据（metadata/volume_index 已过滤）"
            )
        if not mainlines:
            raise ValueError(
                f"世界 '{world_id}' 没有可用主线数据（Firestore: worlds/{world_id}/mainlines）"
            )

        self._mainlines_by_world[world_id] = mainlines
        self._chapters_by_world[world_id] = chapters
        self._loaded_worlds.add(world_id)

    @staticmethod
    def _merge_unique_items(items: List[str]) -> List[str]:
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

    def _parse_objectives(self, chapter_id: str, raw_objectives: Any) -> List[ChapterObjective]:
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

    @staticmethod
    def _normalize_condition_dict(raw: Any) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        return dict(raw)

    def _normalize_completion_conditions(self, raw: Any) -> Dict[str, Any]:
        """统一 completion_conditions 字段，确保 events_required 为字符串数组。"""
        result = self._normalize_condition_dict(raw)

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

    def _parse_chapter(self, raw: Any) -> Optional[Chapter]:
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
        available_maps = self._merge_unique_items(
            [str(map_id) for map_id in available_maps_raw if str(map_id).strip()]
        )

        # v2 新增字段
        raw_events = raw.get("events", [])
        events = self._parse_story_events(chapter_id, raw_events)
        transitions = self._parse_chapter_transitions(raw.get("transitions", []))
        pacing = self._parse_pacing_config(raw.get("pacing"))
        entry_conditions = self._parse_condition_group(raw.get("entry_conditions"))
        tags = [str(t) for t in raw.get("tags", []) if isinstance(t, str)]

        completion_conditions = self._normalize_completion_conditions(raw.get("completion_conditions"))

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
            objectives=self._parse_objectives(chapter_id, raw.get("objectives", [])),
            available_maps=available_maps,
            trigger_conditions=self._normalize_condition_dict(raw.get("trigger_conditions")),
            completion_conditions=completion_conditions,
            events=events,
            transitions=transitions,
            pacing=pacing,
            entry_conditions=entry_conditions,
            tags=tags,
        )

    def _merge_chapter(self, existing: Chapter, incoming: Chapter) -> Chapter:
        """当 chapter id 冲突时，合并两份数据。"""
        description = existing.description
        if len(incoming.description) > len(description):
            description = incoming.description

        objectives = existing.objectives
        if len(incoming.objectives) > len(objectives):
            objectives = incoming.objectives

        available_maps = self._merge_unique_items(existing.available_maps + incoming.available_maps)
        trigger_conditions = {**existing.trigger_conditions, **incoming.trigger_conditions}
        completion_conditions = {**existing.completion_conditions, **incoming.completion_conditions}

        # v2 字段：取更丰富的一方
        events = incoming.events if len(incoming.events) >= len(existing.events) else existing.events
        transitions = incoming.transitions if len(incoming.transitions) >= len(existing.transitions) else existing.transitions
        pacing = incoming.pacing if incoming.pacing != PacingConfig() else existing.pacing
        entry_conditions = incoming.entry_conditions or existing.entry_conditions
        tags = self._merge_unique_items(existing.tags + incoming.tags)

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

    def _parse_mainline(
        self,
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
            chapters=self._merge_unique_items(chapter_ids),
            chapter_graph=chapter_graph,
        )

    # ----- v2 字段解析辅助方法 -----

    @staticmethod
    def _parse_condition(raw: Any) -> Optional[Condition]:
        """解析单个条件。"""
        if not isinstance(raw, dict):
            return None
        ctype = raw.get("type", "")
        try:
            cond_type = ConditionType(ctype)
        except ValueError:
            return None
        return Condition(type=cond_type, params=raw.get("params", {}))

    @classmethod
    def _parse_condition_group(cls, raw: Any) -> Optional[ConditionGroup]:
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
                nested = cls._parse_condition_group(item)
                if nested:
                    conditions.append(nested)
            else:
                cond = cls._parse_condition(item)
                if cond:
                    conditions.append(cond)
        return ConditionGroup(operator=operator, conditions=conditions)

    @classmethod
    def _parse_story_events(cls, chapter_id: str, raw_events: Any) -> List[StoryEvent]:
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
            trigger_cg = cls._parse_condition_group(raw.get("trigger_conditions"))
            events.append(StoryEvent(
                id=event_id,
                name=str(raw.get("name", event_id)),
                description=str(raw.get("description", "")),
                trigger_conditions=trigger_cg or ConditionGroup(),
                is_required=bool(raw.get("is_required", False)),
                is_repeatable=bool(raw.get("is_repeatable", False)),
                cooldown_rounds=int(raw.get("cooldown_rounds", 0)),
                narrative_directive=str(raw.get("narrative_directive", "")),
                side_effects=raw.get("side_effects", []) if isinstance(raw.get("side_effects"), list) else [],
            ))
        return events

    @classmethod
    def _parse_chapter_transitions(cls, raw_transitions: Any) -> List[ChapterTransition]:
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
            cg = cls._parse_condition_group(raw.get("conditions"))
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

    @staticmethod
    def _parse_pacing_config(raw: Any) -> PacingConfig:
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

    def _world_mainlines(self, world_id: str) -> Dict[str, Mainline]:
        return self._mainlines_by_world.get(world_id, {})

    def _world_chapters(self, world_id: str) -> Dict[str, Chapter]:
        return self._chapters_by_world.get(world_id, {})

    def _resolve_default_progress(self, world_id: str) -> NarrativeProgress:
        """获取默认进度（基于 Firestore 已加载叙事数据）。"""
        mainlines = self._world_mainlines(world_id)
        chapters = self._world_chapters(world_id)

        if not mainlines or not chapters:
            raise ValueError(f"世界 '{world_id}' 未加载可用叙事数据")

        first_mainline = next(iter(mainlines.keys()))
        first_chapter = ""

        chapter_list = mainlines[first_mainline].chapters
        if chapter_list:
            first_chapter = chapter_list[0]

        if first_chapter not in chapters:
            first_chapter = next(iter(chapters.keys()))

        return NarrativeProgress(
            current_mainline=first_mainline,
            current_chapter=first_chapter,
            chapter_started_at=datetime.now(),
        )

    def _ensure_progress_valid(self, world_id: str, progress: NarrativeProgress) -> NarrativeProgress:
        """保证进度字段与当前 world 的叙事数据一致。"""
        mainlines = self._world_mainlines(world_id)
        chapters = self._world_chapters(world_id)

        if progress.current_mainline not in mainlines:
            fallback = self._resolve_default_progress(world_id)
            progress.current_mainline = fallback.current_mainline
            progress.current_chapter = fallback.current_chapter

        if progress.current_chapter not in chapters:
            mainline = mainlines.get(progress.current_mainline)
            if mainline and mainline.chapters:
                progress.current_chapter = mainline.chapters[0]
            elif chapters:
                progress.current_chapter = next(iter(chapters.keys()))

        if not progress.chapter_started_at:
            progress.chapter_started_at = datetime.now()

        return progress

    async def get_progress(
        self,
        world_id: str,
        session_id: str,
    ) -> NarrativeProgress:
        """
        获取当前进度

        Args:
            world_id: 世界ID
            session_id: 会话ID

        Returns:
            叙事进度
        """
        # 确保数据已加载
        await self.load_narrative_data(world_id)

        session = await self._session_store.get_session(world_id, session_id)
        if not session:
            # 返回默认进度
            return self._resolve_default_progress(world_id)

        narrative_data = session.metadata.get("narrative", {})

        if not narrative_data:
            # 初始化进度
            return self._resolve_default_progress(world_id)

        progress = NarrativeProgress.from_dict(narrative_data)
        return self._ensure_progress_valid(world_id, progress)

    async def save_progress(
        self,
        world_id: str,
        session_id: str,
        progress: NarrativeProgress,
    ) -> None:
        """
        保存进度到 session.metadata.narrative

        Args:
            world_id: 世界ID
            session_id: 会话ID
            progress: 叙事进度
        """
        await self._session_store.update_session(
            world_id,
            session_id,
            {"metadata.narrative": progress.to_dict()},
        )

    async def get_available_maps(
        self,
        world_id: str,
        session_id: str,
    ) -> List[str]:
        """
        获取当前章节可用的地图

        Args:
            world_id: 世界ID
            session_id: 会话ID

        Returns:
            可用地图ID列表，["*"] 表示所有地图都可用
        """
        progress = await self.get_progress(world_id, session_id)
        chapter = self._world_chapters(world_id).get(progress.current_chapter)

        if not chapter:
            return ["*"]  # 默认解锁所有

        return chapter.available_maps

    async def is_map_available(
        self,
        world_id: str,
        session_id: str,
        map_id: str,
    ) -> bool:
        """
        检查地图是否解锁

        Args:
            world_id: 世界ID
            session_id: 会话ID
            map_id: 地图ID

        Returns:
            是否解锁
        """
        available_maps = await self.get_available_maps(world_id, session_id)

        # "*" 表示所有地图都可用
        if "*" in available_maps:
            return True

        return map_id in available_maps

    async def trigger_event(
        self,
        world_id: str,
        session_id: str,
        event_id: str,
        skip_advance: bool = False,
    ) -> Dict[str, Any]:
        """
        触发叙事事件，检查章节完成

        Args:
            world_id: 世界ID
            session_id: 会话ID
            event_id: 事件ID
            skip_advance: 为 True 时只记录事件，不检查完成条件/推进章节
                          （v2 流程由 StoryDirector 控制章节转换）

        Returns:
            {
                "event_recorded": bool,
                "chapter_completed": bool,
                "new_chapter": str | None,
                "new_maps_unlocked": List[str]
            }
        """
        progress = await self.get_progress(world_id, session_id)

        # 记录事件
        event_recorded = False
        if event_id not in progress.events_triggered:
            progress.events_triggered.append(event_id)
            event_recorded = True

        # v2 流程跳过 legacy 自动推进（由 StoryDirector 控制）
        if skip_advance:
            await self.save_progress(world_id, session_id, progress)
            return {
                "event_recorded": event_recorded,
                "chapter_completed": False,
                "new_chapter": None,
                "new_maps_unlocked": [],
            }

        # 检查章节完成条件
        chapter = self._world_chapters(world_id).get(progress.current_chapter)
        if chapter and self._check_completion(chapter, progress):
            result = await self._advance_chapter(world_id, session_id, progress)
            return {
                "event_recorded": event_recorded,
                "chapter_completed": True,
                "new_chapter": result.get("new_chapter"),
                "new_maps_unlocked": result.get("new_maps_unlocked", []),
            }

        # 保存进度
        await self.save_progress(world_id, session_id, progress)

        return {
            "event_recorded": event_recorded,
            "chapter_completed": False,
            "new_chapter": None,
            "new_maps_unlocked": [],
        }

    def _check_completion(
        self,
        chapter: Chapter,
        progress: NarrativeProgress,
    ) -> bool:
        """检查章节完成条件"""
        required_events = chapter.completion_conditions.get("events_required", [])
        if not required_events:
            return False  # 没有完成条件

        return all(ev in progress.events_triggered for ev in required_events)

    async def _advance_chapter(
        self,
        world_id: str,
        session_id: str,
        progress: NarrativeProgress,
    ) -> Dict[str, Any]:
        """
        推进到下一章节

        Returns:
            {
                "new_chapter": str | None,
                "new_maps_unlocked": List[str]
            }
        """
        mainlines = self._world_mainlines(world_id)
        chapters = self._world_chapters(world_id)
        mainline = mainlines.get(progress.current_mainline)
        if not mainline:
            return {"new_chapter": None, "new_maps_unlocked": []}

        # DAG 章节导航：优先使用 chapter_graph
        next_chapter_id = None
        if mainline.chapter_graph and progress.current_chapter in mainline.chapter_graph:
            successors = mainline.chapter_graph[progress.current_chapter]
            if successors:
                if len(successors) == 1:
                    next_chapter_id = successors[0]
                else:
                    # 多个后继：根据 chapter.transitions 的条件选择
                    current_chapter = chapters.get(progress.current_chapter)
                    if current_chapter and current_chapter.transitions:
                        for trans in sorted(
                            current_chapter.transitions,
                            key=lambda t: t.priority,
                            reverse=True,
                        ):
                            if trans.target_chapter_id in successors:
                                next_chapter_id = trans.target_chapter_id
                                break
                    # 兜底：取第一个后继
                    if not next_chapter_id:
                        next_chapter_id = successors[0]

        # 线性 fallback：使用 mainline.chapters 列表顺序
        if not next_chapter_id:
            try:
                current_idx = mainline.chapters.index(progress.current_chapter)
            except ValueError:
                return {"new_chapter": None, "new_maps_unlocked": []}

            if current_idx + 1 >= len(mainline.chapters):
                return {"new_chapter": None, "new_maps_unlocked": []}  # 已完成所有章节

            next_chapter_id = mainline.chapters[current_idx + 1]

        # 记录完成的章节
        progress.chapters_completed.append(progress.current_chapter)

        # 推进到下一章节
        progress.current_chapter = next_chapter_id
        progress.events_triggered = []  # 重置事件
        progress.chapter_started_at = datetime.now()
        # 重置 v2 回合计数
        progress.rounds_in_chapter = 0
        progress.rounds_since_last_progress = 0

        # 获取新章节解锁的地图
        next_chapter = chapters.get(next_chapter_id)
        new_maps = next_chapter.available_maps if next_chapter else []

        # 保存进度
        await self.save_progress(world_id, session_id, progress)

        return {
            "new_chapter": next_chapter_id,
            "new_maps_unlocked": new_maps,
        }

    async def transition_to_chapter(
        self,
        world_id: str,
        session_id: str,
        target_chapter_id: str,
        transition_type: str = "normal",
    ) -> Dict[str, Any]:
        """按 StoryDirector 评估结果强制切换到目标章节。"""
        progress = await self.get_progress(world_id, session_id)
        chapters = self._world_chapters(world_id)

        target_chapter = chapters.get(target_chapter_id)
        if not target_chapter:
            logger.warning(
                "[Narrative] transition_to_chapter 目标不存在: world=%s chapter=%s",
                world_id,
                target_chapter_id,
            )
            return {"new_chapter": None, "new_maps_unlocked": []}

        previous_chapter = progress.current_chapter
        if (
            previous_chapter
            and previous_chapter != target_chapter_id
            and previous_chapter not in progress.chapters_completed
        ):
            progress.chapters_completed.append(previous_chapter)

        progress.current_chapter = target_chapter_id
        progress.events_triggered = []
        progress.chapter_started_at = datetime.now()
        progress.rounds_in_chapter = 0
        progress.rounds_since_last_progress = 0

        # 记录分支/失败路径历史，便于后续调试与回放
        if previous_chapter and previous_chapter != target_chapter_id:
            progress.branch_history.append(
                {
                    "from": previous_chapter,
                    "to": target_chapter_id,
                    "type": transition_type or "normal",
                    "at": datetime.now().isoformat(),
                }
            )

        # 当前章节切换后，移除 active_chapters 中已完成章节，避免并行列表积累脏数据
        if progress.active_chapters:
            progress.active_chapters = [
                chapter_id
                for chapter_id in progress.active_chapters
                if chapter_id and chapter_id != previous_chapter
            ]

        await self.save_progress(world_id, session_id, progress)
        return {
            "new_chapter": target_chapter_id,
            "new_maps_unlocked": target_chapter.available_maps,
        }

    async def complete_objective(
        self,
        world_id: str,
        session_id: str,
        objective_id: str,
    ) -> bool:
        """
        完成章节目标

        Args:
            world_id: 世界ID
            session_id: 会话ID
            objective_id: 目标ID

        Returns:
            是否成功标记
        """
        progress = await self.get_progress(world_id, session_id)

        if objective_id not in progress.objectives_completed:
            progress.objectives_completed.append(objective_id)
            await self.save_progress(world_id, session_id, progress)
            return True

        return False

    def _extract_content_beats(self, chapter: Chapter, limit: int = 4) -> List[str]:
        """从章节信息提取可用于当前轮叙事编排的内容要点。"""
        if chapter.objectives:
            return [obj.description for obj in chapter.objectives[:limit]]

        text = (chapter.description or "").strip()
        if not text:
            return []

        beats: List[str] = []
        lines = [line.strip(" -*\t") for line in text.splitlines()]
        for line in lines:
            if not line:
                continue
            if line.startswith("<") and line.endswith(">"):
                continue
            # 优先抓章节时间线样式：第一卷:第一章:xxx
            if re.search(r"第.+章", line) and ("：" in line or ":" in line):
                beats.append(line[:80])
                continue
            if len(line) >= 8 and any(token in line for token in ("。", "，", ":", "：")):
                beats.append(line[:80])
            if len(beats) >= limit:
                break

        if beats:
            return beats[:limit]

        compact = re.sub(r"\s+", " ", text)
        for sentence in re.split(r"[。！？!?]", compact):
            sentence = sentence.strip()
            if len(sentence) < 8:
                continue
            beats.append(sentence[:80])
            if len(beats) >= limit:
                break
        return beats

    @staticmethod
    def _objectives_with_status(
        objectives: List[ChapterObjective],
        completed_ids: List[str],
    ) -> List[Dict[str, Any]]:
        completed_set = set(completed_ids)
        return [
            {
                "id": obj.id,
                "description": obj.description,
                "completed": obj.id in completed_set,
            }
            for obj in objectives
        ]

    @staticmethod
    def _extract_required_events(completion_conditions: Dict[str, Any]) -> List[str]:
        events_required = completion_conditions.get("events_required", [])
        if isinstance(events_required, list):
            return [
                str(event_id).strip()
                for event_id in events_required
                if isinstance(event_id, str) and event_id.strip()
            ]
        return []

    async def get_flow_board(
        self,
        world_id: str,
        session_id: str,
        lookahead: int = 3,
    ) -> Dict[str, Any]:
        """
        获取基于世界书主线的流程编排板。

        返回当前章节、前后章节及目标状态，供前端/编排器直接消费。
        """
        await self.load_narrative_data(world_id)
        progress = await self.get_progress(world_id, session_id)
        mainlines = self._world_mainlines(world_id)
        chapters = self._world_chapters(world_id)

        mainline = mainlines.get(progress.current_mainline)
        if not mainline:
            return {
                "current_mainline": None,
                "current_chapter": None,
                "progress": {},
                "steps": [],
                "lookahead": lookahead,
            }

        ordered_chapters = [
            chapter_id for chapter_id in mainline.chapters
            if chapter_id in chapters
        ]
        if not ordered_chapters:
            ordered_chapters = [
                chapter.id for chapter in chapters.values()
                if chapter.mainline_id == mainline.id
            ]

        if not ordered_chapters:
            return {
                "current_mainline": {
                    "id": mainline.id,
                    "name": mainline.name,
                    "description": mainline.description,
                },
                "current_chapter": None,
                "progress": {},
                "steps": [],
                "lookahead": lookahead,
            }

        try:
            current_idx = ordered_chapters.index(progress.current_chapter)
        except ValueError:
            current_idx = 0

        start_idx = max(0, current_idx - 1)
        end_idx = min(len(ordered_chapters), current_idx + max(1, lookahead) + 1)
        completed_set = set(progress.chapters_completed)

        steps: List[Dict[str, Any]] = []
        for idx in range(start_idx, end_idx):
            chapter_id = ordered_chapters[idx]
            chapter = chapters[chapter_id]
            if chapter_id == progress.current_chapter:
                status = "current"
            elif chapter_id in completed_set or idx < current_idx:
                status = "completed"
            else:
                status = "upcoming"

            objectives = self._objectives_with_status(
                chapter.objectives,
                progress.objectives_completed,
            )
            steps.append(
                {
                    "id": chapter.id,
                    "name": chapter.name,
                    "description": chapter.description,
                    "status": status,
                    "index": idx + 1,
                    "available_maps": chapter.available_maps,
                    "required_events": self._extract_required_events(chapter.completion_conditions),
                    "objectives": objectives,
                    "content_beats": self._extract_content_beats(chapter),
                }
            )

        current_chapter_id = ordered_chapters[current_idx]
        current_chapter = chapters[current_chapter_id]
        next_chapter = None
        if current_idx + 1 < len(ordered_chapters):
            next_chapter_id = ordered_chapters[current_idx + 1]
            next_chapter = {
                "id": next_chapter_id,
                "name": chapters[next_chapter_id].name,
            }

        return {
            "current_mainline": {
                "id": mainline.id,
                "name": mainline.name,
                "description": mainline.description,
            },
            "current_chapter": {
                "id": current_chapter.id,
                "name": current_chapter.name,
                "description": current_chapter.description,
                "required_events": self._extract_required_events(current_chapter.completion_conditions),
                "content_beats": self._extract_content_beats(current_chapter),
            },
            "progress": {
                "chapter_index": current_idx + 1,
                "chapter_total": len(ordered_chapters),
                "completed_count": len(completed_set),
                "percentage": round((current_idx + 1) / len(ordered_chapters) * 100, 2),
                "next_chapter": next_chapter,
            },
            "steps": steps,
            "lookahead": lookahead,
        }

    async def get_current_chapter_plan(
        self,
        world_id: str,
        session_id: str,
    ) -> Dict[str, Any]:
        """
        获取当前章节的内容编排建议。

        用于 GM 提示和前端任务面板。
        """
        flow_board = await self.get_flow_board(world_id, session_id, lookahead=2)
        current_chapter = flow_board.get("current_chapter")
        if not current_chapter:
            return {
                "chapter": None,
                "goals": [],
                "required_events": [],
                "suggested_maps": [],
                "next_chapter": None,
            }

        progress = await self.get_progress(world_id, session_id)
        chapter_data = self._world_chapters(world_id).get(current_chapter["id"])
        if not chapter_data:
            return {
                "chapter": current_chapter,
                "goals": [],
                "required_events": [],
                "suggested_maps": [],
                "next_chapter": flow_board.get("progress", {}).get("next_chapter"),
            }

        objectives = self._objectives_with_status(
            chapter_data.objectives,
            progress.objectives_completed,
        )
        pending_goals = [obj["description"] for obj in objectives if not obj["completed"]]
        if not pending_goals:
            pending_goals = self._extract_content_beats(chapter_data)

        required_events = self._extract_required_events(chapter_data.completion_conditions)

        return {
            "chapter": {
                "id": chapter_data.id,
                "name": chapter_data.name,
                "description": chapter_data.description,
            },
            "goals": pending_goals[:4],
            "required_events": required_events,
            "suggested_maps": chapter_data.available_maps,
            "next_chapter": flow_board.get("progress", {}).get("next_chapter"),
        }

    def get_chapter_info(self, world_id: str, chapter_id: str) -> Optional[Dict[str, Any]]:
        """获取章节信息"""
        chapter = self._world_chapters(world_id).get(chapter_id)
        if not chapter:
            return None

        return {
            "id": chapter.id,
            "name": chapter.name,
            "description": chapter.description,
            "objectives": [
                {"id": obj.id, "description": obj.description}
                for obj in chapter.objectives
            ],
            "available_maps": chapter.available_maps,
        }

    def get_mainline_info(self, world_id: str, mainline_id: str) -> Optional[Dict[str, Any]]:
        """获取主线信息"""
        mainline = self._world_mainlines(world_id).get(mainline_id)
        if not mainline:
            return None

        return {
            "id": mainline.id,
            "name": mainline.name,
            "description": mainline.description,
            "chapters": mainline.chapters,
        }
