"""
Game loop CLI.

Run:
    cd backend
    python -m app.tools.game_cli create --world demo_world --payload examples/phase6/session.json
    python -m app.tools.game_cli scene --world demo_world --session sess_xxx --payload examples/phase6/scene.json
    python -m app.tools.game_cli combat-start --world demo_world --session sess_xxx --payload examples/phase6/combat_start.json
    python -m app.tools.game_cli combat-resolve --world demo_world --session sess_xxx --payload examples/phase6/combat_resolve.json
"""
import argparse
import asyncio
import json
from pathlib import Path

from app.models.game import (
    CombatResolveRequest,
    CombatStartRequest,
    CreateSessionRequest,
    UpdateSceneRequest,
)
from app.services.admin.admin_coordinator import AdminCoordinator


async def _create_session(world_id: str, payload_path: Path) -> None:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    request = CreateSessionRequest(**payload)
    service = AdminCoordinator.get_instance()
    response = await service.create_session(world_id, request)
    print(response.model_dump())


async def _update_scene(world_id: str, session_id: str, payload_path: Path) -> None:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    request = UpdateSceneRequest(**payload)
    service = AdminCoordinator.get_instance()
    response = await service.update_scene(world_id, session_id, request)
    print(response.model_dump())


async def _start_combat(world_id: str, session_id: str, payload_path: Path) -> None:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    request = CombatStartRequest(**payload)
    service = AdminCoordinator.get_instance()
    response = await service.start_combat(world_id, session_id, request)
    print(response.model_dump())


async def _resolve_combat(world_id: str, session_id: str, payload_path: Path) -> None:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    request = CombatResolveRequest(**payload)
    service = AdminCoordinator.get_instance()
    response = await service.resolve_combat(world_id, session_id, request)
    print(response.model_dump())


def main() -> None:
    parser = argparse.ArgumentParser(description="Game loop CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="Create session")
    create_parser.add_argument("--world", required=True)
    create_parser.add_argument("--payload", required=True)

    scene_parser = subparsers.add_parser("scene", help="Update scene")
    scene_parser.add_argument("--world", required=True)
    scene_parser.add_argument("--session", required=True)
    scene_parser.add_argument("--payload", required=True)

    combat_start_parser = subparsers.add_parser("combat-start", help="Start combat")
    combat_start_parser.add_argument("--world", required=True)
    combat_start_parser.add_argument("--session", required=True)
    combat_start_parser.add_argument("--payload", required=True)

    combat_resolve_parser = subparsers.add_parser("combat-resolve", help="Resolve combat")
    combat_resolve_parser.add_argument("--world", required=True)
    combat_resolve_parser.add_argument("--session", required=True)
    combat_resolve_parser.add_argument("--payload", required=True)

    args = parser.parse_args()
    payload_path = Path(args.payload)

    if args.command == "create":
        asyncio.run(_create_session(args.world, payload_path))
    elif args.command == "scene":
        asyncio.run(_update_scene(args.world, args.session, payload_path))
    elif args.command == "combat-start":
        asyncio.run(_start_combat(args.world, args.session, payload_path))
    elif args.command == "combat-resolve":
        asyncio.run(_resolve_combat(args.world, args.session, payload_path))


if __name__ == "__main__":
    main()
