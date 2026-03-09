# P17 - party 静默兜底与 private 语义收口

状态：已完成
日期：2026-03-08

## 1. 本次修正的真实问题

基于实机验证，utterance 主链落地后还剩两条真实偏差：

- 显式 `party` 频道在整轮无人回应时，玩家体感仍像“队友没收到”
- `private_chat` 的后续选项存在作用域漂移，继续交谈类 option 会掉回 `public`

这两条都不是总线失效，而是编排和 dispatch 语义还没完全收口。

## 2. 实际落地

### 2.1 `party` 静默兜底

- 保留队友回应概率门，不改成保底至少一人开口
- 仅在显式 `scope="party"` 的 utterance 上新增静默兜底
- 若整轮没有任何 `teammate_response`，则补一条 `gm_comment`
- 先尝试走专用 GM prompt 生成毒舌/说明性短评
- 若模型选择 `pass_turn` 或无有效 comment，则回退到静态兜底文案

### 2.2 队友回应概率改为“阶段 + 数值”

- 在原有 `response_tendency` 上叠加：
  - `relationship_stage`
  - `approval`
  - `trust`
  - scene tags 修正
  - `explicit_party` bonus
  - recent speaks penalty
- Stage B 被动反应路径没有一起改重，只把这套关系修正接入 utterance/group dialogue 相关路径

### 2.3 `private_chat` 语义收口

- 保留私聊临时子地点 `scene_change`
- 保留 `gm_comment` 作为私聊内心旁白
- 私聊中的继续对话类 option 现在统一下发显式 `dispatch.kind="interact"` + `scope="private"`
- 带检定的私聊 option 同样保持 `scope="private"`
- `farewell` 继续保持本地 `leave_dialogue`
- `browse` 这类不该自动私聊化的公共交互，不再在私聊里生成误导性 dispatch

### 2.4 前端 fallback 收紧

- `dialogue_options` 优先尊重服务端 dispatch
- `private_chat` 模式下，仅对“继续对话类”无 dispatch 选项做 private fallback
- 不再把私聊里的所有无 dispatch 选项一律当作 private talk 发送

## 3. 关键代码落点

- `app/agent_orchestration.py`
- `app/game_core/orchestration/npc_interaction.py`
- `app/game_core/narrative/context_builder.py`
- `frontend/src/hooks/useGameStream.ts`

## 4. 验证结果

代码级验证：

- `./venv/bin/pytest tests/test_private_chat.py tests/test_group_dialogue.py tests/test_agent_orchestration.py tests/test_api_shell.py -q`
  - 结果：`143 passed`
- `npm run build`
  - 结果：通过

实机本地验证：

- 强制 `party` 静默时，实际收到 `interaction_resolved -> gm_comment -> stream_end`
- `private_chat` 返回的 follow-up option 现为：
  - `继续交谈 -> scope="private"`
  - `询问任务 -> scope="private"`
  - `告别 -> local leave_dialogue`

## 5. 结果判断

这次之后，当前 utterance 相关真实状态是：

- `public / direct / checked dialogue` 主链可用
- `party` 的“没人理我”现已转为“沉默也有 GM 说明”，不再像消息丢失
- `private_chat` 已不再把继续对话 option 泄漏回公开链

剩余未做的是产品调优，不是语义断裂：

- 队友回应频率是否还要继续调高
- 非焦点附近 NPC 是否要开放受控插话
