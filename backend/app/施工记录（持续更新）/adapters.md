# 适配器层施工记录

**设计文档**：表现层设计-视觉小说风格.md（部分）
**代码路径**：`app/game_core/adapters/`
**Phase**：5D

## 模块状态

| 组件 | 状态 | 说明 |
|------|------|------|
| `InputPort` Protocol | [完成] | process_text / process_action |
| `NullInputPort` | [完成] | stub 返回 |
| `OutputPort` Protocol | [完成] | emit(payload) |
| `NullOutputPort` | [完成] | 记录 last_payload |
| `PersistencePort` Protocol | [完成] | load(key) / save(key, payload) |
| `NullPersistencePort` | [完成] | 内存 dict 存储 |
| `PresentationPort` Protocol | [完成] | publish(event) |
| `NullPresentationPort` | [完成] | 记录 events 列表 |
| `FastAPIInputPort` | [完成] | 纯输入归一化桥接（text aliases + interaction normalize） |

## 决策记录

### [D-A01] 四端口模型

六层架构的外部边界通过 4 个 Protocol 端口定义：
- **Inbound**：玩家输入（文本 / 结构化动作）
- **Outbound**：响应输出
- **Persistence**：状态持久化（Firestore 适配器）
- **Presentation**：SSE 事件流推送

Null 实现用于测试和骨架验证，无外部依赖。

## 填充 TODO

- [ ] `FirestorePersistencePort`：接入 Firestore（复用现有 state_manager.py 逻辑）
- [ ] `SSEPresentationPort`：SceneBus → SSE event 格式化推送
- [ ] `FastAPIOutputPort`：game_core → CoordinatorResponse 的桥接

## 补充说明

- `FastAPIInputPort` 已经完成，但当前定位已收敛为：
  - 路由层 → `game_core` 的输入协议归一化桥接
  - 负责 text alias / interaction payload normalize
  - **不**直接承担 runtime-aware presence / precheck / execution 编排
