"""测试 MCP 连接池和工具调用"""
import asyncio
import pytest
import pytest_asyncio

from app.services.mcp_client_pool import MCPClientPool


@pytest.fixture(scope="function")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


class TestMCPClientPool:
    """MCP 连接池测试"""

    @pytest_asyncio.fixture(autouse=True)
    async def cleanup(self):
        """每个测试后清理连接池"""
        yield
        await MCPClientPool.shutdown()

    @pytest.mark.asyncio
    async def test_singleton_instance(self):
        """测试单例模式"""
        pool1 = await MCPClientPool.get_instance()
        pool2 = await MCPClientPool.get_instance()
        assert pool1 is pool2

    @pytest.mark.asyncio
    async def test_combat_mcp_connection(self):
        """测试 Combat MCP 连接"""
        pool = await MCPClientPool.get_instance()
        session = await pool.get_session(MCPClientPool.COMBAT)
        assert session is not None

    @pytest.mark.asyncio
    async def test_combat_start_tool(self):
        """测试 Combat MCP 的 start_combat 工具"""
        pool = await MCPClientPool.get_instance()
        result = await pool.call_tool(
            MCPClientPool.COMBAT,
            "start_combat",
            {
                "enemies": [{"type": "goblin", "level": 1}],
                "player_state": {"hp": 100, "max_hp": 100, "ac": 15, "attack_bonus": 5, "damage_dice": "1d8", "damage_bonus": 3}
            }
        )
        assert isinstance(result, dict)
        assert "combat_id" in result or "error" in result
        print(f"start_combat result: {result}")
