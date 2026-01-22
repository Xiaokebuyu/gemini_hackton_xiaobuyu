"""
Flash service for single-character memory operations.
"""
import uuid
from datetime import datetime
from typing import Optional

from app.models.flash import EventIngestRequest, EventIngestResponse, RecallRequest, RecallResponse
from app.services.graph_schema import GraphSchemaOptions, validate_edge, validate_node
from app.services.graph_store import GraphStore
from app.services.memory_graph import MemoryGraph
from app.services.reference_resolver import ReferenceResolver
from app.services.spreading_activation import (
    SpreadingActivationConfig,
    extract_subgraph,
    spread_activation,
)


class FlashService:
    """Character-level memory service."""

    def __init__(
        self,
        graph_store: Optional[GraphStore] = None,
        reference_resolver: Optional[ReferenceResolver] = None,
    ) -> None:
        self.graph_store = graph_store or GraphStore()
        self.reference_resolver = reference_resolver or ReferenceResolver(self.graph_store)

    async def ingest_event(
        self,
        world_id: str,
        character_id: str,
        request: EventIngestRequest,
    ) -> EventIngestResponse:
        event_id = request.event_id or f"event_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        node_count = 0
        edge_count = 0

        if request.nodes:
            for node in request.nodes:
                if request.validate:
                    options = GraphSchemaOptions(
                        allow_unknown_node_types=not request.strict,
                        allow_unknown_relations=True,
                        validate_event_properties=request.strict,
                    )
                    errors = validate_node(node, options)
                    if errors:
                        raise ValueError(f"Invalid node {node.id}: {errors}")
                await self.graph_store.upsert_node(
                    world_id,
                    "character",
                    node,
                    character_id=character_id,
                    index=request.write_indexes,
                )
                node_count += 1

        if request.edges:
            for edge in request.edges:
                if request.validate:
                    options = GraphSchemaOptions(
                        allow_unknown_node_types=True,
                        allow_unknown_relations=not request.strict,
                        validate_event_properties=False,
                    )
                    errors = validate_edge(edge, None, options)
                    if errors:
                        raise ValueError(f"Invalid edge {edge.id}: {errors}")
                await self.graph_store.upsert_edge(
                    world_id,
                    "character",
                    edge,
                    character_id=character_id,
                )
                edge_count += 1

        state_updated = False
        if request.state_updates:
            await self.graph_store.update_character_state(
                world_id,
                character_id,
                request.state_updates,
            )
            state_updated = True

        note = None
        if node_count == 0 and edge_count == 0:
            note = "no structured memory provided"

        return EventIngestResponse(
            event_id=event_id,
            node_count=node_count,
            edge_count=edge_count,
            state_updated=state_updated,
            note=note,
        )

    async def recall_memory(
        self,
        world_id: str,
        character_id: str,
        request: RecallRequest,
    ) -> RecallResponse:
        if not request.seed_nodes:
            return RecallResponse(seed_nodes=[], activated_nodes={}, subgraph=None, used_subgraph=False)

        if request.use_subgraph:
            graph_data = await self.graph_store.load_local_subgraph(
                world_id,
                "character",
                seed_nodes=request.seed_nodes,
                depth=request.subgraph_depth,
                direction=request.subgraph_direction,
                character_id=character_id,
            )
            used_subgraph = True
        else:
            graph_data = await self.graph_store.load_graph(world_id, "character", character_id)
            used_subgraph = False

        graph = MemoryGraph.from_graph_data(graph_data)
        config = request.config or SpreadingActivationConfig()
        activated = spread_activation(graph, request.seed_nodes, config)

        subgraph = None
        if request.include_subgraph:
            subgraph_graph = extract_subgraph(graph, activated)
            subgraph = subgraph_graph.to_graph_data()
            if request.resolve_refs:
                subgraph = await self.reference_resolver.resolve_graph_data(world_id, subgraph)

        return RecallResponse(
            seed_nodes=request.seed_nodes,
            activated_nodes=activated,
            subgraph=subgraph,
            used_subgraph=used_subgraph,
        )
