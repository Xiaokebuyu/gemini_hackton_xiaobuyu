"""Unified agent executor — single-pass + multi-turn agentic loop."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable, Mapping

from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.models import AgentResult, ToolResult
from app.game_core.narrative.registry import RoleToolRegistry
from app.game_core.narrative.tools import AgentTool

if TYPE_CHECKING:
    from app.game_core.adapters.llm import LlmPort, LlmResponse


class AgenticExecutor:
    """Thin orchestration wrapper around RoleToolRegistry.

    Supports two modes:
    - ``run()``: single-pass tool execution (pre-built tool_calls list)
    - ``run_agentic()``: multi-turn LLM-driven loop (requires LlmPort)
    """

    def __init__(
        self,
        tool_registry: RoleToolRegistry | None = None,
        llm: LlmPort | None = None,
    ) -> None:
        self.tool_registry = tool_registry or RoleToolRegistry()
        self._llm = llm

    async def run(
        self,
        role: str,
        context: AgentContext,
        tool_calls: list[Any] | None = None,
        traits: list[str] | None = None,
    ) -> list[ToolResult]:
        if not tool_calls:
            return []

        available = {
            tool.name: tool
            for tool in self.tool_registry.get_tools_for(role, traits)
        }
        results: list[ToolResult] = []
        for tool_call in tool_calls:
            if not isinstance(tool_call, Mapping):
                results.append(
                    self._error_result(
                        status="invalid_params",
                        tool_name="",
                        message="invalid tool call",
                    )
                )
                continue
            tool_name = str(tool_call.get("name", ""))
            params = tool_call.get("params", {})
            tool = available.get(tool_name)
            if tool is None:
                results.append(
                    self._error_result(
                        status="unknown_tool",
                        tool_name=tool_name,
                        message=f"unknown tool: {tool_name}",
                    )
                )
                continue
            if not isinstance(params, dict):
                results.append(
                    self._error_result(
                        status="invalid_params",
                        tool_name=tool_name,
                        message=f"invalid params: {tool_name}",
                    )
                )
                continue
            results.append(await tool.execute(params, context))
        return results

    # ------------------------------------------------------------------
    # Multi-turn agentic loop
    # ------------------------------------------------------------------

    async def run_agentic(
        self,
        role: str,
        context: AgentContext,
        *,
        system_prompt: str = "",
        user_message: str = "",
        max_turns: int = 5,
        conversation_history: list[dict[str, Any]] | None = None,
        context_layers: dict[str, Any] | None = None,
        text_chunk_sink: Callable[[str], Awaitable[None]] | None = None,
        traits: list[str] | None = None,
    ) -> AgentResult:
        """Multi-turn agentic loop driven by an LLM.

        Each turn: LLM generates → parse tool_calls → execute tools → feed
        results back as history → repeat until LLM returns text-only or
        *max_turns* is reached.

        Args:
            conversation_history: Optional prior conversation to prepend.
                When provided, the initial history is built by appending
                *user_message* to this list rather than calling
                ``_build_initial_history()``.  Pass ``None`` (default) to
                preserve the existing stateless behaviour.
            context_layers: Optional 7-layer context dict from
                AgentContextBuilder.  When provided (and conversation_history
                is None), serialised L0/L2/L3/L5 (+ L7 for GM) are prepended
                to the first user message, and the raw scene_entries injection
                is suppressed when L5 entries are present (N-7).
        """
        if self._llm is None:
            return AgentResult(metadata={"status": "no_llm"})

        tools = self.tool_registry.get_tools_for(role, traits)
        declarations = self._build_declarations(tools)
        if conversation_history is not None:
            history: list[dict[str, Any]] = list(conversation_history)
            if user_message:
                history.append({"role": "user", "parts": [{"text": user_message}]})
            if not history:
                history = [{"role": "user", "parts": [{"text": "Proceed with your role."}]}]
        else:
            has_l5 = bool(
                context_layers is not None
                and isinstance(context_layers.get("l5_scene_bus"), dict)
                and context_layers["l5_scene_bus"].get("entries")
            )
            history = self._build_initial_history(
                context, user_message, include_scene=not has_l5
            )
            if context_layers is not None:
                layers_text = self._serialize_context_layers(role, context_layers)
                if layers_text and history:
                    history[0]["parts"].insert(0, {"text": layers_text})
        all_results: list[ToolResult] = []

        for turn in range(max_turns):
            response = await self._llm.generate(system_prompt, history, declarations)

            if not response.tool_calls:
                final_text = response.text
                # True streaming on the final text turn.
                # TODO: optimize to single streaming call (currently double-calls LLM
                # on final turn to get streaming output after detecting no tool_calls).
                if text_chunk_sink is not None and hasattr(self._llm, "generate_stream"):
                    streamed = ""
                    async for chunk in self._llm.generate_stream(
                        system_prompt, history, [],
                    ):
                        streamed += chunk
                        await text_chunk_sink(chunk)
                    if streamed:
                        final_text = streamed
                return AgentResult(
                    text=final_text,
                    tool_results=all_results,
                    turns_used=turn + 1,
                    metadata={
                        "status": "completed",
                        "finish_reason": response.finish_reason,
                    },
                )

            # Append model response to history
            history.append(self._model_turn(response))

            # Execute tool calls via existing single-pass run()
            turn_results = await self.run(
                role,
                context,
                tool_calls=[
                    {"name": tc["name"], "params": tc.get("args", {})}
                    for tc in response.tool_calls
                ],
                traits=traits,
            )
            all_results.extend(turn_results)

            # Append tool results to history
            history.append(
                self._tool_response_turn(response.tool_calls, turn_results)
            )

        return AgentResult(
            text="",
            tool_results=all_results,
            turns_used=max_turns,
            metadata={"status": "max_turns_reached"},
        )

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    @staticmethod
    def _build_declarations(tools: list[AgentTool]) -> list[dict[str, Any]]:
        """Convert AgentTool schemas to function declarations."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in tools
        ]

    @staticmethod
    def _build_initial_history(
        context: AgentContext,
        user_message: str,
        *,
        include_scene: bool = True,
    ) -> list[dict[str, Any]]:
        """Build initial conversation history from context.

        Args:
            include_scene: When False, suppresses context.scene_entries
                injection (used when context_layers provides L5 entries to
                avoid duplicating scene data, N-7).
        """
        parts: list[dict[str, Any]] = []
        if include_scene and context.scene_entries:
            parts.append({"text": f"Scene context: {context.scene_entries}"})
        if user_message:
            parts.append({"text": user_message})
        if not parts:
            parts.append({"text": "Proceed with your role."})
        return [{"role": "user", "parts": parts}]

    @staticmethod
    def _model_turn(response: LlmResponse) -> dict[str, Any]:
        """Format model response as history entry.

        Uses raw_model_parts when available to preserve thought_signature and
        exact part ordering from the original SDK response (Gemini 3 requirement).
        """
        if response.raw_model_parts:
            return {"role": "model", "parts": response.raw_model_parts}
        parts: list[dict[str, Any]] = []
        if response.text:
            parts.append({"text": response.text})
        for tc in response.tool_calls:
            parts.append({
                "function_call": {
                    "name": tc["name"],
                    "args": tc.get("args", {}),
                },
            })
        return {"role": "model", "parts": parts}

    @staticmethod
    def _tool_response_turn(
        tool_calls: list[dict[str, Any]],
        results: list[ToolResult],
    ) -> dict[str, Any]:
        """Format tool execution results as history entry."""
        parts: list[dict[str, Any]] = []
        for tc, result in zip(tool_calls, results):
            parts.append({
                "function_response": {
                    "name": tc["name"],
                    "response": {
                        "success": result.success,
                        "message": result.message,
                        **result.metadata,
                    },
                },
            })
        return {"role": "user", "parts": parts}

    @staticmethod
    def _serialize_context_layers(role: str, layers: dict[str, Any]) -> str:
        """Serialize L0/L2/L3/L5 (+ L7 for GM) into structured text blocks.

        L4 and L6 are handled via system_prompt (relationship + memory).
        L1 is GM-only chapter state (omitted here for brevity; GM already
        has full context from build_gm_context).

        Returns an empty string if no meaningful content is found.
        """
        blocks: list[str] = []

        # L0: World constants (cap: 5 lore entries + 5 faction names)
        l0 = layers.get("l0_world_constants") or {}
        l0_parts: list[str] = []
        world_id = l0.get("world_id", "")
        if world_id:
            l0_parts.append(f"World: {world_id}")
        for label, items, cap in (
            ("Lore", l0.get("lore", []), 5),
            ("Factions", l0.get("factions", []), 5),
        ):
            lines: list[str] = []
            for item in (items or [])[:cap]:
                name = (
                    getattr(item, "name", None)
                    or (item.get("name") if isinstance(item, dict) else None)
                )
                if name:
                    lines.append(f"  - {name}")
            if lines:
                l0_parts.append(f"{label}:\n" + "\n".join(lines))
        if l0_parts:
            blocks.append("## World Context\n" + "\n".join(l0_parts))

        # L2: Area environment
        l2 = layers.get("l2_area_environment") or {}
        l2_parts: list[str] = []
        tmpl = l2.get("template") or {}
        area_name = (
            (tmpl.get("name") if isinstance(tmpl, dict) else None)
            or l2.get("area_id", "")
        )
        if area_name:
            l2_parts.append(f"Current area: {area_name}")
        if isinstance(tmpl, dict) and tmpl.get("description"):
            l2_parts.append(f"Description: {tmpl['description']}")
        state2 = l2.get("state") or {}
        if isinstance(state2, dict) and state2.get("danger_level"):
            l2_parts.append(f"Danger level: {state2['danger_level']}")
        if l2_parts:
            blocks.append("## Area Environment\n" + "\n".join(l2_parts))

        # L3: Location details
        l3 = layers.get("l3_location_details") or {}
        l3_parts: list[str] = []
        tmpl3 = l3.get("template") or {}
        loc_name = (
            (tmpl3.get("name") if isinstance(tmpl3, dict) else None)
            or l3.get("location_id")
            or ""
        )
        if loc_name:
            suffix = " (temporary)" if l3.get("is_dynamic") else ""
            l3_parts.append(f"Current location: {loc_name}{suffix}")
        if isinstance(tmpl3, dict) and tmpl3.get("description"):
            l3_parts.append(f"Description: {tmpl3['description']}")
        disc = l3.get("discovered_items", [])
        if disc:
            l3_parts.append(f"Discovered: {', '.join(str(i) for i in disc[:5])}")
        if l3_parts:
            blocks.append("## Location Details\n" + "\n".join(l3_parts))

        # L5: Scene entries (already visibility-filtered by context_builder) — cap 10
        l5 = layers.get("l5_scene_bus") or {}
        entries = l5.get("entries", []) if isinstance(l5, dict) else []
        scene_lines = [
            f"  [{e.get('source', '?')}] {e.get('content', '')}"
            for e in entries[-10:]
            if isinstance(e, dict) and e.get("content")
        ]
        if scene_lines:
            blocks.append("## Scene\n" + "\n".join(scene_lines))

        # L7: Narrative hints — GM only
        if role == "gm":
            l7 = layers.get("l7_engine_result") or {}
            hints = l7.get("narrative_hints", []) if isinstance(l7, dict) else []
            if hints:
                blocks.append(
                    "## Narrative Hints\n"
                    + "\n".join(f"  - {h}" for h in hints)
                )

        return "\n\n".join(blocks)

    @staticmethod
    def _error_result(
        *,
        status: str,
        tool_name: str,
        message: str,
    ) -> ToolResult:
        return ToolResult(
            success=False,
            message=message,
            metadata={
                "status": status,
                "tool_name": tool_name,
            },
        )
