"""Adapter layer for integrating the new kernel with external entrypoints."""

from app.game_core.adapters.inbound import (
    CommandAliasInputPort,
    FastAPIInputPort,
    InputPort,
    NullInputPort,
)
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
)
from app.game_core.adapters.session_store import SaveResult, SaveStore

__all__ = [
    "CommandAliasInputPort",
    "FastAPIInputPort",
    "InputPort",
    "NullInputPort",
    "LocalFilePersistencePort",
    "NullOutputPort",
    "NullPersistencePort",
    "NullPresentationPort",
    "OutputPort",
    "PersistencePort",
    "PresentationPort",
    "SessionCatalogPort",
    "SaveResult",
    "SaveStore",
]
