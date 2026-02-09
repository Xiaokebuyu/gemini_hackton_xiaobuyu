#!/bin/bash
# FastAPI 端到端测试运行脚本
#
# 前置条件：
# 1. MCP 服务必须已启动 (run_mcp_services.sh)
# 2. 虚拟环境已激活
#
# 用法:
#   ./run_e2e_tests.sh              # 运行所有测试
#   ./run_e2e_tests.sh phase1       # 只运行阶段1
#   ./run_e2e_tests.sh -k "health"  # 运行包含 health 的测试
#   ./run_e2e_tests.sh --check      # 只检查前置条件

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 配置
TOOLS_PORT="${MCP_TOOLS_PORT:-9101}"
COMBAT_PORT="${MCP_COMBAT_PORT:-9102}"
HOST="${MCP_HOST:-127.0.0.1}"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
echo_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
echo_error() { echo -e "${RED}[ERROR]${NC} $1"; }

check_tcp_port() {
    local host="$1"
    local port="$2"

    if command -v nc >/dev/null 2>&1; then
        nc -z "$host" "$port" >/dev/null 2>&1
        return $?
    fi

    "$PY" - "$host" "$port" <<'PY' >/dev/null 2>&1
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
try:
    with socket.create_connection((host, port), timeout=1.5):
        pass
except OSError:
    raise SystemExit(1)
PY
}

# 检查前置条件
check_prerequisites() {
    echo_info "检查前置条件..."

    # 检查 Python 环境
    if [ -x "$ROOT/venv/bin/python" ]; then
        PY="$ROOT/venv/bin/python"
    else
        PY="python"
    fi
    echo_info "Python: $PY"

    # 检查 MCP Game Tools 服务
    if ! check_tcp_port "$HOST" "$TOOLS_PORT"; then
        echo_error "Game Tools MCP 未运行 (端口 $TOOLS_PORT)"
        echo "请先启动 MCP 服务: bash 启动服务/run_mcp_services.sh"
        return 1
    fi
    echo_info "Game Tools MCP: http://$HOST:$TOOLS_PORT/mcp ✓"

    # 检查 Combat MCP 服务
    if ! check_tcp_port "$HOST" "$COMBAT_PORT"; then
        echo_error "Combat MCP 未运行 (端口 $COMBAT_PORT)"
        echo "请先启动 MCP 服务: bash 启动服务/run_mcp_services.sh"
        return 1
    fi
    echo_info "Combat MCP: http://$HOST:$COMBAT_PORT/mcp ✓"

    # 检查依赖
    if ! "$PY" -c "import pytest, httpx, pytest_asyncio" 2>/dev/null; then
        echo_error "缺少测试依赖"
        echo "请运行: pip install pytest pytest-asyncio httpx"
        return 1
    fi
    echo_info "测试依赖: ✓"

    echo_info "所有前置条件满足"
    return 0
}

# 显示帮助
show_help() {
    cat << EOF
FastAPI 端到端测试运行脚本

用法:
  $0 [选项] [pytest参数]

选项:
  --check       只检查前置条件，不运行测试
  --help, -h    显示此帮助信息

快捷方式:
  phase1        只运行阶段1 (基础连通性)
  phase2        只运行阶段2 (Game Tools MCP)
  phase3        只运行阶段3 (Combat MCP)
  phase4        只运行阶段4 (队伍系统)
  phase5        只运行阶段5 (路人与事件)
  integration   只运行集成测试

示例:
  $0                           # 运行所有测试
  $0 phase1                    # 只运行阶段1
  $0 -k "health"               # 运行包含 health 的测试
  $0 -v --tb=short             # 详细输出，简短回溯
  $0 --check                   # 只检查前置条件

环境变量:
  MCP_HOST         MCP 服务主机 (默认: 127.0.0.1)
  MCP_TOOLS_PORT   Game Tools 端口 (默认: 9101)
  MCP_COMBAT_PORT  Combat 端口 (默认: 9102)
EOF
}

# 主函数
main() {
    # 处理参数
    case "${1:-}" in
        --help|-h)
            show_help
            exit 0
            ;;
        --check)
            check_prerequisites
            exit $?
            ;;
        phase1)
            shift
            set -- -k "TestPhase1" "$@"
            ;;
        phase2)
            shift
            set -- -k "TestPhase2" "$@"
            ;;
        phase3)
            shift
            set -- -k "TestPhase3" "$@"
            ;;
        phase4)
            shift
            set -- -k "TestPhase4" "$@"
            ;;
        phase5)
            shift
            set -- -k "TestPhase5" "$@"
            ;;
        integration)
            shift
            set -- -k "TestIntegration" "$@"
            ;;
    esac

    # 检查前置条件
    if ! check_prerequisites; then
        exit 1
    fi

    echo ""
    echo_info "开始运行测试..."
    echo ""

    # 设置环境变量并运行测试
    PYTHONPATH="$ROOT" \
    MCP_TOOLS_TRANSPORT=streamable-http \
    MCP_TOOLS_ENDPOINT="http://$HOST:$TOOLS_PORT/mcp" \
    MCP_COMBAT_TRANSPORT=streamable-http \
    MCP_COMBAT_ENDPOINT="http://$HOST:$COMBAT_PORT/mcp" \
    "$ROOT/venv/bin/pytest" tests/test_fastapi_to_mcp.py -v -s "$@"
}

main "$@"
