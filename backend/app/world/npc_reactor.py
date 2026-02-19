"""NPCReactor — NPC 自主反应收集器。

Direction A.3 实现。
遍历在场 NPC，判断是否需要自主反应。

约束:
- 每轮最多 2 个 NPC 反应
- 用 Passerby 模型（轻量，低延迟）
- 并发执行
- 私密对话时，非目标 NPC 不反应
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from app.world.scene_bus import BusEntry, BusEntryType, SceneBus

if TYPE_CHECKING:
    from app.runtime.session_runtime import SessionRuntime

logger = logging.getLogger(__name__)

MAX_REACTIONS_PER_ROUND = 2


class NPCReactor:
    """NPC 自主反应收集器。"""

    def __init__(
        self,
        instance_manager: Optional[Any] = None,
        world_graph: Optional[Any] = None,
        use_llm: bool = False,
        llm_service: Optional[Any] = None,
    ) -> None:
        self.instance_manager = instance_manager
        self.wg = world_graph
        self.use_llm = use_llm
        self._llm_service = llm_service
        self._llm_consecutive_failures: int = 0

    async def collect_reactions(
        self,
        scene_bus: SceneBus,
        session: "SessionRuntime",
        context: Dict[str, Any],
    ) -> List[BusEntry]:
        """遍历在场 NPC，判断是否需要自主反应。

        返回最多 MAX_REACTIONS_PER_ROUND 个 BusEntry。
        """
        if not self.wg:
            return []

        current_area = session.player_location
        if not current_area:
            return []

        # 收集在场 NPC
        from app.world.models import WorldNodeType
        npc_ids = self._get_area_npcs(current_area, session.sub_location)

        if not npc_ids:
            return []

        # 分析总线内容，找出被提及/相关的 NPC
        entries = scene_bus.entries
        if not entries:
            return []

        # 判断私密性
        is_private = any(e.visibility.startswith("private:") for e in entries)

        # 筛选需要反应的 NPC
        candidates: List[Dict[str, Any]] = []
        for npc_id in npc_ids:
            node = self.wg.get_node(npc_id)
            if not node:
                continue
            if not node.state.get("is_alive", True):
                continue

            npc_name = node.name

            # 私密对话检查
            if is_private:
                private_entries = [
                    e for e in entries
                    if e.visibility.startswith("private:")
                ]
                if private_entries:
                    targets = {e.visibility.split(":", 1)[1] for e in private_entries}
                    if npc_id not in targets:
                        continue  # 非目标 NPC 在私密对话中不反应

            # 检查是否被点名或话题相关
            relevance = self._calculate_relevance(npc_id, npc_name, entries)
            if relevance > 0:
                candidates.append({
                    "npc_id": npc_id,
                    "npc_name": npc_name,
                    "relevance": relevance,
                    "node": node,
                })

        if not candidates:
            return []

        # 按关联度排序，取前 N 个
        candidates.sort(key=lambda c: c["relevance"], reverse=True)
        selected = candidates[:MAX_REACTIONS_PER_ROUND]

        # 并发生成反应
        tasks = [
            self._generate_reaction(c, scene_bus, session, context)
            for c in selected
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        reactions: List[BusEntry] = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning("[NPCReactor] reaction generation failed: %s", result)
                continue
            if result:
                reactions.append(result)

        return reactions

    def _get_area_npcs(self, area_id: str, sub_location: Optional[str] = None) -> List[str]:
        """获取当前区域在场的 NPC ID 列表。"""
        if not self.wg:
            return []

        from app.world.models import WorldNodeType
        npc_ids: List[str] = []
        seen = set()

        # 从区域和子地点的 entities_at 索引收集 NPC
        scope_nodes = [area_id]
        if sub_location:
            scope_nodes.append(sub_location)
        for child_id in self.wg.get_children(area_id, WorldNodeType.LOCATION.value):
            scope_nodes.append(child_id)

        for scope_id in scope_nodes:
            for entity_id in self.wg.get_entities_at(scope_id):
                if entity_id in seen:
                    continue
                seen.add(entity_id)
                node = self.wg.get_node(entity_id)
                if node and node.type == WorldNodeType.NPC.value:
                    npc_ids.append(entity_id)

        return npc_ids

    @staticmethod
    def _calculate_relevance(
        npc_id: str,
        npc_name: str,
        entries: List[BusEntry],
    ) -> float:
        """计算 NPC 与总线内容的关联度。

        点名 → 高关联
        话题匹配 → 中关联
        引擎事件（ON_ENTER 等）→ 低关联
        """
        score = 0.0
        for entry in entries:
            content_lower = entry.content.lower()
            # 点名检查
            if npc_id.lower() in content_lower or npc_name.lower() in content_lower:
                score += 10.0
            # 话题匹配
            for topic in entry.topics:
                if npc_id in topic or npc_name in topic:
                    score += 5.0
            # 引擎事件（ON_ENTER 等）
            if entry.type == BusEntryType.ENGINE_RESULT:
                if entry.data.get("tool") == "navigate":
                    score += 1.0  # NPC 看到玩家到达
        return score

    async def _generate_reaction(
        self,
        candidate: Dict[str, Any],
        scene_bus: SceneBus,
        session: Any,
        context: Dict[str, Any],
    ) -> Optional[BusEntry]:
        """为单个 NPC 生成反应。

        使用 Passerby 模型（轻量，低延迟）。
        如果没有 instance_manager 或 LLM 服务，返回基于模板的简短反应。
        """
        npc_id = candidate["npc_id"]
        npc_name = candidate["npc_name"]
        node = candidate["node"]

        # 尝试使用 LLM 生成反应
        if self.use_llm or self.instance_manager:
            try:
                reaction_text = await self._llm_reaction(
                    npc_id, npc_name, node, scene_bus, session, context
                )
                if reaction_text:
                    self._llm_consecutive_failures = 0
                    return BusEntry(
                        actor=npc_id,
                        actor_name=npc_name,
                        type=BusEntryType.REACTION,
                        content=reaction_text,
                    )
            except Exception as exc:
                self._llm_consecutive_failures += 1
                if self._llm_consecutive_failures >= 3:
                    logger.error(
                        "[NPCReactor] LLM consecutive failures: %d, npc=%s: %s",
                        self._llm_consecutive_failures, npc_id, exc,
                    )
                else:
                    logger.warning(
                        "[NPCReactor] LLM reaction failed for %s (%d): %s",
                        npc_id, self._llm_consecutive_failures, exc,
                    )

        # Fallback: 基于节点属性的模板反应
        personality = node.properties.get("personality", "")
        if candidate["relevance"] >= 10:
            # 被点名
            template = f"{npc_name}注意到了你的呼唤。"
        else:
            # 一般存在感
            template = f"{npc_name}注意到了你的到来。"

        return BusEntry(
            actor=npc_id,
            actor_name=npc_name,
            type=BusEntryType.REACTION,
            content=template,
        )

    async def _llm_reaction(
        self,
        npc_id: str,
        npc_name: str,
        node: Any,
        scene_bus: SceneBus,
        session: Any,
        context: Dict[str, Any],
    ) -> Optional[str]:
        """通过 Passerby 模型生成 NPC 反应。"""
        from app.config import settings
        from app.services.llm_service import LLMService

        bus_summary = scene_bus.get_round_summary(viewer_id=npc_id)
        if not bus_summary:
            return None

        personality = node.properties.get("personality", "普通")
        occupation = node.properties.get("occupation", "")
        prompt = (
            f"你是{npc_name}，一个{occupation or '角色'}，性格：{personality}。\n"
            f"场景中发生了以下事件：\n{bus_summary}\n\n"
            f"用 1-2 句话简短地做出你的自然反应（中文）。"
            f"只输出反应文本，不要任何格式标记。"
        )

        if self._llm_service is None:
            self._llm_service = LLMService()
        result = await self._llm_service.generate_simple(
            prompt,
            model_override=settings.npc_tier_config.passerby_model,
        )
        return result.strip() if result else None
