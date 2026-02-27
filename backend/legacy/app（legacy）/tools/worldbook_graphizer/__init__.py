"""
世界书图谱化工具

推荐入口：`python -m app.tools.init_world_cli extract`（UnifiedWorldExtractor）

模块职责：
- tavern_card_parser: 解析 SillyTavern JSON 格式
- map_extractor:      地图提取
- npc_classifier:     NPC 分类与角色档案
- graph_extractor:    LLM 知识图谱提取（Batch API / Direct）
- graph_prefill:      JSON → MemoryNode/Edge（Firestore 写前转换）
- unified_pipeline:   统一编排器（主推荐）
- narrative_orchestrator: 章节/主线编排（从 unified_pipeline 提取）
- models:             数据模型（MapsData、CharactersData 等）

[DEPRECATED] graphizer.py — 旧编排器，仅被旧 CLI 命令调用，勿在新代码中引用
"""
from .map_extractor import MapExtractor
from .npc_classifier import NPCClassifier
from .tavern_card_parser import TavernCardParser
from .graph_extractor import GraphExtractor
from .unified_pipeline import UnifiedWorldExtractor

__all__ = [
    "MapExtractor",
    "NPCClassifier",
    "TavernCardParser",
    "GraphExtractor",
    "UnifiedWorldExtractor",
]
