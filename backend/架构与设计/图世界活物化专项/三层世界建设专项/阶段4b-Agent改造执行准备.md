# 阶段 4b — Agent 改造执行准备

> 4a 完成了基础设施（SceneBus 成员、immersive_tools 19 工具、RoleRegistry、recall_for_role）。
> 4b 目标：统一三套 Agent 实现到 AgenticExecutor + 沉浸式工具体系。

---

## 一、现状梳理

### 1.1 三套独立 Agent 实现

| 角色 | 入口 | 工具注册 | LLM 调用 | 工具数 |
|------|------|---------|---------|--------|
| **GM** | `FlashCPU.agentic_process_v4()` | `V4AgenticToolRegistry` | `LLMService.agentic_generate()` | 23 |
| **队友** | `TeammateResponseService._run_agentic_generation_payload()` | `TeammateAgenticToolRegistry` | `FlashCPU.agentic_generate()` → `LLMService` | 3 |
| **NPC** | `NPCReactor._llm_reaction()` | 无工具 | `LLMService.generate_simple()` | 0 |

**问题**：
- 三套 Agent 各自维护工具注册、签名包装、调用录制、SSE 推送逻辑
- GM 22 个工具中 13 个是机械操作（heal/damage/add_item 等），应由引擎处理
- NPC 无工具调用能力，无法表达好感度变化、记忆形成等
- 队友只有 3 个工具（update_disposition / recall_memory / combat_action）

### 1.2 GM 工具全清单（V4AgenticToolRegistry, 23 个）

```
v4_agentic_tools.py 注册顺序:
  npc_dialogue          → 删（NPC 有自己的 agentic 流程了）
  recall_memory         → 迁移到 recall_experience（沉浸式）
  create_memory         → 迁移到 form_impression（沉浸式）
  update_time           → 删（IntentExecutor 处理）
  heal_player           → 删（IntentExecutor/engine 处理）
  damage_player         → 删（engine 处理）
  add_xp                → 删（engine 处理）
  add_item              → 删（engine 处理）
  remove_item           → 删（engine 处理）
  start_combat          → 保留为 extra_tools（战斗时注入）
  get_combat_options    → 保留为 extra_tools
  choose_combat_action  → 保留为 extra_tools
  ability_check         → 保留为 extra_tools
  add_teammate          → 删（engine 处理）
  remove_teammate       → 删（engine 处理）
  disband_party         → 删（engine 处理）
  activate_event        → 删（玩家 UI 点击激活, P1-C）
  complete_event        → 迁移到 conclude_quest（沉浸式）
  fail_event            → 已在沉浸式工具中
  advance_chapter       → 已在沉浸式工具中
  complete_objective    → 删（合并到 conclude_quest）
  advance_stage         → 删（合并到 conclude_quest）
  complete_event_objective → 删（合并到 conclude_quest）
  update_disposition    → 迁移到 react_to_interaction（沉浸式）
  generate_scene_image  → 已在沉浸式工具中
  report_flash_evaluation → 已在沉浸式工具中
```

**结论：23 → 10（沉浸式）+ 4（战斗 extra_tools）**

### 1.3 队友工具（TeammateAgenticToolRegistry, 3 个）

```
teammate_agentic_tools.py:
  update_my_disposition  → 迁移到 react_to_interaction（沉浸式）
  recall_my_memory       → 迁移到 recall_experience（沉浸式）
  choose_my_combat_action → 迁移到 choose_battle_action（沉浸式）
```

**结论：3 → 全部迁移到沉浸式工具，TeammateAgenticToolRegistry 可删除**

### 1.4 NPC 对话流现状

当前 NPC 交互路径：
```
主 Pipeline B-stage → GM 调用 npc_dialogue 工具
  → FlashCPU.execute_request(NPC_DIALOGUE)
    → LLMService.generate_simple() (无工具)
    → 返回纯文本
```

**问题**：NPC 只能说话，不能感知情绪、形成记忆、调整好感度。

### 1.5 关键文件位置

