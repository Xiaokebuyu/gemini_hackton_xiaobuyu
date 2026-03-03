"""QuestRegistry implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry


@dataclass(slots=True)
class MilestoneCondition:
    """单个里程碑完成/失败条件。"""

    type: str = ""      # location_visited / flag_set / npc_talked / item_obtained / kill_count / time_elapsed
    params: dict[str, Any] = field(default_factory=dict)
    optional: bool = False


@dataclass(slots=True)
class MilestoneTemplate:
    id: str
    title: str = ""
    description: str = ""
    chapter_id: str = ""
    tags: list[str] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)
    next_milestones: list[str] = field(default_factory=list)
    # 叙事与进度字段
    completion_value: int = 10
    sequence: int = 0
    narrative_context: str = ""
    key_elements: list[str] = field(default_factory=list)
    involved_npcs: list[str] = field(default_factory=list)
    involved_locations: list[str] = field(default_factory=list)
    success_conditions: list[MilestoneCondition] = field(default_factory=list)
    failure_conditions: list[MilestoneCondition] = field(default_factory=list)
    failure_fallback: str | None = None


@dataclass(slots=True)
class ChapterMeta:
    id: str
    title: str = ""
    description: str = ""


@dataclass(slots=True)
class InitialEvent:
    id: str
    event_type: str = ""
    conditions: list[Any] | dict[str, Any] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class QuestRegistry(ContentRegistry):
    """Registry for story milestones, not runtime quest state."""

    def __init__(self) -> None:
        super().__init__("quests")
        self._milestones: dict[str, MilestoneTemplate] = {}
        self._chapter_meta: list[ChapterMeta] = []
        self._initial_events: list[InitialEvent] = []
        self._load_issues: list[str] = []

    def load(self, data: dict[str, Any]) -> None:
        self._milestones = {}
        self._chapter_meta = []
        self._initial_events = []
        self._load_issues = []
        if not isinstance(data, Mapping):
            return

        # Milestones
        raw_milestones = data.get("milestones", data)
        if isinstance(raw_milestones, Mapping):
            for key, raw in raw_milestones.items():
                if not isinstance(raw, Mapping):
                    self._load_issues.append(
                        f"milestone '{key}' must be a mapping"
                    )
                    continue
                self._milestones[str(key)] = self._build_milestone(
                    str(key), dict(raw),
                )
        elif isinstance(raw_milestones, list):
            for index, raw in enumerate(raw_milestones):
                if not isinstance(raw, Mapping):
                    self._load_issues.append(
                        f"milestone list entry {index} must be a mapping"
                    )
                    continue
                milestone_id = self._coerce_non_empty_string(raw.get("id"))
                if not milestone_id:
                    self._load_issues.append(
                        f"milestone list entry {index} missing id"
                    )
                    continue
                self._milestones[milestone_id] = self._build_milestone(
                    milestone_id, dict(raw),
                )
        else:
            self._load_issues.append("milestones must be a mapping or list")

        # Chapters
        chapters = data.get("chapters", [])
        if isinstance(chapters, list):
            for index, item in enumerate(chapters):
                if not isinstance(item, Mapping):
                    self._load_issues.append(
                        f"chapter entry {index} must be a mapping"
                    )
                    continue
                chapter = self._build_chapter(index, dict(item))
                if chapter is not None:
                    self._chapter_meta.append(chapter)
        elif "chapters" in data:
            self._load_issues.append("chapters must be a list")

        # Initial events
        initial_events = data.get("initial_events", [])
        if isinstance(initial_events, list):
            for index, item in enumerate(initial_events):
                if not isinstance(item, Mapping):
                    self._load_issues.append(
                        f"initial_events[{index}] must be a mapping"
                    )
                    continue
                event = self._build_initial_event(index, dict(item))
                if event is not None:
                    self._initial_events.append(event)
        elif "initial_events" in data:
            self._load_issues.append("initial_events must be a list")

    def get(self, content_id: str) -> MilestoneTemplate | None:
        return self._milestones.get(content_id)

    def list_all(self) -> list[MilestoneTemplate]:
        return list(self._milestones.values())

    def validate(self) -> list[str]:
        issues: list[str] = list(self._load_issues)

        # Duplicate chapter ids
        chapter_ids: set[str] = set()
        for chapter in self._chapter_meta:
            if chapter.id in chapter_ids:
                issues.append(f"duplicate chapter id '{chapter.id}'")
                continue
            chapter_ids.add(chapter.id)

        # Milestone cross-references
        for item_id, milestone in self._milestones.items():
            if milestone.chapter_id:
                if milestone.chapter_id not in chapter_ids:
                    issues.append(
                        f"milestone '{item_id}' references unknown chapter '{milestone.chapter_id}'"
                    )

            for index, next_id in enumerate(milestone.next_milestones):
                if next_id not in self._milestones:
                    issues.append(
                        f"milestone '{item_id}' references unknown next milestone '{next_id}'"
                    )

        return issues

    def get_milestone(self, milestone_id: str) -> MilestoneTemplate | None:
        return self._milestones.get(milestone_id)

    def get_chapter_milestones(self, chapter_id: str) -> list[MilestoneTemplate]:
        return [
            milestone
            for milestone in self._milestones.values()
            if milestone.chapter_id == chapter_id
        ]

    def get_next_milestones(self, milestone_id: str) -> list[MilestoneTemplate]:
        milestone = self._milestones.get(milestone_id)
        if milestone is None:
            return []
        return [
            self._milestones[next_id]
            for next_id in milestone.next_milestones
            if next_id in self._milestones
        ]

    def get_milestone_graph(self) -> dict[str, list[str]]:
        return {
            milestone_id: list(milestone.next_milestones)
            for milestone_id, milestone in self._milestones.items()
        }

    def get_by_tags(
        self, tags: list[str], match_all: bool = True,
    ) -> list[MilestoneTemplate]:
        return list(self.query_by_tags(tags, match_all=match_all))

    def get_chapter(self, chapter_id: str) -> ChapterMeta | None:
        """Return a single chapter by id."""
        for chapter in self._chapter_meta:
            if chapter.id == chapter_id:
                return chapter
        return None

    def get_initial_event(self, event_id: str) -> InitialEvent | None:
        """Return a single initial event by id."""
        for event in self._initial_events:
            if event.id == event_id:
                return event
        return None

    def chapters(self) -> list[ChapterMeta]:
        return list(self._chapter_meta)

    def initial_events(self) -> list[InitialEvent]:
        return list(self._initial_events)

    # ------------------------------------------------------------------
    # Build helpers
    # ------------------------------------------------------------------

    def _build_milestone(
        self, item_id: str, raw: dict[str, Any],
    ) -> MilestoneTemplate:
        # Validate explicit id field if present
        raw_id = raw.get("id")
        if raw_id is not None and not self._coerce_non_empty_string(raw_id):
            self._load_issues.append(f"milestone '{item_id}' missing id")

        # title
        title = ""
        raw_title = raw.get("title")
        if raw_title is not None:
            s = str(raw_title).strip()
            if not s:
                self._load_issues.append(
                    f"milestone '{item_id}' has invalid title"
                )
            else:
                title = s

        # description
        description = ""
        raw_desc = raw.get("description")
        if raw_desc is not None:
            s = str(raw_desc).strip()
            if not s:
                self._load_issues.append(
                    f"milestone '{item_id}' has invalid description"
                )
            else:
                description = s

        # chapter_id
        chapter_id = ""
        raw_chapter = raw.get("chapter_id")
        if raw_chapter is not None:
            s = self._coerce_non_empty_string(raw_chapter)
            if s is None:
                self._load_issues.append(
                    f"milestone '{item_id}' has invalid chapter_id"
                )
            else:
                chapter_id = s

        # tags
        tags: list[str] = []
        raw_tags = raw.get("tags")
        if raw_tags is not None:
            if isinstance(raw_tags, list):
                tags = [str(t) for t in raw_tags if str(t).strip()]
            else:
                self._load_issues.append(
                    f"milestone '{item_id}' has invalid tags"
                )

        # prerequisites
        prerequisites = self._load_validated_string_list(
            raw, "prerequisites", item_id,
        )

        # next_milestones
        next_milestones = self._load_validated_string_list(
            raw, "next_milestones", item_id,
        )

        completion_value = self._coerce_non_negative_int(raw.get("completion_value")) or 10
        sequence = self._coerce_non_negative_int(raw.get("sequence")) or 0
        narrative_context = str(raw.get("narrative_context", "")).strip()
        key_elements = (
            [str(e) for e in raw.get("key_elements", []) if str(e).strip()]
            if isinstance(raw.get("key_elements"), list) else []
        )
        involved_npcs = (
            [str(n) for n in raw.get("involved_npcs", []) if str(n).strip()]
            if isinstance(raw.get("involved_npcs"), list) else []
        )
        involved_locations = (
            [str(l) for l in raw.get("involved_locations", []) if str(l).strip()]
            if isinstance(raw.get("involved_locations"), list) else []
        )
        success_conditions = self._build_conditions(item_id, raw, "success_conditions")
        failure_conditions = self._build_conditions(item_id, raw, "failure_conditions")
        failure_fallback_raw = raw.get("failure_fallback")
        failure_fallback = (
            str(failure_fallback_raw).strip()
            if failure_fallback_raw and str(failure_fallback_raw).strip()
            else None
        )

        return MilestoneTemplate(
            id=item_id,
            title=title,
            description=description,
            chapter_id=chapter_id,
            tags=tags,
            prerequisites=prerequisites,
            next_milestones=next_milestones,
            completion_value=completion_value,
            sequence=sequence,
            narrative_context=narrative_context,
            key_elements=key_elements,
            involved_npcs=involved_npcs,
            involved_locations=involved_locations,
            success_conditions=success_conditions,
            failure_conditions=failure_conditions,
            failure_fallback=failure_fallback,
        )

    def _build_conditions(
        self, mid: str, raw: dict[str, Any], field_name: str,
    ) -> list[MilestoneCondition]:
        raw_conds = raw.get(field_name)
        if raw_conds is None:
            return []
        if not isinstance(raw_conds, list):
            self._load_issues.append(f"milestone '{mid}' has invalid {field_name}")
            return []
        result: list[MilestoneCondition] = []
        for idx, entry in enumerate(raw_conds):
            if not isinstance(entry, Mapping):
                self._load_issues.append(
                    f"milestone '{mid}' {field_name}[{idx}] must be a mapping"
                )
                continue
            cond_type = str(entry.get("type", "")).strip()
            if not cond_type:
                self._load_issues.append(
                    f"milestone '{mid}' {field_name}[{idx}] missing type"
                )
                continue
            params = (
                dict(entry.get("params", {}))
                if isinstance(entry.get("params"), Mapping) else {}
            )
            optional = bool(entry.get("optional", False))
            result.append(MilestoneCondition(type=cond_type, params=params, optional=optional))
        return result

    def _build_chapter(
        self, index: int, raw: dict[str, Any],
    ) -> ChapterMeta | None:
        # Merge id / chapter_id alias
        chapter_id = self._coerce_non_empty_string(
            raw.get("id", raw.get("chapter_id"))
        )
        if chapter_id is None:
            self._load_issues.append(f"chapter entry {index} missing id")
            return None

        title = ""
        raw_title = raw.get("title")
        if raw_title is not None:
            s = str(raw_title).strip()
            if not s:
                self._load_issues.append(
                    f"chapter entry {index} has invalid title"
                )
            else:
                title = s

        description = ""
        raw_desc = raw.get("description")
        if raw_desc is not None:
            s = str(raw_desc).strip()
            if not s:
                self._load_issues.append(
                    f"chapter entry {index} has invalid description"
                )
            else:
                description = s

        return ChapterMeta(id=chapter_id, title=title, description=description)

    def _build_initial_event(
        self, index: int, raw: dict[str, Any],
    ) -> InitialEvent | None:
        # Merge id / event_id alias
        event_id = self._coerce_non_empty_string(
            raw.get("id", raw.get("event_id"))
        )
        if event_id is None:
            if "event_id" in raw or "id" in raw:
                self._load_issues.append(
                    f"initial_events[{index}] has invalid event id"
                )
            return None

        # event_type
        event_type = ""
        raw_type = raw.get("event_type")
        if raw_type is not None:
            s = str(raw_type).strip()
            if not s:
                self._load_issues.append(
                    f"initial_events[{index}] has invalid event_type"
                )
            else:
                event_type = s

        # conditions — validate both fields, use first valid one
        conditions: list[Any] | dict[str, Any] = []
        found_valid = False
        for cond_field in ("preconditions", "conditions"):
            raw_cond = raw.get(cond_field)
            if raw_cond is None:
                continue
            if isinstance(raw_cond, list):
                if not found_valid:
                    conditions = list(raw_cond)
                    found_valid = True
            elif isinstance(raw_cond, Mapping):
                if not found_valid:
                    conditions = dict(raw_cond)
                    found_valid = True
            else:
                self._load_issues.append(
                    f"initial_events[{index}] has invalid {cond_field}"
                )

        # payload
        payload: dict[str, Any] = {}
        raw_payload = raw.get("payload")
        if raw_payload is not None:
            if isinstance(raw_payload, Mapping):
                payload = dict(raw_payload)
            else:
                self._load_issues.append(
                    f"initial_events[{index}] has invalid payload"
                )

        # metadata
        metadata: dict[str, Any] = {}
        raw_meta = raw.get("metadata")
        if raw_meta is not None:
            if isinstance(raw_meta, Mapping):
                metadata = dict(raw_meta)
            else:
                self._load_issues.append(
                    f"initial_events[{index}] has invalid metadata"
                )

        return InitialEvent(
            id=event_id,
            event_type=event_type,
            conditions=conditions,
            payload=payload,
            metadata=metadata,
        )

    def _load_validated_string_list(
        self,
        raw: dict[str, Any],
        field_name: str,
        item_id: str,
    ) -> list[str]:
        """Load and validate a list of non-empty strings."""
        raw_list = raw.get(field_name)
        if raw_list is None:
            return []
        if not isinstance(raw_list, list):
            self._load_issues.append(
                f"milestone '{item_id}' has invalid {field_name}"
            )
            return []
        result: list[str] = []
        for index, entry in enumerate(raw_list):
            s = self._coerce_non_empty_string(entry)
            if s is None:
                self._load_issues.append(
                    f"milestone '{item_id}' {field_name}[{index}] must be a non-empty string"
                )
            else:
                result.append(s)
        return result
