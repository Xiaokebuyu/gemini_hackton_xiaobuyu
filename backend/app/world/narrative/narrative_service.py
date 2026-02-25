"""
Narrative Service - 主线叙事管理服务

管理 主线 -> 章节 -> 地图 -> 地点 四层结构。
进度存储在 GameSessionState.metadata.narrative
"""
import asyncio
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from google.cloud import firestore

from app.config import settings
from app.models.narrative import (
    Chapter,
    ChapterObjective,
    Mainline,
    NarrativeProgress,
)
from app.world.narrative.narrative_parser import (
    parse_chapter,
    parse_mainline,
    merge_chapter,
    safe_int,
    merge_unique_items,
)
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

    def __init__(self, db=None):
        """
        初始化叙事服务

        Args:
            db: Firestore client（测试时注入 fake，生产环境留 None 自动创建）
        """
        self._db = db or firestore.Client(database=settings.firestore_database)
        self._mainlines_by_world: Dict[str, Dict[str, Mainline]] = {}
        self._chapters_by_world: Dict[str, Dict[str, Chapter]] = {}
        self._loaded_worlds: set = set()
        self._reload_locks: Dict[str, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()

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
                    safe_int(chapter_data.get("order"), 0),
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

    async def load_narrative_data(self, world_id: str, force_reload: bool = False) -> None:
        """
        加载主线数据

        从 Firestore 加载（worlds/{world_id}/chapters 与 worlds/{world_id}/mainlines）
        per-world 锁防止并发 reload；同步 Firestore 调用通过 to_thread 避免阻塞事件循环。

        Args:
            world_id: 世界ID
        """
        # per-world 锁：防止并发 reload 同一世界
        async with self._meta_lock:
            if world_id not in self._reload_locks:
                self._reload_locks[world_id] = asyncio.Lock()
        async with self._reload_locks[world_id]:
            if force_reload and world_id in self._loaded_worlds:
                self._loaded_worlds.discard(world_id)
                self._mainlines_by_world.pop(world_id, None)
                self._chapters_by_world.pop(world_id, None)

            if world_id in self._loaded_worlds:
                return

            await self._load_narrative_data_inner(world_id)

    async def _load_narrative_data_inner(self, world_id: str) -> None:
        """load_narrative_data 的内部实现（已在锁内调用）。"""
        mainlines: Dict[str, Mainline] = {}
        chapters: Dict[str, Chapter] = {}
        mainlines_raw, chapters_raw = await asyncio.to_thread(
            self._load_narrative_payload_from_firestore, world_id
        )

        # 先解析章节，确保主线的 chapter 引用可以校验
        for ch_data in chapters_raw:
            chapter = parse_chapter(ch_data)
            if not chapter:
                continue
            if chapter.id in chapters:
                chapters[chapter.id] = merge_chapter(chapters[chapter.id], chapter)
            else:
                chapters[chapter.id] = chapter

        # 解析主线并做去重/过滤
        for ml_data in mainlines_raw:
            mainline = parse_mainline(ml_data, chapters)
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
                    chapters=merge_unique_items(chapter_ids),
                )

        if not chapters:
            raise ValueError(
                f"世界 '{world_id}' 没有可用 story 章节数据（metadata/volume_index 已过滤）"
            )
        if not mainlines:
            raise ValueError(
                f"世界 '{world_id}' 没有可用主线数据（Firestore: worlds/{world_id}/mainlines）"
            )

        self._mainlines_by_world[world_id] = dict(sorted(mainlines.items()))
        self._chapters_by_world[world_id] = chapters
        self._loaded_worlds.add(world_id)

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

        first_mainline = sorted(mainlines.keys())[0]
        first_chapter = ""

        chapter_list = mainlines[first_mainline].chapters
        if chapter_list:
            first_chapter = chapter_list[0]

        if first_chapter not in chapters:
            first_chapter = next(iter(chapters.keys()))

        logger.info(
            "[NarrativeService] 默认进度: mainline=%s, chapter=%s",
            first_mainline, first_chapter,
        )

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

        doc = (self._db.collection("worlds").document(world_id)
               .collection("sessions").document(session_id).get())
        if not doc.exists:
            return self._resolve_default_progress(world_id)

        session_data = doc.to_dict() or {}
        metadata = session_data.get("metadata") or {}
        narrative_data = metadata.get("narrative", {})

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
        from datetime import datetime
        ref = (self._db.collection("worlds").document(world_id)
               .collection("sessions").document(session_id))
        ref.update({
            "metadata.narrative": progress.to_dict(),
            "updated_at": datetime.now(),
        })

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
        required_events = self._extract_required_events(chapter)
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
            return [self._sanitize_chapter_text(obj.description) for obj in chapter.objectives[:limit]]

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

    def _objectives_with_status(
        self,
        objectives: List[ChapterObjective],
        completed_ids: List[str],
    ) -> List[Dict[str, Any]]:
        completed_set = set(completed_ids)
        return [
            {
                "id": obj.id,
                "description": self._sanitize_chapter_text(obj.description),
                "completed": obj.id in completed_set,
            }
            for obj in objectives
        ]

    @staticmethod
    def _sanitize_chapter_text(text: str) -> str:
        """替换 SillyTavern 占位符为通用称谓。"""
        if not text:
            return text
        return text.replace("{{user}}", "冒险者").replace("{{char}}", "")

    @staticmethod
    def _extract_required_events(chapter: "Chapter") -> List[str]:
        """从章节 events 数组提取 is_required=True 的事件 ID。
        优先使用 events（始终包含正确 ID），events 为空时回退 completion_conditions。
        """
        if chapter.events:
            return [ev.id for ev in chapter.events if ev.is_required]
        events_required = chapter.completion_conditions.get("events_required", [])
        if isinstance(events_required, list):
            return [str(eid).strip() for eid in events_required if isinstance(eid, str) and eid.strip()]
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
                    "name": self._sanitize_chapter_text(mainline.name),
                    "description": self._sanitize_chapter_text(mainline.description),
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
                    "name": self._sanitize_chapter_text(chapter.name),
                    "description": self._sanitize_chapter_text(chapter.description),
                    "status": status,
                    "index": idx + 1,
                    "available_maps": chapter.available_maps,
                    "required_events": self._extract_required_events(chapter),
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
                "name": self._sanitize_chapter_text(mainline.name),
                "description": self._sanitize_chapter_text(mainline.description),
            },
            "current_chapter": {
                "id": current_chapter.id,
                "name": self._sanitize_chapter_text(current_chapter.name),
                "description": self._sanitize_chapter_text(current_chapter.description),
                "required_events": self._extract_required_events(current_chapter),
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

        required_events = self._extract_required_events(chapter_data)
        triggered_event_set = set(progress.events_triggered or [])
        required_event_summaries: List[Dict[str, Any]] = []
        if chapter_data.events:
            for ev in chapter_data.events:
                if not ev.is_required:
                    continue
                required_event_summaries.append(
                    {
                        "id": ev.id,
                        "name": self._sanitize_chapter_text(ev.name),
                        "description": self._sanitize_chapter_text(ev.description),
                        "completed": ev.id in triggered_event_set,
                    }
                )

        if not required_event_summaries and required_events:
            # fallback: 仅有 required_events id 时也提供可追踪结构
            required_event_summaries = [
                {
                    "id": event_id,
                    "name": event_id,
                    "description": "",
                    "completed": event_id in triggered_event_set,
                }
                for event_id in required_events
            ]

        # 提取即将到来的事件叙事指令（最多 3 个未触发事件）
        event_directives = []
        if chapter_data.events:
            for ev in chapter_data.events:
                if ev.id not in progress.events_triggered and getattr(ev, 'narrative_directive', None):
                    event_directives.append(f"[{ev.name}] {ev.narrative_directive}")
                if len(event_directives) >= 3:
                    break

        # 提取当前事件（第一个未触发的事件）
        current_event = None
        if chapter_data.events:
            for ev in chapter_data.events:
                if ev.id not in progress.events_triggered:
                    current_event = {
                        "id": ev.id,
                        "name": self._sanitize_chapter_text(ev.name),
                        "description": self._sanitize_chapter_text(ev.description),
                    }
                    break

        event_total = len(required_event_summaries)
        event_completed = sum(
            1 for event_item in required_event_summaries if event_item.get("completed")
        )
        event_completion_pct = (
            round((event_completed / event_total) * 100, 2) if event_total > 0 else 0.0
        )
        all_required_events_completed = event_total > 0 and event_completed >= event_total
        waiting_transition = bool(
            all_required_events_completed and not current_event
        )
        pending_required_events = [
            event_item.get("id")
            for event_item in required_event_summaries
            if event_item.get("id") and not event_item.get("completed")
        ]

        return {
            "chapter": {
                "id": chapter_data.id,
                "name": self._sanitize_chapter_text(chapter_data.name),
                "description": self._sanitize_chapter_text(chapter_data.description),
            },
            "goals": [self._sanitize_chapter_text(g) for g in pending_goals[:4]],
            "required_events": required_events,
            "pending_required_events": pending_required_events,
            "required_event_summaries": required_event_summaries,
            "events_triggered": [
                event_id
                for event_id in progress.events_triggered
                if isinstance(event_id, str) and event_id.strip()
            ],
            "event_total": event_total,
            "event_completed": event_completed,
            "event_completion_pct": event_completion_pct,
            "all_required_events_completed": all_required_events_completed,
            "waiting_transition": waiting_transition,
            "suggested_maps": chapter_data.available_maps,
            "next_chapter": flow_board.get("progress", {}).get("next_chapter"),
            "event_directives": [self._sanitize_chapter_text(d) for d in event_directives],
            "current_event": current_event,
        }

    def get_chapter_info(self, world_id: str, chapter_id: str) -> Optional[Dict[str, Any]]:
        """获取章节信息"""
        chapter = self._world_chapters(world_id).get(chapter_id)
        if not chapter:
            return None

        return {
            "id": chapter.id,
            "name": self._sanitize_chapter_text(chapter.name),
            "description": self._sanitize_chapter_text(chapter.description),
            "objectives": [
                {"id": obj.id, "description": self._sanitize_chapter_text(obj.description)}
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
