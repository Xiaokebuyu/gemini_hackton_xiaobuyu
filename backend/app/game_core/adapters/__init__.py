"""Adapter layer for integrating the new kernel with external entrypoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.game_core.adapters.inbound import (
    CommandAliasInputPort,
    FastAPIInputPort,
    InputPort,
    NullInputPort,
)
from app.game_core.adapters.firestore_persistence import FirestorePersistencePort
from app.game_core.adapters.local_persistence import LocalFilePersistencePort
from app.game_core.adapters.outbound import NullOutputPort, OutputPort
from app.game_core.adapters.persistence import (
    NullPersistencePort,
    PersistencePort,
    SessionCatalogPort,
)
from app.game_core.adapters.presentation import (
    NullPresentationPort,
    PresentationPort,
    SSEPresentationPort,
)
from app.game_core.adapters.design_skill import DesignSkillPort, NullDesignSkillPort
from app.game_core.adapters.llm import LlmPort, LlmResponse, NullLlmProvider
from app.game_core.adapters.memory_graph_port import MemoryGraphPort, NullMemoryGraphPort
from app.game_core.adapters.planner_system import (
    PlannerAgentPort,
    PlannerBlackboardPort,
    PlannerSystemAssembly,
)

if TYPE_CHECKING:
    from app.game_core.adapters.session_store import SaveResult, SaveStore

__all__ = [
    "CommandAliasInputPort",
    "DesignSkillPort",
    "FastAPIInputPort",
    "InputPort",
    "LlmPort",
    "LlmResponse",
    "MemoryGraphPort",
    "NullDesignSkillPort",
    "NullInputPort",
    "NullLlmProvider",
    "NullMemoryGraphPort",
    "FirestorePersistencePort",
    "LocalFilePersistencePort",
    "NullOutputPort",
    "NullPersistencePort",
    "NullPresentationPort",
    "OutputPort",
    "PersistencePort",
    "PlannerAgentPort",
    "PlannerBlackboardPort",
    "PlannerSystemAssembly",
    "PresentationPort",
    "SSEPresentationPort",
    "SessionCatalogPort",
    "SaveResult",
    "SaveStore",
]


def __getattr__(name: str):
    if name in {"SaveResult", "SaveStore"}:
        from app.game_core.adapters.session_store import SaveResult, SaveStore

        exports = {
            "SaveResult": SaveResult,
            "SaveStore": SaveStore,
        }
        return exports[name]
    raise AttributeError(name)
