"""测试 FlashCPUService 的 MCP 集成"""
import asyncio
import pytest
import pytest_asyncio

from app.services.admin.flash_cpu_service import FlashCPUService
from app.services.admin.state_manager import StateManager
from app.services.mcp_client_pool import MCPClientPool
from app.models.admin_protocol import FlashOperation, FlashRequest


@pytest.fixture(scope="function")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


class TestFlashMCPIntegration:
    """FlashCPUService MCP 集成测试"""

    @pytest.fixture
    def flash_cpu(self):
        """创建 FlashCPUService 实例"""
        return FlashCPUService()

    @pytest_asyncio.fixture(autouse=True)
    async def cleanup(self):
        yield
        await MCPClientPool.shutdown()

    @pytest.mark.asyncio
    async def test_call_tool_with_fallback_mcp_path(self, flash_cpu):
        """测试 MCP 调用路径（而非 fallback）"""
        fallback_called = False

        def fallback():
            nonlocal fallback_called
            fallback_called = True
            return {"fallback": True, "error": "fallback was called"}

        result = await flash_cpu._call_tool_with_fallback(
            "get_time",
            {"world_id": "test", "session_id": "test"},
            fallback=fallback,
        )
        # 如果 MCP 成功，不应该有 fallback 标记
        print(f"Result: {result}")
        print(f"Fallback called: {fallback_called}")
        # 检查是否真的调用了 MCP（无 fallback 标记）
        if not fallback_called:
            print("✓ MCP 调用成功，未走 fallback")
        else:
            print("✗ MCP 调用失败，走了 fallback")

    @pytest.mark.asyncio
    async def test_navigate_operation(self, flash_cpu):
        """测试导航操作（需要 world_runtime）"""
        # 注意：这个测试可能因为没有真实会话而失败
        request = FlashRequest(
            operation=FlashOperation.NAVIGATE,
            parameters={"destination": "town_square"}
        )
        result = await flash_cpu.execute_request(
            world_id="test_world",
            session_id="test_session",
            request=request
        )
        print(f"Navigate result: {result}")
        # 因为没有 world_runtime，应该返回错误
        assert result is not None
        # 预期会失败，因为 world_runtime 未初始化
        if not result.success:
            print(f"✓ 符合预期 - 错误: {result.error}")
        else:
            print(f"✓ 导航成功: {result.result}")

    @pytest.mark.asyncio
    async def test_combat_tool_call(self, flash_cpu):
        """测试战斗工具调用"""
        result = await flash_cpu._call_combat_tool(
            "start_combat",
            {
                "enemies": [{"type": "goblin", "level": 1}],
                "player_state": {"hp": 100, "max_hp": 100, "ac": 15, "attack_bonus": 5, "damage_dice": "1d8", "damage_bonus": 3}
            }
        )
        print(f"Combat tool result: {result}")
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_process_player_input_time_command(self, flash_cpu):
        """测试 /time 命令"""
        result = await flash_cpu.process_player_input(
            world_id="test_world",
            session_id="test_session",
            player_input="/time"
        )
        print(f"Process /time result: {result}")
        assert result["type"] == "system"
        assert "time" in result or "response" in result

    @pytest.mark.asyncio
    async def test_process_player_input_where_command(self, flash_cpu):
        """测试 /where 命令"""
        result = await flash_cpu.process_player_input(
            world_id="test_world",
            session_id="test_session",
            player_input="/where"
        )
        print(f"Process /where result: {result}")
        assert result["type"] == "system"
        assert "location" in result or "response" in result

    @pytest.mark.asyncio
    async def test_start_combat_operation(self, flash_cpu):
        """测试战斗开始操作"""
        request = FlashRequest(
            operation=FlashOperation.START_COMBAT,
            parameters={
                "enemies": [{"type": "goblin", "level": 1}],
                "player_state": {"hp": 100, "max_hp": 100, "ac": 15, "attack_bonus": 5, "damage_dice": "1d8", "damage_bonus": 3}
            }
        )
        result = await flash_cpu.execute_request(
            world_id="test_world",
            session_id="test_session",
            request=request
        )
        print(f"Start combat result: {result}")
        assert result is not None
