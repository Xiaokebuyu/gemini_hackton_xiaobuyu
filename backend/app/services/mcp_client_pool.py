"""
MCP Client Pool - Singleton connection pool for MCP server sessions.

Provides persistent subprocess sessions with health checks and auto-reconnection.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shlex
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ServerConfig:
    """Configuration for an MCP server."""

    command: str
    args: str | list[str]
    cwd: Path
    name: str
    transport: str
    endpoint: str


class MCPClientPool:
    """
    Singleton pool for MCP client sessions.

    Features:
    - Maintains persistent MCP server subprocess
    - Session health checking via ping
    - Automatic reconnection on failure
    - Per-server call serialization to avoid stdio interleaving
    """

    _instance: Optional["MCPClientPool"] = None
    _lock: asyncio.Lock = asyncio.Lock()

    # Server type constants
    GAME_TOOLS = "game_tools"
    COMBAT = "combat"

    def __init__(self) -> None:
        self._sessions: Dict[str, ClientSession] = {}
        self._exit_stacks: Dict[str, contextlib.AsyncExitStack] = {}
        self._healthy: Dict[str, bool] = {}
        self._call_locks: Dict[str, asyncio.Lock] = {}
        self._connect_locks: Dict[str, asyncio.Lock] = {}
        self._cooldowns: Dict[str, float] = {}

        self._cooldown_seconds = 30.0
        self._tool_timeout_seconds = 12.0
        self._ping_timeout_seconds = 2.0

        self._server_root = Path(__file__).resolve().parents[2]

        self._configs = {
            self.GAME_TOOLS: ServerConfig(
                command=settings.mcp_tools_command,
                args=settings.mcp_tools_args,
                cwd=self._server_root,
                name="Game Tools MCP",
                transport=settings.mcp_tools_transport,
                endpoint=settings.mcp_tools_endpoint,
            ),
            self.COMBAT: ServerConfig(
                command=settings.mcp_combat_command,
                args=settings.mcp_combat_args,
                cwd=self._server_root,
                name="Combat MCP",
                transport=settings.mcp_combat_transport,
                endpoint=settings.mcp_combat_endpoint,
            ),
        }

    @classmethod
    async def get_instance(cls) -> "MCPClientPool":
        """Get or create the singleton instance."""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    async def shutdown(cls) -> None:
        """Shutdown all sessions and clear the singleton."""
        if cls._instance:
            await cls._instance._close_all()
            cls._instance = None

    def _get_lock(self, lock_map: Dict[str, asyncio.Lock], server_type: str) -> asyncio.Lock:
        if server_type not in lock_map:
            lock_map[server_type] = asyncio.Lock()
        return lock_map[server_type]

    def _in_cooldown(self, server_type: str) -> bool:
        until = self._cooldowns.get(server_type, 0.0)
        return time.monotonic() < until

    def _mark_cooldown(self, server_type: str, error: Exception | None = None) -> None:
        self._cooldowns[server_type] = time.monotonic() + self._cooldown_seconds
        if error:
            logger.warning("[MCPPool] %s entering cooldown: %s", server_type, error)

    def _resolve_command(self, command: str) -> str:
        if command in {"python", "python3"}:
            return sys.executable
        return command

    async def get_session(self, server_type: str) -> ClientSession:
        """Get a healthy session for the specified server type."""
        if server_type not in self._configs:
            raise ValueError(f"Unknown server type: {server_type}")

        session = self._sessions.get(server_type)
        if session and await self._check_health(session):
            return session

        connect_lock = self._get_lock(self._connect_locks, server_type)
        async with connect_lock:
            session = self._sessions.get(server_type)
            if session and await self._check_health(session):
                return session
            return await self._connect(server_type)

    async def call_tool(
        self,
        server_type: str,
        tool_name: str,
        arguments: Dict[str, Any],
        max_retries: int = 2,
    ) -> Dict[str, Any]:
        """Call a tool with automatic reconnection on failure."""
        if self._in_cooldown(server_type):
            raise RuntimeError(f"MCP server in cooldown: {server_type}")

        last_error: Optional[Exception] = None
        call_lock = self._get_lock(self._call_locks, server_type)

        async with call_lock:
            for attempt in range(max_retries + 1):
                try:
                    session = await self.get_session(server_type)
                    result = await asyncio.wait_for(
                        session.call_tool(tool_name, arguments),
                        timeout=self._tool_timeout_seconds,
                    )
                    return self._decode_tool_result(result)
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "[MCPPool] Tool call failed (%s, attempt %s/%s): %s",
                        server_type,
                        attempt + 1,
                        max_retries + 1,
                        exc,
                    )
                    self._healthy[server_type] = False
                    if attempt < max_retries:
                        await asyncio.sleep(0.1 * (attempt + 1))

        self._mark_cooldown(server_type, last_error)
        raise RuntimeError(
            f"Tool call failed after {max_retries + 1} attempts: {last_error}"
        )

    async def _connect(self, server_type: str) -> ClientSession:
        """Create a new connection to the specified server."""
        await self._close_session(server_type)

        config = self._configs[server_type]
        transport = (config.transport or "stdio").lower()
        args = shlex.split(config.args) if isinstance(config.args, str) else list(config.args)

        exit_stack = contextlib.AsyncExitStack()
        try:
            if transport in {"stdio"}:
                command = self._resolve_command(config.command)
                env = os.environ.copy()
                root_str = str(config.cwd)
                existing_path = env.get("PYTHONPATH")
                if existing_path:
                    if root_str not in existing_path.split(os.pathsep):
                        env["PYTHONPATH"] = root_str + os.pathsep + existing_path
                else:
                    env["PYTHONPATH"] = root_str

                server_params = StdioServerParameters(
                    command=command,
                    args=args,
                    cwd=str(config.cwd),
                    env=env,
                )
                read_stream, write_stream = await exit_stack.enter_async_context(
                    stdio_client(server_params)
                )
            elif transport in {"streamable-http", "streamable_http"}:
                if not config.endpoint:
                    raise RuntimeError(f"{config.name} missing endpoint for {transport}")
                read_stream, write_stream, _ = await exit_stack.enter_async_context(
                    streamable_http_client(config.endpoint)
                )
            elif transport in {"sse"}:
                if not config.endpoint:
                    raise RuntimeError(f"{config.name} missing endpoint for {transport}")
                read_stream, write_stream = await exit_stack.enter_async_context(
                    sse_client(config.endpoint)
                )
            else:
                raise RuntimeError(f"Unsupported MCP transport: {transport}")
            session = ClientSession(read_stream, write_stream)
            await exit_stack.enter_async_context(session)
            await session.initialize()

            self._sessions[server_type] = session
            self._exit_stacks[server_type] = exit_stack
            self._healthy[server_type] = True

            logger.info("[MCPPool] Connected to %s", config.name)
            return session
        except Exception as exc:
            await exit_stack.aclose()
            self._mark_cooldown(server_type, exc)
            raise RuntimeError(f"Failed to connect to {config.name}: {exc}") from exc

    async def _check_health(self, session: ClientSession) -> bool:
        """Check if a session is healthy via ping."""
        try:
            await asyncio.wait_for(
                session.send_ping(),
                timeout=self._ping_timeout_seconds,
            )
            return True
        except Exception:
            return False

    async def _close_session(self, server_type: str) -> None:
        """Close a specific session."""
        if server_type in self._exit_stacks:
            try:
                await self._exit_stacks[server_type].aclose()
            except Exception as exc:
                logger.warning("[MCPPool] Error closing %s: %s", server_type, exc)
            finally:
                self._sessions.pop(server_type, None)
                self._exit_stacks.pop(server_type, None)
                self._healthy[server_type] = False

    async def _close_all(self) -> None:
        """Close all sessions."""
        for server_type in list(self._sessions.keys()):
            await self._close_session(server_type)

    def _decode_tool_result(self, result) -> Dict[str, Any]:
        """Decode MCP tool result to dictionary."""
        if getattr(result, "structuredContent", None) is not None:
            return result.structuredContent
        if not result.content:
            return {}
        first = result.content[0]
        text = getattr(first, "text", "")
        if not text:
            return {}
        try:
            return json.loads(text)
        except Exception:
            return {"raw": text}
