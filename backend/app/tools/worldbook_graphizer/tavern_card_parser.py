"""
酒馆卡片解析器

解析 SillyTavern 世界书 JSON 格式，按类型分组条目。
"""
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from .models import WorldbookEntry, EntryTypeGroup, TavernCardData


# 条目类型映射：将 comment 前缀映射到标准化类型
# 注意：前缀匹配按 key 长度降序执行，确保更精确的前缀优先
ENTRY_TYPE_MAPPING = {
    "角色": "character",
    "地点": "location",
    "区域详情": "location",
    "神祇详情": "deity",
    "种族详情": "race",
    "怪物图鉴": "monster",
    "怪物生态": "monster",
    "核心规则": "concept",
    "核心机制": "concept",
    "核心设定": "concept",
    "世界观": "concept",
    "世界元数据": "metadata",
    "道具列表": "item",
    "装备列表": "item",
    "经济系统": "concept",
    "势力组织概述": "faction",
    "历史年表": "event",
    "世界简史": "event",

    # ---- 修复误匹配：精确前缀优先于通用前缀"角色" ----
    "角色成长途径": "concept",
    "角色活动地图": "concept",

    # ---- 补充缺失前缀 ----
    # 地理/地点
    "地理": "location",
    "区域": "location",
    "区域总览": "location",
    # 怪物
    "怪物": "monster",
    "异界存在": "monster",
    "不死者": "monster",
    # 组织
    "组织": "faction",
    "势力": "faction",
    "全球政治格局": "faction",
    # 神祇
    "神祇": "deity",
    "神祇互动": "deity",
    "神迹体系总览": "deity",
    # 物品
    "关键物品与技术": "item",
    "道具": "item",
    "武器": "item",
    # 规则/概念
    "特殊机制": "concept",
    "战斗规则": "concept",
    "职业": "concept",
    "技能": "concept",
    "贡献系统": "concept",
    "冒险等级": "concept",

    # ---- 游戏规则/概念 ----
    "骰子": "concept",
    "委托": "concept",
    "法术": "concept",
    "判定": "concept",
    "合理性": "concept",
    "进阶职业": "concept",
    "冒险者等级": "concept",
    "拜师": "concept",
    "宿命": "concept",

    # ---- 格式/元数据 ----
    "文风": "metadata",
    "叙事": "metadata",
    "输出": "metadata",
    "🌐": "metadata",
    "需要改进": "metadata",
    "需要重做": "metadata",
    "已重新编译": "metadata",
    "后宫化": "metadata",

    # ---- 补充散落条目 ----
    "队伍": "concept",
    "任务": "story",
    "属性": "concept",
    "装备": "item",
    "鸣神": "location",
    "规定": "concept",
    "世界主要神祇": "deity",
    "不祈祷者": "character",
    "剑客": "character",
    "Leviathan": "character",

    # ---- 新增：故事章节 ----
    "第": "story",
    "前传": "story",
    "番外": "story",
    "序章": "story",
    "📖": "story",
    # 自定义卷名
    "古神卷": "story",
    "边境的魔物饭卷": "story",
    "抉择卷": "story",
}

# 要提取为图谱节点的类型（排除规则类和元数据类）
GRAPHABLE_TYPES = {
    "character",
    "location",
    "deity",
    "race",
    "monster",
    "faction",
    "event",
    "item",
    "concept",
    "story",
}


