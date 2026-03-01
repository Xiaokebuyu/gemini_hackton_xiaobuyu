"""Concrete LLM adapter using google-genai SDK (Gemini).

This file lives in app/ (NOT in game_core/) to respect the isolation boundary.
Injected into AgenticExecutor via the LlmPort protocol.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from google import genai
from google.genai import types

from app.game_core.adapters.llm import LlmPort, LlmResponse


class GeminiLlmAdapter:
    """Gemini function-calling adapter implementing LlmPort."""

    def __init__(
        self,
        model: str = "gemini-3-flash-preview",
        temperature: float = 1.0,
    ) -> None:
        self._client = genai.Client()
        self._model = model
        self._temperature = temperature

    async def generate(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tool_declarations: list[dict[str, Any]],
    ) -> LlmResponse:
        contents = [self._to_content(msg) for msg in history]

        gemini_tools = (
            types.Tool(function_declarations=[
                self._to_declaration(d) for d in tool_declarations
            ])
            if tool_declarations
            else None
        )

        config = types.GenerateContentConfig(
            system_instruction=system_prompt or None,
            tools=[gemini_tools] if gemini_tools else None,
            temperature=self._temperature,
        )

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=contents,
            config=config,
        )

        return self._parse_response(response)

    async def generate_stream(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tool_declarations: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        """Stream the final narrative text turn (tool_config=NONE enforces pure text).

        Intended for the final turn of the agentic loop where no function calls
        are expected. Tool declarations are accepted for interface compatibility
        but ignored — function calling is disabled via tool_config.
        """
        contents = [self._to_content(msg) for msg in history]
        config = types.GenerateContentConfig(
            system_instruction=system_prompt or None,
            temperature=self._temperature,
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="NONE"),
            ),
        )
        async for chunk in await self._client.aio.models.generate_content_stream(
            model=self._model,
            contents=contents,
            config=config,
        ):
            if chunk.text:
                yield chunk.text

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_content(msg: dict[str, Any]) -> types.Content:
        """Convert abstract history entry to Gemini Content."""
        role = msg["role"]
        parts: list[types.Part] = []
        for p in msg.get("parts", []):
            if "text" in p:
                parts.append(types.Part(text=p["text"]))
            elif "function_call" in p:
                fc = p["function_call"]
                parts.append(types.Part(function_call=types.FunctionCall(
                    name=fc["name"],
                    args=fc.get("args", {}),
                )))
            elif "function_response" in p:
                fr = p["function_response"]
                parts.append(types.Part.from_function_response(
                    name=fr["name"],
                    response=fr.get("response", {}),
                ))
            elif "thought_signature" in p:
                # Preserve Gemini 3 thought signatures for multi-turn context integrity
                parts.append(types.Part(thought_signature=p["thought_signature"]))
        return types.Content(role=role, parts=parts)

    @staticmethod
    def _to_declaration(d: dict[str, Any]) -> dict[str, Any]:
        """Tool declaration already in JSON-schema format — pass through."""
        return d

    @staticmethod
    def _parse_response(response: Any) -> LlmResponse:
        """Parse Gemini response into abstract LlmResponse."""
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        raw_model_parts: list[dict[str, Any]] = []

        for candidate in response.candidates:
            for part in candidate.content.parts:
                raw_part: dict[str, Any] = {}
                # Preserve thought_signature for Gemini 3 multi-turn context integrity
                sig = getattr(part, "thought_signature", None)
                if sig:
                    raw_part["thought_signature"] = sig
                if part.function_call:
                    fc_dict: dict[str, Any] = {
                        "name": part.function_call.name,
                        "args": (
                            dict(part.function_call.args)
                            if part.function_call.args
                            else {}
                        ),
                    }
                    tool_calls.append(fc_dict)
                    raw_part["function_call"] = fc_dict
                elif part.text:
                    text_parts.append(part.text)
                    raw_part["text"] = part.text
                if raw_part:
                    raw_model_parts.append(raw_part)

        finish = "tool_calls" if tool_calls else "stop"
        return LlmResponse(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=finish,
            raw_model_parts=raw_model_parts if raw_model_parts else None,
        )
