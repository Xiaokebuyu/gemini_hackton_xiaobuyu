"""
图谱基础设施 API 路由
"""
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.models.graph import GraphData, MemoryEdge, MemoryNode
from app.services.graph_schema import GraphSchemaOptions, validate_edge, validate_graph_data, validate_node
from app.services.graph_store import GraphStore
from app.services.memory_graph import MemoryGraph
from app.services.reference_resolver import ReferenceResolver
from app.services.spreading_activation import (
    SpreadingActivationConfig,
    extract_subgraph,
    spread_activation,
)


router = APIRouter()
graph_store = GraphStore()
reference_resolver = ReferenceResolver(graph_store)

VALID_GRAPH_TYPES = {"gm", "ontology", "character"}


class ActivationRequest(BaseModel):
    """激活扩散请求"""

    seed_nodes: List[str]
    config: Optional[SpreadingActivationConfig] = None
    include_subgraph: bool = False


class ActivationResponse(BaseModel):
    """激活扩散响应"""

    activated_nodes: Dict[str, float]
    subgraph: Optional[GraphData] = None


def _validate_graph_type(graph_type: str) -> None:
    if graph_type not in VALID_GRAPH_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"graph_type 必须是 {sorted(VALID_GRAPH_TYPES)} 之一",
        )


def _resolve_character_id(graph_type: str, character_id: Optional[str]) -> Optional[str]:
    if graph_type == "character" and not character_id:
        raise HTTPException(status_code=400, detail="character 图谱需要 character_id")
    return character_id


def _schema_options(strict: bool) -> GraphSchemaOptions:
    return GraphSchemaOptions(
        allow_unknown_node_types=not strict,
        allow_unknown_relations=not strict,
        validate_event_properties=strict,
    )


@router.get("/graphs/{world_id}/{graph_type}")
async def get_graph(
    world_id: str,
    graph_type: str,
    character_id: Optional[str] = Query(default=None),
    resolve_refs: bool = Query(default=False),
) -> GraphData:
    """获取整张图谱"""
    _validate_graph_type(graph_type)
    character_id = _resolve_character_id(graph_type, character_id)
    graph_data = await graph_store.load_graph(world_id, graph_type, character_id)
    if resolve_refs:
        graph_data = await reference_resolver.resolve_graph_data(world_id, graph_data)
    return graph_data


@router.get("/graphs/{world_id}/{graph_type}/subgraph")
async def get_subgraph(
    world_id: str,
    graph_type: str,
    seed_nodes: List[str] = Query(default=[]),
    depth: int = Query(default=1, ge=0, le=6),
    direction: str = Query(default="both"),
    character_id: Optional[str] = Query(default=None),
    fast: bool = Query(default=False),
    resolve_refs: bool = Query(default=False),
) -> GraphData:
    """获取种子节点的局部子图"""
    _validate_graph_type(graph_type)
    character_id = _resolve_character_id(graph_type, character_id)
    try:
        if fast:
            graph_data = await graph_store.load_local_subgraph(
                world_id,
                graph_type,
                seed_nodes,
                depth=depth,
                direction=direction,
                character_id=character_id,
            )
            if resolve_refs:
                graph_data = await reference_resolver.resolve_graph_data(world_id, graph_data)
            return graph_data
        graph_data = await graph_store.load_graph(world_id, graph_type, character_id)
        graph = MemoryGraph.from_graph_data(graph_data)
        node_ids = graph.expand_nodes(seed_nodes, depth=depth, direction=direction)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    subgraph = graph.subgraph(node_ids)
    subgraph_data = subgraph.to_graph_data()
    if resolve_refs:
        subgraph_data = await reference_resolver.resolve_graph_data(world_id, subgraph_data)
    return subgraph_data


@router.get("/graphs/{world_id}/{graph_type}/index/type/{node_type}")
async def get_nodes_by_type(
    world_id: str,
    graph_type: str,
    node_type: str,
    character_id: Optional[str] = Query(default=None),
    include_nodes: bool = Query(default=False),
) -> Dict[str, List]:
    """按类型索引查询节点"""
    _validate_graph_type(graph_type)
    character_id = _resolve_character_id(graph_type, character_id)
    hits = await graph_store.query_index_by_type(world_id, graph_type, node_type, character_id)
    if include_nodes:
        node_ids = [hit.get("node_id") for hit in hits if hit.get("node_id")]
        nodes = await graph_store.get_nodes_by_ids(world_id, graph_type, node_ids, character_id)
        return {"nodes": nodes}
    return {"hits": hits}


@router.get("/graphs/{world_id}/{graph_type}/index/name/{name}")
async def get_nodes_by_name(
    world_id: str,
    graph_type: str,
    name: str,
    character_id: Optional[str] = Query(default=None),
    include_nodes: bool = Query(default=False),
) -> Dict[str, List]:
    """按名称索引查询节点"""
    _validate_graph_type(graph_type)
    character_id = _resolve_character_id(graph_type, character_id)
    hits = await graph_store.query_index_by_name(world_id, graph_type, name, character_id)
    if include_nodes:
        node_ids = [hit.get("node_id") for hit in hits if hit.get("node_id")]
        nodes = await graph_store.get_nodes_by_ids(world_id, graph_type, node_ids, character_id)
        return {"nodes": nodes}
    return {"hits": hits}