| 文件 | 作用 | 行数 |
|------|------|------|
| `app/services/admin/flash_cpu_service.py` | GM agentic loop 入口 (`agentic_process_v4()`) | ~630 |
| `app/services/admin/v4_agentic_tools.py` | GM 23 工具注册 + _wrap_tool_for_afc + _record | ~700 |
| `app/services/admin/pipeline_orchestrator.py` | 三阶段管线编排 | ~650 |
| `app/services/admin/admin_coordinator.py` | 入口协调器 | ~1450 |
| `app/services/teammate_response_service.py` | 队友决策+响应 | ~1100 |
| `app/services/admin/teammate_agentic_tools.py` | 队友 3 工具 | ~300 |
| `app/services/npc_reactor.py` | NPC 自主反应 | ~280 |
| `app/world/immersive_tools.py` | 4a 沉浸式工具 (19 个, 大部分 stub) | ~360 |
| `app/world/role_registry.py` | 4a 角色工具注册 | ~70 |
| `app/services/llm_service.py` | LLM 统一调用 (`agentic_generate`) | ~700 |
| `app/routers/game_v2.py` | REST 路由 | ~1035 |
| `app/models/admin_protocol.py` | AgenticResult/CoordinatorResponse 等模型 | ~234 |

---

## 二、AgenticExecutor 设计

### 2.1 核心思路

将 V4AgenticToolRegistry 的三大职责拆分：
- **工具注册** → RoleRegistry（4a 已完成）
- **签名绑定** → bind_tool（4a 已完成）
- **执行录制 + SSE** → AgenticExecutor（4b 新建）

```
AgenticExecutor.run()
  ├─ RoleRegistry.get_tools(role, traits, ctx)  → 绑定好的工具列表
  ├─ _wrap_recording(tool)                       → 计时 + 错误处理 + SSE
  ├─ LLMService.agentic_generate(tools=wrapped)  → Gemini AFC loop
  └─ AgenticResult(narration, tool_calls, ...)   → 统一结果
```

### 2.2 AgenticContext 问题

4a 的 `bind_tool(tool_def, session, agent_id)` 只绑定两个参数。但 4b 工具实现需要更多服务：

| 工具 | 需要的服务 |
|------|-----------|
| `recall_experience` | RecallOrchestrator |
| `form_impression` | GraphStore |
| `generate_scene_image` | ImageGenerationService |
| `share_thought` / `notice_something` | SceneBus |
| `conclude_quest` 等 | SessionRuntime (已有) |

**方案：引入 AgenticContext 数据类**

```python
@dataclass
class AgenticContext:
    session: Any          # SessionRuntime
    agent_id: str
    role: str             # "gm" / "npc" / "teammate"
    scene_bus: Any        # SceneBus
    world_id: str = ""
    recall_orchestrator: Any = None
    graph_store: Any = None
    image_service: Any = None
```

bind_tool 从剔除 `{session, agent_id}` 改为剔除 `{ctx}`。19 个工具签名 `(session, agent_id, ...)` → `(ctx, ...)`。

### 2.3 AgenticExecutor 接口

```python
class AgenticExecutor:
    def __init__(self, llm_service: LLMService):
        self.llm = llm_service

    async def run(
        self,
        *,
        ctx: AgenticContext,
        system_prompt: str,
        user_prompt: str,
        traits: Optional[Set[str]] = None,
        extra_tools: Optional[List[Callable]] = None,  # 战斗工具等
        event_queue: Optional[asyncio.Queue] = None,
        model_override: Optional[str] = None,
        thinking_level: Optional[str] = None,
        max_tool_rounds: int = 5,
    ) -> AgenticResult:
```

### 2.4 _wrap_recording 模式

复用 V4AgenticToolRegistry._record() 的 SSE 事件格式（`agentic_tool_call`, `disposition_change`），但作为通用包装器：

```python
def _wrap_recording(self, tool_fn, tool_calls, event_queue):
    @functools.wraps(tool_fn)
    async def wrapper(**kwargs):
        started = time.perf_counter()
        try:
            result = await tool_fn(**kwargs)
            # 录制 + SSE
            ...
            return result
        except Exception as e:
            ...
            return {"success": False, "error": str(e)}
    # 保留签名（Gemini SDK 兼容）
    wrapper.__annotations__ = tool_fn.__annotations__
    wrapper.__signature__ = inspect.signature(tool_fn)
    return wrapper
```

### 2.5 [PASS] 机制

并行 Agent 响应时，GM/队友可选择不发言：
- 系统提示："如果没有有意义的内容要补充，只回复 `[PASS]`"
- AgenticExecutor 检测到 `[PASS]` → `AgenticResult(narration="")`

---

