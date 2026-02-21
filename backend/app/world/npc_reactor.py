"""NPCReactor 已迁移至 app.services.npc_reactor — 此文件仅保留向后兼容导入。"""
from app.services.npc_reactor import NPCReactor, MAX_REACTIONS_PER_ROUND

__all__ = ["NPCReactor", "MAX_REACTIONS_PER_ROUND"]
