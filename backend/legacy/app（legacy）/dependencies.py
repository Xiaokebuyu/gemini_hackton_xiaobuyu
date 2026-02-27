"""
FastAPI dependencies.
"""
from functools import lru_cache

from app.services.admin.admin_coordinator import AdminCoordinator


@lru_cache()
def get_coordinator() -> AdminCoordinator:
    return AdminCoordinator.get_instance()
