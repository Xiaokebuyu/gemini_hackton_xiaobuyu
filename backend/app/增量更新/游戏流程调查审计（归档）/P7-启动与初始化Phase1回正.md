# Phase 1 启动与初始化回正

## 本轮已落地

1. `POST /api/game/{world_id}/sessions/{session_id}/resume` 改为完整恢复响应
   - 返回 `player`
   - 返回 `scene`
   - 返回 `party`
   - 返回 `location_visual`
   - 返回 `resume_narration`
   - 前端恢复首屏改为优先消费这份响应，避免再额外请求一次 `getScene/getCharacter`

2. “建角完成后进入 opening” 改成后端 phase 契约
   - 建角完成后 phase 为 `opening_ready`
   - `opening/stream` 消费 `opening_ready`
   - opening 开始即将 phase 持久化为 `active`
   - 前端不再依赖 `?opening=1` 触发 opening

3. world bootstrap 收回 runtime
   - `GameRuntime.get_world()` 在 world 未缓存时自行加载 canonical world data
   - 路由层不再需要自己决定读哪套 goblin_slayer 数据

4. world 自动校验
   - `build_default_world()` 在加载 world data 后立刻执行 `validate()`
   - 校验失败直接抛错，阻止非法世界进入 runtime cache

5. SaveStore 元数据写盘条件修正
   - 过去只有 dirty slice 才会落盘
   - 现在只要 meta 变化也会写盘
   - `phase`、`last_played` 这类元数据不再静默丢失

## 明确延后

1. 云端存储 fallback
   - 仍保持单一 persistence backend
   - 暂不实现“本地优先 -> Firestore 回退”

2. 用户维度 session 隔离
   - 当前仍是单用户 shell
   - 不引入 `user_id`
   - 不实现 ownership 校验

3. registry freeze 只读
   - 当前只做到 validate fail-fast
   - 尚未增加 registry 层冻结机制

4. registry 真并行加载
   - 当前保持分组顺序正确
   - 仍是串行加载

5. 文档层 slice 数量与恢复语义统一
   - 当前实现继续以代码为准
   - 暂不反向全面整理全文档中的 `8 slice / 10 slice` 表述漂移

## 当前契约

1. 新会话
   - `POST /sessions` -> `phase=character_creation`

2. 角色创建完成
   - `POST /character` -> `phase=opening_ready`

3. 开场
   - `POST /opening/stream` -> 播放 opening，并将 session phase 持久化为 `active`

4. 读档
   - `POST /resume` -> 返回完整恢复数据
   - 若 phase 为 `opening_ready`，前端继续走 opening
   - 若 phase 为 `active`，前端直接 hydrate 到可操作场景
