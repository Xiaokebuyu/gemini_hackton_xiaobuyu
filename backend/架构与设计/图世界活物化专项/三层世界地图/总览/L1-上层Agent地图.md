# L1 上层 Agent 地图 — 代码对齐版

> 更新时间：2026-02-24
> 口径：以当前仓库代码为准（`app/` 实际文件 + 可导入性验证）
> 基线：`全量文件清单与层级归类.md`（2026-02-24 代码对齐版）
> 状态：Phase A/B/C 代码已落地；仍有跨层断裂需修复

---

## 一、L1 判定口径

**L1 = Agent 决策与 LLM 行为层**，包括：
- Agent 执行器与工具绑定
- LLM 驱动的响应/解析/图谱化
- Agent 系统提示加载与角色工具路由

**不计入 L1**：
- 纯规则/状态/算法（L3）
- 路由与编排（L2）
- 启动/配置/SDK 适配（Infra）

---

## 二、当前 L1 模块（代码实况）

### 2.1 `app/agentic/`（8 个 L1 文件）

| 模块 | 路径 | 行数 | 说明 |
|------|------|------|------|
| AgenticExecutor | `app/agentic/agentic_executor.py` | 197 | GM/NPC/队友统一执行循环 |
| ImmersiveTools | `app/agentic/immersive_tools.py` | 577 | 工具注册、`AgenticContext`、bind |
| RoleRegistry | `app/agentic/role_registry.py` | 64 | 角色到工具集映射 |
| MemoryGraphizer | `app/agentic/memory_graphizer.py` | 487 | 对话→图谱转换 |
| EventLLMService | `app/agentic/event_llm_service.py` | 270 | 事件 NLP 三步解析 |
| PasserbyLLM | `app/agentic/passerby_llm.py` | 83 | 路人对话 LLM 逻辑 |
| PromptLoader | `app/agentic/prompt_loader.py` | 22 | Agentic 系统提示加载 |
| TeammateResponseService | `app/agentic/teammate/response_service.py` | 1136 | 队友决策与响应生成 |

### 2.2 已移除/迁出项

| 旧模块 | 现状 |
|------|------|
| `app/services/admin/flash_cpu_service.py` | 已删除 |
| `app/agentic/gm_extra_tools.py` | 已删除 |
| `app/services/tiered_ai_service.py` | 已删除 |
| `app/services/teammate_response_service.py` | 已迁入 `app/agentic/teammate/response_service.py` |
| `app/services/memory_graphizer.py` | 已迁入 `app/agentic/memory_graphizer.py` |
| `app/services/event_llm_service.py` | 已迁入 `app/agentic/event_llm_service.py` |

---

## 三、Infra 适配器（物理在 services，逻辑归 Infra）

| 模块 | 路径 | 行数 | 归类 |
|------|------|------|------|
| LLMService | `app/services/llm_service.py` | 917 | Infra（Gemini 文本 SDK 适配器） |
| ImageGenerationService | `app/services/image_generation_service.py` | 152 | Infra（Gemini 图像 SDK 适配器） |

---

## 四、L1 资源文件（Prompts）

当前保留 8 个提示模板：
- `app/prompts/flash_agentic_system.md`
- `app/prompts/flash_cpu_system.md`（遗留模板，代码主路径已不依赖 FlashCPU）
- `app/prompts/teammate_agentic_system.md`
- `app/prompts/teammate_decision.md`
- `app/prompts/teammate_response.md`
- `app/prompts/travel_narration.md`
- `app/prompts/opening_narration.md`
- `app/prompts/session_resume.md`

---

## 五、当前关键风险（按严重级）

### P0（阻断运行）

1. 删除的 Store 模块仍被核心路径 import：
   - `app/services/admin/admin_coordinator.py`
   - `app/world/player/character.py`
   - `app/world/player/ability_check.py`
   - `app/mcp/tools/character_tools.py`
   - `app/mcp/tools/inventory_tools.py`
   - `app/mcp/tools/party_tools.py`
   - `app/combat/combat_mcp_server.py`

   以上会触发 `ModuleNotFoundError`（`character_store` / `game_session_store` / `party_store`）。

2. `area_runtime.py` 已删除但仍被运行时依赖：
   - `app/runtime/session_runtime.py`
   - `app/runtime/event_machine.py`

   会导致 `SessionRuntime` 初始化失败。

### P1（L1 质量债）

1. `LLMService` 仍有 `print()` 异常输出（7 处），未统一到 logging。  
2. `AgenticExecutor` 工具超时仍硬编码 30 秒（未走 `settings`）。  
3. `PipelineOrchestrator` 文档注释仍提及 FlashCPU，和现代码不一致。  
4. `AdminCoordinator` 仍保留 `**kwargs` 兼容已废弃参数（技术债）。

---

## 六、关键统计（当前快照）

| 指标 | 数值 |
|------|------|
| L1 源文件（代码） | 8（全部位于 `app/agentic/`） |
| L1 提示模板 | 8 |
| L1 代码总行数 | 2836 |
| 沉浸工具总数 | 29（base 11 / gm 16 / teammate 2） |
| `app/services/` 中 L1 文件 | 0（按逻辑口径；`llm/image` 归 Infra） |

### 6.1 结构屎山信号（L1）

- `except Exception`：16 处（集中在 `teammate/response_service.py`、`immersive_tools.py`、`agentic_executor.py`）
- 最大 L1 文件：`app/agentic/teammate/response_service.py`（1136 行）
- L1 最大函数：`TeammateResponseService.process_round_stream`（146 行）
- L1 当前屎山度（结构口径）：**6.1/10**

---

## 七、建议修复顺序

1. 先修 P0：去掉对已删除 Store / `area_runtime` 的导入依赖，恢复可运行性。  
2. 再修 L1 可观测性：`LLMService` 的 `print()` → `logger`。  
3. 统一工具超时配置：`AgenticExecutor` timeout 改走 `settings`。  
4. 清理文档与兼容参数残留：去掉 FlashCPU 时代术语和废弃兼容分支。
