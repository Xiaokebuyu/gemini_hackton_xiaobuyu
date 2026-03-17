# Clue 与环境交互

## 模块概要

场景中的可交互物件系统：线索调查、容器、发现点、NPC 服务设施。Clue 是核心叙事触发器。

### 核心模块

| 模块 | 位置 | 职责 |
|------|------|------|
| ClueHandler | `rules/handlers/clue.py` | investigate_clue / resolve_clue_option 两阶段处理：首次调查（on_first_inspect 效果）→ 选项解决（skill check + 分支效果） |
| clue_investigation | `game_core/clue_investigation.py` | Clue 定义 schema、效果执行、选项归一化。6 种效果类型：set_flag / remove_flag / advance_quest / add_knowledge / modify_approval / unlock_sub_location |
| environment_access | `game_core/environment_access.py` | 解析玩家当前可达的 interactable 列表（静态 + overlay + dynamic_room + dynamic_sub_area） |
| environment_rewards | `game_core/environment_rewards.py` | 环境奖励应用（物品/子地点创建） |

### 其他 Interactable Handler

| 模块 | 位置 | 职责 |
|------|------|------|
| InteractableHandler | `rules/handlers/interactable.py` | 通用 interactable 多检定路径交互 |
| ContainerHandler | `rules/handlers/container.py` | 容器打开/交互 |
| DiscoveryHandler | `rules/handlers/discovery.py` | 主动发现搜索（骰点） |
| DonationHandler | `rules/handlers/donation.py` | 神殿/NPC 奉献服务效果 |
| BoardHandler | `rules/handlers/board.py` | 任务板交互（浏览/接受/完成/退回） |

### 辅助模块

| 模块 | 位置 | 职责 |
|------|------|------|
| scene_interactables | `game_core/scene_interactables.py` | 场景级 interactable 语义和迁移 |
| container_access | `game_core/container_access.py` | 容器可达性和延迟状态 |
| PassivePerceptionHook | `orchestration/hooks/passive_perception.py` | 进入场景时自动感知 discoveries/interactables/traps |
| DynamicSubAreaExpiryHook | `orchestration/hooks/dynamic_sub_area_expiry.py` | 过期动态子区域清理 |
