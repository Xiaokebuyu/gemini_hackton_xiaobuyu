"""World-level registry container for static content."""

from __future__ import annotations

from typing import Any

from app.game_core.content.base import ContentRegistry


class WorldInstance:
    """Container for all static content registries in one world."""

    def __init__(self, world_id: str) -> None:
        if not world_id:
            raise ValueError("world_id must not be empty")
        self.world_id = world_id
        self._registries: dict[str, ContentRegistry] = {}

    def register(self, registry: ContentRegistry) -> None:
        """Register one content registry by its canonical name."""
        name = registry.name
        if name in self._registries:
            raise ValueError(f"registry already registered: {name}")
        self._registries[name] = registry

    def get_registry(self, name: str) -> ContentRegistry:
        """Return a registered content registry."""
        try:
            return self._registries[name]
        except KeyError as exc:
            raise KeyError(f"unknown registry: {name}") from exc

    def has_registry(self, name: str) -> bool:
        """Whether a registry is registered."""
        return name in self._registries

    def all_registries(self) -> list[tuple[str, ContentRegistry]]:
        """Return registered registries in insertion order."""
        return list(self._registries.items())

    def load_all(self, world_data: dict[str, Any]) -> None:
        """Load registries in dependency order."""
        ordered_groups = [
            ["tags"],
            ["maps", "classes", "skills", "lore"],
            ["characters", "items", "monsters", "factions", "quests"],
        ]
        loaded: set[str] = set()
        for group in ordered_groups:
            for name in group:
                registry = self._registries.get(name)
                if registry is None:
                    continue
                registry.load(world_data.get(name, {}))
                loaded.add(name)

        for name, registry in self._registries.items():
            if name in loaded:
                continue
            registry.load(world_data.get(name, {}))

    def query_by_tags(
        self,
        tags: list[str],
        registry_name: str | None = None,
        match_all: bool = True,
    ) -> list[Any]:
        """Query one registry or all registries by tags."""
        if registry_name is not None:
            return self.get_registry(registry_name).query_by_tags(
                tags,
                match_all=match_all,
            )

        results: list[Any] = []
        for registry in self._registries.values():
            results.extend(registry.query_by_tags(tags, match_all=match_all))
        return results

    @property
    def tags(self) -> ContentRegistry:
        return self.get_registry("tags")

    @property
    def maps(self) -> ContentRegistry:
        return self.get_registry("maps")

    @property
    def characters(self) -> ContentRegistry:
        return self.get_registry("characters")

    @property
    def items(self) -> ContentRegistry:
        return self.get_registry("items")

    @property
    def skills(self) -> ContentRegistry:
        return self.get_registry("skills")

    @property
    def classes(self) -> ContentRegistry:
        return self.get_registry("classes")

    @property
    def monsters(self) -> ContentRegistry:
        return self.get_registry("monsters")

    @property
    def factions(self) -> ContentRegistry:
        return self.get_registry("factions")

    @property
    def lore(self) -> ContentRegistry:
        return self.get_registry("lore")

    @property
    def quests(self) -> ContentRegistry:
        return self.get_registry("quests")

    def snapshot(self) -> dict[str, Any]:
        """Return a world-level registry summary."""
        return {
            "world_id": self.world_id,
            "registries": {
                name: registry.snapshot()
                for name, registry in self._registries.items()
            },
        }

    def validate(self) -> dict[str, list[str]]:
        """Collect validation issues from all registries."""
        return {
            name: issues
            for name, registry in self._registries.items()
            if (issues := registry.validate())
        }
