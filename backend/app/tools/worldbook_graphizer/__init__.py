"""
世界书图谱化工具

将 worldbook.md 转换为结构化的箱庭数据：
- maps.json: 地图/箱庭定义
- characters.json: 角色分类和资料
- world_graph.json: 世界知识图谱
- world_map.json: 世界地图描述
"""
from .graphizer import WorldbookGraphizer
from .map_extractor import MapExtractor
from .npc_classifier import NPCClassifier

__all__ = [
    "WorldbookGraphizer",
    "MapExtractor",
    "NPCClassifier",
]