## 三、NPC 对话流设计

### 3.1 流程全景

```
前端: 玩家点击"与酒保交谈"
  │
  ├─ POST /interact/stream {npc_id: "bartender", message: "你好"}
  │
  ├─ A: 精简上下文
  │   ├─ SessionRuntime.restore()
  │   ├─ SceneBus.contact("bartender")
  │   └─ SceneBus.publish(player_message)
  │
  ├─ B: 并行 Agent 响应
  │   ├─ NPC (必答): AgenticExecutor.run(role="npc")
  │   ├─ GM (自主): AgenticExecutor.run(role="gm") → 可能 [PASS]
  │   └─ 队友 (自主): AgenticExecutor.run(role="teammate") → 可能 [PASS]
  │
  ├─ C: 后处理
  │   ├─ GM 快速生成 4 个对话选项 (Flash, 无 thinking)
  │   ├─ 发布所有结果到 SceneBus
  │   └─ SessionRuntime.persist()
  │
  └─ 返回: {npc_response, gm_narration?, teammate_comments?, options[4]}
```

### 3.2 端点设计

```
POST /game/{world_id}/sessions/{session_id}/interact/stream
Body: { "npc_id": "bartender_01", "message": "你好" }
SSE Events:
  - {"type": "phase", "phase": "npc_thinking"}
  - {"type": "npc_chunk", "npc_id": "...", "text": "..."} (streaming)
  - {"type": "gm_chunk", "text": "..."} (if GM speaks)
  - {"type": "options", "options": ["打听委托", "点酒", "问路", "告辞"]}
  - {"type": "complete", "data": InteractResponse}
```

### 3.3 NPC 系统提示构建

从 WorldGraph 节点读取 NPC 属性 → 构建系统提示：
```python
def _build_npc_prompt(self, npc_node, disposition) -> str:
    return (
        f"你是 {npc_node.name}，{npc_node.properties.get('occupation', '')}。\n"
        f"性格：{npc_node.properties.get('personality', '')}\n"
        f"背景：{npc_node.properties.get('background', '')}\n"
        f"对玩家的态度：{disposition_description}\n\n"
        f"你必须回应玩家的话。态度由好感度决定（好感低→冷淡短句，高→热情详细）。\n"
        f"你可以使用工具来表达情感变化和形成记忆。"
    )
```

### 3.4 选项生成

```python
async def _generate_dialogue_options(self, npc_response, context, last_message) -> List[str]:
    prompt = f"NPC 回复: {npc_response}\n上下文: {context_summary}\n生成 4 个简短对话选项（JSON 数组）"
    raw = await self.llm_service.generate_simple(prompt, thinking_level=None)
    return json.loads(raw)
```

---

## 四、NPCReactor 改造

### 4.1 当前（自主反应生成器）

```
collect_reactions()
  → _get_area_npcs()          # 获取在场 NPC
  → _calculate_relevance()    # 评分
  → _generate_reaction()      # LLM 生成反应文本
  → 返回 List[BusEntry]
```

### 4.2 改造后（相关 NPC 推荐器）

```
get_relevant_npcs()
  → _get_area_npcs()          # 保留
  → _calculate_relevance()    # 保留
  → 返回 List[Dict]           # {npc_id, name, relevance} 给前端展示
```

**删除**：`_generate_reaction()` + `_llm_reaction()`
**保留**：`_get_area_npcs()` + `_calculate_relevance()`
**新增**：`get_relevant_npcs()` 返回推荐列表

PipelineOrchestrator 中原 `collect_reactions()` 改为 `get_relevant_npcs()`，结果进 `metadata["nearby_npcs"]`。

---

## 五、Teammate 迁移路径

### 5.1 当前流程

```
TeammateResponseService.process_round()
  → decide_responses()               # 并行决策：是否响应
  → FOR EACH: _generate_single_response_core()
    → _run_agentic_generation_payload()
      → TeammateAgenticToolRegistry(3 tools)
      → FlashCPU.agentic_generate()
    → parse JSON response
    → _write_response_to_instance()   # ContextWindow 写回
```

### 5.2 迁移后

```
TeammateResponseService.process_round()
  → decide_responses()               # 不变
  → FOR EACH: _generate_single_response_core()
    → AgenticExecutor.run(role="teammate", ctx=...)   # 替换
    → parse response
    → _write_response_to_instance()   # 不变
```

