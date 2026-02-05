"""
FastAPI dependencies.
"""
from functools import lru_cache

from app.services.admin.admin_coordinator import AdminCoordinator
from app.services.graph_store import GraphStore


@lru_cache()
def get_coordinator() -> AdminCoordinator:
    return AdminCoordinator.get_instance()


@lru_cache()
def get_graph_store() -> GraphStore:
    return GraphStore()
