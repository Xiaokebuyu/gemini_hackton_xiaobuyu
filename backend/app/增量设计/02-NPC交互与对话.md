# NPC 交互与对话

## 模块概要

NPC/队友/GM 三条 agent 路径，通过 LLM 生成对话和行为，工具调用产生游戏机制效果。

### 核心模块

| 模块 | 位置 | 职责 |
|------|------|------|
| NpcInteractionCoordinator | `orchestration/npc_interaction.py` | NPC 对话 6 步管线：构建上下文 → 注入 pending_topic → LLM 对话 → 工具执行 → 清除 topic → SSE 发射 |
| PrivateChatCoordinator | `orchestration/private_chat.py` | 私聊 4 步管线：visibility="private" + audience 控制 |
| AgentOrchestrationService | `app/agent_orchestration.py` | 应用层 NPC/GM/Teammate agent 编排，含 clue 调查/解决的 GM+队友反应链 |
| AgenticExecutor | `narrative/executor.py` | 统一 agent 执行器：单轮/多轮 agentic loop，工具调用 → ToolResult → 循环 |
| AgentContextBuilder | `narrative/context_builder.py` | 7 层上下文组装（L0 世界设定 → L7 对话历史），含 area_situation/blackboard 注入 |

### NPC/队友 Agent 工具

| 模块 | 位置 | 工具 |
|------|------|------|
| character_tools | `narrative/character_tools.py` | SuggestOptionsTool, UpdateFeelingTool, AcceptQuestTool, AssignQuestTool 等 |
| blackboard_tools | `narrative/blackboard_tools.py` | NPC 自我更新 blackboard + 跨 NPC 消息 |
| trade_tool | `narrative/trade_tool.py` | SellToPlayerTool（NPC 交易） |
| service_tool | `narrative/service_tool.py` | ExecuteServiceTool（NPC 服务执行） |
| exploration_tools | `narrative/exploration_tools.py` | 探索相关 agent 工具 |
| help_tool | `narrative/help_tool.py` | OfferHelpTool（NPC/队友主动援助） |
| gm_tools | `narrative/gm_tools.py` | GM 只读叙述工具（不构造 Command） |

### 辅助模块

| 模块 | 位置 | 职责 |
|------|------|------|
| RoleToolRegistry | `narrative/registry.py` | 按角色过滤可用工具集 |
| RoleStateProxy | `narrative/role_proxy.py` | 按角色限制 StateContainer 访问权限 |
| InstanceManager | `narrative/instance_manager.py` | 每 NPC 的 LRU 实例池 + session 隔离 |
| CompanionRuntime | `narrative/companion_runtime.py` | 活跃队友实例池和生命周期 |
| ContextWindow | `narrative/context_window.py` | 每 actor 200K token 工作记忆（滑动窗口 + graphize 溢出） |
| DirectiveTriggerHook | `orchestration/hooks/directive_trigger.py` | 将 planner directive 转为"NPC 有话对你说"前端提示 |
| NpcAutonomyHook | `orchestration/hooks/npc_autonomy.py` | NPC 空跑 tick：area_events → blackboard.observations，directive → goals |

---

## 当前 NPC 数据现状（2026-03-16 审计）

### 角色数量与分布

16 个 NPC，分布在 frontier_town（14）+ cow_girl_farm（1）+ 流动商人（1）。

女性可攻略角色（7）：guild_girl, cow_girl, priestess, high_elf_archer, tavern_keeper, tavern_waitress, temple_matron

### 角色数据字段完成度

| 字段 | 完成度 | 说明 |
|------|--------|------|
| personality | 16/16 ✅ | 50-80 字性格描述 |
| dialogue_style | 16/16 ✅ | 对话风格描述 |
| speech_pattern | 16/16 ✅ | 口头禅/说话习惯 |
| backstory | 15/16 ✅ | 背景故事（blacksmith 缺） |
| secrets | 16/16 ✅ | 每人 1-2 条秘密（trust 门槛解锁） |
| schedule | 16/16 ✅ | 日程表（dawn/day/dusk/night） |
| base_disposition | 16/16 ✅ | 初始好感数值 |
| appearance | 部分有 | 外貌描述（非必须但有加分） |
| stats | 16/16 ✅ | D&D 六维属性 |

