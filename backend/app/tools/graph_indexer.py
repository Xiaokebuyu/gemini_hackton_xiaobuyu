"""
Graph index rebuild tool.

Run:
    cd backend
    python -m app.tools.graph_indexer --world demo_world --graph gm
    python -m app.tools.graph_indexer --world demo_world --graph character --character gorn --clear
"""
import argparse
import asyncio

from app.services.graph_store import GraphStore


async def rebuild_indexes(world_id: str, graph_type: str, character_id: str | None, clear: bool) -> None:
    store = GraphStore()
    count = await store.rebuild_indexes(
        world_id=world_id,
        graph_type=graph_type,
        character_id=character_id,
        clear_first=clear,
    )
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
