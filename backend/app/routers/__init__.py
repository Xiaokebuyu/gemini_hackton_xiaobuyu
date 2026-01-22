"""
API 路由包
"""
from .topics import router as topics_router
from .graphs import router as graphs_router
from .flash import router as flash_router
from .pro import router as pro_router
from .gm import router as gm_router
from .game import router as game_router

__all__ = [
    "topics_router",
    "graphs_router",
    "flash_router",
    "pro_router",
    "gm_router",
    "game_router",
]
