"""Adapter layer for integrating the new kernel with external entrypoints."""

from app.game_core.adapters.inbound import InputPort, NullInputPort
from app.game_core.adapters.outbound import NullOutputPort, OutputPort
from app.game_core.adapters.persistence import NullPersistencePort, PersistencePort
from app.game_core.adapters.presentation import (
    NullPresentationPort,
    PresentationPort,
)

__all__ = [
    "InputPort",
    "NullInputPort",
    "NullOutputPort",
    "NullPersistencePort",
    "NullPresentationPort",
    "OutputPort",
    "PersistencePort",
    "PresentationPort",
]
