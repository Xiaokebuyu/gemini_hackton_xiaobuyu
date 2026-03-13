#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.game_core import GameRuntime
from app.game_core.rules.models import Command


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed one planner capability into an existing saved session.",
    )
    parser.add_argument("--world-id", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--npc-id", required=True)
    parser.add_argument("--capability-id", required=True)
    parser.add_argument("--instruction", required=True)
    parser.add_argument("--functional", default="")
    parser.add_argument(
        "--functional-params-json",
        default="{}",
        help="JSON object used as functional_params. Default: {}",
    )
    parser.add_argument(
        "--expiry-ticks",
        type=int,
        default=0,
        help="Relative expiry window in ticks. 0 means no expiry.",
    )
    return parser.parse_args()


def _load_functional_params(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid --functional-params-json: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("--functional-params-json must decode to a JSON object")
    return dict(parsed)


async def _run(args: argparse.Namespace) -> int:
    runtime = GameRuntime()
    session = await runtime.resume_session(args.world_id, args.session_id)
    if session is None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "session_not_found",
                    "world_id": args.world_id,
                    "session_id": args.session_id,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1

    functional_params = _load_functional_params(args.functional_params_json)
    current_tick = (
        session.runtime.state.time.absolute_tick()
        if session.runtime.state.has_slice("time")
        else 0
    )
    params = {
        "npc_id": args.npc_id,
        "capability_id": args.capability_id,
        "instruction": args.instruction,
        "functional": args.functional,
        "functional_params": functional_params,
        "current_tick": current_tick,
    }
    if args.expiry_ticks > 0:
        params["expiry_ticks"] = args.expiry_ticks
    command = Command(
        type="planner_assign_capability",
        params=params,
        source="narrative_planner",
    )
    result = session.runtime.rules_engine.execute(
        command,
        session.runtime.state,
        session.runtime.world,
    )
    if not result.executed:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "planner_assign_capability_failed",
                    "errors": list(result.errors),
                    "world_id": args.world_id,
                    "session_id": args.session_id,
                    "npc_id": args.npc_id,
                    "capability_id": args.capability_id,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1

    session.runtime.tick_coordinator.apply_external_result(result)
    await runtime.save_session(session)

    print(
        json.dumps(
            {
                "ok": True,
                "world_id": args.world_id,
                "session_id": args.session_id,
                "npc_id": args.npc_id,
                "capability_id": args.capability_id,
                "capabilities": session.runtime.state.narrative_plan.get_capabilities(
                    args.npc_id
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main() -> None:
    args = _parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
