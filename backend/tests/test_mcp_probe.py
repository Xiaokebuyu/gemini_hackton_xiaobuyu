import pytest

from app.services.mcp_client_pool import MCPClientPool, ServerConfig


@pytest.mark.asyncio
async def test_probe_streamable_http_falls_back_to_endpoint(monkeypatch):
    pool = MCPClientPool()
    pool._configs[MCPClientPool.COMBAT] = ServerConfig(
        command="python",
        args="",
        cwd=pool._server_root,
        name="Combat MCP",
        transport="streamable-http",
        endpoint="http://127.0.0.1:9102/mcp",
    )

    async def _fake_probe(url: str, timeout_seconds: float):
        if url.endswith("/health"):
            return {"ok": False, "url": url, "error": "connection refused"}
        return {"ok": True, "url": url, "status_code": 405}

    monkeypatch.setattr(pool, "_probe_http_endpoint", _fake_probe)
    result = await pool.probe(MCPClientPool.COMBAT, timeout_seconds=0.5)

    assert result["ok"] is True
    assert result["health_probe"]["ok"] is False
    assert result["endpoint_probe"]["ok"] is True


@pytest.mark.asyncio
async def test_probe_streamable_http_missing_endpoint():
    pool = MCPClientPool()
    pool._configs[MCPClientPool.COMBAT] = ServerConfig(
        command="python",
        args="",
        cwd=pool._server_root,
        name="Combat MCP",
        transport="streamable-http",
        endpoint="",
    )

    result = await pool.probe(MCPClientPool.COMBAT)
    assert result["ok"] is False
    assert result["error"] == "missing endpoint"


@pytest.mark.asyncio
async def test_probe_dependencies_returns_all_targets(monkeypatch):
    pool = MCPClientPool()

    async def _fake_probe(server_type: str, timeout_seconds: float = 2.0):
        return {"ok": server_type == MCPClientPool.GAME_TOOLS, "server_type": server_type}

    monkeypatch.setattr(pool, "probe", _fake_probe)

    result = await pool.probe_dependencies(
        timeout_seconds=0.5,
        server_types=[MCPClientPool.GAME_TOOLS, MCPClientPool.COMBAT],
    )
    assert set(result.keys()) == {MCPClientPool.GAME_TOOLS, MCPClientPool.COMBAT}
    assert result[MCPClientPool.GAME_TOOLS]["ok"] is True
    assert result[MCPClientPool.COMBAT]["ok"] is False
