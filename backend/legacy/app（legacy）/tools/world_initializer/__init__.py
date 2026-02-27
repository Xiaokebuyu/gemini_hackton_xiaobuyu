"""
世界初始化器

将结构化的世界数据加载到 Firestore。
"""
from .initializer import WorldInitializer
from .map_loader import MapLoader
from .character_loader import CharacterLoader

__all__ = [
    "WorldInitializer",
    "MapLoader",
    "CharacterLoader",
]
