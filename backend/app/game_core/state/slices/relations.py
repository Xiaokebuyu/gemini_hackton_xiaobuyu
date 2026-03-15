"""RelationSlice implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.state.base import StateSlice
from app.game_core.state.delta import StateChange


class RelationSlice(StateSlice):
    """Relationship and disposition runtime state."""

    def __init__(self) -> None:
        super().__init__("relations")
        self.npc_dispositions: dict[str, dict[str, int]] = {}
        self.relationship_stages: dict[str, str] = {}
        self.faction_standings: dict[str, int] = {}
        self.npc_impressions: dict[str, list[str]] = {}
        self.shop_states: dict[str, dict[str, Any]] = {}
        self.npc_blackboards: dict[str, dict[str, Any]] = {}

    def restore(self, payload: Mapping[str, Any]) -> None:
        self.npc_dispositions = {
            str(key): {
                str(dim): int(value)
                for dim, value in raw.items()
            }
            for key, raw in payload.get("npc_dispositions", {}).items()
            if isinstance(raw, Mapping)
        }
        self.relationship_stages = {
            str(key): str(value)
            for key, value in payload.get("relationship_stages", {}).items()
        }
        self.faction_standings = {
            str(key): int(value)
            for key, value in payload.get("faction_standings", {}).items()
        }
        self.npc_impressions = {
            str(key): [str(item) for item in value]
            for key, value in payload.get("npc_impressions", {}).items()
            if isinstance(value, list)
        }
        self.shop_states = {
            str(key): dict(value)
            for key, value in payload.get("shop_states", {}).items()
            if isinstance(value, Mapping)
        }
        self.npc_blackboards = {
            str(k): dict(v)
            for k, v in payload.get("npc_blackboards", {}).items()
            if isinstance(v, Mapping)
        }
        self.clear_dirty()

    def serialize(self) -> dict[str, Any]:
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "npc_dispositions": {
                key: dict(value)
                for key, value in self.npc_dispositions.items()
            },
            "relationship_stages": dict(self.relationship_stages),
            "faction_standings": dict(self.faction_standings),
            "npc_impressions": {
                key: list(value)
                for key, value in self.npc_impressions.items()
            },
            "shop_states": {
                key: dict(value)
                for key, value in self.shop_states.items()
            },
            "npc_blackboards": {
                k: dict(v)
                for k, v in self.npc_blackboards.items()
            },
        }

    def get_impressions(self, npc_id: str) -> list[str]:
        return list(self.npc_impressions.get(npc_id, []))

    def add_impression(self, npc_id: str, impression: str) -> None:
        self.npc_impressions.setdefault(npc_id, []).append(impression)
        self._dirty = True

    def modify_disposition(self, npc_id: str, dimension: str, delta: int) -> None:
        bucket = self.npc_dispositions.setdefault(npc_id, {})
        bucket[dimension] = bucket.get(dimension, 0) + delta
        self._dirty = True

    def set_relationship_stage(self, npc_id: str, stage: str) -> None:
        self.relationship_stages[npc_id] = stage
        self._dirty = True

    def modify_faction(self, faction_id: str, delta: int) -> None:
        self.faction_standings[faction_id] = self.faction_standings.get(faction_id, 0) + delta
        self._dirty = True

    def update_shop_state(self, npc_id: str, state: dict[str, Any]) -> None:
        self.shop_states[npc_id] = dict(state)
        self._dirty = True

    def get_disposition(
        self, npc_id: str, dimension: str | None = None
    ) -> dict[str, int] | int | None:
        """Return NPC disposition dict or single dimension value."""
        bucket = self.npc_dispositions.get(npc_id)
        if bucket is None:
            return None
        if dimension is None:
            return dict(bucket)
        return bucket.get(dimension)

    def get_stage(self, npc_id: str) -> str | None:
        """Return relationship stage for npc_id."""
        return self.relationship_stages.get(npc_id)

    def get_faction(self, faction_id: str) -> int:
        """Return faction standing, 0 if not tracked."""
        return self.faction_standings.get(faction_id, 0)

    def get_shop_state(self, npc_id: str) -> dict[str, Any] | None:
        """Return defensive copy of shop state, or None."""
        state = self.shop_states.get(npc_id)
        if not isinstance(state, dict):
            return None
        result = dict(state)
        if isinstance(result.get("inventory"), list):
            result["inventory"] = [
                dict(item) if isinstance(item, dict) else item
                for item in result["inventory"]
            ]
        return result

    def reduce_stock(self, npc_id: str, item_id: str, count: int) -> bool:
        """Decrement shop inventory item count. Returns False if not found or insufficient."""
        if count <= 0:
            raise ValueError("count must be positive")
        shop = self.shop_states.get(npc_id)
        if not isinstance(shop, dict):
            return False
        inventory = shop.get("inventory", [])
        if not isinstance(inventory, list):
            return False
        for item in inventory:
            if not isinstance(item, dict) or item.get("item_id") != item_id:
                continue
            current = int(item.get("count", 0))
            if current < count:
                return False
            if current == count:
                inventory.remove(item)
            else:
                item["count"] = current - count
            self._dirty = True
            return True
        return False

    def validate(self) -> list[str]:
        issues: list[str] = []
        if not isinstance(self.npc_dispositions, dict):
            issues.append("npc_dispositions must be a dict")
        else:
            for npc_id, dims in self.npc_dispositions.items():
                if not isinstance(dims, dict):
                    issues.append(f"npc_dispositions[{npc_id}] must be a dict")
                    continue
                for dim, val in dims.items():
                    if not isinstance(val, int):
                        issues.append(f"npc_dispositions[{npc_id}].{dim} must be an integer")
        if not isinstance(self.relationship_stages, dict):
            issues.append("relationship_stages must be a dict")
        else:
            for npc_id, stage in self.relationship_stages.items():
                if not isinstance(stage, str):
                    issues.append(f"relationship_stages[{npc_id}] must be a string")
        if not isinstance(self.faction_standings, dict):
            issues.append("faction_standings must be a dict")
        else:
            for fid, val in self.faction_standings.items():
                if not isinstance(val, int):
                    issues.append(f"faction_standings[{fid}] must be an integer")
        if not isinstance(self.npc_impressions, dict):
            issues.append("npc_impressions must be a dict")
        else:
            for npc_id, imps in self.npc_impressions.items():
                if not isinstance(imps, list):
                    issues.append(f"npc_impressions[{npc_id}] must be a list")
        if not isinstance(self.shop_states, dict):
            issues.append("shop_states must be a dict")
        else:
            for npc_id, state in self.shop_states.items():
                if not isinstance(state, dict):
                    issues.append(f"shop_states[{npc_id}] must be a dict")
        if not isinstance(self.npc_blackboards, dict):
            issues.append("npc_blackboards must be a dict")
        else:
            for npc_id, board in self.npc_blackboards.items():
                if not isinstance(board, dict):
                    issues.append(f"npc_blackboards[{npc_id}] must be a dict")
        return issues

    def apply_state_change(self, change: StateChange) -> None:
        if change.path.startswith("npc_dispositions."):
            _, npc_id, dimension = change.path.split(".", 2)
            if change.operation == "add":
                self.modify_disposition(npc_id, dimension, int(change.value))
            else:
                bucket = self.npc_dispositions.setdefault(npc_id, {})
                bucket[dimension] = int(change.value)
                self._dirty = True
            return
        if change.path.startswith("relationship_stages."):
            _, npc_id = change.path.split(".", 1)
            self.set_relationship_stage(npc_id, str(change.value))
            return
        if change.path.startswith("faction_standings."):
            _, faction_id = change.path.split(".", 1)
            if change.operation == "add":
                self.modify_faction(faction_id, int(change.value))
            else:
                self.faction_standings[faction_id] = int(change.value)
                self._dirty = True
            return
        if change.path.startswith("npc_impressions."):
            _, npc_id = change.path.split(".", 1)
            if change.operation == "add":
                self.add_impression(npc_id, str(change.value))
            else:
                values = change.value if isinstance(change.value, list) else [change.value]
                self.npc_impressions[npc_id] = [str(item) for item in values]
                self._dirty = True
            return
        if change.path.startswith("shop_states.") and isinstance(change.value, Mapping):
            _, npc_id = change.path.split(".", 1)
            self.update_shop_state(npc_id, dict(change.value))
            return
        if change.path.startswith("npc_blackboards.") and isinstance(change.value, Mapping):
            _, npc_id = change.path.split(".", 1)
            self.npc_blackboards[npc_id] = dict(change.value)
            self._dirty = True
            return
        raise ValueError(
            f"unsupported relation state change: {change.operation} {change.path}"
        )

    def get_blackboard(self, npc_id: str) -> dict[str, Any]:
        """Return defensive copy of npc blackboard, or empty dict if not present."""
        return dict(self.npc_blackboards.get(npc_id, {}))

    def update_blackboard(self, npc_id: str, updates: dict[str, Any]) -> None:
        """Merge updates into the NPC's blackboard. Creates it if absent."""
        existing = self.npc_blackboards.setdefault(npc_id, {})
        existing.update(updates)
        self._dirty = True
