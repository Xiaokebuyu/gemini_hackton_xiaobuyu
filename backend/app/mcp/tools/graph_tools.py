"""Graph tools for MCP server.

Provides scoped graph operations, disposition management, choice tracking,
and memory recall.
"""
import json
from typing import Any, Dict, List, Optional

from app.models.activation import SpreadingActivationConfig
from app.models.flash import RecallRequest, RecallResponse
from app.models.graph import MemoryEdge, MemoryNode
from app.models.graph_scope import GraphScope
from app.services.graph_store import GraphStore
from app.services.memory_graph import MemoryGraph
from app.services.spreading_activation import extract_subgraph, spread_activation

_graph_store = GraphStore()


def _build_scope(
    scope_type: str,
    chapter_id: Optional[str] = None,
    area_id: Optional[str] = None,
    location_id: Optional[str] = None,
    character_id: Optional[str] = None,
) -> GraphScope:
    """Build a GraphScope from individual parameters."""
    return GraphScope(
        scope_type=scope_type,
        chapter_id=chapter_id,
        area_id=area_id,
        location_id=location_id,
        character_id=character_id,
    )


def register(game_mcp) -> None:

    # ==================== Scoped Graph Tools (v2) ====================

    @game_mcp.tool()
    async def query_scoped_graph(
        world_id: str,
        scope_type: str,
        chapter_id: Optional[str] = None,
        area_id: Optional[str] = None,
        location_id: Optional[str] = None,
        character_id: Optional[str] = None,
    ) -> str:
        """Load a full graph from a specific scope.

        Scope types and required parameters:
        - world: no extra params needed
        - chapter: requires chapter_id
        - area: requires chapter_id + area_id
        - location: requires chapter_id + area_id + location_id
        - character: requires character_id
        - camp: no extra params needed

        Returns: JSON with nodes and edges arrays.
        """
        scope = _build_scope(scope_type, chapter_id, area_id, location_id, character_id)
        graph = await _graph_store.load_graph_v2(world_id, scope)
        return json.dumps(graph.model_dump(), ensure_ascii=False, indent=2, default=str)

    @game_mcp.tool()
    async def upsert_scoped_node(
        world_id: str,
        scope_type: str,
        node: Dict[str, Any],
        chapter_id: Optional[str] = None,
        area_id: Optional[str] = None,
        location_id: Optional[str] = None,
        character_id: Optional[str] = None,
    ) -> str:
        """Add or update a node in a scoped graph.

        The node dict must contain: id, type, name.
        Optional fields: importance, properties.

        Scope types: world, chapter, area, location, character, camp.
        See query_scoped_graph for required parameters per scope type.

        Returns: JSON with success status and node_id.
        """
        scope = _build_scope(scope_type, chapter_id, area_id, location_id, character_id)
        node_obj = MemoryNode(**node)
        await _graph_store.upsert_node_v2(world_id, scope, node_obj)
        return json.dumps({"success": True, "node_id": node_obj.id}, ensure_ascii=False)

    @game_mcp.tool()
    async def upsert_scoped_edge(
        world_id: str,
        scope_type: str,
        edge: Dict[str, Any],
        chapter_id: Optional[str] = None,
        area_id: Optional[str] = None,
        location_id: Optional[str] = None,
        character_id: Optional[str] = None,
    ) -> str:
        """Add or update an edge in a scoped graph.

        The edge dict must contain: id, source, target, relation.
        Optional fields: weight, properties.

        Both source and target nodes must already exist in the graph.

        Returns: JSON with success status and edge_id.
        """
        scope = _build_scope(scope_type, chapter_id, area_id, location_id, character_id)
        edge_obj = MemoryEdge(**edge)
        await _graph_store.upsert_edge_v2(world_id, scope, edge_obj)
        return json.dumps({"success": True, "edge_id": edge_obj.id}, ensure_ascii=False)

    @game_mcp.tool()
    async def query_local_subgraph(
        world_id: str,
        scope_type: str,
        seed_nodes: List[str],
        depth: int = 1,
        direction: str = "both",
        chapter_id: Optional[str] = None,
        area_id: Optional[str] = None,
        location_id: Optional[str] = None,
        character_id: Optional[str] = None,
    ) -> str:
        """Load a local subgraph from a scoped graph by traversing edges.

        Starts from the given seed nodes and follows edges up to the
        specified depth. More efficient than loading a full graph when
        only a neighborhood is needed.

        Args:
            world_id: World identifier.
            scope_type: world/chapter/area/location/character/camp.
            seed_nodes: List of node IDs to start traversal from.
            depth: How many hops to follow (default 1).
            direction: 'out', 'in', or 'both' (default 'both').
            chapter_id: Required for chapter/area/location scopes.
            area_id: Required for area/location scopes.
            location_id: Required for location scope.
            character_id: Required for character scope.

        Returns: JSON with nodes and edges in the local neighborhood.
        """
        scope = _build_scope(scope_type, chapter_id, area_id, location_id, character_id)
        graph = await _graph_store.load_local_subgraph_v2(
            world_id=world_id,
            scope=scope,
            seed_nodes=seed_nodes,
            depth=depth,
            direction=direction,
        )
        return json.dumps(graph.model_dump(), ensure_ascii=False, indent=2, default=str)

    # ==================== Disposition Tools ====================

    @game_mcp.tool()
    async def get_disposition(
        world_id: str,
        character_id: str,
        target_id: str,
    ) -> str:
        """Get a character's disposition toward a target.

        Returns approval (-100 to 100), trust (-100 to 100),
        fear (0 to 100), romance (0 to 100), and recent history.

        Returns: JSON disposition data, or null if no relationship exists.
        """
        result = await _graph_store.get_disposition(world_id, character_id, target_id)
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)

    # ==================== Choice Tools ====================

    @game_mcp.tool()
    async def record_choice(
        world_id: str,
        choice_id: str,
        description: str,
        chapter_id: Optional[str] = None,
        character_id: Optional[str] = None,
        consequences: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        write_to_character_graph: bool = True,
    ) -> str:
        """Record a player choice with its potential consequences.

        Each consequence should have a 'description' field.
        Consequences start as unresolved and can be resolved later
        using resolve_consequence.

        Args:
            world_id: World identifier.
            choice_id: Unique identifier for this choice.
            description: What the player chose to do.
            chapter_id: Which chapter the choice occurred in.
            character_id: Character owner of this choice for recall graphing.
            consequences: List of potential consequences.
            metadata: Additional context (game_day, location, etc.).
            write_to_character_graph: Mirror choice as a character graph node.

        Returns: JSON with the recorded choice document.
        """
        result = await _graph_store.record_choice(
            world_id=world_id,
            choice_id=choice_id,
            description=description,
            chapter_id=chapter_id,
            consequences=consequences,
            metadata=metadata,
            character_id=character_id,
            write_to_character_graph=write_to_character_graph,
        )
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)

    @game_mcp.tool()
    async def get_unresolved_consequences(
        world_id: str,
        chapter_id: Optional[str] = None,
    ) -> str:
        """Get all choices with unresolved consequences.

        Optionally filter by chapter. Useful for the GM to decide
        which past choices should create ripple effects.

        Returns: JSON array of choice documents with pending consequences.
        """
        result = await _graph_store.get_unresolved_consequences(world_id, chapter_id)
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)

    # ==================== Recall Memory (extended) ====================

    @game_mcp.tool()
    async def recall_memory(
        world_id: str,
        character_id: str,
        request: Dict[str, Any],
        chapter_id: Optional[str] = None,
        area_id: Optional[str] = None,
        include_narrative: bool = True,
        include_camp: bool = True,
    ) -> str:
        """Recall memories using spreading activation on merged graphs.

        Loads the character's personal graph and optionally merges in
        narrative (area/chapter) and camp graphs before running
        spreading activation. This enables cross-scope memory recall.

        Args:
            world_id: World identifier.
            character_id: Character whose memories to recall.
            request: Dict with recall parameters:
                - seed_nodes: List of node IDs to activate from (required).
                - config: SpreadingActivationConfig overrides (optional).
                  Supports CRPG fields: perspective_cross_decay,
                  cross_chapter_decay, causal_min_signal, current_chapter_id.
                - include_subgraph: Return activated subgraph (default true).
            chapter_id: Current chapter ID for loading narrative graphs.
            area_id: Current area ID for loading area-level narrative.
            include_narrative: Merge narrative graphs into activation
                (default true). Requires chapter_id.
            include_camp: Merge camp graph into activation (default true).

        Returns: JSON with activated_nodes scores and optional subgraph.
        """
        recall_req = request if isinstance(request, RecallRequest) else RecallRequest(**request)

        if not recall_req.seed_nodes:
            payload = RecallResponse(
                seed_nodes=[], activated_nodes={}, subgraph=None, used_subgraph=False
            ).model_dump()
            return json.dumps(payload, ensure_ascii=False, indent=2, default=str)

        # Build list of scoped graphs to merge
        scoped_data: List[tuple] = []

        # 1. Character personal graph (always loaded)
        char_scope = GraphScope.character(character_id)
        char_data = await _graph_store.load_graph_v2(world_id, char_scope)
        scoped_data.append((char_scope, char_data))

        # 2. Narrative graphs (area and/or chapter level)
        if include_narrative and chapter_id:
            if area_id:
                area_scope = GraphScope.area(chapter_id, area_id)
                area_data = await _graph_store.load_graph_v2(world_id, area_scope)
                scoped_data.append((area_scope, area_data))
            chapter_scope = GraphScope.chapter(chapter_id)
            chapter_data = await _graph_store.load_graph_v2(world_id, chapter_scope)
            scoped_data.append((chapter_scope, chapter_data))

        # 3. Camp graph (always accessible across chapters)
        if include_camp:
            camp_scope = GraphScope.camp()
            camp_data = await _graph_store.load_graph_v2(world_id, camp_scope)
            scoped_data.append((camp_scope, camp_data))

        # Merge all graphs with scope injection
        merged_graph = MemoryGraph.from_multi_scope(scoped_data)

        # Build config, auto-set current_chapter_id if provided
        config = recall_req.config or SpreadingActivationConfig()
        if chapter_id and not config.current_chapter_id:
            config = config.model_copy(update={"current_chapter_id": chapter_id})

        # Run spreading activation on merged graph
        activated = spread_activation(merged_graph, recall_req.seed_nodes, config)

        # Filter out placeholder nodes
        activated = {
            nid: score for nid, score in activated.items()
            if not (merged_graph.get_node(nid) and
                    (merged_graph.get_node(nid).properties or {}).get("placeholder", False))
        }

        subgraph_data = None
        if recall_req.include_subgraph:
            subgraph_graph = extract_subgraph(merged_graph, activated)
            subgraph_data = subgraph_graph.to_graph_data()
            subgraph_data.nodes = [
                n for n in subgraph_data.nodes
                if not (n.properties or {}).get("placeholder", False)
            ]

        result = RecallResponse(
            seed_nodes=recall_req.seed_nodes,
            activated_nodes=activated,
            subgraph=subgraph_data,
            used_subgraph=False,
        )
        return json.dumps(result.model_dump(), ensure_ascii=False, indent=2, default=str)