class TavernCardParser:
    """SillyTavern 酒馆卡片解析器"""

    def __init__(self, type_mapping: Optional[Dict[str, str]] = None):
        """
        初始化解析器

        Args:
            type_mapping: 自定义类型映射，覆盖默认映射
        """
        self.type_mapping = {**ENTRY_TYPE_MAPPING, **(type_mapping or {})}

    def parse(self, json_path: Path) -> TavernCardData:
        """
        解析酒馆卡片 JSON 文件

        Args:
            json_path: JSON 文件路径

        Returns:
            TavernCardData: 解析结果
        """
        with open(json_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        entries = []
        raw_entries = raw_data.get("entries", {})

        for index_str, raw_entry in raw_entries.items():
            try:
                index = int(index_str)
            except ValueError:
                index = len(entries)

            entry = WorldbookEntry.from_raw(raw_entry, index)

            # 跳过禁用的条目
            if entry.disable:
                continue

            entries.append(entry)

        # 按类型分组
        groups = self.group_by_type(entries)

        # 提取元数据
        metadata = {}
        if "originalData" in raw_data:
            original = raw_data["originalData"]
            metadata["name"] = original.get("name", "")
            metadata["description"] = original.get("description", "")
            metadata["creator"] = original.get("creator", "")

        return TavernCardData(
            entries=entries,
            groups=groups,
            metadata=metadata,
        )

    def group_by_type(
        self, entries: List[WorldbookEntry]
    ) -> Dict[str, EntryTypeGroup]:
        """
        按类型分组条目

        Args:
            entries: 条目列表

        Returns:
            Dict[str, EntryTypeGroup]: 类型 -> 分组
        """
        groups: Dict[str, List[WorldbookEntry]] = {}

        for entry in entries:
            # 获取标准化类型
            raw_type = entry.entry_type or "other"
            normalized_type = self._normalize_type(raw_type)

            if normalized_type not in groups:
                groups[normalized_type] = []
            groups[normalized_type].append(entry)

        # 转换为 EntryTypeGroup
        return {
            entry_type: EntryTypeGroup(
                entry_type=entry_type,
                entries=entries_list,
                count=len(entries_list),
            )
            for entry_type, entries_list in groups.items()
        }

    def _normalize_type(self, raw_type: str) -> str:
        """
        将原始类型映射为标准化类型

        Args:
            raw_type: 原始类型字符串

        Returns:
            str: 标准化类型
        """
        # 1. 直接查找映射
        if raw_type in self.type_mapping:
            return self.type_mapping[raw_type]

        # 2. 前缀匹配（按 key 长度降序，确保更精确的前缀优先）
        for prefix in sorted(self.type_mapping.keys(), key=len, reverse=True):
            if raw_type.startswith(prefix):
                return self.type_mapping[prefix]

        # 3. 后缀匹配兜底
        _SUFFIX_MAP = {
            "详情": "concept", "图鉴": "monster", "列表": "item",
            "系统": "concept", "规则": "concept", "神迹": "deity",
            "技能": "concept", "总览": "location", "文风": "metadata",
            "输出规范": "metadata", "规定": "concept", "详解": "concept",
        }
        for suffix, normalized in _SUFFIX_MAP.items():
            if raw_type.endswith(suffix):
                return normalized

        # 4. 含"卷"兜底：任意 X卷 模式视为 story
        if "卷" in raw_type:
            return "story"

        # 5. 默认为 other
        return "other"

    def get_graphable_entries(
        self, data: TavernCardData
    ) -> List[WorldbookEntry]:
        """
        获取可以转化为图谱节点的条目

        Args:
            data: 解析后的酒馆卡片数据

        Returns:
            List[WorldbookEntry]: 可图谱化的条目列表
        """
        result = []
        for entry_type, group in data.groups.items():
            if entry_type in GRAPHABLE_TYPES:
                result.extend(group.entries)
        return result

    def get_entries_by_types(
        self, data: TavernCardData, types: List[str]
    ) -> List[WorldbookEntry]:
        """
        获取指定类型的条目

        Args:
            data: 解析后的酒馆卡片数据
            types: 类型列表

        Returns:
            List[WorldbookEntry]: 匹配类型的条目
        """
        result = []
        for entry_type in types:
            if entry_type in data.groups:
                result.extend(data.groups[entry_type].entries)
        return result

    def print_summary(self, data: TavernCardData) -> None:
        """打印解析摘要"""
        print(f"Total entries: {len(data.entries)}")
        print(f"Groups: {len(data.groups)}")
        print()
        print("Entry distribution:")
        for entry_type, group in sorted(
            data.groups.items(), key=lambda x: -x[1].count
        ):
            marker = "  [graphable]" if entry_type in GRAPHABLE_TYPES else ""
            print(f"  {entry_type}: {group.count}{marker}")
