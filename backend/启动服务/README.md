# 服务启动说明

## 启动顺序

1. **先启动 MCP 服务**
2. **再启动 FastAPI**

---

## 1. 启动 MCP 服务

```bash
cd /home/xiaokebuyu/workplace/gemini-hackton/backend
bash 启动服务/run_mcp_services.sh
```

**服务端点**：
- Game Tools MCP: `http://127.0.0.1:9101/mcp`
- Combat MCP: `http://127.0.0.1:9102/mcp`

**日志位置**: `logs/` 目录

---

## 2. 启动 FastAPI

### 方式 A：使用启动脚本（推荐）

```bash
cd /home/xiaokebuyu/workplace/gemini-hackton/backend
MCP_TOOLS_TRANSPORT=streamable-http \
MCP_TOOLS_ENDPOINT=http://127.0.0.1:9101/mcp \
MCP_COMBAT_TRANSPORT=streamable-http \
MCP_COMBAT_ENDPOINT=http://127.0.0.1:9102/mcp \
bash 启动服务/run_fastapi.sh
```

### 方式 B：添加到 .env 文件

在 `.env` 文件中添加：
```
MCP_TOOLS_TRANSPORT=streamable-http
MCP_TOOLS_ENDPOINT=http://127.0.0.1:9101/mcp
MCP_COMBAT_TRANSPORT=streamable-http
MCP_COMBAT_ENDPOINT=http://127.0.0.1:9102/mcp
```

然后直接运行：
```bash
bash 启动服务/run_fastapi.sh
```

---

## 环境变量说明

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MCP_TOOLS_TRANSPORT` | `stdio` | MCP 传输方式 (stdio/streamable-http) |
| `MCP_TOOLS_ENDPOINT` | - | Game Tools MCP 端点 |
| `MCP_COMBAT_TRANSPORT` | `stdio` | Combat MCP 传输方式 |
| `MCP_COMBAT_ENDPOINT` | - | Combat MCP 端点 |
| `MCP_HOST` | `127.0.0.1` | MCP 服务监听地址 |
| `MCP_TOOLS_PORT` | `9101` | Game Tools 端口 |
| `MCP_COMBAT_PORT` | `9102` | Combat 端口 |
| `API_HOST` | `0.0.0.0` | FastAPI 监听地址 |
| `API_PORT` | `8000` | FastAPI 端口 |

---

## 停止服务

- MCP 服务：在终端按 `Ctrl+C`
- FastAPI：在终端按 `Ctrl+C`

---

## 检查服务状态

```bash
# 检查 MCP 进程
ps aux | grep -E "game_tools_server|combat_mcp_server" | grep -v grep

# 检查端口监听
ss -tlnp | grep -E "9101|9102|8000"
```
