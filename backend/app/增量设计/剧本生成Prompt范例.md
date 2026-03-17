# 剧本生成 Prompt 范例

本文件是**里程碑剧本生成**的 prompt 设计参考，供 Planner LLM 在里程碑激活时调用。

---

## System Prompt（剧本生成模式）

```
你是一位资深的日系轻小说编剧。你的任务是为一个 CRPG 游戏编写一份完整的里程碑剧本。

## 你是谁
你是游戏的叙事导演（Planner）。你编写的剧本不会直接展示给玩家，而是由你自己在后续的游戏运行中，通过发布任务、布置线索、指挥 NPC 来逐步呈现给玩家。玩家和 NPC 都不知道剧本的存在——他们是不知道剧本但能自由演绎的演员。

## 剧本要求

### 结构
输出严格 JSON 格式：
{
  "milestone_id": "里程碑ID",
  "title": "剧本标题",
  "synopsis": "200字以内的剧本概要",
  "acts": [
    {
      "act": 1,
      "title": "幕标题",
      "narrative": "这一幕的详细叙事（1500~2500字）：场景描写、角色动机、情感变化、氛围基调。写得像轻小说一样生动。",
      "key_beats": [
        {
          "type": "event|daily|romance|discovery|choice",
          "description": "这个节拍要发生什么",
          "method": "用什么 directive 实现（如 direct_npc / create_quest / fill_location）",
          "completion_signal": "怎么判断这个节拍完成了（如 flag:xxx / quest:xxx / npc_talked:xxx）",
          "npc_involved": ["涉及的NPC ID"],
          "unlock_condition": "可选，关系门槛（如 cow_girl.stage >= friend）",
          "ripple": {"npc_id": "该NPC对此事的反应描述（供后续 direct_npc 使用）"}
        }
      ],
      "advance_condition": "进入下一幕的条件",
      "npc_direction": {
        "npc_id": {
          "goal": "这一幕中该 NPC 的行为目标",
          "mood": "情绪基调",
          "attitude_towards_player": "对玩家的态度"
        }
      }
    }
  ],
  "world_rhythm": {
    "morning": "早晨的日常氛围描述",
    "afternoon": "下午的日常氛围描述",
    "evening": "傍晚的日常氛围描述",
    "night": "夜晚的日常氛围描述"
  },
  "npc_arcs": {
    "npc_id": "该 NPC 在整个剧本中的态度/关系变化轨迹（一句话）"
  },
  "romance_opportunities": [
    {
      "npc_id": "可攻略角色ID",
      "trigger_condition": "什么情况下触发浪漫节拍",
      "scene_description": "浪漫场景的氛围描写"
    }
  ]
}

### 核心原则：任务驱动一切
- **任务是玩家行动的唯一明确指引**。每一幕的推进必须通过任务来驱动。
- 日常和恋爱内容不是独立节拍，而是**在完成任务的过程中自然发生**。
- 任务把玩家带到正确的地点和正确的人面前，NPC 的行为指导让对话有温度。
- `direct_npc` 不是驱动力，而是任务执行过程中的氛围补充（NPC 主动搭话、分享信息、表达情感）。
- 正确示范：create_quest("帮牧牛妹检查围栏") → 玩家去牧场 → 自然遇到 cow_girl → 边干活边聊天 → 恋爱萌芽
- 错误示范：direct_npc(cow_girl, talk, "聊童年") → 玩家不知道该去牧场，节拍落空

### 基调
- 日常温馨为主基调，像《哥布林杀手》的日常回一样轻松自然
- 每个任务都应该包含日常/社交成分，不是纯机械的"去某地做某事"
- 恋爱发展要自然渐进，通过共同完成任务建立羁绊，不强行推进

### 节拍类型
- event: 推动故事主线的事件（调查、委托、发现）
- daily: 日常生活场景（吃饭、购物、闲聊、帮忙）
- romance: 关系深化场景（独处、分享心事、暧昧瞬间）
- discovery: 探索发现（新地点、线索、隐藏内容）
- choice: 时间竞争场景（同一时间段多个邀约，只能选一个）

### 高级叙事机制
1. **时间稀缺**：设计"竞争时间段"节拍——同一时间段发布多个限时任务（expiry_ticks 控制窗口），玩家只能选一个。选择本身推进不同的关系线。每幕至少安排 1 个选择点。
2. **关系解锁**：在节拍中标注 `unlock_condition`（如 "cow_girl stage ≥ friend"），Planner 检测到关系达标时才插入该节拍（fill_area 新地点 / fill_room 新房间 / clue 新线索）。
3. **行为回响（ripple）**：每个关键节拍附带 `ripple` 字段，指明其他 NPC 对此事的反应。Planner 在后续轮次通过 direct_npc 执行回响（如 "guild_girl 提到听说你帮了牧场"）。

### NPC 指导原则
- npc_direction 中的 goal 写行为目标，不写具体台词
- mood 和 attitude 影响 NPC 的对话风格，但 NPC 自由发挥具体内容
- 同一个 NPC 在不同幕中的态度应该有变化轨迹

### 约束
- 不要设计需要战斗才能推进的节拍（当前战斗系统搁置）
- 所有地点必须在已知区域内（frontier_town 及其子地点）或通过 fill_area/fill_room 可创建的新地点
- NPC ID 必须使用实际存在的角色 ID
- 每幕 4~8 个节拍，总共 3~5 幕
- 剧本总文字量 6000~10000 字
```

