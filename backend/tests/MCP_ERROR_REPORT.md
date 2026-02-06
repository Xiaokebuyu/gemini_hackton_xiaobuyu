# FastAPI → Admin → MCP 端到端测试错误报告

**测试日期**: 2026-02-05
**测试环境**: Linux, Python 3.13.5

---

## 测试概览

| 测试类型 | 通过 | 失败 | 总计 |
|----------|------|------|------|
| MCPClientPool 单元测试 | 1 | 6 | 7 |
| FlashCPUService 集成测试 | 5 | 1 | 6 |
| FastAPI 端到端测试 | 10 | 0 | 10 |

---

## 核心错误：MCP 子进程启动失败

### 错误现象

```
/home/xiaokebuyu/workplace/gemini-hackton/backend/venv/bin/python: Error while finding module specification for 'app.mcp.game_tools_server' (ModuleNotFoundError: No module named 'app')
```

### 错误根因

MCP 服务器子进程通过 `python -m app.mcp.game_tools_server` 启动时，**子进程没有正确的 PYTHONPATH**，导致 Python 无法找到 `app` 模块。

### 详细分析

1. **配置来源** (`app/config.py:64-67`):
   ```python
   mcp_tools_command: str = os.getenv("MCP_TOOLS_COMMAND", "python")
   mcp_tools_args: str = os.getenv("MCP_TOOLS_ARGS", "-m app.mcp.game_tools_server")
   mcp_combat_command: str = os.getenv("MCP_COMBAT_COMMAND", "python")
   mcp_combat_args: str = os.getenv("MCP_COMBAT_ARGS", "-m app.combat.combat_mcp_server")
   ```

2. **子进程启动** (`app/services/mcp_client_pool.py:170-181`):
   ```python
   server_params = StdioServerParameters(
       command=config.command,       # "python"
       args=args,                    # ["-m", "app.mcp.game_tools_server"]
       cwd=str(config.cwd),          # backend 目录
   )
   ```

3. **问题**：虽然 `cwd` 设置正确，但子进程的 `PYTHONPATH` 没有包含 backend 目录，导致 `import app` 失败。

### 影响范围

| 工具 | 状态 | 错误 |
|------|------|------|
| Game Tools MCP | ❌ 连接失败 | `ModuleNotFoundError: No module named 'app'` |
| Combat MCP | ❌ 连接失败 | `ModuleNotFoundError: No module named 'app'` |

---

## 错误链条

```
MCPClientPool.get_session()
    │
    ▼
MCPClientPool._connect()
    │
    ├─► StdioServerParameters(command="python", args=["-m", "app.mcp.game_tools_server"])
    │
    ▼
stdio_client() 启动子进程
    │
    ▼
子进程: python -m app.mcp.game_tools_server
    │
    ▼
❌ ModuleNotFoundError: No module named 'app'
    │
    ▼
子进程立即退出，连接关闭
    │
    ▼
MCP 客户端收到 "Connection closed"
    │
    ▼
MCPClientPool 进入 30 秒冷却期
```

---

## Fallback 机制验证

### 验证结果

Fallback 机制工作正常：

1. **FlashCPUService._call_tool_with_fallback**:
   - MCP 调用失败后，成功触发 fallback
   - 日志: `[FlashCPU] MCP call failed, using fallback`

2. **FlashCPUService._call_combat_tool_with_fallback**:
   - MCP 调用失败后，成功触发 fallback
   - 返回: `{'type': 'error', 'response': '战斗工具不可用'}`

### 测试证据

```python
# test_flash_mcp_integration.py::test_call_tool_with_fallback_mcp_path
Result: {'fallback': True, 'error': 'fallback was called'}
Fallback called: True
✗ MCP 调用失败，走了 fallback
```

---

## FastAPI 层行为

### 测试结果

| 端点 | 状态码 | MCP 调用 | 结果 |
|------|--------|----------|------|
| `GET /health` | 200 | 无 | 正常 |
| `POST /{world}/sessions` | 200 | 无 | 正常创建会话 |
| `GET /{world}/sessions/{id}/time` | 200 | 未触发 | 返回本地时间 |
| `GET /{world}/sessions/{id}/location` | 404 | 未触发 | 返回"当前位置未知" |
| `POST /{world}/sessions/{id}/input` (=/time) | 200 | 触发 fallback | 返回 GM 叙述 |
| `POST /{world}/sessions/{id}/navigate` | 400 | 未触发 | 返回"未知的目的地" |
| `POST /{world}/sessions/{id}/combat/trigger` | 500 | MCP 失败 | 返回 MCP 错误 |
| `POST /{world}/sessions/{id}/dialogue/start` | 200 | 触发 fallback | 返回 NPC 响应 |

### 关键发现

1. **会话创建和基础操作正常**
2. **MCP 失败时，fallback 机制大多数情况下正常工作**
3. **战斗触发是唯一返回 500 错误的情况**（没有有效的 fallback）

---

## 修复建议

### 方案 1：设置子进程环境变量（推荐）

修改 `mcp_client_pool.py` 中的 `_connect` 方法，添加 `PYTHONPATH` 环境变量：

```python
import os

async def _connect(self, server_type: str) -> ClientSession:
    config = self._configs[server_type]
    args = shlex.split(config.args) if isinstance(config.args, str) else list(config.args)

    # 设置 PYTHONPATH 环境变量
    env = os.environ.copy()
    env["PYTHONPATH"] = str(config.cwd)

    server_params = StdioServerParameters(
        command=config.command,
        args=args,
        cwd=str(config.cwd),
        env=env,  # 传递环境变量
    )
    # ... 其余代码不变
```

### 方案 2：使用绝对路径启动

修改配置，使用完整的模块路径：

```bash
# .env
MCP_TOOLS_COMMAND=/home/xiaokebuyu/workplace/gemini-hackton/backend/venv/bin/python
MCP_TOOLS_ARGS=-c "import sys; sys.path.insert(0, '/home/xiaokebuyu/workplace/gemini-hackton/backend'); from app.mcp.game_tools_server import main; main()"
```

### 方案 3：创建启动脚本

创建 `run_mcp_server.py` 包装脚本：

```python
#!/usr/bin/env python
import sys
import os

# 确保 app 模块可导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.mcp.game_tools_server import main
main()
```

---

## 总结

| 指标 | 结果 |
|------|------|
| **MCP 连接** | ❌ 失败 - 子进程 PYTHONPATH 问题 |
| **Fallback 机制** | ✓ 正常工作 |
| **FastAPI 层** | ✓ 正常（依赖 fallback） |
| **连接池单例** | ✓ 正常 |
| **冷却机制** | ✓ 正常（30秒冷却） |

### 核心问题

**MCP 子进程启动时缺少 PYTHONPATH 环境变量**，导致无法导入 `app` 模块。

### 建议的修复优先级

1. **高优先级**: 修复 `MCPClientPool._connect` 中的环境变量传递
2. **中优先级**: 为 `trigger_combat` 添加有效的 fallback 处理
3. **低优先级**: 改进错误消息，区分 MCP 连接失败和工具调用失败
