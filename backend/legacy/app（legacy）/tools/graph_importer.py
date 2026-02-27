"""
Graph import tool for JSON/JSONL batches.

Run:
    cd backend
    python -m app.tools.graph_importer --input data.json --world demo_world --graph ontology
    python -m app.tools.graph_importer --input data.jsonl --world demo_world --graph gm --build-indexes --validate
"""
import argparse
import asyncio
import json
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from google.cloud import firestore

from app.config import settings
from app.models.graph import GraphData, MemoryEdge, MemoryNode
from app.world.graph.schema import GraphSchemaOptions, validate_graph_data


def _get_base_ref(
    db: firestore.Client,
    world_id: str,
    graph_type: str,
    character_id: Optional[str] = None,
) -> firestore.DocumentReference:
    if graph_type == "character":
        if not character_id:
            raise ValueError("character graph requires character_id")
        return (
            db.collection("worlds")
            .document(world_id)
            .collection("characters")
            .document(character_id)
        )
    return (
        db.collection("worlds")
        .document(world_id)
        .collection("graphs")
        .document(graph_type)
    )


def _sanitize_index_key(value: str) -> str:
    return value.replace("/", "_").strip()


def _index_node_operations(
    base_ref: firestore.DocumentReference,
    node: MemoryNode,
) -> list:
    operations = []
    payload = {
        "node_id": node.id,
        "name": node.name,
        "type": node.type,
    }
    if node.type:
        type_ref = (
            base_ref.collection("type_index")
            .document(node.type)
            .collection("nodes")
            .document(node.id)
        )
        operations.append((type_ref, payload, True))
    if node.name:
        name_key = _sanitize_index_key(node.name.lower())
        name_ref = (
            base_ref.collection("name_index")
            .document(name_key)
            .collection("nodes")
            .document(node.id)
        )
        operations.append((name_ref, payload, True))
    return operations


def _commit_in_batches(
    db: firestore.Client,
    operations: Iterable[Tuple[firestore.DocumentReference, dict, bool]],
) -> None:
    batch = db.batch()
    op_count = 0
    for doc_ref, payload, merge in operations:
        batch.set(doc_ref, payload, merge=merge)
        op_count += 1
        if op_count >= 450:
            batch.commit()
            batch = db.batch()
            op_count = 0
    if op_count:
        batch.commit()


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


def _merge_graph_payloads(payloads: Iterable[dict]) -> GraphData:
    nodes: List[MemoryNode] = []
    edges: List[MemoryEdge] = []
    for payload in payloads:
        for node in payload.get("nodes", []):
            nodes.append(MemoryNode(**node))
        for edge in payload.get("edges", []):
            edges.append(MemoryEdge(**edge))
    return GraphData(nodes=nodes, edges=edges)


def _collect_input_files(path: Path) -> List[Path]:
    if path.is_dir():
        files = []
        for item in sorted(path.iterdir()):
            if item.suffix.lower() in {".json", ".jsonl"}:
                files.append(item)
        return files
    return [path]


async def import_graph(
    input_path: Path,
    world_id: str,
    graph_type: str,
    character_id: str | None,
    merge: bool,
    build_indexes: bool,
    validate: bool,
    strict: bool,
) -> Tuple[int, int]:
    db = firestore.Client(database=settings.firestore_database)
    payloads = []
    for file_path in _collect_input_files(input_path):
        payloads.extend(_load_payloads(file_path))
    graph_data = _merge_graph_payloads(payloads)

    if validate:
        options = GraphSchemaOptions(
            allow_unknown_node_types=not strict,
            allow_unknown_relations=not strict,
            validate_event_properties=strict,
        )
        errors = validate_graph_data(graph_data, options)
        if errors:
            raise ValueError(f"Schema validation failed: {errors}")

    base_ref = _get_base_ref(db, world_id, graph_type, character_id)
    nodes_ref = base_ref.collection("nodes")
    edges_ref = base_ref.collection("edges")

    operations = []
    for node in graph_data.nodes:
        operations.append((nodes_ref.document(node.id), node.model_dump(), merge))
    for edge in graph_data.edges:
        operations.append((edges_ref.document(edge.id), edge.model_dump(), merge))
    if build_indexes:
        for node in graph_data.nodes:
            operations.extend(_index_node_operations(base_ref, node))
    _commit_in_batches(db, operations)

    return len(graph_data.nodes), len(graph_data.edges)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import graph data from JSON/JSONL")
    parser.add_argument("--input", required=True, help="Input file or directory (.json/.jsonl)")
    parser.add_argument("--world", required=True, help="World ID")
    parser.add_argument("--graph", required=True, help="Graph type: gm/ontology/character")
    parser.add_argument("--character", default=None, help="Character ID (when graph=character)")
    parser.add_argument("--no-merge", action="store_true", help="Disable merge writes")
    parser.add_argument("--build-indexes", action="store_true", help="Build indexes on import")
    parser.add_argument("--validate", action="store_true", help="Validate schema before import")
    parser.add_argument("--strict", action="store_true", help="Strict schema validation")
    args = parser.parse_args()

    try:
        merge = not args.no_merge
        node_count, edge_count = asyncio.run(
            import_graph(
                input_path=Path(args.input),
                world_id=args.world,
                graph_type=args.graph,
                character_id=args.character,
                merge=merge,
                build_indexes=args.build_indexes,
                validate=args.validate,
                strict=args.strict,
            )
        )
        print(f"Imported nodes={node_count}, edges={edge_count}")
    except Exception as exc:
        print(f"Import failed: {exc}")


if __name__ == "__main__":
    main()
