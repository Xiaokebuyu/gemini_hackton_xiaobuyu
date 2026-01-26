# NPC 分类与提取 Prompt

你是一个世界观分析专家。请从以下世界书内容中提取所有角色，并按重要性分类。

## NPC 层级定义

1. **main (主要角色)**:
   - 剧情核心角色
   - 有完整的背景故事和性格设定
   - 会在多个地图出现
   - 例如：女神官、哥布林杀手、勇者

2. **secondary (次要角色)**:
   - 重要配角
   - 有固定的职责或功能
   - 通常固定在某个地点
   - 例如：柜台小姐、酒馆老板娘、铁匠

3. **passerby (路人)**:
   - 背景角色或临时 NPC
   - 只有简单的模板性格
   - 用于丰富场景氛围
   - 例如：新手冒险者、旅行商人、村民

## 提取要求

对于每个角色，提取：

1. **id**: 英文标识符（如 "priestess"、"goblin_slayer"）
2. **name**: 中文名称
3. **tier**: NPC 层级 (main/secondary/passerby)
4. **default_map**: 默认所在地图 ID（使用之前提取的地图 ID）
5. **aliases**: 别名列表
6. **occupation**: 职业
7. **age**: 年龄（如有）
8. **personality**: 核心性格描述（简洁版）
9. **speech_pattern**: 说话方式（如何表达、口癖等）
10. **example_dialogue**: 示例对话（1-2 句代表性台词）
11. **appearance**: 外貌描述
12. **backstory**: 背景故事（简洁版）
13. **relationships**: 与其他角色的关系（字典格式：{角色ID: 关系描述}）
14. **importance**: 重要性评分 (0-1)
15. **tags**: 标签（如 "治愈者"、"战士"、"导师"）

## 已知地图列表

{known_maps}

## 输出格式（JSON）

```json
{
  "characters": [
    {
      "id": "priestess",
      "name": "女神官",
      "tier": "main",
      "default_map": "frontier_town",
      "aliases": ["小神官", "地母神的信徒"],
      "occupation": "神官（瓷器级冒险者）",
      "age": 15,
      "personality": "温柔善良但内心坚强，对哥布林受害者充满同情",
      "speech_pattern": "礼貌、温和，常使用敬语，祈祷时会念诵经文",
      "example_dialogue": "我相信，我们一定能拯救更多的人。请让我与您同行。",
      "appearance": "金色长发，碧蓝色眼睛，身穿白色神官服，手持神杖",
      "backstory": "在神殿长大的孤儿，第一次冒险时差点被哥布林杀死，被哥布林杀手所救",
      "relationships": {
        "goblin_slayer": "同伴、崇敬的对象",
        "guild_girl": "好友"
      },
      "importance": 0.95,
      "tags": ["治愈者", "神官", "主角团"]
    }
  ],
  "map_assignments": {
    "frontier_town": {
      "main": ["priestess", "goblin_slayer"],
      "secondary": ["guild_girl", "tavern_keeper"],
      "passerby_templates": ["rookie_adventurer", "merchant"]
    }
  }
}
```

## 注意事项

1. main 和 secondary 角色需要详细信息
2. passerby 角色只需要基础模板
3. relationships 中使用角色 ID 作为 key
4. importance 评分参考：
   - 0.9-1.0: 主角、核心角色
   - 0.7-0.9: 重要配角
   - 0.5-0.7: 次要角色
   - 0.3-0.5: 背景角色
   - 0.1-0.3: 路人
5. map_assignments 确保每个地图都有 NPC 分配

## 世界书内容

{worldbook_content}
