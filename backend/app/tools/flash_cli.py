"""
Flash CLI for manual ingest/recall.

Run:
    cd backend
    python -m app.tools.flash_cli ingest --world demo_world --character gorn --payload ingest.json
    python -m app.tools.flash_cli recall --world demo_world --character gorn --payload recall.json
"""
import argparse
import asyncio
import json
from pathlib import Path

from app.models.flash import EventIngestRequest, RecallRequest
from app.services.flash_service import FlashService


async def _ingest(world_id: str, character_id: str, payload_path: Path) -> None:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    request = EventIngestRequest(**payload)
    service = FlashService()
    response = await service.ingest_event(world_id, character_id, request)
    print(response.model_dump())


async def _recall(world_id: str, character_id: str, payload_path: Path) -> None:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    request = RecallRequest(**payload)
    service = FlashService()
    response = await service.recall_memory(world_id, character_id, request)
    print(response.model_dump())


def main() -> None:
    parser = argparse.ArgumentParser(description="Flash CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Ingest an event")
    ingest_parser.add_argument("--world", required=True)
    ingest_parser.add_argument("--character", required=True)
    ingest_parser.add_argument("--payload", required=True)

    recall_parser = subparsers.add_parser("recall", help="Recall memory")
    recall_parser.add_argument("--world", required=True)
    recall_parser.add_argument("--character", required=True)
    recall_parser.add_argument("--payload", required=True)

    args = parser.parse_args()
    payload_path = Path(args.payload)

    if args.command == "ingest":
        asyncio.run(_ingest(args.world, args.character, payload_path))
    elif args.command == "recall":
        asyncio.run(_recall(args.world, args.character, payload_path))


if __name__ == "__main__":
    main()
