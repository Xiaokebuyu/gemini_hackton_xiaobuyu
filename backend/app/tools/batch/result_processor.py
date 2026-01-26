"""
Phase 6: Process batch results and merge graphs.

Parses LLM outputs from batch jobs, validates graph data,
merges duplicate nodes/edges, and prepares for import.

Run:
    cd backend
    python -m app.tools.batch.result_processor \
        --input data/goblin_slayer/batch_results.jsonl \
        --output data/goblin_slayer/extracted_graphs.jsonl

    # With merge options
    python -m app.tools.batch.result_processor \
        --input data/goblin_slayer/batch_results.jsonl \
        --output data/goblin_slayer/merged_graph.json \
        --merge-by-name \
        --dedupe-edges \
        --report data/goblin_slayer/merge_report.json
"""
import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.models.graph import GraphData, MemoryEdge, MemoryNode


def parse_batch_result_line(line: str) -> Tuple[str, Optional[Dict[str, Any]], Optional[str]]:
    """
    Parse a single line from batch results JSONL.

    Args:
        line: Raw JSONL line

    Returns:
        Tuple of (key, parsed_result, error_message)
    """
    try:
        data = json.loads(line)
    except json.JSONDecodeError as e:
        return "unknown", None, f"JSON parse error: {e}"

    key = data.get("key", "unknown")

    # Check for error response
    if "error" in data:
        return key, None, f"API error: {data['error']}"

    # Extract response
    response = data.get("response", {})

    # Handle different response formats
    if "candidates" in response:
        # Standard Gemini response format
        try:
            text = ""
            for candidate in response["candidates"]:
                content = candidate.get("content", {})
                for part in content.get("parts", []):
                    if "text" in part:
                        text += part["text"]

            # Parse JSON from text
            result = _parse_json_response(text)
            return key, result, None

        except Exception as e:
            return key, None, f"Response parse error: {e}"

    elif "text" in response:
        # Simplified response format
        result = _parse_json_response(response["text"])
        return key, result, None

    return key, None, "Unknown response format"


def _parse_json_response(text: str) -> Dict[str, Any]:
    """
    Parse JSON from LLM response text.

    Args:
        text: Raw response text

    Returns:
        Parsed JSON object
    """
    text = text.strip()

    # 处理空响应
    if not text:
        raise ValueError("空响应")

    def _normalize_result(data):
        """如果是列表，取第一个 dict 元素"""
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and ("nodes" in item or "edges" in item):
                    return item
            if data and isinstance(data[0], dict):
                return data[0]
            raise ValueError(f"列表中没有有效的图谱数据")
        return data

    # Try direct parse first
    try:
        result = json.loads(text)
        return _normalize_result(result)
    except json.JSONDecodeError:
        pass

    # Try extracting from code block
    if "```json" in text:
        match = re.search(r'```json\s*([\s\S]*?)\s*```', text)
        if match:
            result = json.loads(match.group(1))
            return _normalize_result(result)
    elif "```" in text:
        match = re.search(r'```\s*([\s\S]*?)\s*```', text)
        if match:
            result = json.loads(match.group(1))
            return _normalize_result(result)

    # Try finding JSON object
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        result = json.loads(match.group(0))
        return _normalize_result(result)

    raise ValueError(f"无法解析 JSON: {text[:200]}...")


def validate_graph_result(result: Dict[str, Any]) -> Tuple[List[Dict], List[Dict], List[str]]:
    """
    Validate and normalize graph extraction result.

    Args:
        result: Raw extraction result

    Returns:
        Tuple of (valid_nodes, valid_edges, errors)
    """
    errors = []
    valid_nodes = []
    valid_edges = []

    # Process nodes
    for node in result.get("nodes", []):
        if not node.get("id"):
            errors.append(f"Node missing id: {node}")
            continue
        if not node.get("type"):
            node["type"] = "unknown"
        if not node.get("name"):
            node["name"] = node["id"]

        # Normalize importance
        importance = node.get("importance", 0.5)
        try:
            importance = float(importance)
            importance = max(0.0, min(1.0, importance))
        except (TypeError, ValueError):
            importance = 0.5
        node["importance"] = importance

        # Ensure properties is dict
        if not isinstance(node.get("properties"), dict):
            node["properties"] = {}

        valid_nodes.append(node)

    # Process edges
    for edge in result.get("edges", []):
        if not edge.get("source") or not edge.get("target"):
            errors.append(f"Edge missing source/target: {edge}")
            continue

        # Generate id if missing
        if not edge.get("id"):
            src = edge["source"].split("_")[-1][:10]
            tgt = edge["target"].split("_")[-1][:10]
            rel = edge.get("relation", "related")[:10]
            edge["id"] = f"edge_{src}_{tgt}_{rel}"

        if not edge.get("relation"):
            edge["relation"] = "related"

        # Normalize weight
        weight = edge.get("weight", 1.0)
        try:
            weight = float(weight)
            weight = max(0.0, min(1.0, weight))
        except (TypeError, ValueError):
            weight = 1.0
        edge["weight"] = weight

        # Ensure properties is dict
        if not isinstance(edge.get("properties"), dict):
            edge["properties"] = {}

        valid_edges.append(edge)

    return valid_nodes, valid_edges, errors


