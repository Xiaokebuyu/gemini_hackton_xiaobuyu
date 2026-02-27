"""
Graph index rebuild tool.

Run:
    cd backend
    python -m app.tools.graph_indexer --world demo_world --graph gm
    python -m app.tools.graph_indexer --world demo_world --graph character --character gorn --clear
"""
import argparse
import asyncio
from typing import Optional

from google.cloud import firestore

from app.config import settings
from app.models.graph import MemoryNode


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


def _clear_subcollection(col_ref: firestore.CollectionReference, subcol_name: str) -> None:
    for doc in col_ref.stream():
        for sub_doc in doc.reference.collection(subcol_name).stream():
            sub_doc.reference.delete()
        doc.reference.delete()


def _commit_in_batches(db: firestore.Client, operations: list) -> None:
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


async def rebuild_indexes(world_id: str, graph_type: str, character_id: str | None, clear: bool) -> None:
    db = firestore.Client(database=settings.firestore_database)
    base_ref = _get_base_ref(db, world_id, graph_type, character_id)
    nodes_ref = base_ref.collection("nodes")

    if clear:
        _clear_subcollection(base_ref.collection("type_index"), "nodes")
        _clear_subcollection(base_ref.collection("name_index"), "nodes")
        _clear_subcollection(base_ref.collection("timeline"), "events")

    operations = []
    count = 0
    for doc in nodes_ref.stream():
        data = doc.to_dict() or {}
        if not data:
            continue
        if "id" not in data:
            data["id"] = doc.id
        node = MemoryNode(**data)
        operations.extend(_index_node_operations(base_ref, node))
        count += 1
        if len(operations) >= 400:
            _commit_in_batches(db, operations)
            operations = []
    if operations:
        _commit_in_batches(db, operations)

    print(f"Rebuilt indexes for {count} nodes.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild graph indexes")
    parser.add_argument("--world", required=True, help="World ID")
    parser.add_argument("--graph", required=True, help="Graph type: gm/ontology/character")
    parser.add_argument("--character", default=None, help="Character ID (when graph=character)")
    parser.add_argument("--clear", action="store_true", help="Clear existing indexes first")
    args = parser.parse_args()

    asyncio.run(rebuild_indexes(args.world, args.graph, args.character, args.clear))


if __name__ == "__main__":
    main()
