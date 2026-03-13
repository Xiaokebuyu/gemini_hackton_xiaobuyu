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
        *,
        thinking_level: str | types.ThinkingLevel = "low",
        profile_name: str = "default",
    ) -> None:
        self._client = genai.Client()
        self._model = model
        self._temperature = temperature
        self._thinking_level = self._normalize_thinking_level(thinking_level)
        self._thinking_level_name = self._thinking_level.name.lower()
        self._profile_name = profile_name

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
            thinking_config=types.ThinkingConfig(
                thinking_level=self._thinking_level,
            ),
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
            thinking_config=types.ThinkingConfig(
                thinking_level=self._thinking_level,
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
            sig = p.get("thought_signature")
            if "text" in p:
                parts.append(types.Part(text=p["text"]))
            elif "function_call" in p:
                fc = p["function_call"]
                part = types.Part(function_call=types.FunctionCall(
                    name=fc["name"],
                    args=fc.get("args", {}),
                ))
                if sig:
                    part.thought_signature = sig
                parts.append(part)
            elif "function_response" in p:
                fr = p["function_response"]
                parts.append(types.Part.from_function_response(
                    name=fr["name"],
                    response=fr.get("response", {}),
                ))
            elif sig:
                parts.append(types.Part(thought_signature=sig))
        return types.Content(role=role, parts=parts)

    @staticmethod
    def _to_declaration(d: dict[str, Any]) -> dict[str, Any]:
        """Tool declaration already in JSON-schema format — pass through."""
        return d

    def _parse_response(self, response: Any) -> LlmResponse:
        """Parse Gemini response into abstract LlmResponse."""
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        raw_model_parts: list[dict[str, Any]] = []

        # Guard against empty/None candidates (safety filter, overload, timeout)
        if not response.candidates:
            return LlmResponse(
                text="",
                finish_reason="error",
                metadata={
                    "provider": "gemini",
                    "profile": self._profile_name,
                    "model": self._model,
                    "error": "empty_candidates",
                },
            )

        for candidate in response.candidates:
            # Inner guard: skip candidates with no content parts
            if not candidate.content or not candidate.content.parts:
                continue
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
        metadata = {
            "provider": "gemini",
            "profile": self._profile_name,
            "model": self._model,
            "thinking_level": self._thinking_level_name,
            "temperature": self._temperature,
        }
        usage_metadata = getattr(response, "usage_metadata", None)
        token_usage: dict[str, int] = {}
        if usage_metadata is not None:
            for field_name in (
                "prompt_token_count",
                "candidates_token_count",
                "tool_use_prompt_token_count",
                "thoughts_token_count",
                "total_token_count",
            ):
                raw_value = getattr(usage_metadata, field_name, None)
                if raw_value is None:
                    continue
                try:
                    token_usage[field_name] = int(raw_value)
                except (TypeError, ValueError):
                    continue
        if token_usage:
            metadata["token_usage"] = token_usage
        return LlmResponse(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=finish,
            metadata=metadata,
            raw_model_parts=raw_model_parts if raw_model_parts else None,
        )

    @staticmethod
    def _normalize_thinking_level(
        raw: str | types.ThinkingLevel,
    ) -> types.ThinkingLevel:
        if isinstance(raw, types.ThinkingLevel):
            return raw
        normalized = str(raw).strip().lower()
        if normalized == "medium":
            return types.ThinkingLevel.MEDIUM
        if normalized == "high":
            return types.ThinkingLevel.HIGH
        return types.ThinkingLevel.LOW
