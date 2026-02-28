"""Concrete LLM adapter using google-genai SDK (Gemini).

This file lives in app/ (NOT in game_core/) to respect the isolation boundary.
Injected into AgenticExecutor via the LlmPort protocol.
"""

from __future__ import annotations

from typing import Any

from google import genai
from google.genai import types

from app.game_core.adapters.llm import LlmPort, LlmResponse


class GeminiLlmAdapter:
    """Gemini function-calling adapter implementing LlmPort."""

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
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

        for candidate in response.candidates:
            for part in candidate.content.parts:
                if part.function_call:
                    tool_calls.append({
                        "name": part.function_call.name,
                        "args": (
                            dict(part.function_call.args)
                            if part.function_call.args
                            else {}
                        ),
                    })
                elif part.text:
                    text_parts.append(part.text)

        finish = "tool_calls" if tool_calls else "stop"
        return LlmResponse(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=finish,
        )
