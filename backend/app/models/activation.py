"""
Activation configuration models.
"""
from pydantic import BaseModel


class SpreadingActivationConfig(BaseModel):
    """Spreading activation parameters."""
    max_iterations: int = 3
    decay: float = 0.6
    fire_threshold: float = 0.1
    output_threshold: float = 0.15
    hub_threshold: int = 20
    hub_penalty: float = 0.5
    max_activation: float = 1.0
    convergence_threshold: float = 0.01
    lateral_inhibition: bool = False
    inhibition_factor: float = 0.1
