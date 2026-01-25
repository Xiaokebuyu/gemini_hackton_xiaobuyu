#!/usr/bin/env python3
"""
Context Memory MCP Server 启动脚本

使用方法：
    # 启动 stdio 传输（用于 Claude Desktop 等本地客户端）
    python run_mcp_server.py
    
    # 启动 HTTP 传输（用于远程调用）
    python run_mcp_server.py --transport streamable-http --port 8080
    
    # 启动 SSE 传输（用于 Web 应用）
    python run_mcp_server.py --transport sse --port 8080
"""

import sys
import os

# 确保可以导入 app 模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 加载环境变量
from dotenv import load_dotenv
load_dotenv()


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Context Memory MCP Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # stdio 模式（默认，用于 Claude Desktop）
  python run_mcp_server.py
  
  # HTTP 模式
  python run_mcp_server.py --transport streamable-http --port 8080
  
  # SSE 模式
  python run_mcp_server.py --transport sse --port 8080

配置 Claude Desktop:
  在 claude_desktop_config.json 中添加:
  {
    "mcpServers": {
      "context-memory": {
        "command": "python",
        "args": ["/path/to/backend/run_mcp_server.py"],
        "env": {
          "GOOGLE_APPLICATION_CREDENTIALS": "/path/to/credentials.json"
        }
      }
    }
  }
"""
    )
    
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="传输方式 (默认: stdio)"
    )
    
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="HTTP/SSE 传输的端口 (默认: 8080)"
    )
    
    parser.add_argument(
        "--debug",
        action="store_true",
        help="启用调试模式"
    )
    
    args = parser.parse_args()
    
    # 设置环境变量
    if args.transport in ["streamable-http", "sse"]:
        os.environ["MCP_HTTP_PORT"] = str(args.port)
    
    if args.debug:
        os.environ["MCP_DEBUG"] = "1"
    
    # 检查必要的环境变量
    required_env = ["GOOGLE_APPLICATION_CREDENTIALS"]
    missing = [env for env in required_env if not os.getenv(env)]
    
    if missing and not args.debug:
        print(f"警告: 缺少环境变量: {', '.join(missing)}")
        print("可能导致 Firestore 连接失败")
    
    # 启动服务器
    print("=" * 60)
    print("Context Memory MCP Server")
    print("=" * 60)
    print(f"传输方式: {args.transport}")
    if args.transport != "stdio":
        print(f"端口: {args.port}")
    print("=" * 60)
    
    from app.legacy_mcp import run_mcp_server
    run_mcp_server(args.transport)


if __name__ == "__main__":
    main()
