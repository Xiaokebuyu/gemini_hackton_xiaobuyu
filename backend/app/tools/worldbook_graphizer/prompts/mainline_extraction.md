# 主线剧情提取 Prompt

你是一个叙事结构分析专家。请从以下章节信息中生成结构化的主线剧情数据。

## 输入说明

你将收到：
1. 已按卷/章结构整理的章节列表（包含卷号、章号、标题、内容摘要）
2. 已知的地图 ID 列表

## 任务

为每个章节生成结构化数据，包括：
- **available_maps**: 该章节中出现或可前往的地图（从已知地图列表中选择）
- **objectives**: 该章节的主要目标/任务
- **trigger_conditions**: 触发此章节的条件
- **completion_conditions**: 完成此章节的条件

## 已知地图列表

{known_maps}

## 章节列表

{chapters_input}

## 输出格式（JSON）

```json
{
  "mainlines": [
    {
      "id": "vol_1",
      "name": "第一卷标题",
      "description": "卷的简要描述",
      "chapters": ["ch_1_1", "ch_1_2"]
    }
  ],
  "chapters": [
    {
      "id": "ch_1_1",
      "mainline_id": "vol_1",
      "name": "第一章标题",
      "description": "章节简要描述（1-2 句）",
      "available_maps": ["frontier_town", "goblin_cave"],
      "objectives": [
        "描述目标1",
        "描述目标2"
      ],
      "trigger_conditions": {
        "type": "chapter_complete",
        "chapter_id": "ch_0_prologue"
      },
      "completion_conditions": {
        "type": "event",
        "description": "完成条件描述"
      }
    }
  ]
}
```

## 注意事项

1. 章节 ID 格式: `ch_{卷号}_{章号}`，如 `ch_1_1`、`ch_2_3`
2. 主线 ID 格式: `vol_{卷号}`，如 `vol_1`、`vol_2`
3. available_maps 必须从已知地图列表中选择，不要编造不存在的地图 ID
4. 如果无法确定某章节的地图，可以留空数组
5. trigger_conditions 中第一章设为 `{"type": "auto"}`，后续章节设为前一章完成
6. objectives 简洁明了，每个目标不超过 20 字
7. 保持章节的原始顺序