**改动点**：
- `_run_agentic_generation_payload()` 内部改用 AgenticExecutor
- 删除 TeammateAgenticToolRegistry（3 工具已在 immersive_tools.py）
- InstanceManager + ContextWindow + graphization 逻辑不变

---

## 六、GM 迁移路径

### 6.1 当前流程

```
PipelineOrchestrator.process() B-stage:
  → FlashCPU.agentic_process_v4()
    → V4AgenticToolRegistry(23 tools)
    → V4AgenticToolRegistry._wrap_tool_for_afc()  # 签名包装
    → LLMService.agentic_generate()
    → V4AgenticToolRegistry._record()              # 录制 + SSE
  → AgenticResult
```

### 6.2 迁移后

```
PipelineOrchestrator.process() B-stage:
  → AgenticExecutor.run(role="gm", ctx=..., extra_tools=combat_tools_if_needed)
    → RoleRegistry.get_tools("gm")                # 10 沉浸式工具
    → AgenticExecutor._wrap_recording()            # 录制 + SSE
    → LLMService.agentic_generate()
  → AgenticResult
```

### 6.3 Engine Exclusion 适配

现有 `_ENGINE_TOOL_EXCLUSIONS` 逻辑（v4_agentic_tools.py:26-31）：
```python
_ENGINE_TOOL_EXCLUSIONS = {
    "move_area": {"update_time"},
    "rest": {"update_time"},
    "talk": {"npc_dialogue"},
    "use_item": {"add_item", "remove_item", "heal_player"},
}
```

迁移后大部分被排除的工具已删除（update_time, npc_dialogue, add_item 等），engine exclusion 逻辑可大幅简化或暂时保留空壳。

---

## 七、执行步骤

| Step | 任务 | 依赖 | 预估改动 |
|------|------|------|---------|
| 1 | AgenticContext + bind_tool 重构 | 无 | ~50 行改动 |
| 2 | AgenticExecutor 类 | Step 1 | +120 行新文件 |
| 3 | 沉浸式工具实现 (stub→真实) | Step 1 | ~100 行改动 |
| 4 | NPC /interact 端点 + Pipeline | Step 2+3 | +200 行 |
| 5 | NPCReactor 简化 | Step 4 | ~-100 行 |
| 6 | Teammate → AgenticExecutor | Step 2+3 | ~-150 行 |
| 7 | GM → AgenticExecutor + 工具缩减 | Step 2+3 | ~-300 行 |
| 8 | 清理 (recall_v4 等) | Step 7 | ~-100 行 |

### 执行批次

```
批次 A: 基础设施 (Steps 1+2+3)
  AgenticContext 重构 + AgenticExecutor 新建 + 工具 stub→真实
  改的是同一组文件 (immersive_tools / role_registry / 新建 executor)
  ~270 行改动，完成后跑 test_phase4a + test_phase4b 验证
      ↓
批次 B: NPC 对话流 (Steps 4+5)
  /interact 端点 + NPCReactor 简化
  新端点建好后顺手删旧的 LLM 反应代码
  ~300 行改动，完成后端到端验证 /interact 流程
      ↓
批次 C: 迁移清理 (Steps 6+7+8)
  队友/GM 切到 AgenticExecutor + 删旧工具注册 + recall 清理
  改动大但主要是删代码 (~550 行，净减 ~400 行)
  完成后全量回归测试
```

---

## 八、4a 待处理发现（纳入 4b）

| # | 发现 | 位置 | 处置时机 |
|---|------|------|---------|
| 1 | `recall_v4()` 被 `recall_for_role()` 取代 | `recall_orchestrator.py:225-323` | Step 8 |
| 2 | recall 三方法共享重复代码 | `recall_orchestrator.py` | Step 8 |
| 3 | `_wrap_tool_for_afc()` 与 `bind_tool()` 重叠 | `v4_agentic_tools.py:86-120` | Step 7 |
| 4 | `TeammateAgenticToolRegistry` 可删除 | `teammate_agentic_tools.py` | Step 6 |

---

## 变更日志

| 日期 | 操作 |
|------|------|
| 2026-02-21 | 创建：基于 4a 完成状态 + 代码探索，整理 4b 执行准备文档 |
| 2026-02-21 | 更新：添加执行批次划分（A 基础设施 / B NPC 对话流 / C 迁移清理） |
