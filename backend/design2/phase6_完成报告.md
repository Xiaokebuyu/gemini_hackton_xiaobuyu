# Phase 6 完成报告：完整游戏循环 + 战斗整合

## 概述

Phase 6 实现了完整的游戏循环编排系统，整合了之前所有阶段的功能：
- 图谱基础设施（Phase 1）
- 世界观图谱（Phase 2）
- Flash 记忆服务（Phase 3）
- Pro + Flash 联动（Phase 4）
- GM Flash + 事件总线（Phase 5）
- **完整游戏循环 + 战斗整合**（Phase 6）

## 新增组件

### 1. GameMasterService (`app/services/game_master_service.py`)

核心编排服务，负责：
- 管理游戏会话的完整生命周期
- 处理玩家输入并决定响应类型（GM叙述/NPC对话/战斗）
- 协调GM Pro（叙述者）和NPC Pro（角色扮演）
- 整合战斗系统并将结果转换为事件
- 管理场景切换和记忆预加载

**游戏状态机**：
```
IDLE → SCENE → DIALOGUE/COMBAT → SCENE → ...
```

### 2. Game Master Router (`app/routers/game_master.py`)

提供完整的游戏循环 API：
- `POST /gm/{world_id}/sessions` - 创建游戏会话
- `GET /gm/{world_id}/sessions/{session_id}/context` - 获取游戏上下文
- `POST /gm/{world_id}/sessions/{session_id}/scene` - 进入场景
- `POST /gm/{world_id}/sessions/{session_id}/input` - 处理玩家输入
- `POST /gm/{world_id}/sessions/{session_id}/dialogue/start` - 开始对话
- `POST /gm/{world_id}/sessions/{session_id}/dialogue/end` - 结束对话
- `POST /gm/{world_id}/sessions/{session_id}/combat/trigger` - 触发战斗
- `POST /gm/{world_id}/sessions/{session_id}/combat/action` - 执行战斗行动

### 3. 交互式 CLI (`app/tools/game_master_cli.py`)

类似 Claude Code 的交互式命令行工具，用于测试完整系统。

## 使用方法

### 启动交互式测试

```bash
cd backend

# 运行交互式CLI
python -m app.tools.game_master_cli

# 或者先设置演示世界
python -m app.tools.game_master_cli --setup-demo
```

### 示例游戏流程

```
> /setup              # 初始化演示世界（创建NPC、设置初始记忆）
> /start              # 开始新游戏
> /scene tavern       # 进入旅店场景
> /talk elena         # 开始与Elena对话
> "你好，今天有什么新鲜事吗？"  # 与NPC对话
> /leave              # 结束对话
> /scene forest_edge  # 进入森林边缘
> /combat             # 触发演示战斗
> /action attack_goblin_1  # 攻击敌人
> /quit               # 退出游戏
```

### 可用命令

| 命令 | 说明 |
|------|------|
| `/setup` | 设置演示世界（初始化角色和记忆） |
| `/start` | 开始新游戏 |
| `/scene <场景ID>` | 进入场景 (blacksmith, tavern, forest_edge) |
| `/talk <NPC ID>` | 开始与NPC对话 (gorn, marcus, elena) |
| `/leave` | 结束当前对话 |
| `/status` | 查看当前状态 |
| `/combat` | 触发演示战斗 |
| `/action <行动ID>` | 执行战斗行动 |
| `/help` | 显示帮助 |
| `/quit` | 退出游戏 |

### 演示NPC

| NPC ID | 名字 | 职业 | 特点 |
|--------|------|------|------|
| gorn | Gorn | 铁匠 | 严肃直接，用锻造比喻 |
| marcus | Marcus | 猎人 | 活泼，喜欢讲故事 |
| elena | Elena | 旅店老板娘 | 温和，知道所有八卦 |

### 演示场景

| 场景 ID | 地点 | 在场NPC |
|---------|------|---------|
| blacksmith | 铁匠铺 | gorn |
| tavern | 金麦旅店 | elena, marcus |
| forest_edge | 森林边缘 | （无） |

## 验收标准检查

✅ **玩家进入场景**
- `enter_scene` 方法实现场景切换
- 自动为在场NPC预加载相关记忆
- GM生成沉浸式场景描述

✅ **和NPC对话**
- `start_dialogue` / `_handle_dialogue` 实现对话系统
- NPC使用 Pro 服务进行角色扮演
- NPC可以按需调用 Flash 检索记忆

✅ **触发战斗**
- `trigger_combat` 整合战斗引擎
- 战斗状态管理和行动执行
- 战斗叙述生成

✅ **战斗结果被记录**
- 战斗结束后自动创建 COMBAT 类型事件
- 通过 GM Flash 服务分发给相关角色

✅ **NPC记得发生了什么**
- 事件通过事件总线分发到角色 Flash
- 下次对话时NPC可以回忆相关事件

## 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                     GameMasterService                        │
│                     (核心编排器)                              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ GameLoop    │  │ Pro Service │  │ GM Flash Service    │ │
│  │ Service     │  │ (NPC对话)   │  │ (事件记录/分发)     │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
│         │                │                    │             │
│         ▼                ▼                    ▼             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ Combat      │  │ Flash       │  │ Event Bus           │ │
│  │ Engine      │  │ Service     │  │ (事件发布/订阅)     │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
│                          │                                  │
│                          ▼                                  │
│                  ┌─────────────────┐                       │
│                  │ Graph Store     │                       │
│                  │ (图谱存储)      │                       │
│                  └─────────────────┘                       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## 数据流示例

### 场景进入流程
```
1. 玩家调用 enter_scene(tavern)
2. GameMasterService 更新会话状态
3. 为在场NPC（elena, marcus）预加载记忆
   - Flash 检索与场景/其他角色相关的记忆
4. GM 生成场景描述
5. 记录 SCENE_CHANGE 事件到 GM 图谱
6. 返回描述给玩家
```

### NPC对话流程
```
1. 玩家输入 "你好，有什么消息吗？"
2. 输入被分类为 DIALOGUE 类型
3. Pro Service 调用 NPC 的 LLM
4. LLM 决定是否需要调用 recall_memory 工具
5. 如需要，Flash 执行激活扩散检索记忆
6. 记忆被翻译并注入对话
7. LLM 生成最终回复
8. 对话事件被记录并分发
```

### 战斗流程
```
1. 触发战斗（trigger_combat）
2. CombatEngine 初始化战斗会话
3. 玩家选择行动（attack_goblin_1）
4. 引擎执行行动并计算结果
5. 战斗结束后生成 COMBAT 事件
6. 事件分发给参与者和目击者
7. NPC 的记忆被更新
```

## 示例文件

位于 `backend/examples/phase6/`:
- `gm_session.json` - 创建会话请求
- `enter_scene.json` - 进入场景请求
- `character_profiles.json` - 演示NPC资料
- `trigger_combat.json` - 触发战斗请求

## 后续优化方向

1. **NPC自主行为**：让NPC在玩家不在场时也能行动
2. **多玩家支持**：支持多个玩家同时在线
3. **剧情触发器**：基于条件自动触发剧情事件
4. **战斗AI增强**：更智能的敌人决策
5. **记忆持久化**：对话历史的长期存储和检索
# 注意：本文档基于旧架构（GameMasterService / gm_flash_service），当前已迁移至 Admin Layer（admin_coordinator / admin/event_service）。仅供历史参考。
