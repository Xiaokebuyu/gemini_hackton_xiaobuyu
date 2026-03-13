# P28 逐 Wave 完成度核查与未完成事项

创建时间：2026-03-13
更新时间：2026-03-13
状态：Wave 5 hostile 入口已完成，仅剩 Gemini capability live 复验
范围：`P28-动态能力系统与综合体验修复.md`

---

## 0. 核查依据

本文件以当前工作区代码、自动化测试、前端构建与本地 live API 结果为准，不以原计划文档中的“已完成”标记为准。

### 已执行核查

1. P23-P28 交叉回归：
   - 覆盖 Planner、奖励链、等级成长、房间系统、Capability、functional、quest panel 等定向用例
   - 结果：`535 passed`
2. 第一批闭环验证：
   - `npm run build`
   - `./venv/bin/python -m pytest tests/test_api_shell.py -q`
   - `./venv/bin/python -m pytest tests/test_p27_room_navigation.py -q`
   - `./venv/bin/python -m pytest tests/test_phase5_quest_tracking.py -q`
   - 结果：`build passed`、`40 passed`、`26 passed`、`20 passed`
3. 第二批后端回归：
   - `./venv/bin/python -m pytest tests/test_content_registries.py tests/test_phase3a_milestone_completion.py tests/test_world_state_handler.py tests/test_p28_task_monitor.py tests/test_growth_handler.py -q`
   - 结果：`197 passed`
4. 第二批 API / 恢复链回归：
   - `./venv/bin/python -m pytest tests/test_api_shell.py -q`
   - 结果：`41 passed`
5. 升级链 SSE 回归：
   - `./venv/bin/python -m pytest tests/test_p28_growth_loop.py -q`
   - 结果：`17 passed`
6. 第三批 functional / 房间专项回归：
   - `npm run build`
   - `./venv/bin/python -m pytest tests/test_presence_source.py tests/test_p27_sub_location_activation.py tests/test_p27_room_navigation.py -q`
   - `./venv/bin/python -m pytest tests/test_api_shell.py -q`
   - 结果：`build passed`、`61 passed`、`42 passed`
7. 本地 live API 冒烟：
   - 已实测创建会话、建角、`resume`、`opening/stream`、交易、任务领取、任务完成、公告板、`skill_check`、`move_area`、`enter_room`、`leave_sub_location`
   - 当前环境仍未配置 `GOOGLE_API_KEY` / `GEMINI_API_KEY`，因此无法完成真实 LLM 在环验证
8. 第四批 capability / encounter bridge 回归：
   - `./venv/bin/python -m pytest tests/test_content_registries.py tests/test_navigation_handler.py tests/test_encounter_hook.py tests/test_npc_interaction.py tests/test_p28_wave4_capability.py tests/test_p28_wave4_gm_functional.py tests/test_p28_wave4_capability_harness.py tests/test_api_shell.py -q`
   - 结果：`281 passed`
9. 第四批 localhost live：
   - `U12` 真实世界数据复验：将时间推进到 `dusk` 后，`move_area -> ancient_ruins` 已实测出现 `encounter_spotted`
   - 同一条 hostile 路径继续执行 `encounter/action -> enter`，若先通过潜行则 `surprise_attack` 后已实测进入 `combat_start`
   - `U7` capability 注入 CLI 冒烟：`scripts/p28_seed_capability.py` 已可向真实 session 写入 capability 并回读 snapshot

---

## 1. 逐 Wave 结论

| Wave | 当前判断 | 结论 |
|---|---|---|
| Wave 0 | 完成 | 架构越界修复与前端 build blocker 已清除 |
| Wave 1 | 完成 | Planner 基础修复、语言约束、NPC 能力边界 prompt 已接通 |
| Wave 2 | 完成 | 成长主链、里程碑奖励、升级提示与 ASI 持续入口已闭环 |
| Wave 3 | 完成 | Task Monitor 已回到 `execute_command()` 主链 |
| Wave 4 | 部分完成 | capability harness、注入 CLI 与回归已完成；剩余真实 Gemini 在环复验 |
| Wave 5 | 完成 | 房间协议、状态一致性、房间专项回归与真实 hostile 入口 live 验收已收口 |

---

## 2. 总表

| ID | Wave | 问题 | 当前状态 |
|---|---|---|---|
| U1 | 0 / 5 | 前端构建失败，阻断 Wave 5 验收 | 已完成 |
| U2 | 2 | 里程碑奖励未实际发放 | 已完成 |
| U3 | 2 / 5 | `player_level_up` 前后端字段不一致 | 已完成 |
| U4 | 2 / 5 | ASI 只有提示，无持续入口/面板入口 | 已完成 |
| U5 | 3 | `TaskMonitorHook` auto 路径绕过 `execute_command()` 主链 | 已完成 |
| U6 | 4 / 5 | functional 选项点击未触发对应 UI 动作 | 已完成 |
| U7 | 4 | 动态能力“NPC 真实按能力行事”尚缺运行时验证 | 代码完成，待真实 Gemini 复验 |
| U8 | 5 / P27遗留 | 房间导航前后端协议未闭环 | 已完成 |
| U9 | 5 / P27遗留 | 切换 area / sub_location 时 `current_room` 未清空 | 已完成 |
| U10 | 5 / P27遗留 | 房间系统关键回归测试仍有缺口 | 已完成 |
| U11 | 5 / P25遗留 | `/quests` 顶层仍返回 `milestone_states` 占位字段 | 已完成 |
| U12 | 5 / 战斗验收 | 真实 hostile 内容路径未稳定触发遭遇 / 战斗 | 已完成 |