@router.get("/graphs/{world_id}/{graph_type}/index/day/{day}")
async def get_events_by_day(
    world_id: str,
    graph_type: str,
    day: str,
    character_id: Optional[str] = Query(default=None),
    include_nodes: bool = Query(default=False),
) -> Dict[str, List]:
    """按时间线索引查询事件"""
    _validate_graph_type(graph_type)
    character_id = _resolve_character_id(graph_type, character_id)
    hits = await graph_store.query_index_by_day(world_id, graph_type, day, character_id)
    if include_nodes:
        node_ids = [hit.get("node_id") for hit in hits if hit.get("node_id")]
        nodes = await graph_store.get_nodes_by_ids(world_id, graph_type, node_ids, character_id)
        return {"nodes": nodes}
    return {"hits": hits}


@router.post("/graphs/{world_id}/{graph_type}")
async def save_graph(
    world_id: str,
    graph_type: str,
    graph: GraphData,
    character_id: Optional[str] = Query(default=None),
    merge: bool = Query(default=True),
    build_indexes: bool = Query(default=False),
    validate: bool = Query(default=False),
    strict: bool = Query(default=False),
) -> Dict[str, int]:
    """保存整张图谱"""
    _validate_graph_type(graph_type)
    character_id = _resolve_character_id(graph_type, character_id)
    if validate:
        options = _schema_options(strict)
        errors = validate_graph_data(graph, options)
        if errors:
            raise HTTPException(status_code=400, detail={"errors": errors})
    await graph_store.save_graph(
        world_id,
        graph_type,
        graph,
        character_id,
        merge=merge,
        build_indexes=build_indexes,
    )
    return {"nodes": len(graph.nodes), "edges": len(graph.edges)}


@router.get("/graphs/{world_id}/{graph_type}/nodes/{node_id}")
async def get_node(
    world_id: str,
    graph_type: str,
    node_id: str,
    character_id: Optional[str] = Query(default=None),
) -> MemoryNode:
    """获取单个节点"""
    _validate_graph_type(graph_type)
    character_id = _resolve_character_id(graph_type, character_id)
    node = await graph_store.get_node(world_id, graph_type, node_id, character_id)
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    return node


@router.post("/graphs/{world_id}/{graph_type}/nodes")
async def upsert_node(
    world_id: str,
    graph_type: str,
    node: MemoryNode,
    character_id: Optional[str] = Query(default=None),
    index: bool = Query(default=False),
    validate: bool = Query(default=False),
    strict: bool = Query(default=False),
) -> Dict[str, str]:
    """写入单个节点"""
    _validate_graph_type(graph_type)
    character_id = _resolve_character_id(graph_type, character_id)
    if validate:
        options = _schema_options(strict)
        errors = validate_node(node, options)
        if errors:
            raise HTTPException(status_code=400, detail={"errors": errors})
    await graph_store.upsert_node(world_id, graph_type, node, character_id, index=index)
    return {"node_id": node.id}


@router.get("/graphs/{world_id}/{graph_type}/edges/{edge_id}")
async def get_edge(
    world_id: str,
    graph_type: str,
    edge_id: str,
    character_id: Optional[str] = Query(default=None),
) -> MemoryEdge:
    """获取单条边"""
    _validate_graph_type(graph_type)
    character_id = _resolve_character_id(graph_type, character_id)
    edge = await graph_store.get_edge(world_id, graph_type, edge_id, character_id)
    if not edge:
        raise HTTPException(status_code=404, detail="边不存在")
    return edge


@router.post("/graphs/{world_id}/{graph_type}/edges")
async def upsert_edge(
    world_id: str,
    graph_type: str,
    edge: MemoryEdge,
    character_id: Optional[str] = Query(default=None),
    validate: bool = Query(default=False),
    strict: bool = Query(default=False),
) -> Dict[str, str]:
    """写入单条边"""
    _validate_graph_type(graph_type)
    character_id = _resolve_character_id(graph_type, character_id)
    if validate:
        options = _schema_options(strict)
        errors = validate_edge(edge, None, options)
        if errors:
            raise HTTPException(status_code=400, detail={"errors": errors})
    await graph_store.upsert_edge(world_id, graph_type, edge, character_id)
    return {"edge_id": edge.id}


@router.post("/graphs/{world_id}/{graph_type}/activation")
async def activation(
    world_id: str,
    graph_type: str,
    payload: ActivationRequest,
    character_id: Optional[str] = Query(default=None),
    resolve_refs: bool = Query(default=False),
) -> ActivationResponse:
    """运行激活扩散"""
    _validate_graph_type(graph_type)
    character_id = _resolve_character_id(graph_type, character_id)
    graph_data = await graph_store.load_graph(world_id, graph_type, character_id)
    graph = MemoryGraph.from_graph_data(graph_data)
    config = payload.config or SpreadingActivationConfig()
    activated = spread_activation(graph, payload.seed_nodes, config)
    subgraph = None
    if payload.include_subgraph:
        subgraph_graph = extract_subgraph(graph, activated)
        subgraph = subgraph_graph.to_graph_data()
        if resolve_refs:
            subgraph = await reference_resolver.resolve_graph_data(world_id, subgraph)
    return ActivationResponse(activated_nodes=activated, subgraph=subgraph)
