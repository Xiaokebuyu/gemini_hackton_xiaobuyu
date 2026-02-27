# 章节结构化增强

你是一个世界书数据分析助手。请根据以下章节信息，提取结构化数据。

## 章节信息
- ID: {chapter_id}
- 名称: {chapter_name}
- 描述:
{chapter_description}

## 已知地图列表
{known_maps}

## 已提取的目标（可能为空）
{existing_objectives}

## 任务

请分析章节描述，输出以下 JSON：

```json
{{
  "available_maps": ["该章节涉及的地图ID列表，从已知地图列表中选择"],
  "objectives": ["章节目标列表（如果已有目标为空，从描述中提取）"],
  "completion_conditions": {{
    "events_required": ["完成该章节需要触发的事件ID列表，使用 chapter_id_event_N 格式"]
  }}
}}
```

注意：
1. available_maps 只能从已知地图列表中选择
2. objectives 应简洁明确，每条不超过30字
3. events_required 使用章节ID作为前缀，如 "vol1_ch3_event_1"
4. 如果无法确定，返回空数组
