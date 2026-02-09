import asyncio

import pytest

from app.services.admin.flash_cpu_service import FlashCPUService
from app.services.mcp_client_pool import MCPClientPool, MCPServiceUnavailableError


class _PoolRaisesCancelled:
    async def call_tool(self, server_type, tool_name, arguments):
        raise asyncio.CancelledError()


class _PoolRaisesUnavailable:
    async def call_tool(self, server_type, tool_name, arguments):
        raise MCPServiceUnavailableError(
            server_type=server_type,
            endpoint="http://127.0.0.1:9102/mcp",
            detail="ConnectError: All connection attempts failed",
        )


class _PoolRaisesRuntime:
    async def call_tool(self, server_type, tool_name, arguments):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_call_combat_tool_propagates_cancelled_error(monkeypatch):
    service = FlashCPUService()

    async def _fake_get_instance(cls):
        return _PoolRaisesCancelled()

    monkeypatch.setattr(MCPClientPool, "get_instance", classmethod(_fake_get_instance))

    with pytest.raises(asyncio.CancelledError):
        await service._call_combat_tool("start_combat_session", {})


@pytest.mark.asyncio
async def test_call_combat_tool_propagates_mcp_unavailable(monkeypatch):
    service = FlashCPUService()

    async def _fake_get_instance(cls):
        return _PoolRaisesUnavailable()

    monkeypatch.setattr(MCPClientPool, "get_instance", classmethod(_fake_get_instance))

    with pytest.raises(MCPServiceUnavailableError):
        await service._call_combat_tool("start_combat_session", {})


@pytest.mark.asyncio
async def test_call_combat_tool_propagates_generic_errors(monkeypatch):
    service = FlashCPUService()

    async def _fake_get_instance(cls):
        return _PoolRaisesRuntime()

    monkeypatch.setattr(MCPClientPool, "get_instance", classmethod(_fake_get_instance))

    with pytest.raises(RuntimeError, match="boom"):
        await service._call_combat_tool("start_combat_session", {})