def process_batch_results(
    input_path: Path,
) -> Tuple[List[Dict], List[Dict], Dict[str, Any]]:
    """
    Process all batch results from JSONL file.

    Args:
        input_path: Path to batch results JSONL

    Returns:
        Tuple of (all_nodes, all_edges, stats)
    """
    all_nodes = []
    all_edges = []
    stats = {
        "total_lines": 0,
        "successful": 0,
        "failed": 0,
        "parse_errors": [],
        "validation_errors": [],
    }

    with input_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            stats["total_lines"] += 1
            key, result, error = parse_batch_result_line(line)

            if error:
                stats["failed"] += 1
                stats["parse_errors"].append({"key": key, "error": error})
                continue

            if not result:
                stats["failed"] += 1
                stats["parse_errors"].append({"key": key, "error": "Empty result"})
                continue

            # Validate and extract
            nodes, edges, validation_errors = validate_graph_result(result)

            if validation_errors:
                for err in validation_errors:
                    stats["validation_errors"].append({"key": key, "error": err})

            all_nodes.extend(nodes)
            all_edges.extend(edges)
            stats["successful"] += 1

    return all_nodes, all_edges, stats


def merge_nodes(nodes: List[Dict], merge_by_name: bool = False) -> Tuple[List[Dict], Dict[str, str]]:
    """
    Merge duplicate nodes.

    Args:
        nodes: List of node dicts
        merge_by_name: Also merge by (type, name) combination

    Returns:
        Tuple of (merged_nodes, id_alias_map)
    """
    node_map: Dict[str, Dict] = {}
    name_key_map: Dict[Tuple[str, str], str] = {}
    id_alias_map: Dict[str, str] = {}

    for node in nodes:
        node_id = node["id"]

        if merge_by_name and node.get("type") and node.get("name"):
            key = (node["type"], node["name"].lower())
            if key in name_key_map:
                canonical_id = name_key_map[key]
                id_alias_map[node_id] = canonical_id
                # Merge into canonical
                if canonical_id in node_map:
                    _merge_node_into(node_map[canonical_id], node)
                continue
            name_key_map[key] = node_id

        if node_id in node_map:
            _merge_node_into(node_map[node_id], node)
        else:
            node_map[node_id] = node.copy()

    return list(node_map.values()), id_alias_map


def _merge_node_into(base: Dict, incoming: Dict) -> None:
    """Merge incoming node into base node."""
    # Keep higher importance
    base["importance"] = max(
        base.get("importance", 0),
        incoming.get("importance", 0)
    )

    # Merge properties
    base_props = base.get("properties", {})
    incoming_props = incoming.get("properties", {})

    for key, value in incoming_props.items():
        if key not in base_props:
            base_props[key] = value
        elif isinstance(base_props[key], list) and isinstance(value, list):
            # Merge lists
            base_props[key] = list(set(base_props[key] + value))
        elif isinstance(base_props[key], str) and isinstance(value, str):
            # Keep longer description
            if len(value) > len(base_props[key]):
                base_props[key] = value

    base["properties"] = base_props


def merge_edges(
    edges: List[Dict],
    id_alias_map: Dict[str, str],
    dedupe: bool = False,
) -> List[Dict]:
    """
    Merge duplicate edges, applying ID aliases.

    Args:
        edges: List of edge dicts
        id_alias_map: Node ID alias mapping
        dedupe: Deduplicate by (source, target, relation)

    Returns:
        Merged edges
    """
    edge_map: Dict[str, Dict] = {}
    edge_key_map: Dict[Tuple[str, str, str], str] = {}

    for edge in edges:
        # Apply aliases
        source = id_alias_map.get(edge["source"], edge["source"])
        target = id_alias_map.get(edge["target"], edge["target"])

        edge = edge.copy()
        edge["source"] = source
        edge["target"] = target

        edge_id = edge["id"]

        if edge_id in edge_map:
            _merge_edge_into(edge_map[edge_id], edge)
            continue

        if dedupe:
            key = (source, target, edge.get("relation", ""))
            if key in edge_key_map:
                existing_id = edge_key_map[key]
                _merge_edge_into(edge_map[existing_id], edge)
                continue
            edge_key_map[key] = edge_id

        edge_map[edge_id] = edge

    return list(edge_map.values())


