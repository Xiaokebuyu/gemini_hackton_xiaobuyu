"""CompanionInstance — 同伴实例（Phase 5A 实现）。

独立于区域 NPC，具备三层记忆：
1. 短期：ContextWindow（~100-150K tokens），覆盖 30-80 轮
2. 中期：结构化事件 + 区域摘要（紧凑）
3. 长期：character graph 扩散激活召回（通过 GraphStore 按需加载）
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from google.cloud import firestore

from app.config import settings
from app.runtime.models.companion_state import (
    CompactEvent,
    CompanionAreaSummary,
    CompanionEmotionalState,
)

logger = logging.getLogger(__name__)


class CompanionInstance:
    """同伴实例 — 管理单个同伴的三层记忆和持久化。

    Firestore 路径::

        worlds/{wid}/sessions/{sid}/companions/{char_id}/
          state              <- emotional_state + 元数据
          shared_events/     <- CompactEvent 列表
          area_summaries/    <- CompanionAreaSummary 列表
    """

    # 中期记忆上限
    MAX_SHARED_EVENTS = 100
    MAX_AREA_SUMMARIES = 50

    def __init__(
        self,
        character_id: str,
        name: str,
        world_id: str,
        session_id: str,
    ) -> None:
        self.character_id = character_id
        self.name = name
        self.world_id = world_id
        self.session_id = session_id

        # 短期记忆：ContextWindow（可选，按需初始化）
        self.context_window: Optional[Any] = None  # ContextWindow

        # 中期记忆：结构化事件 + 区域摘要
        self.shared_events: List[CompactEvent] = []
        self.area_summaries: List[CompanionAreaSummary] = []

        # 长期记忆：character graph 扩散激活召回
        # (通过 GraphStore 按需加载，不在此处持有)

        # 情感状态
        self.emotional_state: CompanionEmotionalState = CompanionEmotionalState()

        self._db: Optional[firestore.Client] = None
        self._loaded: bool = False

    def _get_db(self) -> firestore.Client:
        if self._db is None:
            self._db = firestore.Client(database=settings.firestore_database)
        return self._db

    def _companion_ref(self) -> firestore.DocumentReference:
        """获取同伴根文档引用。"""
        return (
            self._get_db()
            .collection("worlds")
            .document(self.world_id)
            .collection("sessions")
            .document(self.session_id)
            .collection("companions")
            .document(self.character_id)
        )

    # =========================================================================
    # 加载 / 持久化
    # =========================================================================

    async def load(self) -> None:
        """从 Firestore 加载同伴数据。"""
        ref = self._companion_ref()

        # 1. 加载状态
        state_doc = ref.collection("data").document("state").get()
        if state_doc.exists:
            data = state_doc.to_dict() or {}
            if "emotional_state" in data:
                self.emotional_state = CompanionEmotionalState(
                    **data["emotional_state"]
                )
            if "name" in data:
                self.name = data["name"]

        # 2. 加载共享事件
        self.shared_events = []
        events_ref = ref.collection("shared_events")
        for doc in events_ref.order_by(
            "timestamp", direction=firestore.Query.DESCENDING
        ).limit(self.MAX_SHARED_EVENTS).stream():
            doc_data = doc.to_dict()
            if doc_data:
                try:
                    self.shared_events.append(CompactEvent(**doc_data))
                except Exception as e:
                    logger.warning(
                        "跳过无效同伴事件 %s: %s", doc.id, e
                    )
        # 恢复时间正序
        self.shared_events.reverse()

        # 3. 加载区域摘要
        self.area_summaries = []
        summaries_ref = ref.collection("area_summaries")
        for doc in summaries_ref.order_by(
            "visit_day", direction=firestore.Query.DESCENDING
        ).limit(self.MAX_AREA_SUMMARIES).stream():
            doc_data = doc.to_dict()
            if doc_data:
                try:
                    self.area_summaries.append(
                        CompanionAreaSummary(**doc_data)
                    )
                except Exception as e:
                    logger.warning(
                        "跳过无效区域摘要 %s: %s", doc.id, e
                    )
        self.area_summaries.reverse()

        self._loaded = True
        logger.info(
            "CompanionInstance '%s' 已加载: %d 事件, %d 区域摘要",
            self.character_id,
            len(self.shared_events),
            len(self.area_summaries),
        )

    async def save(self) -> None:
        """持久化同伴数据到 Firestore。"""
        ref = self._companion_ref()

        # 1. 保存状态
        ref.collection("data").document("state").set(
            {
                "character_id": self.character_id,
                "name": self.name,
                "emotional_state": self.emotional_state.model_dump(),
            },
            merge=True,
        )

        # 2. 批量保存共享事件（仅最近 MAX_SHARED_EVENTS 条）
        events_to_save = self.shared_events[-self.MAX_SHARED_EVENTS :]
        batch = self._get_db().batch()
        events_ref = ref.collection("shared_events")
        for event in events_to_save:
            doc_ref = events_ref.document(event.event_id)
            batch.set(doc_ref, event.model_dump())
        batch.commit()

        # 3. 批量保存区域摘要
        summaries_to_save = self.area_summaries[-self.MAX_AREA_SUMMARIES :]
        batch = self._get_db().batch()
        summaries_ref = ref.collection("area_summaries")
        for summary in summaries_to_save:
            doc_id = f"{summary.area_id}_{summary.visit_day}"
            doc_ref = summaries_ref.document(doc_id)
            batch.set(doc_ref, summary.model_dump())
        batch.commit()

        logger.info(
            "CompanionInstance '%s' 已保存: %d 事件, %d 区域摘要",
            self.character_id,
            len(events_to_save),
            len(summaries_to_save),
        )

    # =========================================================================
    # 事件与摘要管理
    # =========================================================================

    def add_event(self, event: CompactEvent) -> None:
        """添加共享事件（事件完成时自动调用）。"""
        self.shared_events.append(event)
        # 超出上限时移除最旧的
        if len(self.shared_events) > self.MAX_SHARED_EVENTS:
            self.shared_events = self.shared_events[-self.MAX_SHARED_EVENTS :]

    def add_area_summary(self, summary: CompanionAreaSummary) -> None:
        """添加区域访问摘要。"""
        self.area_summaries.append(summary)
        if len(self.area_summaries) > self.MAX_AREA_SUMMARIES:
            self.area_summaries = self.area_summaries[-self.MAX_AREA_SUMMARIES :]

    # =========================================================================
    # 记忆上下文（供 LLM 使用）
    # =========================================================================

    def get_memory_context(self) -> Dict[str, Any]:
        """获取同伴记忆上下文（供 LLM 使用）。

        整合三层记忆为简洁的上下文字典。
        """
        return {
            "character_id": self.character_id,
            "name": self.name,
            "recent_events": [
                e.model_dump() for e in self.shared_events[-20:]
            ],
            "area_summaries": [
                s.model_dump() for s in self.area_summaries[-10:]
            ],
            "emotional_state": self.emotional_state.model_dump(),
        }
