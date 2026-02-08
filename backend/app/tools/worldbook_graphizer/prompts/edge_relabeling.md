# 知识图谱边类型标注

你是一个知识图谱分析助手。请为以下边（关系）标注正确的类型。

## 可用关系类型

### 结构性关系
- opens_area: 解锁区域
- has_location: 包含地点
- connects_to: 连接到
- hosts_npc: 驻留NPC
- default_area: 默认区域

### 世界设定关系
- companion_of: 同伴/伙伴
- enemy_of: 敌人
- member_of: 成员/隶属
- worships: 信仰/崇拜
- ally_of: 盟友
- rules: 统治/管辖
- native_to: 原住/出身
- located_at: 位于
- knows: 认识/了解

### 因果/任务关系
- caused: 导致
- led_to: 引发
- resulted_from: 源于
- advances: 推进

### 通用
- related_to: 相关（无法确定更具体类型时使用）

## 待标注的边

{edges_batch}

## 输出格式

返回 JSON 数组，每个元素包含 edge_id 和标注的 relation：

```json
[
  {{"edge_id": "edge_001", "relation": "companion_of"}},
  {{"edge_id": "edge_002", "relation": "member_of"}}
]
```

注意：
1. 根据 source 和 target 的名称和类型推断最合适的关系
2. 如果无法确定，使用 "related_to"
3. 同类型的实体对倾向于使用对应的关系（如两个角色 → companion_of/enemy_of/ally_of）
