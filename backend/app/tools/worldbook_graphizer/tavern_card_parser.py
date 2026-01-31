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
        # 直接查找映射
        if raw_type in self.type_mapping:
            return self.type_mapping[raw_type]

        # 模糊匹配：检查前缀
        for prefix, normalized in self.type_mapping.items():
            if raw_type.startswith(prefix):
                return normalized

        # 默认为 other
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
