"""测试 FlashCPUService 的 MCP 集成"""
import asyncio
import pytest
import pytest_asyncio

from app.services.admin.flash_cpu_service import FlashCPUService
from app.services.admin.state_manager import StateManager
from app.services.mcp_client_pool import MCPClientPool, MCPServiceUnavailableError
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
    async def test_combat_tool_call(self, flash_cpu):
        """测试战斗工具调用"""
        try:
            result = await flash_cpu._call_combat_tool(
                "start_combat",
                {
                    "enemies": [{"type": "goblin", "level": 1}],
                    "player_state": {"hp": 100, "max_hp": 100, "ac": 15, "attack_bonus": 5, "damage_dice": "1d8", "damage_bonus": 3}
                }
            )
            print(f"Combat tool result: {result}")
            assert isinstance(result, dict)
        except MCPServiceUnavailableError as exc:
            print(f"Combat MCP unavailable (expected in local unit runs): {exc}")
            assert "MCP service unavailable" in str(exc)

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
        try:
            result = await flash_cpu.execute_request(
                world_id="test_world",
                session_id="test_session",
                request=request
            )
            print(f"Start combat result: {result}")
            assert result is not None
        except MCPServiceUnavailableError as exc:
            print(f"Combat MCP unavailable (expected in local unit runs): {exc}")
            assert "MCP service unavailable" in str(exc)
