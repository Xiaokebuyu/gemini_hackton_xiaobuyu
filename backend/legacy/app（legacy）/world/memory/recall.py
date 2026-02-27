"""WorldGraph-based recall engine (L3 M2).

直接在 WorldGraph 上运行扩散激活，无 Firestore I/O。
旧 RecallOrchestrator 保留给 CLI/离线工具。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.models.activation import SpreadingActivationConfig
from app.models.flash import RecallResponse
from app.world.memory.activation import spread_activation

logger = logging.getLogger(__name__)


RECALL_CONFIGS: Dict[str, Dict[str, Any]] = {
    "exploration": {"depth": 1, "output_threshold": 0.3},
    "dialogue": {"depth": 2, "output_threshold": 0.2},
    "npc_interaction": {"depth": 2, "output_threshold": 0.2},
    "recall": {"depth": 3, "output_threshold": 0.1},
    "lore": {"depth": 3, "output_threshold": 0.1},
    "combat": {"depth": 1, "output_threshold": 0.4},
    "start_combat": {"depth": 1, "output_threshold": 0.4},
    "navigation": {"depth": 1, "output_threshold": 0.3},
    "team_interaction": {"depth": 2, "output_threshold": 0.2},
    "roleplay": {"depth": 2, "output_threshold": 0.2},
    "enter_sublocation": {"depth": 1, "output_threshold": 0.3},
    "leave_sub_location": {"depth": 1, "output_threshold": 0.3},
    "wait": {"depth": 1, "output_threshold": 0.3},
}


class _EmptyNode:
    """Sentinel for missing nodes."""
    properties: dict = {}
    state: dict = {}

_EMPTY_NODE = _EmptyNode()


class WorldGraphRecallOrchestrator:
    """WorldGraph 版 recall 引擎。

    所有操作同步（内存图），async 签名保持调用方兼容。
    """

    def __init__(self, world_graph) -> None:
        self.world_graph = world_graph

    async def recall(
        self,
        *,
        world_id: str = "",
        character_id: str,
        seed_nodes: List[str],
        intent_type: Optional[str] = None,
        chapter_id: Optional[str] = None,
        area_id: Optional[str] = None,
        location_id: Optional[str] = None,
    ) -> RecallResponse:
        """Recall from WorldGraph (synchronous internally, async for compatibility)."""
        cfg = RECALL_CONFIGS.get(intent_type or "", {})
        config = SpreadingActivationConfig(
            max_iterations=max(1, int(cfg.get("depth", 3))),
            output_threshold=cfg.get("output_threshold", 0.15),
            current_chapter_id=chapter_id,
        )

        # Seed expansion (same prefix logic as old RecallOrchestrator)
        expanded: List[str] = []
        for seed in seed_nodes:
            expanded.append(seed)
            for prefix in ("person_", "character_", "location_", "area_"):
                if seed.startswith(prefix):
                    expanded.append(seed[len(prefix):])
                else:
                    expanded.append(f"{prefix}{seed}")
        valid_seeds = [s for s in expanded if self.world_graph.has_node(s)]

        if not valid_seeds:
            return RecallResponse(
                seed_nodes=seed_nodes,
                activated_nodes={},
                subgraph=None,
                used_subgraph=False,
            )

        # Disposition bias: high-approval NPCs' memory nodes get higher initial weight
        seed_weights = self._build_seed_weights(character_id, valid_seeds)

        activated = spread_activation(
            self.world_graph, valid_seeds, config,
            initial_scores=seed_weights,
        )

        # Filter placeholder nodes
        activated = {
            nid: score for nid, score in activated.items()
            if not (self.world_graph.get_node(nid) or _EMPTY_NODE).properties.get("placeholder", False)
        }

        logger.info(
            "[wg_recall] character=%s seeds=%s valid=%d activated=%d",
            character_id, seed_nodes, len(valid_seeds), len(activated),
        )

        return RecallResponse(
            seed_nodes=seed_nodes,
            activated_nodes=activated,
            subgraph=None,
            used_subgraph=False,
        )

    async def recall_for_role(
        self,
        *,
        role: str,
        world_id: str = "",
        character_id: str,
        seed_nodes: List[str],
        intent_type: Optional[str] = None,
        chapter_id: Optional[str] = None,
        area_id: Optional[str] = None,
        location_id: Optional[str] = None,
    ) -> RecallResponse:
        """Role-scoped recall — role affects seed selection."""
        role_seeds = list(seed_nodes)
        if role == "npc":
            role_seeds = [character_id] + role_seeds
        elif role == "teammate":
            role_seeds = [character_id, "camp"] + role_seeds

        return await self.recall(
            world_id=world_id,
            character_id=character_id,
            seed_nodes=role_seeds,
            intent_type=intent_type,
            chapter_id=chapter_id,
            area_id=area_id,
            location_id=location_id,
        )

    def _build_seed_weights(
        self,
        character_id: str,
        valid_seeds: List[str],
    ) -> Dict[str, float]:
        """Read dispositions from WorldGraph node state, adjust seed weights."""
        weights: Dict[str, float] = {}
        node = self.world_graph.get_node(character_id)
        if not node:
            return weights
        dispositions = node.state.get("dispositions", {})
        for target_id, disp_data in dispositions.items():
            if not isinstance(disp_data, dict):
                continue
            approval = disp_data.get("approval", 0)
            bias = (approval + 100) / 200.0  # normalize to 0.0-1.0
            candidates = [target_id, f"character_{target_id}", f"person_{target_id}"]
            for c in candidates:
                if c in valid_seeds:
                    weights[c] = min(1.0, 0.5 + bias * 0.5)
        return weights
