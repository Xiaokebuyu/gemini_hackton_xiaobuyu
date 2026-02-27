from httpx import ASGITransport, AsyncClient
import pytest

from app.main import app
from app.services.mcp_client_pool import MCPClientPool


class _FakePool:
    async def get_diagnostics(self, *, include_probe: bool, timeout_seconds: float):
        return {
            "generated_at": "2026-02-09T00:00:00+00:00",
            "settings": {"probe_mode": "handshake"},
            "servers": {},
            "recent_errors": [],
            "recent_tool_calls": [],
            "echo": {
                "include_probe": include_probe,
                "timeout_seconds": timeout_seconds,
            },
        }


@pytest.mark.asyncio
async def test_mcp_diagnostics_endpoint_returns_payload(monkeypatch):
    async def _fake_get_instance(cls):
        return _FakePool()

    monkeypatch.setattr(MCPClientPool, "get_instance", classmethod(_fake_get_instance))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/admin/mcp/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert "servers" in data
    assert data["echo"]["include_probe"] is True