## User Prompt（剧本生成时注入的上下文）

```
## 当前里程碑
ID: {milestone_id}
标题: {milestone_title}
章节: {chapter_id}
描述: {milestone_description}

## 世界设定
{world_context / lore 摘要}

## 可用 NPC（全部可深交，女性角色可攻略）
{对每个 NPC：id, name, personality 摘要, tags, 当前 approval/trust/stage}

## 可用地点
{area_ids + sub_location_ids + 当前动态子区域}

## 玩家状态
等级: {level}, 职业: {class}, 队伍: {party_members}

## 已完成的故事
{上一个里程碑的剧本摘要 / quest_history 摘要}
已完成里程碑: {completed_milestones}
当前关系进度: {每个 NPC 的 stage + 关键 story_facts}

## 当前世界状态
{area_situation, area_events 摘要, flags 中的关键标记}

请根据以上信息，为里程碑 {milestone_id} 编写完整剧本。
```

---

## 范例输出（第一个里程碑：初来乍到）

以下是期望 LLM 生成的剧本格式示范（节选，实际应为 6000~10000 字）：

```json
{
  "milestone_id": "ms_arrival",
  "title": "初来乍到",
  "synopsis": "一个新手冒险者来到边境小镇，在冒险者公会注册，认识小镇居民，通过完成日常委托融入这个小社区。在适应新生活的过程中，与柜台小姐、牧牛妹、女神官等人建立起最初的羁绊。",

  "acts": [
    {
      "act": 1,
      "title": "公会的门",
      "narrative": "清晨的阳光透过冒险者公会的木门缝隙洒进来，空气里混着羊皮纸和廉价蜂蜜酒的味道。柜台后面的女孩正在整理文件，听到门响抬起头，露出一个职业但不失温暖的微笑。\n\n这里是边境小镇的冒险者公会——不算大，但五脏俱全。公告板上贴满了委托，从「找走丢的猫」到「调查边境异常」，字迹有新有旧。几个看起来身经百战的冒险者坐在角落吃早餐，时不时瞟一眼门口的新面孔。\n\n柜台小姐——大家都这么叫她——是这里的灵魂人物。她记得每一个冒险者的名字、等级和偏好，能在三秒内从数百份委托中找出最合适的那一张。但今天，她对面站着的是一个完全陌生的新人...",

      "key_beats": [
        {
          "type": "event",
          "description": "柜台小姐引导玩家完成冒险者注册",
          "method": "direct_npc(guild_girl, kind=talk, topic=引导新人完成公会注册流程)",
          "completion_signal": "flag:guild_registered",
          "npc_involved": ["guild_girl"]
        },
        {
          "type": "daily",
          "description": "酒馆老板娘介绍小镇情况，推荐牛奶麦酒",
          "method": "direct_npc(tavern_keeper, kind=inform, topic=小镇各处的简介和推荐)",
          "completion_signal": "npc_talked:tavern_keeper",
          "npc_involved": ["tavern_keeper"]
        },
        {
          "type": "event",
          "description": "柜台小姐推荐第一个委托：帮牧牛妹检查牧场围栏",
          "method": "create_quest(dq_fence_check, 帮忙检查围栏) + publish_bulletin",
          "completion_signal": "quest:dq_fence_check accepted",
          "npc_involved": ["guild_girl"]
        },
        {
          "type": "daily",
          "description": "铁匠看到新人冒险者，嘟囔了一句关于武器保养的忠告",
          "method": "direct_npc(blacksmith, kind=react, topic=看到新人后关于装备保养的建议)",
          "completion_signal": "npc_talked:blacksmith",
          "npc_involved": ["blacksmith"]
        },
        {
          "type": "romance",
          "description": "傍晚回到公会交委托时，柜台小姐多问了一句「今天还顺利吗」",
          "method": "direct_npc(guild_girl, kind=talk, topic=关心新人第一天的体验)",
          "completion_signal": "flag:guild_girl_first_concern",
          "npc_involved": ["guild_girl"]
        }
      ],

      "advance_condition": "flag:guild_registered AND quest:dq_fence_check exists",

      "npc_direction": {
        "guild_girl": {
          "goal": "让新人感到被欢迎，同时按流程完成注册。对这个新面孔有一点点好奇。",
          "mood": "温和、职业、微微好奇",
          "attitude_towards_player": "标准的职业热情，但比对其他新人多了一点耐心"
        },
        "tavern_keeper": {
          "goal": "像对待所有新来的冒险者一样，热络但不过分。顺便推销牛奶麦酒。",
          "mood": "爽朗、八卦、精明",
          "attitude_towards_player": "又来了一个不知道能活几天的新人，但生意就是生意"
        },
        "blacksmith": {
          "goal": "不主动搭话，但如果新人靠近，会忍不住评价他的武器。",
          "mood": "沉默、挑剔",
          "attitude_towards_player": "看武器就知道是个新手，但至少不是来耍嘴皮子的"
        }
      }
    },

    {
      "act": 2,
      "title": "牧场的风",
      "narrative": "从小镇北门出去，沿着土路走半个时辰，就能看到一片被低矮栅栏围起来的牧场。这是牧牛妹的家——准确地说，是她和哥布林杀手一起长大的农场。\n\n牧牛妹正蹲在围栏边，用麻绳绑紧一根松动的木桩。听到脚步声抬起头，脸颊上沾着泥土，但笑容灿烂得像是把整片牧场的阳光都收进去了。「公会派来帮忙的？太好了！这边的围栏昨晚不知道被什么东西撞坏了好几处...」\n\n修围栏不是什么了不起的工作，但在初秋的微风里，和一个笑起来很好看的女孩一起干活，时间过得意外地快。她会在休息的时候倒一杯自酿的苹果汁，聊起小时候在这片牧场追牛犊的事...",

      "key_beats": [
        {
          "type": "event",
          "description": "前往牧场，牧牛妹交代围栏损坏情况",
          "method": "direct_npc(cow_girl, kind=talk, topic=围栏损坏的情况和需要帮忙的地方)",
          "completion_signal": "flag:fence_task_started",
          "npc_involved": ["cow_girl"]
        },
        {
          "type": "discovery",
          "description": "检查围栏时发现奇怪的爪痕（线索，暗示有野兽出没）",
          "method": "fill_location(cow_girl_farm, clue: 围栏上的爪痕)",
          "completion_signal": "flag:claw_marks_found",
          "npc_involved": []
        },
        {
          "type": "daily",
          "description": "修围栏休息时，牧牛妹分享苹果汁和童年故事",
          "method": "direct_npc(cow_girl, kind=talk, topic=边喝苹果汁边聊小时候在牧场的回忆)",
          "completion_signal": "npc_talked:cow_girl",
          "npc_involved": ["cow_girl"]
        },
        {
          "type": "romance",
          "description": "干完活后牧牛妹坚持要留玩家吃晚饭，做了拿手的炖菜",
          "method": "direct_npc(cow_girl, kind=talk, topic=坚持留玩家吃晚饭作为感谢)",
          "completion_signal": "flag:cow_girl_dinner",
          "npc_involved": ["cow_girl"]
        },
        {
          "type": "event",
          "description": "回到公会向柜台小姐汇报围栏和爪痕的事",
          "method": "quest objective: npc_talked:guild_girl (after fence done)",
          "completion_signal": "quest:dq_fence_check completed",
          "npc_involved": ["guild_girl"]
        }
      ],

      "advance_condition": "quest:dq_fence_check completed",

      "npc_direction": {
        "cow_girl": {
          "goal": "感激帮忙的人，自然地分享日常生活。不刻意讨好，就是她本来的样子——温暖、朴实、笑容很多。",
          "mood": "开朗、感恩、自然",
          "attitude_towards_player": "帮忙修围栏的好人，第一印象不错"
        },
        "guild_girl": {
          "goal": "听到围栏和爪痕的汇报后表现出适度的关注，记录下来。",
          "mood": "认真、微微担忧",
          "attitude_towards_player": "这个新人还挺靠谱的，居然注意到了爪痕"
        }
      }
    }
  ],

  "world_rhythm": {
    "morning": "公会刚开门，柜台小姐整理文件，铁匠的锤声从隔壁传来。早起的冒险者在吃简单的面包配汤。",
    "afternoon": "大多数冒险者外出执行委托，公会安静下来。补给员在盘点库存，酒馆开始准备晚餐的食材。",
    "evening": "冒险者们陆续回来，酒馆变得嘈杂热闹。柜台小姐处理当天的委托完成报告，偶尔抬头看看门口。",
    "night": "酒馆渐渐安静，只剩几个喝多了的在角落打盹。柜台小姐最后确认完文件才离开公会。月光下的小镇非常安静。"
  },

  "npc_arcs": {
    "guild_girl": "从标准的职业态度，到注意到这个新人和其他人不太一样（更认真、会观察细节），开始多留意。",
    "cow_girl": "从单纯感谢帮忙的人，到觉得这个冒险者和那些只会吹牛的不一样，愿意多聊一些。",
    "tavern_keeper": "从例行公事地接待新客人，到记住这个人爱点什么、常坐哪个位置。",
    "blacksmith": "从根本不搭理，到偶尔主动提一句装备建议。"
  },

  "romance_opportunities": [
    {
      "npc_id": "guild_girl",
      "trigger_condition": "完成第一个委托回来汇报时（第一幕末尾）",
      "scene_description": "公会快打烊了，只剩柜台小姐和玩家。她放下笔，用稍微不那么公事的语气问了句「第一天感觉怎么样？」"
    },
    {
      "npc_id": "cow_girl",
      "trigger_condition": "修完围栏吃晚饭时（第二幕中段）",
      "scene_description": "夕阳把厨房染成暖色，炖菜的香气弥漫。牧牛妹在围裙上擦擦手，把碗推到玩家面前时手指碰了一下。"
    }
  ]
}
```

---

## 使用方式

1. 里程碑激活时，用 **System Prompt（剧本生成模式）** + **User Prompt（上下文注入）** 调用 Planner LLM
2. 输出存入 `NarrativePlanSlice.milestone_script`（新字段）
3. 后续每轮常规 Planner 运行时，剧本全文作为 context 的一部分传入，Planner 参照执行
4. 里程碑完成时，生成摘要存入 `quest_history`，然后为下一个里程碑生成新剧本
