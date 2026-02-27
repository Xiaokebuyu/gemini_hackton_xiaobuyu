"""
Graph merge tool for JSON/JSONL batches.

Run:
    cd backend
    python -m app.tools.graph_merge --input data/llm_outputs.jsonl --output data/merged.json
    python -m app.tools.graph_merge --input data/ --merge-by-name --report merge_report.json
    python -m app.tools.graph_merge --input data/llm_outputs.jsonl --alias alias_map.json
"""
import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from app.models.graph import GraphData, MemoryEdge, MemoryNode


def _load_payloads(path: Path) -> List[dict]:
    if path.suffix.lower() == ".jsonl":
        payloads = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            payloads.append(json.loads(line))
        return payloads
    return [json.loads(path.read_text(encoding="utf-8"))]


def _collect_input_files(path: Path) -> List[Path]:
    if path.is_dir():
        files = []
        for item in sorted(path.iterdir()):
            if item.suffix.lower() in {".json", ".jsonl"}:
                files.append(item)
        return files
    return [path]


def _merge_graph_payloads(payloads: Iterable[dict]) -> GraphData:
    nodes: List[MemoryNode] = []
    edges: List[MemoryEdge] = []
    for payload in payloads:
        for node in payload.get("nodes", []):
            nodes.append(MemoryNode(**node))
        for edge in payload.get("edges", []):
            edges.append(MemoryEdge(**edge))
    return GraphData(nodes=nodes, edges=edges)


def _merge_nodes(base: MemoryNode, incoming: MemoryNode) -> MemoryNode:
    merged = base.model_dump()
    if not merged.get("name") and incoming.name:
        merged["name"] = incoming.name
    if not merged.get("type") and incoming.type:
        merged["type"] = incoming.type
    merged["importance"] = max(base.importance, incoming.importance)
    properties = dict(base.properties or {})
    properties.update(incoming.properties or {})
    merged["properties"] = properties
    return MemoryNode(**merged)


def _merge_edges(base: MemoryEdge, incoming: MemoryEdge) -> MemoryEdge:
    merged = base.model_dump()
    if not merged.get("relation") and incoming.relation:
        merged["relation"] = incoming.relation
    merged["weight"] = max(base.weight, incoming.weight)
    properties = dict(base.properties or {})
    properties.update(incoming.properties or {})
    merged["properties"] = properties
    return MemoryEdge(**merged)


def _load_alias_map(path: Optional[Path]) -> Dict[str, str]:
    if not path:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data.items()}


def merge_graph(
    graph: GraphData,
    alias_map: Dict[str, str],
    merge_by_name: bool,
    dedupe_edges: bool,
) -> Tuple[GraphData, dict]:
    report = {
        "merged_nodes_by_id": 0,
        "merged_nodes_by_name": 0,
        "merged_edges_by_id": 0,
        "deduped_edges": 0,
        "alias_applied": 0,
    }

    node_map: Dict[str, MemoryNode] = {}
    name_key_map: Dict[Tuple[str, str], str] = {}
    id_alias_map: Dict[str, str] = {}

    for node in graph.nodes:
        node_id = alias_map.get(node.id, node.id)
        if node_id != node.id:
            report["alias_applied"] += 1
        node = MemoryNode(**{**node.model_dump(), "id": node_id})

        if merge_by_name and node.type and node.name:
            key = (node.type, node.name.lower())
            if key in name_key_map:
                canonical_id = name_key_map[key]
                id_alias_map[node.id] = canonical_id
                if canonical_id in node_map:
                    node_map[canonical_id] = _merge_nodes(node_map[canonical_id], node)
                    report["merged_nodes_by_name"] += 1
                continue
            name_key_map[key] = node.id

        if node.id in node_map:
            node_map[node.id] = _merge_nodes(node_map[node.id], node)
            report["merged_nodes_by_id"] += 1
        else:
            node_map[node.id] = node

    edge_map: Dict[str, MemoryEdge] = {}
    edge_key_map: Dict[Tuple[str, str, str], str] = {}

    for edge in graph.edges:
        edge_id = edge.id
        source = id_alias_map.get(alias_map.get(edge.source, edge.source), alias_map.get(edge.source, edge.source))
        target = id_alias_map.get(alias_map.get(edge.target, edge.target), alias_map.get(edge.target, edge.target))
        edge = MemoryEdge(
            **{
                **edge.model_dump(),
                "source": source,
                "target": target,
                "id": edge_id,
            }
        )

        if edge.id in edge_map:
            edge_map[edge.id] = _merge_edges(edge_map[edge.id], edge)
            report["merged_edges_by_id"] += 1
            continue

        if dedupe_edges:
            key = (edge.source, edge.target, edge.relation)
            if key in edge_key_map:
                existing_id = edge_key_map[key]
                edge_map[existing_id] = _merge_edges(edge_map[existing_id], edge)
                report["deduped_edges"] += 1
                continue
            edge_key_map[key] = edge.id

        edge_map[edge.id] = edge

    return GraphData(nodes=list(node_map.values()), edges=list(edge_map.values())), report


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge graph JSON/JSONL batches")
    parser.add_argument("--input", required=True, help="Input file or directory (.json/.jsonl)")
    parser.add_argument("--output", default="merged_graph.json", help="Output JSON path")
    parser.add_argument("--alias", default=None, help="Alias map JSON file")
    parser.add_argument("--merge-by-name", action="store_true", help="Merge nodes by (type, name)")
    parser.add_argument("--dedupe-edges", action="store_true", help="Dedupe edges by (source, target, relation)")
    parser.add_argument("--report", default=None, help="Output merge report JSON path")
    args = parser.parse_args()

    input_path = Path(args.input)
    payloads = []
    for file_path in _collect_input_files(input_path):
        payloads.extend(_load_payloads(file_path))
    graph = _merge_graph_payloads(payloads)

    alias_map = _load_alias_map(Path(args.alias)) if args.alias else {}
    merged_graph, report = merge_graph(
        graph,
        alias_map=alias_map,
        merge_by_name=args.merge_by_name,
        dedupe_edges=args.dedupe_edges,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(merged_graph.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Merged nodes={len(merged_graph.nodes)}, edges={len(merged_graph.edges)}")


if __name__ == "__main__":
    main()
