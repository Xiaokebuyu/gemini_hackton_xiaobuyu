# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

AI 驱动的 CRPG 游戏后端（类博德之门 3）。两套代码共存：
- **`app/game_core/`** — 新隔离游戏内核（干净架构，活跃开发中）
- **`legacy/app（legacy）/`** — 旧版 FastAPI 运行时（生产环境，逐步替换中）

> **⚠️ 隔离红线：所有开发工作仅限 `app/game_core/` 目录。严禁扫描、引用或修改 `legacy/` 下的任何代码。新内核不得 import legacy 模块，两套代码必须完全隔离。**

game_core 当前为纯 Python 标准库（dataclasses、abc、typing、uuid、copy），零外部依赖。
外部集成（Firestore、Gemini、NetworkX、MCP）全部通过 `adapters/` 端口协议隔离，由上层注入。
世界数据来源：SillyTavern-v2 角色卡（哥布林杀手，415 条目，310K tokens）。

## 常用命令

```bash
# 安装依赖
pip install -r requirements.txt

# 运行全部测试（需在 backend/ 目录下）
cd /home/xiaokebuyu/workplace/gemini-hackton/backend
PYTHONPATH=. pytest -v

# 运行单个测试文件
PYTHONPATH=. pytest tests/test_game_core_scaffold.py -v

# 按名称运行单个测试
PYTHONPATH=. pytest tests/test_game_core_scaffold.py -k "test_name" -v
```

测试配置在 `pyproject.toml`：`asyncio_mode = "auto"`，testpaths = `["tests"]`。未配置 linter。

## 架构（game_core）

五层六边形架构，无全局状态——一切通过注入传递。

### Tick 生命周期

```
输入 → TickCoordinator.process()
  → PipelineOrchestrator（3 阶段：ContextAssembler → ActionDispatcher → RulesEngine）
  → 将 StateDelta 原子应用到 StateContainer
  → 若累积时间 >= 1.0：按优先级执行 SettlementHooks
  → 返回 PipelineResult（含 SSE 事件）
```

### 各层职责

**状态层** (`state/`)：通过 `StateSlice` 模式管理会话状态。每个 slice 拥有一个语义域（time、player、area、quests、scene、relations、flags、events、party、narrative_plan）。`StateContainer` 持有所有 slice，支持脏标记选择性持久化，原子应用 `StateDelta`。

**内容层** (`content/`)：只读 `ContentRegistry` 实例加载到 `WorldInstance`。注册表：characters、items、skills、classes、monsters、maps、factions、lore、quests、tags。按依赖顺序加载（tags 优先）。支持 `query_by_tags()` 过滤。

**规则层** (`rules/`)：纯函数 `CommandHandler` 实现——验证 + 计算，零副作用。`RulesEngine` 按类型路由 `Command` 到对应 handler。结果为 `ExecuteResult`，包含 `StateDelta` + 叙事提示 + 骰子结果。

**编排层** (`orchestration/`)：`TickCoordinator` 拥有会话 tick 生命周期。`PipelineOrchestrator` 执行 3 阶段流水线。结算钩子（`hooks/` 中 10 个）在时间累积达阈值后执行，按优先级排序，产出 SSE 事件。

**叙事层** (`narrative/`)：AI 集成点。`AgentTool` 契约按角色鉴权（gm/npc/teammate）。`AgenticExecutor` 编排 Gemini 工具调用。`ContextAssembler` 构建 7 层上下文载荷（meta、time、player、area、world、scene、narrative）。

**适配器** (`adapters/`)：端口协议——`InputPort`、`OutputPort`、`PersistencePort`、`PresentationPort`。提供 Null 实现用于测试。

### 关键约定

- 全局使用 `@dataclass(slots=True)` 定义数据结构
- 状态变更只能通过 `StateDelta`——handler 永远不直接修改状态
- `StateChange` 使用点号路径（如 `"area_id.danger_level"`）
- Slice 使用惰性初始化（`setdefault` 处理稀疏状态）
- `snapshot()` 返回防御性拷贝；`serialize()`/`restore()` 用于持久化
- 所有编排方法均为 async
- game_core 不得 import legacy 模块，外部能力一律通过 `adapters/` 端口注入

### 编码风格：防御性编程

**所有代码必须采用防御性编程风格，不信任任何上游数据。**

- **参数入口**：对所有 public 方法参数做类型检查和强制转换（`str()`、`int()`、`isinstance`）
- **内部读取**：从 dict 取值用 `.get()` + 类型守卫，不假设内部数据一定干净
- **返回值**：逐字段规范化后返回，不暴露原始引用（防御性拷贝）
- **每层独立防御**：不依赖调用方或上游已做过验证，每个函数自己兜底
- **脏数据不传播**：遇到类型不匹配时回退到安全默认值（空 dict、0、None），而非直接透传
