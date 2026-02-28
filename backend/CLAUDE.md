# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## 项目概览

AI 驱动的 CRPG 游戏后端（类博德之门 3）。

- **活跃代码**：`app/game_core/` — 五层六边形纯 Python 内核（零外部依赖）
- **应用层**：`app/main.py` + `app/interaction_service.py` + `app/interaction_views.py`
- **运营入口**：`CODEX_INIT.md` — 顶层操作基线与当前主线方向
- **施工记录**：`app/施工记录（持续更新）/*.md` — 各子系统决策记录（D-Rxx）
- **架构细节**：`app/CLAUDE.md` — game_core 各层职责与编码约定

> **⚠️ 隔离红线：game_core 不得 import legacy，外部能力通过 `adapters/` 端口注入。**

## 常用命令

```bash
# 运行全部测试（backend/ 目录下）
PYTHONPATH=. pytest --ignore=tests/test_api_shell.py --ignore=tests/test_interaction_service.py -v

# 运行单个测试文件
PYTHONPATH=. pytest tests/test_game_core_scaffold.py -v

# 按名称运行单个测试
PYTHONPATH=. pytest tests/test_game_core_scaffold.py -k "test_name" -v
```

测试配置在 `pyproject.toml`：`asyncio_mode = "auto"`，testpaths = `["tests"]`。测试基线：290 passed（2026-02-28）。

## 操作纪律

### 核心原则

1. **先稳定扩展点，再深化语义** — 不要过早"完成"一个子系统
2. **严格划定边界** — 每次改动明确属于哪一层，不跨层操作
3. **改代码必须更新施工记录** — 决策记录是活文档，深化 MVP 时更新对应 D-Rxx
4. **控制性占位优于假实现** — `noop/blocked/deferred` 可以，`"not implemented"` 不行
5. **关闭生命周期循环优先于拓宽功能面** — 端到端薄循环 > 多个断头半功能

### 规模评估先行

发现任务可能一次无法完成时，**必须事先通知用户**。先评估规模、拆分阶段、确认范围，再动手实施。不要闷头开干后发现做不完。

### 每次改动后检查清单

- [ ] 更新对应施工记录（`app/施工记录（持续更新）/*.md`）
- [ ] 如果 handler 变化，检查 `DEFAULT_ACTION_COMMAND_TYPES` 是否需要同步
- [ ] 补针对性测试（比例适当，不做无关扩展）
- [ ] 确认测试基线全部通过

### 边界判断规则

- **start_combat 不注册到 dispatcher** — 它是 engine-only 命令
- **交互验证在 InteractionService** — 不在 FastAPIInputPort（适配器只做归一化）
- **所有状态变更走 TickCoordinator** — 不直接调 rules_engine + state.apply
- **注入式 provider/evaluator** — 高不确定性组件用 injectable 接口 + 安全默认实现

## 文件导航

| 路径 | 用途 |
|------|------|
| `app/game_core/state/` | 10 个 StateSlice（time, player, area, quests, scene, relations, flags, events, party, narrative_plan） |
| `app/game_core/content/` | 10 个 ContentRegistry（characters, items, skills, classes, monsters, maps, factions, lore, quests, tags） |
| `app/game_core/rules/handlers/` | 13 个 CommandHandler（combat, skill_check, navigation, inventory, economy, growth, rest, crime, encounter, container, world_state, status_effect, spell） |
| `app/game_core/orchestration/hooks/` | 10 个 SettlementHook |
| `app/game_core/orchestration/defaults.py` | DEFAULT_ACTION_COMMAND_TYPES 注册表 |
| `app/game_core/narrative/` | AgenticExecutor + RoleToolRegistry（LLM 集成待做） |
| `app/game_core/adapters/` | 4 端口 Protocol + Null 实现 |
| `app/interaction_service.py` | 应用层交互编排（presence, prechecks, execution） |
| `app/interaction_views.py` | snapshot 构建器 |

## 编码风格

- 全局 `@dataclass(slots=True)`
- 状态变更只通过 StateDelta
- snapshot() 返回防御性拷贝
- 所有编排方法 async
- 但不要用力过度——边界内保持简洁

### 防御性编程（重要）

防御性编程不是机械地套规则（加 isinstance、加边界检查），而是一种**写代码前的工作习惯**：

1. **写之前先充分调查** — 扩展一个模块时，先读透同层已有实现的模式、深度、边界处理方式，确保新代码与已有代码风格和防御深度一致
2. **理解上下游契约** — 比如 `restore()` 做了哪些类型强转，`validate()` 就应该覆盖对应的不变量；`apply_state_change()` 接受什么路径，handler 就应该只生成合法路径
3. **不盲目套模板** — 不是每个字段都需要深层校验，schemaless 设计（如 FlagSlice 的 `dict[str, Any]`）就不该强加值类型约束
4. **每层独立防御，不信任上游数据** — 但防御的粒度要匹配该层的实际职责

### 从目的推导，不从结构复制（重要）

做任何决策前先问"我要解决什么问题，最小充分条件是什么"，从答案推导行动。不要看到已有的结构就搬/套/复制。
