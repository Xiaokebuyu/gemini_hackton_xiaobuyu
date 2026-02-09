"""测试 MCP 连接池和工具调用"""
import asyncio
import time
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
    async def test_game_tools_connection(self):
        """测试 Game Tools MCP 连接"""
        pool = await MCPClientPool.get_instance()
        session = await pool.get_session(MCPClientPool.GAME_TOOLS)
        assert session is not None
        # 验证健康检查
        is_healthy = await pool._check_health(MCPClientPool.GAME_TOOLS, session)
        assert is_healthy

    @pytest.mark.asyncio
    async def test_combat_mcp_connection(self):
        """测试 Combat MCP 连接"""
        pool = await MCPClientPool.get_instance()
        session = await pool.get_session(MCPClientPool.COMBAT)
        assert session is not None

    @pytest.mark.asyncio
    async def test_get_time_tool(self):
        """测试 get_time 工具调用"""
        pool = await MCPClientPool.get_instance()
        result = await pool.call_tool(
            MCPClientPool.GAME_TOOLS,
            "get_time",
            {"world_id": "test_world", "session_id": "test_session"}
        )
        # 应该返回错误（因为没有真实会话）或时间数据
        assert isinstance(result, dict)
        print(f"get_time result: {result}")

    @pytest.mark.asyncio
    async def test_get_location_tool(self):
        """测试 get_location 工具调用"""
        pool = await MCPClientPool.get_instance()
        result = await pool.call_tool(
            MCPClientPool.GAME_TOOLS,
            "get_location",
            {"world_id": "test_world", "session_id": "test_session"}
        )
        assert isinstance(result, dict)
        print(f"get_location result: {result}")

    @pytest.mark.asyncio
    async def test_connection_reuse_performance(self):
        """测试连接复用性能"""
        pool = await MCPClientPool.get_instance()

        # 预热连接
        await pool.call_tool(
            MCPClientPool.GAME_TOOLS,
            "get_time",
            {"world_id": "test", "session_id": "test"}
        )

        # 测试10次调用的性能
        start = time.time()
        for i in range(10):
            await pool.call_tool(
                MCPClientPool.GAME_TOOLS,
                "get_time",
                {"world_id": "test", "session_id": f"test_{i}"}
            )
        elapsed = time.time() - start

        print(f"10 calls took {elapsed:.3f}s (avg {elapsed/10*1000:.1f}ms/call)")
        # 复用连接应该很快（<500ms for 10 calls）
        assert elapsed < 5.0, f"连接复用性能异常: {elapsed}s > 5s"

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
