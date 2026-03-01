"""Adapter layer for integrating the new kernel with external entrypoints."""

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
from app.game_core.adapters.llm import LlmPort, LlmResponse, NullLlmProvider
from app.game_core.adapters.memory_graph_port import MemoryGraphPort, NullMemoryGraphPort
from app.game_core.adapters.session_store import SaveResult, SaveStore

__all__ = [
    "CommandAliasInputPort",
    "FastAPIInputPort",
    "InputPort",
    "LlmPort",
    "LlmResponse",
    "MemoryGraphPort",
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
    "PresentationPort",
    "SSEPresentationPort",
    "SessionCatalogPort",
    "SaveResult",
    "SaveStore",
]
