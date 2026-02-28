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
        self._load_issues: list[str] = []

    def load(self, data: dict[str, Any]) -> None:
        self._milestones = {}
        self._chapter_meta = []
        self._initial_events = []
        self._load_issues = []
        if isinstance(data, Mapping):
            raw_milestones = data.get("milestones", data)
            if isinstance(raw_milestones, Mapping):
                for key, raw in raw_milestones.items():
                    if isinstance(raw, Mapping):
                        payload = dict(raw)
                        payload.setdefault("id", str(key))
                        self._milestones[str(key)] = payload
                    else:
                        self._load_issues.append(
                            f"milestone '{key}' must be a mapping"
                        )
            elif isinstance(raw_milestones, list):
                for index, raw in enumerate(raw_milestones):
                    if isinstance(raw, Mapping):
                        milestone_id = str(raw.get("id", "")).strip()
                        if milestone_id:
                            self._milestones[milestone_id] = dict(raw)
                        else:
                            self._load_issues.append(
                                f"milestone list entry {index} missing id"
                            )
                    else:
                        self._load_issues.append(
                            f"milestone list entry {index} must be a mapping"
                        )
            else:
                self._load_issues.append("milestones must be a mapping or list")
            chapters = data.get("chapters", [])
            if isinstance(chapters, list):
                for index, item in enumerate(chapters):
                    if isinstance(item, Mapping):
                        self._chapter_meta.append(dict(item))
                    else:
                        self._load_issues.append(
                            f"chapter entry {index} must be a mapping"
                        )
            elif "chapters" in data:
                self._load_issues.append("chapters must be a list")
            initial_events = data.get("initial_events", [])
            if isinstance(initial_events, list):
                for index, item in enumerate(initial_events):
                    if isinstance(item, Mapping):
                        self._initial_events.append(dict(item))
                    else:
                        self._load_issues.append(
                            f"initial_events[{index}] must be a mapping"
                        )
            elif "initial_events" in data:
                self._load_issues.append("initial_events must be a list")

    def get(self, content_id: str) -> Any | None:
        item = self._milestones.get(content_id)
        return dict(item) if isinstance(item, dict) else item

    def list_all(self) -> list[Any]:
        return [dict(value) for value in self._milestones.values()]

    def validate(self) -> list[str]:
        issues: list[str] = list(self._load_issues)
        chapter_ids: set[str] = set()
        for index, chapter in enumerate(self._chapter_meta):
            chapter_id = self._coerce_non_empty_string(
                chapter.get("id", chapter.get("chapter_id"))
            )
            if chapter_id is None:
                issues.append(f"chapter entry {index} missing id")
                continue
            if chapter_id in chapter_ids:
                issues.append(f"duplicate chapter id '{chapter_id}'")
                continue
            chapter_ids.add(chapter_id)

        for index, chapter in enumerate(self._chapter_meta):
            # -- Chapter display fields --
            if "title" in chapter and self._coerce_non_empty_string(chapter.get("title")) is None:
                issues.append(f"chapter entry {index} has invalid title")
            if "description" in chapter and self._coerce_non_empty_string(chapter.get("description")) is None:
                issues.append(f"chapter entry {index} has invalid description")

        for item_id, item in self._milestones.items():
            if not item.get("id"):
                issues.append(f"milestone '{item_id}' missing id")
            chapter_id = item.get("chapter_id")
            if chapter_id is not None:
                normalized_chapter_id = self._coerce_non_empty_string(chapter_id)
                if normalized_chapter_id is None:
                    issues.append(f"milestone '{item_id}' has invalid chapter_id")
                elif normalized_chapter_id not in chapter_ids:
                    issues.append(
                        f"milestone '{item_id}' references unknown chapter '{normalized_chapter_id}'"
                    )

            # -- Milestone display / consumer fields --
            if "title" in item and self._coerce_non_empty_string(item.get("title")) is None:
                issues.append(f"milestone '{item_id}' has invalid title")
            if "description" in item and self._coerce_non_empty_string(item.get("description")) is None:
                issues.append(f"milestone '{item_id}' has invalid description")
            if "tags" in item and not isinstance(item.get("tags"), list):
                issues.append(f"milestone '{item_id}' has invalid tags")

            prerequisites = item.get("prerequisites")
            if prerequisites is not None:
                if not isinstance(prerequisites, list):
                    issues.append(f"milestone '{item_id}' has invalid prerequisites")
                else:
                    for p_index, prereq in enumerate(prerequisites):
                        if self._coerce_non_empty_string(prereq) is None:
                            issues.append(
                                f"milestone '{item_id}' prerequisites[{p_index}] must be a non-empty string"
                            )

            next_milestones = item.get("next_milestones")
            if next_milestones is None:
                continue
            if not isinstance(next_milestones, list):
                issues.append(f"milestone '{item_id}' has invalid next_milestones")
                continue
            for index, next_id in enumerate(next_milestones):
                normalized_next_id = self._coerce_non_empty_string(next_id)
                if normalized_next_id is None:
                    issues.append(
                        f"milestone '{item_id}' next_milestones[{index}] must be a non-empty string"
                    )
                    continue
                if normalized_next_id not in self._milestones:
                    issues.append(
                        f"milestone '{item_id}' references unknown next milestone '{normalized_next_id}'"
                    )

        for index, event in enumerate(self._initial_events):
            if "event_id" not in event and "id" not in event:
                continue
            event_id = self._coerce_non_empty_string(
                event.get("event_id", event.get("id"))
            )
            if event_id is None:
                issues.append(f"initial_events[{index}] has invalid event id")

            # -- Initial event consumer fields --
            if "event_type" in event and self._coerce_non_empty_string(event.get("event_type")) is None:
                issues.append(f"initial_events[{index}] has invalid event_type")
            for cond_field in ("conditions", "preconditions"):
                cond = event.get(cond_field)
                if cond is not None and not isinstance(cond, (list, Mapping)):
                    issues.append(f"initial_events[{index}] has invalid {cond_field}")
            if "payload" in event and not isinstance(event.get("payload"), Mapping):
                issues.append(f"initial_events[{index}] has invalid payload")
            if "metadata" in event and not isinstance(event.get("metadata"), Mapping):
                issues.append(f"initial_events[{index}] has invalid metadata")
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

    def get_chapter(self, chapter_id: str) -> dict[str, Any] | None:
        """Return a single chapter by id."""
        for chapter in self._chapter_meta:
            cid = self._coerce_non_empty_string(
                chapter.get("id", chapter.get("chapter_id"))
            )
            if cid == chapter_id:
                return dict(chapter)
        return None

    def get_initial_event(self, event_id: str) -> dict[str, Any] | None:
        """Return a single initial event by id."""
        for event in self._initial_events:
            eid = self._coerce_non_empty_string(
                event.get("id", event.get("event_id"))
            )
            if eid == event_id:
                return dict(event)
        return None

    def chapters(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._chapter_meta]

    def initial_events(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._initial_events]
