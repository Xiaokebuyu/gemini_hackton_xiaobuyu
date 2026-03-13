from __future__ import annotations

from typing import Any

from app.game_core.bootstrap import build_default_world, build_runtime_for_world
from app.game_core.content import WorldInstance
from app.game_core.narrative.character_tools import (
    register_npc_tools,
    register_teammate_tools,
)
from app.game_core.narrative.executor import AgenticExecutor
from app.game_core.narrative.gm_tools import register_gm_tools
from app.game_core.narrative.registry import RoleToolRegistry
from app.game_core.orchestration.npc_interaction import NpcInteractionCoordinator
from app.game_core.orchestration.private_chat import PrivateChatCoordinator
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateContainer


class RecordingLlmProvider:
    """Deterministic LLM stub that records prompts for capability assertions."""

    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses or [])
        self._call_index = 0

    async def generate(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tool_declarations: list[dict[str, Any]],
    ) -> Any:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "history": history,
                "tool_declarations": tool_declarations,
            }
        )
        from app.game_core.adapters.llm import LlmResponse

        if self._call_index < len(self._responses):
            resp = self._responses[self._call_index]
            self._call_index += 1
            return LlmResponse(
                text=resp.get("text", ""),
                tool_calls=resp.get("tool_calls", []),
                finish_reason=resp.get("finish_reason", "stop"),
            )
        return LlmResponse(text="(no more responses)")


def noop_executor(command: Command) -> ExecuteResult:
    del command
    return ExecuteResult(executed=True)


def npc_speak_response(text: str) -> dict[str, Any]:
    return {
        "tool_calls": [{"name": "speak", "args": {"text": text}}],
        "finish_reason": "tool_calls",
    }


def gm_suggest_options_response(options: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "tool_calls": [{"name": "suggest_options", "args": {"options": options}}],
        "finish_reason": "tool_calls",
    }


def stop_response(text: str = "") -> dict[str, Any]:
    return {"text": text, "finish_reason": "stop"}


def build_capability_world() -> WorldInstance:
    return build_default_world(
        "capability_world",
        world_data={
            "tags": {
                "profession": {"id": "profession", "tags": ["merchant", "clerk", "scholar"]},
                "ancestry": {"id": "ancestry", "tags": ["human"]},
            },
            "maps": {
                "town": {
                    "id": "town",
                    "name": "Frontier Town",
                    "is_starting_area": True,
                    "sub_locations": {
                        "counter": {"id": "counter", "name": "Guild Counter"},
                        "board": {"id": "board", "name": "Guild Board"},
                        "study": {"id": "study", "name": "Quiet Study"},
                    },
                    "default_sub_location": "counter",
                }
            },
            "characters": {
                "merchant_tom": {
                    "id": "merchant_tom",
                    "name": "Merchant Tom",
                    "personality": "A practical merchant.",
                    "dialogue_style": "Brief and businesslike.",
                    "tags": ["merchant", "human"],
                    "current_area": "town",
                },
                "guild_girl": {
                    "id": "guild_girl",
                    "name": "Guild Girl",
                    "personality": "Professional and polite.",
                    "dialogue_style": "Calm and structured.",
                    "tags": ["clerk", "human"],
                    "current_area": "town",
                },
                "lore_keeper": {
                    "id": "lore_keeper",
                    "name": "Lore Keeper",
                    "personality": "Careful and observant.",
                    "dialogue_style": "Measured and secretive.",
                    "tags": ["scholar", "human"],
                    "current_area": "town",
                },
            },
        },
    )


def build_capability_state(world: WorldInstance) -> StateContainer:
    runtime = build_runtime_for_world(world)
    state = runtime.state
    state.player.restore(
        {
            "character_name": "Hero",
            "character_class": "fighter",
            "current_area": "town",
            "current_location": "counter",
        }
    )
    state.relations.restore(
        {
            "npc_dispositions": {
                "merchant_tom": {"approval": 20, "trust": 10, "fear": 0, "romance": 0},
                "guild_girl": {"approval": 25, "trust": 15, "fear": 0, "romance": 0},
                "lore_keeper": {"approval": 15, "trust": 20, "fear": 0, "romance": 0},
            },
            "relationship_stages": {
                "merchant_tom": "acquaintance",
                "guild_girl": "acquaintance",
                "lore_keeper": "acquaintance",
            },
        }
    )
    return state


def assign_capability(
    state: StateContainer,
    *,
    npc_id: str,
    capability_id: str,
    instruction: str,
    functional: str = "",
    functional_params: dict[str, Any] | None = None,
) -> None:
    state.narrative_plan.assign_capability(
        npc_id,
        {
            "capability_id": capability_id,
            "npc_id": npc_id,
            "instruction": instruction,
            "functional": functional,
            "functional_params": dict(functional_params or {}),
            "assigned_tick": 0,
            "expiry_tick": 0,
            "source": "test_harness",
        },
    )


def build_npc_capability_harness(
    *,
    llm_responses: list[dict[str, Any]],
    capabilities: list[dict[str, Any]] | None = None,
) -> tuple[NpcInteractionCoordinator, RecordingLlmProvider, StateContainer]:
    world = build_capability_world()
    state = build_capability_state(world)
    for cap in capabilities or []:
        assign_capability(state, **cap)
    llm = RecordingLlmProvider(llm_responses)
    registry = RoleToolRegistry()
    register_gm_tools(registry)
    register_npc_tools(registry)
    register_teammate_tools(registry)
    executor = AgenticExecutor(tool_registry=registry, llm=llm)
    return NpcInteractionCoordinator(executor, world, state), llm, state


def build_private_capability_harness(
    *,
    llm_responses: list[dict[str, Any]],
    capabilities: list[dict[str, Any]] | None = None,
) -> tuple[PrivateChatCoordinator, RecordingLlmProvider, StateContainer]:
    world = build_capability_world()
    state = build_capability_state(world)
    for cap in capabilities or []:
        assign_capability(state, **cap)
    llm = RecordingLlmProvider(llm_responses)
    registry = RoleToolRegistry()
    register_gm_tools(registry)
    register_npc_tools(registry)
    register_teammate_tools(registry)
    executor = AgenticExecutor(tool_registry=registry, llm=llm)
    return PrivateChatCoordinator(executor, world, state), llm, state
