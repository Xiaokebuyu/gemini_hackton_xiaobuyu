"""
Graph review tool for JSON/JSONL batches.

Run:
    cd backend
    python -m app.tools.graph_review --input data/llm_outputs.jsonl
    python -m app.tools.graph_review --input data/ --report report.json
"""
import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from app.models.graph import GraphData, MemoryEdge, MemoryNode
from app.models.graph_schema import GRAPH_NODE_TYPES, GRAPH_RELATIONS
from app.services.graph_schema import GraphSchemaOptions, validate_graph_data


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


def _count_by_key(items: Iterable[Dict], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        value = item.get(key, "") or ""
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: (-x[1], x[0])))


def review_graph(graph: GraphData) -> Tuple[dict, List[str]]:
    node_ids = [node.id for node in graph.nodes]
    edge_ids = [edge.id for edge in graph.edges]
    duplicate_nodes = _find_duplicates(node_ids)
    duplicate_edges = _find_duplicates(edge_ids)

    node_payloads = [node.model_dump() for node in graph.nodes]
    edge_payloads = [edge.model_dump() for edge in graph.edges]

    unknown_node_types = sorted({n.get("type") for n in node_payloads if n.get("type") not in GRAPH_NODE_TYPES})
    unknown_relations = sorted({e.get("relation") for e in edge_payloads if e.get("relation") not in GRAPH_RELATIONS})

    node_id_set = set(node_ids)
    missing_sources = sorted({edge.source for edge in graph.edges if edge.source not in node_id_set})
    missing_targets = sorted({edge.target for edge in graph.edges if edge.target not in node_id_set})

    options = GraphSchemaOptions(
        allow_unknown_node_types=True,
        allow_unknown_relations=True,
        validate_event_properties=False,
    )
    validation_errors = validate_graph_data(graph, options)

    report = {
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "node_type_counts": _count_by_key(node_payloads, "type"),
        "relation_counts": _count_by_key(edge_payloads, "relation"),
        "duplicate_node_ids": duplicate_nodes,
        "duplicate_edge_ids": duplicate_edges,
        "unknown_node_types": unknown_node_types,
        "unknown_relations": unknown_relations,
        "edges_missing_source": missing_sources,
        "edges_missing_target": missing_targets,
        "validation_error_count": len(validation_errors),
    }
    return report, validation_errors


def _find_duplicates(ids: List[str]) -> List[str]:
    seen = set()
    duplicates = []
    for item in ids:
        if item in seen:
            duplicates.append(item)
        else:
            seen.add(item)
    return sorted(set(duplicates))


def main() -> None:
    parser = argparse.ArgumentParser(description="Review graph JSON/JSONL batches")
    parser.add_argument("--input", required=True, help="Input file or directory (.json/.jsonl)")
    parser.add_argument("--report", default=None, help="Output report JSON path")
    args = parser.parse_args()

    input_path = Path(args.input)
    payloads = []
    for file_path in _collect_input_files(input_path):
        payloads.extend(_load_payloads(file_path))
    graph = _merge_graph_payloads(payloads)

    report, validation_errors = review_graph(graph)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if validation_errors:
        print("\nValidation errors (first 20):")
        for err in validation_errors[:20]:
            print(f"- {err}")

    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
