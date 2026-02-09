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
from urllib.parse import urlsplit, urlunsplit

import httpx
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from app.config import settings

logger = logging.getLogger(__name__)


class MCPServiceUnavailableError(RuntimeError):
    """Raised when an MCP service endpoint is unavailable."""

    def __init__(self, server_type: str, endpoint: str, detail: str) -> None:
        self.server_type = server_type
        self.endpoint = endpoint
        self.detail = detail
        super().__init__(
            f"MCP service unavailable ({server_type}) at {endpoint or '<unset>'}: {detail}"
        )


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
        self._tool_timeout_seconds = float(settings.mcp_tool_timeout_seconds)
        self._tool_timeouts: Dict[str, Dict[str, float]] = {
            self.GAME_TOOLS: {
                # NPC 对话可能触发二次模型调用（工具回忆），通常明显慢于其他轻量工具。
                "npc_respond": float(settings.mcp_npc_tool_timeout_seconds),
            },
        }
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
            logger.warning(
                "[MCPPool] %s entering cooldown: %s: %r",
                server_type,
                type(error).__name__,
                error,
            )

    @staticmethod
    def _is_timeout_error(error: Exception | None) -> bool:
        if error is None:
            return False
        if isinstance(error, TimeoutError):
            return True
        text = str(error).lower()
        return "timeout" in text or "timed out" in text

    @staticmethod
    def _iter_related_errors(error: BaseException | None):
        if error is None:
            return
        stack: list[BaseException] = [error]
        seen: set[int] = set()
        while stack:
            current = stack.pop()
            marker = id(current)
            if marker in seen:
                continue
            seen.add(marker)
            yield current

            if isinstance(current, BaseExceptionGroup):
                stack.extend(list(current.exceptions))
            cause = getattr(current, "__cause__", None)
            if isinstance(cause, BaseException):
                stack.append(cause)
            context = getattr(current, "__context__", None)
            if isinstance(context, BaseException):
                stack.append(context)

    def _is_service_unavailable_error(self, error: Exception | None) -> bool:
        if error is None:
            return False

        # Tool timeout is handled separately; do not classify it as unavailable.
        if self._is_timeout_error(error):
            return False

        keywords = (
            "connecterror",
            "all connection attempts failed",
            "connection refused",
            "connection reset",
            "connection aborted",
            "network is unreachable",
            "name or service not known",
            "failed to establish a new connection",
            "server disconnected",
            "connection closed",
            "mcp service unavailable",
        )

        for exc in self._iter_related_errors(error):
            if isinstance(exc, MCPServiceUnavailableError):
                return True

            if isinstance(
                exc,
                (
                    httpx.ConnectError,
                    httpx.ConnectTimeout,
                    httpx.NetworkError,
                    httpx.ReadError,
                    httpx.WriteError,
                    httpx.RemoteProtocolError,
                ),
            ):
                return True

            text = f"{type(exc).__name__}: {exc}".lower()
            if any(keyword in text for keyword in keywords):
                return True

        return False

    def _resolve_tool_timeout(self, server_type: str, tool_name: str) -> float:
        server_cfg = self._tool_timeouts.get(server_type, {})
        timeout = server_cfg.get(tool_name, self._tool_timeout_seconds)
        # 防御式下限，避免误配 0 或负值导致立即超时。
        return max(1.0, float(timeout))

    def _resolve_command(self, command: str) -> str:
        if command in {"python", "python3"}:
            return sys.executable
        return command

    @staticmethod
    def _replace_url_path(url: str, path: str) -> str:
        parsed = urlsplit(url)
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

    async def _probe_http_endpoint(self, url: str, timeout_seconds: float) -> Dict[str, Any]:
        try:
            timeout = httpx.Timeout(max(0.5, float(timeout_seconds)))
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, headers={"Accept": "application/json"})
            return {
                "ok": response.status_code < 500,
                "url": url,
                "status_code": response.status_code,
            }
        except Exception as exc:
            return {
                "ok": False,
                "url": url,
                "error": f"{type(exc).__name__}: {exc}",
            }

    async def probe(
        self,
        server_type: str,
        timeout_seconds: float = 2.0,
    ) -> Dict[str, Any]:
        """Probe a configured MCP dependency without creating a client session."""
        if server_type not in self._configs:
            raise ValueError(f"Unknown server type: {server_type}")

        config = self._configs[server_type]
        transport = (config.transport or "stdio").lower()
        endpoint = (config.endpoint or "").strip()
        result: Dict[str, Any] = {
            "ok": False,
            "server_type": server_type,
            "name": config.name,
            "transport": transport,
            "endpoint": endpoint,
        }

        if transport in {"streamable-http", "streamable_http"}:
            if not endpoint:
                result["error"] = "missing endpoint"
                return result

            health_url = self._replace_url_path(endpoint, "/health")
            health_probe = await self._probe_http_endpoint(health_url, timeout_seconds)
            result["health_probe"] = health_probe
            if health_probe.get("ok"):
                result["ok"] = True
                return result

            endpoint_probe = await self._probe_http_endpoint(endpoint, timeout_seconds)
            result["endpoint_probe"] = endpoint_probe
            result["ok"] = bool(endpoint_probe.get("ok"))
            if not result["ok"]:
                result["error"] = (
                    endpoint_probe.get("error")
                    or health_probe.get("error")
                    or "endpoint probe failed"
                )
            return result

        if transport in {"sse"}:
            if not endpoint:
                result["error"] = "missing endpoint"
                return result
            endpoint_probe = await self._probe_http_endpoint(endpoint, timeout_seconds)
            result["endpoint_probe"] = endpoint_probe
            result["ok"] = bool(endpoint_probe.get("ok"))
            if not result["ok"]:
                result["error"] = endpoint_probe.get("error") or "endpoint probe failed"
            return result

        if transport in {"stdio"}:
            # stdio transport cannot be probed externally; we validate it lazily on first tool call.
            result["ok"] = True
            result["note"] = "stdio probe skipped"
            return result

        result["error"] = f"unsupported transport: {transport}"
        return result

    async def probe_dependencies(
        self,
        timeout_seconds: float = 2.0,
        server_types: Optional[list[str]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        targets = server_types or list(self._configs.keys())
        results: Dict[str, Dict[str, Any]] = {}
        for server_type in targets:
            results[server_type] = await self.probe(
                server_type=server_type,
                timeout_seconds=timeout_seconds,
            )
        return results

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
            config = self._configs.get(server_type)
            endpoint = config.endpoint if config else ""
            raise MCPServiceUnavailableError(
                server_type=server_type,
                endpoint=endpoint,
                detail="server in cooldown",
            )

        last_error: Optional[Exception] = None
        call_lock = self._get_lock(self._call_locks, server_type)

        async with call_lock:
            for attempt in range(max_retries + 1):
                try:
                    timeout = self._resolve_tool_timeout(server_type, tool_name)
                    session = await self.get_session(server_type)
                    result = await asyncio.wait_for(
                        session.call_tool(tool_name, arguments),
                        timeout=timeout,
                    )
                    return self._decode_tool_result(result)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "[MCPPool] Tool call failed (%s/%s, attempt %s/%s): %s: %r",
                        server_type,
                        tool_name,
                        attempt + 1,
                        max_retries + 1,
                        type(exc).__name__,
                        exc,
                    )
                    self._healthy[server_type] = False
                    if attempt < max_retries:
                        await asyncio.sleep(0.1 * (attempt + 1))

        # 对话类工具超时可能是慢而非挂，超时不进入全局 cooldown，避免把整个工具服务封死。
        if not self._is_timeout_error(last_error):
            self._mark_cooldown(server_type, last_error)
        if isinstance(last_error, MCPServiceUnavailableError):
            raise last_error
        if self._is_service_unavailable_error(last_error):
            config = self._configs.get(server_type)
            endpoint = config.endpoint if config else ""
            raise MCPServiceUnavailableError(
                server_type=server_type,
                endpoint=endpoint,
                detail=f"{type(last_error).__name__}: {last_error}",
            ) from last_error
        raise RuntimeError(
            f"Tool call failed after {max_retries + 1} attempts ({tool_name}): "
            f"{type(last_error).__name__ if last_error else 'UnknownError'}: {last_error!r}"
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
        except asyncio.CancelledError:
            await exit_stack.aclose()
            raise
        except Exception as exc:
            await exit_stack.aclose()
            self._mark_cooldown(server_type, exc)
            raise MCPServiceUnavailableError(
                server_type=server_type,
                endpoint=config.endpoint,
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc

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

    @staticmethod
    def _try_parse_json(text: Any) -> Any:
        if not isinstance(text, str):
            return None
        stripped = text.strip()
        if not stripped:
            return None
        if not (stripped.startswith("{") or stripped.startswith("[")):
            return None
        try:
            return json.loads(stripped)
        except Exception:
            return None

    def _normalize_structured_content(self, payload: Any) -> Dict[str, Any]:
        """
        FastMCP 常见返回:
        {"result": "<json string>"}
        这里尽量还原成真正的 JSON 对象，减少上层重复兼容代码。
        """
        if isinstance(payload, dict):
            if set(payload.keys()) == {"result"}:
                parsed = self._try_parse_json(payload.get("result"))
                if isinstance(parsed, dict):
                    return parsed
            return payload

        parsed = self._try_parse_json(payload)
        if isinstance(parsed, dict):
            return parsed

        return {"result": payload}

    def _decode_tool_result(self, result) -> Dict[str, Any]:
        """Decode MCP tool result to dictionary."""
        if getattr(result, "structuredContent", None) is not None:
            return self._normalize_structured_content(result.structuredContent)
        if not result.content:
            return {}
        first = result.content[0]
        text = getattr(first, "text", "")
        if not text:
            return {}
        parsed = self._try_parse_json(text)
        if isinstance(parsed, dict):
            return parsed
        return {"raw": text}
