"""
Game tools MCP server.
"""
import argparse
import os
from mcp.server.fastmcp import FastMCP

from app.mcp.tools import (
    npc_tools,
    passerby_tools,
    narrative_tools,
    navigation_tools,
    time_tools,
    graph_tools,
    party_tools,
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
        party_tools,
    ):
        module.register(game_mcp)


_register_tools()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Game Tools MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=os.getenv("MCP_TRANSPORT", "stdio"),
        help="Transport protocol",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("MCP_HOST", "127.0.0.1"),
        help="Bind host for HTTP transports",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MCP_PORT", "9101")),
        help="Bind port for HTTP transports",
    )
    args = parser.parse_args()

    game_mcp.settings.host = args.host
    game_mcp.settings.port = args.port
    game_mcp.run(transport=args.transport)
