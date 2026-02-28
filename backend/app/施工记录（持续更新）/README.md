# game_core 施工记录

按模块维护的实施日志。每个文件对应一个子系统，记录技术决策、接口变更和实施进度。

## 文件索引

| 文件 | 对应层 | 设计文档 |
|------|--------|---------|
| [骨架搭建.md](骨架搭建.md) | 全局 | 实施路线图 v1.1 |
| [content_layer.md](content_layer.md) | ❶ 内容层 | 内容层设计规范 |
| [state_layer.md](state_layer.md) | ❸ 状态层 | 状态层设计规范 |
| [rules_engine.md](rules_engine.md) | ❷ 规则引擎层 | 规则引擎层设计规范 |
| [orchestration.md](orchestration.md) | 编排层 | 编排层设计规范 |
| [narrative.md](narrative.md) | 叙事层 + 叙事规划 | 叙事层设计规范 + 叙事规划子系统设计规范 |
| [adapters.md](adapters.md) | 适配器层 | — |

## 约定

- **决策记录格式**：`[D-xxx] 决策标题` — 简述 + 理由 + 影响范围
- **接口变更格式**：`[I-xxx] 变更标题` — before → after + 受影响的下游
- **状态标记**：`[骨架]` / `[填充中]` / `[完成]` / `[待定]`
