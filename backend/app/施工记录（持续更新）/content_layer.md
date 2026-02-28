# ❶ 内容层施工记录

**设计文档**：内容层设计规范（已读）.md
**代码路径**：`app/game_core/content/`
**Phase**：0A（基类）+ 3.5（具体 Registry）

## 模块状态

| 组件 | 状态 | 说明 |
|------|------|------|
| `ContentRegistry` ABC | [完成] | 含 `_coerce_dict_mapping` 共享方法 |
| `WorldInstance` | [完成] | typed properties + 三步加载顺序 |
| `TagRegistry` | [骨架] | 能 load/get/list_all，无语义校验 |
| `MapRegistry` | [骨架] | 通用 dict 存储，无地图拓扑校验 |
| `CharacterRegistry` | [骨架] | 通用 dict 存储，无 tier/trait 校验 |
| `ItemRegistry` | [骨架] | 通用 dict 存储，无装备槽位校验 |
| `MonsterRegistry` | [骨架] | 通用 dict 存储，无 CR/属性校验 |
| `SkillRegistry` | [骨架] | 通用 dict 存储，无技能树校验 |
| `ClassRegistry` | [骨架] | 通用 dict 存储，无等级特性校验 |
| `FactionRegistry` | [骨架] | 通用 dict 存储 |
| `LoreRegistry` | [骨架] | 通用 dict 存储 |
| `QuestRegistry` | [骨架] | 里程碑 + 章节 + 初始事件结构 |

## 决策记录

### [D-C01] list_all 改为 @abstractmethod

见 `骨架搭建.md` [D-001]。

### [D-C02] _coerce_dict_mapping 提到基类

见 `骨架搭建.md` [D-003]。8 个 Registry 共享同一个 list-or-dict 归一化逻辑。

### [D-C03] WorldInstance.load_all 三步加载

```python
ordered_groups = [
    ["tags"],                                          # Step 1: Tag 先行
    ["maps", "classes", "skills", "lore"],            # Step 2: 基础 Registry
    ["characters", "items", "monsters", "factions", "quests"],  # Step 3: 依赖基础的
]
```

未在 ordered_groups 中的 Registry 最后加载（兜底）。

## 填充 TODO

- [ ] `MapRegistry`：地图拓扑校验（area 之间的连通性）
- [ ] `CharacterRegistry`：NPC tier（main/secondary/passerby）字段校验
- [ ] `ItemRegistry`：装备槽位 + 消耗品类型校验
- [ ] `MonsterRegistry`：CR + 属性完整性校验
- [ ] `SkillRegistry`：法术等级 + 法术位消耗校验
- [ ] `QuestRegistry`：里程碑前置依赖图校验（DAG 检测）
- [ ] `WorldInstance.validate()`：跨 Registry 引用校验（角色引用的物品 ID 是否存在等）
