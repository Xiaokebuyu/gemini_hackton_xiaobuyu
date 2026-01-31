"""
Game tools MCP server.
"""
from mcp.server.fastmcp import FastMCP

from app.mcp.tools import (
    npc_tools,
    passerby_tools,
    narrative_tools,
    navigation_tools,
    time_tools,
    graph_tools,
)


game_mcp = FastMCP(
    name="Game Tools MCP",
    instructions="""
RPG 游戏系统工具集。

包含：NPC实例、路人、章节、导航、时间、图谱等工具。
""",
)


def _register_tools() -> None:
    for module in (
        npc_tools,
        passerby_tools,
        narrative_tools,
        navigation_tools,
        time_tools,
        graph_tools,
    ):
        module.register(game_mcp)


_register_tools()


if __name__ == "__main__":
    game_mcp.run(transport="stdio")