---

## 3. 已完成项

### 第一批：contract / build / 房间闭环

#### U1. 前端 build blocker

- 已将过时的 `npc_interaction` 判断统一收敛到现有 `dialogue` 模式。
- 2026-03-13 复验：`npm run build` 通过。

#### U3. 升级 SSE contract

- 前端已改为以 `to_level` / `features` 为主，保留旧字段兜底。
- `player_level_up` 现在会同步刷新玩家等级与 `asiAvailable` 状态。
- 2026-03-13 复验：`tests/test_p28_growth_loop.py` 通过 `17 passed`。

#### U8. 房间导航前后端协议

- 前端进入/离开房间已改走 `action/stream -> enter_room / leave_room`。
- 不再把房间动作错误打到 `/navigate`。
- 2026-03-13 复验：`tests/test_api_shell.py`、`tests/test_p27_room_navigation.py` 通过。

#### U9. `current_room` 残留

- `move_area` 与 `enter_sub_location` 已统一清空 `player.current_room`。
- 2026-03-13 复验：`tests/test_p27_room_navigation.py` 通过 `26 passed`。

#### U11. quest panel contract 收口

- `/quests` 顶层已移除 `milestone_states`。
- 前端 QuestPanel 已只消费 `dynamic_quests` / `chapter_completion`。
- 2026-03-13 复验：`tests/test_phase5_quest_tracking.py` 通过 `20 passed`。

### 第二批：成长与奖励主链

#### U2. 里程碑奖励闭环

- `QuestRegistry.MilestoneTemplate` 已正式加载 `rewards`。
- `advance_quest` 在 milestone `COMPLETED` 时会读取模板奖励并发放 gold / xp / items。
- `MilestoneCompletionHook` 仍只负责判定并通过 `advance_quest` 完成，不再在 Hook 内直改玩家状态。
- 2026-03-13 复验：
  - `tests/test_content_registries.py`
  - `tests/test_phase3a_milestone_completion.py`
  - `tests/test_world_state_handler.py`
  - 已包含在 `197 passed` 中

#### U4. ASI 持久入口与剩余点

- `PlayerSlice` 新增 `asi_points_remaining`，并派生 `asi_available`。
- `level_up` 会在跨过 `{4,8,12,16,19}` 时发放 `2` 点 ASI。
- `apply_asi` 已改为基于剩余点校验与消费，不再要求“当前等级必须正好处于 ASI 等级”。
- `/character` 与 `/resume` 已暴露 `asi_available` / `asi_points_remaining`。
- 前端 `CharacterPanel` 已新增 ASI 区块、`+1/+2` 分配按钮和分配后自动刷新。
- `MenuOverlay` 已给“角色面板”增加持续 `ASI` 提示标识。
- 2026-03-13 复验：
  - `tests/test_growth_handler.py`
  - `tests/test_p28_growth_loop.py`
  - `tests/test_api_shell.py`
  - 前端 `npm run build`

#### U5. TaskMonitor 主路径回正

- `TaskMonitorHook` 的 `on_complete="auto"` 已改为调用 `execute_command(Command(type="advance_quest", ... claim_rewards=True))`。
- 动态任务自动完成与奖励发放现在与正式 handler 共用同一条语义链。
- 2026-03-13 复验：`tests/test_p28_task_monitor.py` 已覆盖命令路径与奖励行为。

### 第三批：functional / 房间专项回归

#### U6. functional 选项动作落地

- 前端 `useGameStream.ts` 已为 `trade_browse / board_browse / quest_accept / navigate` 补齐真实分发，不再只显示 icon。
- functional 继续优先复用现有结构化主链：
  - `trade_browse` → `interact/stream(intent='browse')`
  - `board_browse` → `action/stream(action_type='browse_board')`
  - `quest_accept` → `interact/stream(intent='accept_quest')`
  - `navigate` → `/navigate`
- 新增 dev-only `window.__GAME_DEBUG__.runFunctionalOption(...)`，便于本地调试 functional-only 选项。
- 未接通的 `inspect_item / rest` 与未知类型现在会明确给出通知，不再静默无响应。
- 2026-03-13 复验：前端 `npm run build` 通过。

#### U10. 房间系统关键回归测试

- `build_location_overview()` 已补直接断言，覆盖：
  - `current_room`
  - `rooms[].discoverable / discovered`
  - 房间级 NPC 过滤
