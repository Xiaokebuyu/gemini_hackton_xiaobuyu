"""Abstract distance system for combat (no grid)."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, Tuple


class DistanceBand(str, Enum):
    ENGAGED = "engaged"
    CLOSE = "close"
    NEAR = "near"
    FAR = "far"
    DISTANT = "distant"


_BAND_ORDER = [
    DistanceBand.ENGAGED,
    DistanceBand.CLOSE,
    DistanceBand.NEAR,
    DistanceBand.FAR,
    DistanceBand.DISTANT,
]


@dataclass
class SimpleDistanceProvider:
    """Maintain abstract distance bands between combatants."""

    distance_map: Dict[Tuple[str, str], DistanceBand] = field(default_factory=dict)

    def initialize(self, combatant_ids: Iterable[str], allies: Iterable[str]) -> None:
        ids = list(combatant_ids)
        ally_set = set(allies)
        for i, source in enumerate(ids):
            for target in ids[i + 1 :]:
                if source == target:
                    continue
                if source in ally_set and target in ally_set:
                    band = DistanceBand.CLOSE
                elif source in ally_set or target in ally_set:
                    band = DistanceBand.NEAR
                else:
                    band = DistanceBand.CLOSE
                self.set_distance(source, target, band)

    def get_distance(self, source: str, target: str) -> DistanceBand:
        if source == target:
            return DistanceBand.ENGAGED
        key = self._key(source, target)
        return self.distance_map.get(key, DistanceBand.NEAR)

    def set_distance(self, source: str, target: str, band: DistanceBand) -> None:
        if source == target:
            return
        key = self._key(source, target)
        self.distance_map[key] = band

    def adjust_distance(self, source: str, target: str, delta: int) -> DistanceBand:
        current = self.get_distance(source, target)
        index = _BAND_ORDER.index(current)
        next_index = min(max(index + delta, 0), len(_BAND_ORDER) - 1)
        next_band = _BAND_ORDER[next_index]
        self.set_distance(source, target, next_band)
        return next_band

    def _key(self, a: str, b: str) -> Tuple[str, str]:
        return tuple(sorted((a, b)))
