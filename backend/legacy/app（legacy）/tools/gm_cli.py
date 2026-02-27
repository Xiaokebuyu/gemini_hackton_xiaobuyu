"""
GM CLI for event ingest.

Run:
    cd backend
    python -m app.tools.gm_cli ingest --world demo_world --payload examples/phase5/gm_event.json
"""
import argparse
import asyncio
import json
from pathlib import Path

from app.models.event import GMEventIngestRequest
from app.services.admin.admin_coordinator import AdminCoordinator


async def _ingest(world_id: str, payload_path: Path) -> None:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    request = GMEventIngestRequest(**payload)
    service = AdminCoordinator.get_instance()
    response = await service.ingest_event(world_id, request)
    print(response.model_dump())


def main() -> None:
    parser = argparse.ArgumentParser(description="GM CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Ingest a GM event")
    ingest_parser.add_argument("--world", required=True)
    ingest_parser.add_argument("--payload", required=True)

    args = parser.parse_args()
    payload_path = Path(args.payload)

    if args.command == "ingest":
        asyncio.run(_ingest(args.world, payload_path))


if __name__ == "__main__":
    main()