def _merge_edge_into(base: Dict, incoming: Dict) -> None:
    """Merge incoming edge into base edge."""
    base["weight"] = max(
        base.get("weight", 0),
        incoming.get("weight", 0)
    )

    base_props = base.get("properties", {})
    incoming_props = incoming.get("properties", {})

    for key, value in incoming_props.items():
        if key not in base_props:
            base_props[key] = value

    base["properties"] = base_props


def validate_edge_references(
    edges: List[Dict],
    node_ids: Set[str],
) -> Tuple[List[Dict], List[Dict]]:
    """
    Validate that all edges reference existing nodes.

    Args:
        edges: List of edges
        node_ids: Set of valid node IDs

    Returns:
        Tuple of (valid_edges, invalid_edges)
    """
    valid = []
    invalid = []

    for edge in edges:
        if edge["source"] in node_ids and edge["target"] in node_ids:
            valid.append(edge)
        else:
            invalid.append(edge)

    return valid, invalid


def write_output(
    nodes: List[Dict],
    edges: List[Dict],
    output_path: Path,
    as_jsonl: bool = False,
) -> None:
    """
    Write output in JSON or JSONL format.

    Args:
        nodes: Merged nodes
        edges: Merged edges
        output_path: Output file path
        as_jsonl: Write as JSONL (one graph per line) or single JSON
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if as_jsonl:
        # Write as single line JSONL
        with output_path.open("w", encoding="utf-8") as f:
            data = {"nodes": nodes, "edges": edges}
            f.write(json.dumps(data, ensure_ascii=False))
            f.write("\n")
    else:
        # Write as pretty JSON
        data = {"nodes": nodes, "edges": edges}
        output_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process batch results and merge graphs"
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="Input batch results JSONL file"
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        help="Output file path (.json or .jsonl)"
    )
    parser.add_argument(
        "--merge-by-name",
        action="store_true",
        help="Merge nodes by (type, name) combination"
    )
    parser.add_argument(
        "--dedupe-edges",
        action="store_true",
        help="Deduplicate edges by (source, target, relation)"
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Save processing report to this path"
    )
    parser.add_argument(
        "--drop-orphan-edges",
        action="store_true",
        help="Remove edges that reference non-existent nodes"
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        return

    # Process batch results
    print(f"Processing batch results from {input_path}...")
    all_nodes, all_edges, stats = process_batch_results(input_path)

    print(f"  Processed {stats['total_lines']} lines")
    print(f"  Successful: {stats['successful']}")
    print(f"  Failed: {stats['failed']}")
    print(f"  Raw nodes: {len(all_nodes)}")
    print(f"  Raw edges: {len(all_edges)}")

    # Merge nodes
    print("Merging nodes...")
    merged_nodes, id_alias_map = merge_nodes(all_nodes, args.merge_by_name)
    print(f"  Merged nodes: {len(merged_nodes)}")
    if id_alias_map:
        print(f"  Aliased IDs: {len(id_alias_map)}")

    # Merge edges
    print("Merging edges...")
    merged_edges = merge_edges(all_edges, id_alias_map, args.dedupe_edges)
    print(f"  Merged edges: {len(merged_edges)}")

    # Validate edge references
    node_ids = {n["id"] for n in merged_nodes}
    valid_edges, orphan_edges = validate_edge_references(merged_edges, node_ids)

    if orphan_edges:
        print(f"  Orphan edges (missing nodes): {len(orphan_edges)}")
        if args.drop_orphan_edges:
            merged_edges = valid_edges
            print(f"  Dropped orphan edges")

    # Write output
    as_jsonl = output_path.suffix.lower() == ".jsonl"
    write_output(merged_nodes, merged_edges, output_path, as_jsonl)
    print(f"\nOutput written to: {output_path}")

    # Save report
    if args.report:
        report = {
            "timestamp": datetime.now().isoformat(),
            "input_file": str(input_path),
            "output_file": str(output_path),
            "stats": stats,
            "merge_stats": {
                "original_nodes": len(all_nodes),
                "merged_nodes": len(merged_nodes),
                "aliased_ids": len(id_alias_map),
                "original_edges": len(all_edges),
                "merged_edges": len(merged_edges),
                "orphan_edges": len(orphan_edges),
            }
        }

        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"Report saved to: {report_path}")

    # Print summary
    print(f"\nFinal graph:")
    print(f"  Nodes: {len(merged_nodes)}")
    print(f"  Edges: {len(merged_edges)}")

    # Node type distribution
    type_counts: Dict[str, int] = {}
    for node in merged_nodes:
        t = node.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
    print(f"  Node types: {type_counts}")


if __name__ == "__main__":
    main()
