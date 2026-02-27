"""QuestRegistry implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry


class QuestRegistry(ContentRegistry):
    """Registry for story milestones, not runtime quest state."""

    def __init__(self) -> None:
        super().__init__("quests")
        self._milestones: dict[str, dict[str, Any]] = {}
        self._chapter_meta: list[dict[str, Any]] = []
        self._initial_events: list[dict[str, Any]] = []

    def load(self, data: dict[str, Any]) -> None:
        self._milestones = {}
        self._chapter_meta = []
        self._initial_events = []
        if isinstance(data, Mapping):
            raw_milestones = data.get("milestones", data)
            if isinstance(raw_milestones, Mapping):
                for key, raw in raw_milestones.items():
                    if isinstance(raw, Mapping):
                        payload = dict(raw)
                        payload.setdefault("id", str(key))
                        self._milestones[str(key)] = payload
            elif isinstance(raw_milestones, list):
                for raw in raw_milestones:
                    if isinstance(raw, Mapping):
                        milestone_id = str(raw.get("id", "")).strip()
                        if milestone_id:
                            self._milestones[milestone_id] = dict(raw)
            chapters = data.get("chapters", [])
            if isinstance(chapters, list):
                self._chapter_meta = [
                    dict(item) for item in chapters if isinstance(item, Mapping)
                ]
            initial_events = data.get("initial_events", [])
            if isinstance(initial_events, list):
                self._initial_events = [
                    dict(item) for item in initial_events if isinstance(item, Mapping)
                ]

    def get(self, content_id: str) -> Any | None:
        item = self._milestones.get(content_id)
        return dict(item) if isinstance(item, dict) else item

    def list_all(self) -> list[Any]:
        return [dict(value) for value in self._milestones.values()]

    def validate(self) -> list[str]:
        issues: list[str] = []
        for item_id, item in self._milestones.items():
            if not item.get("id"):
                issues.append(f"milestone '{item_id}' missing id")
        return issues

    def get_milestone(self, milestone_id: str) -> dict[str, Any] | None:
        item = self._milestones.get(milestone_id)
        return dict(item) if isinstance(item, dict) else item

    def get_chapter_milestones(self, chapter_id: str) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in self._milestones.values()
            if item.get("chapter_id") == chapter_id
        ]

    def get_next_milestones(self, milestone_id: str) -> list[dict[str, Any]]:
        milestone = self._milestones.get(milestone_id, {})
        next_ids = milestone.get("next_milestones", [])
        if not isinstance(next_ids, list):
            return []
        return [
            dict(self._milestones[next_id])
            for next_id in next_ids
            if next_id in self._milestones
        ]

    def get_milestone_graph(self) -> dict[str, list[str]]:
        graph: dict[str, list[str]] = {}
        for milestone_id, item in self._milestones.items():
            next_ids = item.get("next_milestones", [])
            if isinstance(next_ids, list):
                graph[milestone_id] = [str(next_id) for next_id in next_ids]
            else:
                graph[milestone_id] = []
        return graph

    def get_by_tags(self, tags: list[str], match_all: bool = True) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in self.query_by_tags(tags, match_all=match_all)
            if isinstance(item, dict)
        ]

    def chapters(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._chapter_meta]

    def initial_events(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._initial_events]
