"""
Shared recall orchestrator for admin v2/v3 flows.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.models.activation import SpreadingActivationConfig
from app.models.flash import RecallResponse
from app.models.graph_scope import GraphScope
from app.services.memory_graph import MemoryGraph
from app.services.spreading_activation import extract_subgraph, spread_activation

logger = logging.getLogger(__name__)


class RecallOrchestrator:
    """Multi-scope recall engine used by both v2 pipeline and v3 tools."""

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

    ROLE_SCOPES: Dict[str, Optional[List[str]]] = {
        "npc": ["character", "location"],
        "teammate": ["character", "camp", "location"],
        "gm": ["character", "area", "chapter"],
        "player": None,
    }

    def __init__(
        self,
        *,
        graph_store: Any,
        get_character_id_set: Callable[[str], Awaitable[set]],
        get_area_chapter_map: Callable[[str], Awaitable[Dict[str, str]]],
    ) -> None:
        self.graph_store = graph_store
        self._get_character_id_set = get_character_id_set
        self._get_area_chapter_map = get_area_chapter_map

    async def recall(
        self,
        *,
        world_id: str,
        character_id: str,
        seed_nodes: List[str],
        intent_type: Optional[str] = None,
        chapter_id: Optional[str] = None,
        area_id: Optional[str] = None,
        location_id: Optional[str] = None,
    ) -> RecallResponse:
        """Recall memory by merging multi-scope graphs and running activation."""
        recall_cfg = self.RECALL_CONFIGS.get(intent_type or "", {})
        output_threshold = recall_cfg.get("output_threshold", 0.15)
        config = SpreadingActivationConfig(
            output_threshold=output_threshold,
            current_chapter_id=chapter_id,
        )

        scoped_data: List[Tuple[GraphScope, Any]] = []
        mandatory_scopes: List[GraphScope] = []
        mandatory_calls: List[Any] = []

        char_scope = GraphScope(scope_type="character", character_id=character_id)
        mandatory_scopes.append(char_scope)
        mandatory_calls.append(self.graph_store.load_graph_v2(world_id, char_scope))

        area_scope: Optional[GraphScope] = None
        area_data: Optional[Any] = None
        if chapter_id and area_id and location_id:
            location_scope = GraphScope(
                scope_type="location",
                chapter_id=chapter_id,
                area_id=area_id,
                location_id=location_id,
            )
            mandatory_scopes.append(location_scope)
            mandatory_calls.append(self.graph_store.load_graph_v2(world_id, location_scope))

        if chapter_id and area_id:
            area_scope = GraphScope(scope_type="area", chapter_id=chapter_id, area_id=area_id)
            mandatory_scopes.append(area_scope)
            mandatory_calls.append(self.graph_store.load_graph_v2(world_id, area_scope))

        if chapter_id:
            chapter_scope = GraphScope(scope_type="chapter", chapter_id=chapter_id)
            mandatory_scopes.append(chapter_scope)
            mandatory_calls.append(self.graph_store.load_graph_v2(world_id, chapter_scope))

        camp_scope = GraphScope(scope_type="camp")
        mandatory_scopes.append(camp_scope)
        mandatory_calls.append(self.graph_store.load_graph_v2(world_id, camp_scope))

        world_scope = GraphScope(scope_type="world")
        mandatory_scopes.append(world_scope)
        mandatory_calls.append(self.graph_store.load_graph_v2(world_id, world_scope))

        mandatory_results = await asyncio.gather(*mandatory_calls, return_exceptions=True)
        for scope, result in zip(mandatory_scopes, mandatory_results):
            if isinstance(result, Exception):
                logger.warning("[recall] scope load failed scope=%s: %s", scope, result)
                continue
            scoped_data.append((scope, result))
            if area_scope is not None and scope == area_scope:
                area_data = result

        # Fallback area scope to its original chapter if current chapter area graph is empty.
        if chapter_id and area_id and area_scope is not None and not getattr(area_data, "nodes", None):
            area_chapter_map = await self._get_area_chapter_map(world_id)
            original_chapter = area_chapter_map.get(area_id)
            if original_chapter and original_chapter != chapter_id:
                fallback_scope = GraphScope(scope_type="area", chapter_id=original_chapter, area_id=area_id)
                try:
                    fallback_data = await self.graph_store.load_graph_v2(world_id, fallback_scope)
                    scoped_data = [
                        (scope, data)
                        for scope, data in scoped_data
                        if not (scope.scope_type == "area" and getattr(scope, "area_id", None) == area_id)
                    ]
                    scoped_data.append((fallback_scope, fallback_data))
                    logger.info(
                        "[recall] area scope fallback: %s:%s -> %s:%s",
                        chapter_id,
                        area_id,
                        original_chapter,
                        area_id,
                    )
                except Exception as exc:
                    logger.warning("[recall] area fallback load failed: %s", exc)

        known_char_ids = await self._get_character_id_set(world_id)
        loaded_chars = {character_id}
        extra_character_ids: List[str] = []
        for seed in seed_nodes:
            candidates = [seed]
            for prefix in ("person_", "character_", "location_", "area_"):
                if seed.startswith(prefix):
                    candidates.append(seed[len(prefix):])
            for candidate in candidates:
                if candidate in known_char_ids and candidate not in loaded_chars:
                    loaded_chars.add(candidate)
                    extra_character_ids.append(candidate)
                    break

        if extra_character_ids:
            extra_scopes = [GraphScope(scope_type="character", character_id=candidate) for candidate in extra_character_ids]
            extra_results = await asyncio.gather(
                *(self.graph_store.load_graph_v2(world_id, scope) for scope in extra_scopes),
                return_exceptions=True,
            )
            for scope, result in zip(extra_scopes, extra_results):
                if isinstance(result, Exception):
                    logger.warning("[recall] extra character scope load failed scope=%s: %s", scope, result)
                    continue
                scoped_data.append((scope, result))

        if len(loaded_chars) > 1:
            logger.info("[recall] loaded %d extra character scopes: %s", len(loaded_chars) - 1, loaded_chars - {character_id})

        merged = MemoryGraph.from_multi_scope(scoped_data)
        await self._inject_disposition_edges(world_id, character_id, merged)

        logger.info(
            "[recall] merged scopes=%d nodes=%d edges=%d",
            len(scoped_data),
            len(merged.graph.nodes),
            len(merged.graph.edges),
        )

        expanded_seeds: List[str] = []
        for seed in seed_nodes:
            expanded_seeds.append(seed)
            for prefix in ("person_", "character_", "location_", "area_"):
                if seed.startswith(prefix):
                    expanded_seeds.append(seed[len(prefix):])
                else:
                    expanded_seeds.append(f"{prefix}{seed}")
        valid_seeds = [seed for seed in expanded_seeds if merged.has_node(seed)]
        logger.info(
            "[recall] seeds original=%s valid=%s (expanded=%d matched=%d)",
            seed_nodes,
            valid_seeds,
            len(expanded_seeds),
            len(valid_seeds),
        )
        if not valid_seeds:
            return RecallResponse(
                seed_nodes=seed_nodes,
                activated_nodes={},
                subgraph=None,
                used_subgraph=False,
            )

        activated = spread_activation(merged, valid_seeds, config)
        subgraph_graph = extract_subgraph(merged, activated)
        subgraph = subgraph_graph.to_graph_data()
        subgraph.nodes = [
            node
            for node in subgraph.nodes
            if not (node.properties or {}).get("placeholder", False)
        ]

        return RecallResponse(
            seed_nodes=seed_nodes,
            activated_nodes=activated,
            subgraph=subgraph,
            used_subgraph=True,
        )

    async def recall_v4(
        self,
        *,
        world_id: str,
        character_id: str,
        seed_nodes: List[str],
        intent_type: Optional[str] = None,
        chapter_id: Optional[str] = None,
        area_id: Optional[str] = None,
        location_id: Optional[str] = None,
    ) -> RecallResponse:
        """V4 简化记忆召回 — location + area + character 三个作用域。

        相比 recall()（5+ 作用域: world + chapter + area + character + camp），
        此方法仅加载 location + area + character，减少 Firestore 读取和合并开销。
        """
        recall_cfg = self.RECALL_CONFIGS.get(intent_type or "", {})
        output_threshold = recall_cfg.get("output_threshold", 0.15)
        config = SpreadingActivationConfig(
            output_threshold=output_threshold,
            current_chapter_id=chapter_id,
        )

        scopes: List[GraphScope] = []
        calls: List[Any] = []

        # 作用域 1: location（如果有 chapter_id + area_id + location_id）
        if chapter_id and area_id and location_id:
            location_scope = GraphScope(
                scope_type="location",
                chapter_id=chapter_id,
                area_id=area_id,
                location_id=location_id,
            )
            scopes.append(location_scope)
            calls.append(self.graph_store.load_graph_v2(world_id, location_scope))

        # 作用域 2: area（如果有 chapter_id 和 area_id）
        if chapter_id and area_id:
            area_scope = GraphScope(scope_type="area", chapter_id=chapter_id, area_id=area_id)
            scopes.append(area_scope)
            calls.append(self.graph_store.load_graph_v2(world_id, area_scope))

        # 作用域 3: character
        char_scope = GraphScope(scope_type="character", character_id=character_id)
        scopes.append(char_scope)
        calls.append(self.graph_store.load_graph_v2(world_id, char_scope))

        results = await asyncio.gather(*calls, return_exceptions=True)
        scoped_data: List[Tuple[GraphScope, Any]] = []
        for scope, result in zip(scopes, results):
            if isinstance(result, Exception):
                logger.warning("[recall_v4] scope load failed scope=%s: %s", scope, result)
                continue
            scoped_data.append((scope, result))

        merged = MemoryGraph.from_multi_scope(scoped_data)
        await self._inject_disposition_edges(world_id, character_id, merged)

        logger.info(
            "[recall_v4] merged scopes=%d nodes=%d edges=%d",
            len(scoped_data),
            len(merged.graph.nodes),
            len(merged.graph.edges),
        )

        # 扩展种子节点（复用 recall 逻辑）
        expanded_seeds: List[str] = []
        for seed in seed_nodes:
            expanded_seeds.append(seed)
            for prefix in ("person_", "character_", "location_", "area_"):
                if seed.startswith(prefix):
                    expanded_seeds.append(seed[len(prefix):])
                else:
                    expanded_seeds.append(f"{prefix}{seed}")
        valid_seeds = [seed for seed in expanded_seeds if merged.has_node(seed)]
        logger.info(
            "[recall_v4] seeds original=%s valid=%s (expanded=%d matched=%d)",
            seed_nodes,
            valid_seeds,
            len(expanded_seeds),
            len(valid_seeds),
        )
        if not valid_seeds:
            return RecallResponse(
                seed_nodes=seed_nodes,
                activated_nodes={},
                subgraph=None,
                used_subgraph=False,
            )

        activated = spread_activation(merged, valid_seeds, config)
        subgraph_graph = extract_subgraph(merged, activated)
        subgraph = subgraph_graph.to_graph_data()
        subgraph.nodes = [
            node
            for node in subgraph.nodes
            if not (node.properties or {}).get("placeholder", False)
        ]

        return RecallResponse(
            seed_nodes=seed_nodes,
            activated_nodes=activated,
            subgraph=subgraph,
            used_subgraph=True,
        )

    async def _inject_disposition_edges(
        self,
        world_id: str,
        character_id: str,
        graph: MemoryGraph,
    ) -> None:
        """Inject approves edges based on Firestore dispositions."""
        from app.models.graph import MemoryEdge

        dispositions = await self.graph_store.get_all_dispositions(world_id, character_id)
        char_node_id = f"character_{character_id}" if not character_id.startswith("character_") else character_id

        for target_id, disp_data in dispositions.items():
            target_node_id = f"character_{target_id}" if not target_id.startswith("character_") else target_id
            if not graph.has_node(char_node_id) or not graph.has_node(target_node_id):
                continue
            approval = disp_data.get("approval", 0)
            weight = (approval + 100) / 200.0
            weight = max(0.0, min(1.0, weight))
            edge_id = f"disposition_{character_id}_{target_id}_approves"
            if not graph.has_edge(edge_id):
                graph.add_edge(
                    MemoryEdge(
                        id=edge_id,
                        source=char_node_id,
                        target=target_node_id,
                        relation="approves",
                        weight=weight,
                        properties={
                            "created_by": "disposition",
                            "approval": approval,
                            "trust": disp_data.get("trust", 0),
                        },
                    )
                )

    async def recall_for_role(
        self,
        *,
        role: str,
        world_id: str,
        character_id: str,
        seed_nodes: List[str],
        intent_type: Optional[str] = None,
        chapter_id: Optional[str] = None,
        area_id: Optional[str] = None,
        location_id: Optional[str] = None,
    ) -> RecallResponse:
        """Role-scoped recall — 按角色加载不同 scope。"""
        scope_types = self.ROLE_SCOPES.get(role)
        if scope_types is None:
            return await self.recall(
                world_id=world_id,
                character_id=character_id,
                seed_nodes=seed_nodes,
                intent_type=intent_type,
                chapter_id=chapter_id,
                area_id=area_id,
                location_id=location_id,
            )

        recall_cfg = self.RECALL_CONFIGS.get(intent_type or "", {})
        output_threshold = recall_cfg.get("output_threshold", 0.15)
        config = SpreadingActivationConfig(
            output_threshold=output_threshold,
            current_chapter_id=chapter_id,
        )

        scopes: List[GraphScope] = []
        calls: List[Any] = []

        for scope_type in scope_types:
            if scope_type == "character":
                s = GraphScope(scope_type="character", character_id=character_id)
                scopes.append(s)
                calls.append(self.graph_store.load_graph_v2(world_id, s))
            elif scope_type == "location" and chapter_id and area_id and location_id:
                s = GraphScope(
                    scope_type="location",
                    chapter_id=chapter_id,
                    area_id=area_id,
                    location_id=location_id,
                )
                scopes.append(s)
                calls.append(self.graph_store.load_graph_v2(world_id, s))
            elif scope_type == "area" and chapter_id and area_id:
                s = GraphScope(scope_type="area", chapter_id=chapter_id, area_id=area_id)
                scopes.append(s)
                calls.append(self.graph_store.load_graph_v2(world_id, s))
            elif scope_type == "chapter" and chapter_id:
                s = GraphScope(scope_type="chapter", chapter_id=chapter_id)
                scopes.append(s)
                calls.append(self.graph_store.load_graph_v2(world_id, s))
            elif scope_type == "camp":
                s = GraphScope(scope_type="camp")
                scopes.append(s)
                calls.append(self.graph_store.load_graph_v2(world_id, s))

        results = await asyncio.gather(*calls, return_exceptions=True)
        scoped_data: List[Tuple[GraphScope, Any]] = []
        for scope, result in zip(scopes, results):
            if isinstance(result, Exception):
                logger.warning("[recall_for_role] scope load failed scope=%s: %s", scope, result)
                continue
            scoped_data.append((scope, result))

        merged = MemoryGraph.from_multi_scope(scoped_data)
        await self._inject_disposition_edges(world_id, character_id, merged)

        logger.info(
            "[recall_for_role] role=%s merged scopes=%d nodes=%d edges=%d",
            role,
            len(scoped_data),
            len(merged.graph.nodes),
            len(merged.graph.edges),
        )

        expanded_seeds: List[str] = []
        for seed in seed_nodes:
            expanded_seeds.append(seed)
            for prefix in ("person_", "character_", "location_", "area_"):
                if seed.startswith(prefix):
                    expanded_seeds.append(seed[len(prefix):])
                else:
                    expanded_seeds.append(f"{prefix}{seed}")
        valid_seeds = [seed for seed in expanded_seeds if merged.has_node(seed)]

        logger.info(
            "[recall_for_role] seeds original=%s valid=%s (expanded=%d matched=%d)",
            seed_nodes,
            valid_seeds,
            len(expanded_seeds),
            len(valid_seeds),
        )

        if not valid_seeds:
            return RecallResponse(
                seed_nodes=seed_nodes,
                activated_nodes={},
                subgraph=None,
                used_subgraph=False,
            )

        activated = spread_activation(merged, valid_seeds, config)
        subgraph_graph = extract_subgraph(merged, activated)
        subgraph = subgraph_graph.to_graph_data()
        subgraph.nodes = [
            node
            for node in subgraph.nodes
            if not (node.properties or {}).get("placeholder", False)
        ]

        return RecallResponse(
            seed_nodes=seed_nodes,
            activated_nodes=activated,
            subgraph=subgraph,
            used_subgraph=True,
        )
