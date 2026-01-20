# MCP 记忆网关接入文档

## 概述
本项目提供 MCP 记忆网关，面向“外部主聊天 LLM”。主 LLM 负责理解与决策，仅在需要历史记忆时调用 MCP；MCP 负责检索、归档与上下文拼装。

## 推荐交互流程（LLM-first）
1. **会话恢复**：编排器调用 `session_snapshot` 获取最近窗口（默认 120k tokens）与插入区。  
2. **正常对话**：主 LLM 仅使用最近窗口即可回复，无需请求 MCP。  
3. **需要记忆时**：主 LLM 调用 `memory_request(need=自然语言需求)`，MCP 返回插入区上下文。  
4. **写回**：编排器用 `memory_commit` 写入用户/助手消息，触发归档队列。

## 工具接口

### session_snapshot
用途：会话恢复/初次引导。  
输入（关键字段）：
```json
{"user_id":"u1","session_id":"s1","window_tokens":120000,"insert_budget_tokens":20000}
```
输出：`context.current_window_messages` + `insert_messages` + `assembled_messages`。

### memory_request
用途：主 LLM 需要历史记忆时调用。  
输入（关键字段）：
```json
{"user_id":"u1","session_id":"s1","need":"请给我与归档触发条件相关的记忆与原文"}
```
输出：插入区 `insert_messages`（system 消息），包含：
- 当前 session 话题总结
- 检索到的记忆摘要
- 原文消息片段

### memory_commit
用途：编排器写入消息并触发归档。  
输入（关键字段）：
```json
{"user_id":"u1","session_id":"s1","messages":[{"role":"user","content":"..."},{"role":"assistant","content":"..."}]}
```
输出：保存的 `message_id` 与 `stream_stats`。

## 上下文拼装规则
- 插入区是 **system 消息**，每次 `memory_request` 会替换。  
- 插入区不计入窗口预算，但会消耗模型实际上下文 token。  
  建议：`window_tokens + insert_budget_tokens + response_budget <= 模型上限`。

## 何时调用 memory_request
- 用户提到“以前讨论过的内容”、历史结论、具体旧对话细节。  
- 当前窗口无法覆盖的知识点。  
否则优先只用窗口上下文回复。

## 配置项（app/config.py / 环境变量）
- `MEMORY_WINDOW_TOKENS`（默认 120000）
- `MEMORY_INSERT_BUDGET_TOKENS`（默认 20000）
- `MEMORY_MAX_THREADS`、`MEMORY_MAX_RAW_MESSAGES`
- `MEMORY_SESSION_TTL_SECONDS`、`MEMORY_STREAM_LOAD_LIMIT`
- `EMBEDDING_PROVIDER`（`gemini`/`cloudflare`）
- `GEMINI_EMBEDDING_MODEL`、`GEMINI_FLASH_MODEL`

## 测试
单元测试（无外部依赖）：
```
PYTHONPATH=backend pytest tests/test_memory_gateway.py -v
```
真实集成测试（需要 Firestore/Gemini）：
```
./venv/bin/python -m pytest ../tests/test_integration.py -v -s
```
