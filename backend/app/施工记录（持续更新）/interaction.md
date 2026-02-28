# 交互层施工记录

## 边界定义

| 职责 | 归属 | 说明 |
|------|------|------|
| 空间可达性验证 | `game_core/orchestration/interaction.py` | NPC/Board 是否在当前位置 |
| 任务状态转换验证 | `game_core/orchestration/interaction.py` | 任务接取/完成/退出的前置条件 |
| 告示牌-任务关联查询 | `game_core/orchestration/interaction.py` | `board_has_quest()` 最小查询 |
| 视图快照构建 | `app/interaction_service.py` + `app/interaction_views.py` | 前端展示数据格式化 |
| SSE 事件信封 | `app/interaction_service.py` | InteractionOutputEvent 构造 |
| 执行分发 | `app/interaction_service.py` | snapshot / pipeline_action / shop_refresh 路由 |
| 管线执行 | 通过 `_execute_structured_action` → TickCoordinator | 正确走管线 |

## D-I01 交互层增强 — Shop/Talk/InspectItem（2026-02-28）

**问题**：交互层 snapshot 过于简陋，前端缺乏足够信息渲染 UI。

**改动**：

### InteractionContext 扩展
- 新增 `player_gold: int`、`player_inventory: list[dict]`、`item_catalog: dict[str, dict]`
- `build_interaction_context()` 从 PlayerSlice 和 ItemRegistry 填充

### Shop Snapshot 增强
- `build_shop_snapshot_payload()` 新增：
  - `player_gold`：玩家当前金币
  - stock 每项富化 `name`/`type`/`rarity`（从 item_catalog 查询）
  - `player_sellable_items`：玩家可出售物品列表（含 name、base_price）

### Talk Snapshot 增强
- `build_talk_snapshot_payload()` 新增 `available_intents` 列表
- 逻辑：基础 ["talk", "greet"] + 商人 ["browse", "buy", "sell", "inspect_item"] + 有动态任务时追加 quest intents

### inspect_item Intent
- 新 builder：`build_inspect_item_payload()` — 物品详情（name/type/rarity/base_price/slot/damage/ac_bonus/player_owned_count）
- InputPort：NPC intent 白名单 + inspect_item 分支（要求 item_id 非空）
- `_execute_snapshot` 扩展提取 `item_id`，所有 builder wrapper 签名加 `item_id` 参数

### 测试
- 7 新测试 in `tests/test_interaction_service.py`（依赖 FastAPI，独立运行）
- 1 现有测试更新（error message 同步）

**文件清单**：
| 文件 | 改动 |
|------|------|
| `app/interaction_service.py` | InteractionContext +3 字段, build_interaction_context 填充, _execute_snapshot +item_id, 9 wrapper 签名更新, +1 新 wrapper |
| `app/interaction_views.py` | shop/talk builder 增强, +build_inspect_item_payload |
| `app/game_core/adapters/inbound.py` | NPC intent 白名单 +inspect_item, +分支 |
| `tests/test_interaction_service.py` | +7 测试 |
| `tests/test_input_port.py` | error message 同步 |
