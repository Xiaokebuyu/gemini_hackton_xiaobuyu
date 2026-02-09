import asyncio

import httpx
import pytest

from app.services.mcp_client_pool import MCPClientPool, MCPServiceUnavailableError


class _SlowSession:
    async def call_tool(self, tool_name, arguments):
        await asyncio.sleep(2.0)
        return type("Result", (), {"structuredContent": {}, "content": []})()


class _FailSession:
    async def call_tool(self, tool_name, arguments):
        raise RuntimeError("boom")


class _ConnectFailSession:
    async def call_tool(self, tool_name, arguments):
        raise ExceptionGroup(
            "unhandled errors in a TaskGroup",
            [httpx.ConnectError("All connection attempts failed")],
        )


class _SessionLifecycleFailThenSuccess:
    def __init__(self):
        self.calls = 0

    async def call_tool(self, tool_name, arguments):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("ClosedResourceError: stream closed")
        return type("Result", (), {"structuredContent": {"ok": True}, "content": []})()


@pytest.mark.asyncio
async def test_timeout_failure_does_not_enter_cooldown():
    pool = MCPClientPool()
    pool._tool_timeout_seconds = 1.0

    async def _fake_get_session(server_type: str):
        return _SlowSession()

    pool.get_session = _fake_get_session  # type: ignore[assignment]

    with pytest.raises(RuntimeError) as exc:
        await pool.call_tool(
            server_type=MCPClientPool.GAME_TOOLS,
            tool_name="slow_tool",
            arguments={},
            max_retries=0,
        )

    assert "slow_tool" in str(exc.value)
    assert not pool._in_cooldown(MCPClientPool.GAME_TOOLS)


@pytest.mark.asyncio
async def test_non_timeout_failure_enters_cooldown():
    pool = MCPClientPool()
    pool._tool_timeout_seconds = 1.0
    pool._cooldown_seconds = 30.0

    async def _fake_get_session(server_type: str):
        return _FailSession()

    pool.get_session = _fake_get_session  # type: ignore[assignment]

    with pytest.raises(RuntimeError):
        await pool.call_tool(
            server_type=MCPClientPool.GAME_TOOLS,
            tool_name="fail_tool",
            arguments={},
            max_retries=0,
        )

    assert pool._in_cooldown(MCPClientPool.GAME_TOOLS)


@pytest.mark.asyncio
async def test_connect_failure_maps_to_service_unavailable():
    pool = MCPClientPool()
    pool._tool_timeout_seconds = 1.0
    pool._cooldown_seconds = 30.0

    async def _fake_get_session(server_type: str):
        return _ConnectFailSession()

    pool.get_session = _fake_get_session  # type: ignore[assignment]

    with pytest.raises(MCPServiceUnavailableError) as exc:
        await pool.call_tool(
            server_type=MCPClientPool.GAME_TOOLS,
            tool_name="connect_fail_tool",
            arguments={},
            max_retries=0,
        )

    assert exc.value.server_type == MCPClientPool.GAME_TOOLS


@pytest.mark.asyncio
async def test_session_lifecycle_error_forces_reconnect(monkeypatch):
    pool = MCPClientPool()
    pool._tool_timeout_seconds = 1.0

    session = _SessionLifecycleFailThenSuccess()
    close_calls = {"count": 0}

    async def _fake_get_session(server_type: str):
        return session

    async def _fake_close_session(server_type: str):
        close_calls["count"] += 1

    pool.get_session = _fake_get_session  # type: ignore[assignment]
    monkeypatch.setattr(pool, "_close_session", _fake_close_session)

    result = await pool.call_tool(
        server_type=MCPClientPool.GAME_TOOLS,
        tool_name="recoverable_tool",
        arguments={},
        max_retries=1,
    )

    assert result == {"ok": True}
    assert close_calls["count"] == 1
    assert pool._server_stats[MCPClientPool.GAME_TOOLS]["session_errors"] == 1
    assert pool._server_stats[MCPClientPool.GAME_TOOLS]["forced_reconnects"] == 1