- `Runtime._resolve_starting_location_id()` 已补直接单测，并同步修正“无 sub_locations 时仍返回 default_sub_location”的偏差；现在会按 contract 返回 `None`。
- `tests/test_api_shell.py` 已新增 `/scene` 场景回归，覆盖“进房间后切 sub_location / area 都会清空 `current_room`”。
- 2026-03-13 复验：
  - `tests/test_presence_source.py`
  - `tests/test_p27_sub_location_activation.py`
  - `tests/test_p27_room_navigation.py`
  - `tests/test_api_shell.py`
  - 结果：`61 passed`、`42 passed`

### 第四批：hostile 入口桥接与 capability 验收工具

#### U12. 真实 hostile 内容路径战斗入口

- `EncounterHook` 已补 area-entry bridge：玩家 `move_area` 后即使被自动放进 `default_sub_location`，仍会保留一次 area encounter probe 窗口。
- `MapRegistry.resolve_auto_sub_location()` 已成为统一 auto-placement helper，`NavigationHandler` 也已复用。
- `tests/test_api_shell.py` 已新增真实 API 回归，覆盖“`move_area -> ancient_ruins` 时同轮出现 `encounter_spotted` 且 `location_id == forest_approach`”。
- 2026-03-13 真实 localhost 复验：
  - 在真实 `goblin_slayer` 世界数据中，将时间推进到 `dusk`
  - `move_area -> ancient_ruins` 已实测出现 `encounter_spotted`
  - `encounter/action -> enter` 后若先得到 `stealth_result.passed=true`，继续 `surprise_attack` 已实测进入 `combat_start`

#### U7. capability harness / 注入 CLI

- 已新增 `tests/test_p28_wave4_capability_harness.py`，用 `RecordingLlmProvider` 直接覆盖：
  - public `trade_browse`
  - public `board_browse`
  - private chat capability prompt
  - 未分配 capability 的负例 prompt
- 已补 `npc_interaction._finalize_dialogue_options()` 对 `functional` 字段的保留，确保 GM/NPC 路径生成的 functional 选项不会在出口被剥掉。
- 已新增 `scripts/p28_seed_capability.py`，可对真实 session 注入 capability 并回读快照。
- 已新增 [P28-live验收清单与证据模板.md](/home/xiaokebuyu/workplace/gemini-hackton/backend/app/增量更新/P28-live验收清单与证据模板.md) 作为真实 Gemini 验收 runbook。
- 2026-03-13 复验：
  - `tests/test_p28_wave4_capability.py`
  - `tests/test_p28_wave4_gm_functional.py`
  - `tests/test_p28_wave4_capability_harness.py`
  - 已包含在 `281 passed` 中
  - `./venv/bin/python scripts/p28_seed_capability.py --help` 通过
  - 对临时 session 的 capability 注入/回读冒烟通过

---

## 4. 剩余项

### U7. 动态能力真实运行时效果仍缺 Gemini 在环复验

**现状**

- capability 的 prompt 注入、functional option 保留、deterministic harness 和真实 session 注入 CLI 都已完成。
- 当前唯一缺口是：本环境仍未配置 `GOOGLE_API_KEY` / `GEMINI_API_KEY`，因此还不能做真实 Gemini 回合的最终验收。

**通过标准**

1. 按 [P28-live验收清单与证据模板.md](/home/xiaokebuyu/workplace/gemini-hackton/backend/app/增量更新/P28-live验收清单与证据模板.md) 为 `guild_quartermaster` / `guild_girl` / 私聊信息 NPC 注入 capability
2. 真实 `interact/stream` / `private_chat/stream` 输出符合 capability 指令
3. 无能力 NPC 不产生错误的 `functional` 选项

**结论**

- 这已经不是代码缺口，而是外部模型 live 复验缺口。

---

## 5. 当前建议优先级

### 第一优先级

1. U7 动态能力真实 Gemini 在环验证

### 剩余体量估算

估算口径：以下体量按“熟悉当前仓库的单人串行实施”估算，不含产品往返确认、不含美术/UI 调整、不含真实 Gemini API 在环等待时间。

1. 仅计算 `U7` live 复验与证据补齐：约 `0.5-2 人天`
2. 将模型波动与重复采样计入：约 `1-4 人天`

---

## 6. 结论

P28 已从“多条主链未闭环”收敛到“代码与真实 hostile 入口都已收口，只剩 Gemini live 复验”。

当前已经确认：

1. 前端可以稳定构建。
2. 里程碑奖励、动态任务自动完成奖励、升级链、ASI 持久入口都已接通。
3. 房间协议、`current_room` 状态一致性与 quest panel contract 都已收口。
4. 真实 `ancient_ruins` hostile 路径已实测走通到 `combat_start`。
5. capability 的 deterministic harness、functional 出口保留、真实 session 注入 CLI 与 runbook 都已完成。

剩余缺口只剩一项：

1. 配置真实 Gemini key 后完成最终在环复验

因此，P28 当前已经不再有明确的代码收口问题，只剩外部模型验证问题。
