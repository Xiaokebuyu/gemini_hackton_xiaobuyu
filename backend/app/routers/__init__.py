"""
API 路由包
"""
from .graphs import router as graphs_router
from .flash import router as flash_router
from .pro import router as pro_router
from .gm import router as gm_router
from .game import router as game_router
from .game_master import router as game_master_router

__all__ = [
    "graphs_router",
    "flash_router",
    "pro_router",
    "gm_router",
    "game_router",
    "game_master_router",
]
