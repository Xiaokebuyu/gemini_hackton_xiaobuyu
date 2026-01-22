"""
Pro CLI for profile/context.

Run:
    cd backend
    python -m app.tools.pro_cli profile --world demo_world --character gorn --payload profile.json
    python -m app.tools.pro_cli context --world demo_world --character gorn --payload context.json
"""
import argparse
import asyncio
import json
from pathlib import Path

from app.models.pro import CharacterProfile, ProContextRequest
from app.services.pro_service import ProService


async def _set_profile(world_id: str, character_id: str, payload_path: Path) -> None:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    profile = CharacterProfile(**payload)
    service = ProService()
    response = await service.set_profile(world_id, character_id, profile)
    print(response.model_dump())


async def _build_context(world_id: str, character_id: str, payload_path: Path) -> None:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    request = ProContextRequest(**payload)
    service = ProService()
    response = await service.build_context(world_id, character_id, request)
    print(response.model_dump())


def main() -> None:
    parser = argparse.ArgumentParser(description="Pro CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    profile_parser = subparsers.add_parser("profile", help="Set character profile")
    profile_parser.add_argument("--world", required=True)
    profile_parser.add_argument("--character", required=True)
    profile_parser.add_argument("--payload", required=True)

    context_parser = subparsers.add_parser("context", help="Build Pro context")
    context_parser.add_argument("--world", required=True)
    context_parser.add_argument("--character", required=True)
    context_parser.add_argument("--payload", required=True)

    args = parser.parse_args()
    payload_path = Path(args.payload)

    if args.command == "profile":
        asyncio.run(_set_profile(args.world, args.character, payload_path))
    elif args.command == "context":
        asyncio.run(_build_context(args.world, args.character, payload_path))


if __name__ == "__main__":
    main()