### NPC Prompt 注入内容（context_builder.py L1982-2174）

NPC system prompt 当前注入的内容：

```
You are {name}, an NPC in a dark-fantasy CRPG world.

## Your character
{personality}
{dialogue_hook}        — 对话钩子
{tags}                 — 角色标签
{dialogue_style}       — 对话风格
{backstory}            — 背景故事
{speech_pattern}       — 说话习惯
{identity}             — 职业/阵营

## 你所在区域的当前态势
{area_situation}       — 区域汇总
{recent_area_events}   — 近期事件

## 你当前的想法（blackboard）
- 思绪：{thoughts}
- 目标：{goals}
- 近期观察：{observations}
- 情绪：{mood}
- 对冒险者的看法：{attitude_towards_player}

## 与冒险者的关系
阶段：{stage} | 好感：{approval} | 信任：{trust} | 恐惧：{fear} | 浪漫：{romance}

{private_block}        — 私聊时"你可以更真实"
{secrets_block}        — trust 过滤后的秘密
{grounding_block}      — 不编造的约束
{role_block}           — 角色特定数据（商店库存等）
{tool_rules}           — 工具使用规则
```

### 当前问题

| 问题 | 详情 |
|------|------|
| **缺 values/likes/dislikes** | NPC 无法判断玩家行为是否符合自己价值观，好感变化无据可依 |
| **缺 romance_triggers/turn_offs** | 恋爱发展随机，没有"做什么会加分/减分"的标准 |
| **缺 gift_preferences** | 送礼效果无差异，所有礼物等价 |
| **缺 NPC 间关系数据** | personality 中零星提到（guild_girl→哥布林杀手），但没有结构化的 NPC 关系网 |
| **delta 约束过宽** | prompt 写 `±5 to ±15`，实际应为 `[-3,+3]`（设计目标），LLM 可能一次对话+15 直接从 stranger 跳到 friend |
| **缺关系阶段对话指引** | prompt 有 stage 数值但没有"stranger 时只聊公事、friend 时可聊私事、intimate 时更亲密"的分层指引 |
| **多 NPC 同场景对话** | 当前系统是一对一对话，不支持三人聊天（玩家 + NPC A + NPC B 互相接话） |
| **NPC 主动搭话** | 当前靠 DirectiveTriggerHook 弹提示 → 玩家点击，不够自然。日常场景应该 NPC 主动开口 |
| **私聊系统多余** | PrivateChatCoordinator + PrivateChatTriggerHook 是独立的"找僻静处私聊"系统，在日常向游戏中不需要 |

### 设计决定：废弃独立私聊系统

**当前**：PrivateChatCoordinator（4 步管线）+ PrivateChatTriggerHook（romance≥60 / trust≥50 触发）+ 僻静处场景模板 + GM/队友被排除

**问题**：
- 只在休息时触发，太被动
- romance≥60 门槛太高，日常初期触发不了
- "找僻静处""不可被第三方观察"在日常向没意义
- 队友看到你和 NPC 聊天反而应该有反应（恋爱竞争感）

**改为**：所有对话统一走 NpcInteractionCoordinator，对话深度由 NPC prompt 中的 stage + blackboard 自然控制：
- stranger → 聊公事、客套
- friend → 主动聊私事、分享心情
- close_friend → 说出更深层的话，但注意场合（公开含蓄，私下真实）
- intimate → 最坦诚的对话

NPC prompt 中注入关系阶段对话指引（替代僻静处机制），队友在场时可以看到并反应。

### 待补充的角色数据字段（需手写）

```json
{
  "values": ["责任感", "专业", "体贴"],
  "likes": ["认真完成委托", "关心他人", "守时"],
  "dislikes": ["鲁莽", "说谎", "不守承诺"],
  "romance_triggers": ["下班后的关心", "记住她说过的话", "帮她分担工作"],
  "turn_offs": ["在公会大吵大闹", "对其他冒险者粗暴"],
  "gift_preferences": {
    "loves": ["flowers", "stationery"],
    "likes": ["food", "practical"],
    "neutral": ["materials", "tools"],
    "dislikes": ["weapons", "monster_parts"]
  },
  "npc_relationships": {
    "goblin_slayer": "深深关切，暗恋但不说破",
    "tavern_keeper": "经常去酒馆交换情报的朋友"
  }
}
```
