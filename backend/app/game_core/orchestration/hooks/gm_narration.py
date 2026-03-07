"""GmNarrationHook implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Mapping, Protocol

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.hooks.rest_phase import (
    has_player_perceivable_rest_signals,
    resolve_rest_phase,
)
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.state.slices import SceneEntry


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GmNarrationDecision:
    entries: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class GmNarrator(Protocol):
    async def compose(
        self,
        summary: dict[str, Any],
        scene_snapshot: dict[str, Any],
    ) -> GmNarrationDecision | Mapping[str, Any]:
        ...


class NullGmNarrator:
    async def compose(
        self,
        summary: dict[str, Any],
        scene_snapshot: dict[str, Any],
    ) -> GmNarrationDecision:
        del summary, scene_snapshot
        return GmNarrationDecision(metadata={"status": "noop"})


class TemplateGmNarrator:
    """Deterministic template narrator for the default runtime."""

    _TEMPLATES: dict[str, str] = {
        "system": "The world registers the shift, whether you appreciate the omen or not.",
        "quests": "A fresh lead shifts the rhythm of the day.",
        "areas_player": "You reposition as the local situation subtly changes.",
        "areas": "The area around you settles into a new state.",
        "player": "You take stock of yourself and your next move.",
        "generic": "The situation around you settles into a new shape.",
    }

    async def compose(
        self,
        summary: dict[str, Any],
        scene_snapshot: dict[str, Any],
    ) -> GmNarrationDecision:
        del scene_snapshot
        if not isinstance(summary, Mapping):
            return GmNarrationDecision(
                metadata={"status": "noop", "reason": "invalid_summary"}
            )

        system_entries = self._normalize_entries(summary.get("system_entries", []))
        if system_entries:
            entry_contents: list[str] = []
            for entry in system_entries[:2]:
                content = entry.get("content", "")
                if not isinstance(content, str):
                    content = ""
                metadata = entry.get("metadata", {})
                reason = ""
                if isinstance(metadata, Mapping):
                    raw_reason = metadata.get("reason", "")
                    if isinstance(raw_reason, str):
                        reason = raw_reason
                normalized = content.strip() or reason.strip()
                if normalized:
                    entry_contents.append(normalized)
            if entry_contents:
                combined = " ".join(entry_contents)
                return GmNarrationDecision(
                    entries=[
                        {
                            "content": combined,
                            "visibility": "public",
                            "tags": ["gm_narration", "system_followup"],
                        }
                    ],
                    metadata={
                        "status": "templated",
                        "template_key": "system",
                    },
                )

        change_count = self._coerce_int(summary.get("change_count"), 0)
        if change_count <= 0:
            return GmNarrationDecision(
                metadata={"status": "noop", "reason": "no_changes"}
            )

        changed_slices = self._normalize_slices(summary.get("changed_slices", []))
        if not changed_slices:
            return GmNarrationDecision(
                metadata={"status": "noop", "reason": "no_changed_slices"}
            )

        template_key = self._template_key(changed_slices)
        return GmNarrationDecision(
            entries=[
                {
                    "content": self._TEMPLATES[template_key],
                    "visibility": "public",
                    "tags": changed_slices[:4],
                }
            ],
            metadata={
                "status": "templated",
                "template_key": template_key,
            },
        )

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:
        if value is None or isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _normalize_slices(raw: Any) -> list[str]:
        if not isinstance(raw, list):
            return []
        normalized: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            value = item.strip()
            if value:
                normalized.append(value)
        return normalized

    @staticmethod
    def _normalize_entries(raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        return [dict(item) for item in raw if isinstance(item, Mapping)]

    @staticmethod
    def _template_key(changed_slices: list[str]) -> str:
        changed_set = set(changed_slices)
        if "quests" in changed_set:
            return "quests"
        if "areas" in changed_set and "player" in changed_set:
            return "areas_player"
        if "areas" in changed_set:
            return "areas"
        if "player" in changed_set:
            return "player"
        return "generic"


class GmNarrationHook(NoOpSettlementHook):
    HOOK_PRIORITY = 80
    HOOK_NAME = "gm_narration"

    def __init__(self, narrator: GmNarrator | None = None) -> None:
        self._narrator = narrator or TemplateGmNarrator()

    def should_skip(
        self,
        change_log: list[Any],
        action_log: list[dict[str, Any]] | None = None,
    ) -> bool:
        del change_log
        del action_log
        return False

    async def execute(self, context: SettlementContext) -> HookResult:
        if not context.state.has_slice("scene"):
            return HookResult(metadata=self._noop_metadata())

        scene_snapshot = context.scene_bus.snapshot()
        state_changes = scene_snapshot.get("state_changes", [])
        system_entries = self._extract_system_entries(scene_snapshot)
        rest_phase = resolve_rest_phase(context)
        if rest_phase is not None and not has_player_perceivable_rest_signals(context, rest_phase):
            return HookResult(metadata=self._noop_metadata(reason="quiet_rest_slot"))
        if (not isinstance(state_changes, list) or not state_changes) and not system_entries:
            return HookResult(metadata=self._noop_metadata())

        summary = self._build_summary(context, scene_snapshot)
        try:
            raw_decision = await self._narrator.compose(summary, scene_snapshot)
        except Exception as exc:
            logger.exception(
                "hook failed: gm_narration",
                extra={
                    "hook_name": self.HOOK_NAME,
                    "input_change_count": len(state_changes),
                },
            )
            return HookResult(
                sse_events=[
                    SSEEvent(
                        event_type="gm_narration_error",
                        payload={"error": str(exc)},
                    )
                ],
                metadata={
                    "status": "narrator_error",
                    "evaluated": False,
                    "input_change_count": len(state_changes),
                    "generated_entry_count": 0,
                    "public_entry_count": 0,
                    "private_entry_count": 0,
                    "truncated_count": 0,
                    "skipped_invalid_count": 0,
                    "changed_slices": list(summary["changed_slices"]),
                    "narrator_metadata": {},
                },
            )

        decision = self._normalize_decision(raw_decision)
        current_tick = 0.0
        if context.state.has_slice("time"):
            current_tick = float(context.state.time.absolute_tick())

        generated_entries: list[dict[str, Any]] = []
        truncated_count = 0
        skipped_invalid_count = 0
        for raw_entry in decision.entries:
            if len(generated_entries) >= 3:
                truncated_count += 1
                continue
            normalized = self._normalize_entry(raw_entry, current_tick=current_tick)
            if normalized is None:
                skipped_invalid_count += 1
                continue
            context.scene_bus.add_entry(normalized)
            generated_entries.append(normalized)

        public_entry_count = sum(
            1 for entry in generated_entries if entry["visibility"] == "public"
        )
        private_entry_count = sum(
            1 for entry in generated_entries if entry["visibility"] == "private"
        )
        status = "applied" if generated_entries else "noop"
        sse_events: list[SSEEvent] = []
        if generated_entries:
            for entry in generated_entries:
                if entry["visibility"] != "public":
                    continue
                sse_events.append(
                    SSEEvent(
                        event_type="gm_narration",
                        payload={"content": entry["content"]},
                    )
                )
            sse_events.append(
                SSEEvent(
                    event_type="gm_narration_added",
                    payload={
                        "generated_entry_count": len(generated_entries),
                        "public_entry_count": public_entry_count,
                        "private_entry_count": private_entry_count,
                    },
                )
            )

        return HookResult(
            sse_events=sse_events,
            metadata={
                "status": status,
                "evaluated": True,
                "input_change_count": len(state_changes),
                "generated_entry_count": len(generated_entries),
                "public_entry_count": public_entry_count,
                "private_entry_count": private_entry_count,
                "truncated_count": truncated_count,
                "skipped_invalid_count": skipped_invalid_count,
                "changed_slices": list(summary["changed_slices"]),
                "narrator_metadata": dict(decision.metadata),
            },
        )

    @staticmethod
    def _build_summary(
        context: SettlementContext,
        scene_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        rest_phase = resolve_rest_phase(context)
        state_changes = scene_snapshot.get("state_changes", [])
        changed_slices: list[str] = []
        seen: set[str] = set()
        if isinstance(state_changes, list):
            for change in state_changes:
                if not isinstance(change, Mapping):
                    continue
                slice_name = str(change.get("slice", "")).strip()
                if not slice_name or slice_name in seen:
                    continue
                seen.add(slice_name)
                changed_slices.append(slice_name)

        time_summary: dict[str, Any] | None = None
        if context.state.has_slice("time"):
            time_summary = {
                "day": context.state.time.day,
                "slot": context.state.time.slot,
                "period": context.state.time.period,
                "absolute_tick": context.state.time.absolute_tick(),
            }

        location = {"area_id": "", "location_id": None}
        if context.state.has_slice("player"):
            location = {
                "area_id": context.state.player.current_area,
                "location_id": context.state.player.current_location,
            }

        return {
            "time": time_summary,
            "location": location,
            "change_count": len(state_changes) if isinstance(state_changes, list) else 0,
            "changed_slices": changed_slices,
            "state_changes": list(state_changes) if isinstance(state_changes, list) else [],
            "system_entries": GmNarrationHook._extract_system_entries(scene_snapshot),
            "existing_entry_count": len(scene_snapshot.get("entries", [])),
            "rest_phase": rest_phase.snapshot() if rest_phase is not None else None,
        }

    @staticmethod
    def _extract_system_entries(scene_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        raw_entries = scene_snapshot.get("entries", [])
        if not isinstance(raw_entries, list):
            return []
        entries: list[dict[str, Any]] = []
        for entry in raw_entries:
            if not isinstance(entry, Mapping):
                continue
            if str(entry.get("visibility", "")).strip().lower() != "system":
                continue
            entries.append(dict(entry))
        return entries

    @classmethod
    def _normalize_decision(cls, raw: Any) -> GmNarrationDecision:
        if isinstance(raw, GmNarrationDecision):
            return GmNarrationDecision(
                entries=list(raw.entries),
                metadata=cls._normalize_mapping(raw.metadata),
            )
        if not isinstance(raw, Mapping):
            return GmNarrationDecision(metadata={"status": "invalid_response"})
        raw_entries = raw.get("entries", [])
        entries = list(raw_entries) if isinstance(raw_entries, list) else []
        return GmNarrationDecision(
            entries=entries,
            metadata=cls._normalize_mapping(raw.get("metadata")),
        )

    @classmethod
    def _normalize_entry(
        cls,
        raw: Any,
        *,
        current_tick: float,
    ) -> dict[str, Any] | None:
        if isinstance(raw, SceneEntry):
            raw_entry: Any = raw.snapshot()
        else:
            raw_entry = raw
        if not isinstance(raw_entry, Mapping):
            return None

        content = raw_entry.get("content")
        if not isinstance(content, str):
            return None
        content = content.strip()
        if not content:
            return None

        raw_visibility = raw_entry.get("visibility", "public")
        visibility = (
            raw_visibility.strip().lower()
            if isinstance(raw_visibility, str)
            else "public"
        )
        if visibility not in {"public", "private", "system"}:
            visibility = "public"

        audience: list[str] | None = None
        if visibility == "private":
            raw_audience = raw_entry.get("audience")
            if not isinstance(raw_audience, list):
                return None
            parsed_audience = [
                item.strip()
                for item in raw_audience
                if isinstance(item, str) and item.strip()
            ]
            if not parsed_audience:
                return None
            audience = parsed_audience

        raw_tags = raw_entry.get("tags", [])
        tags = [str(tag) for tag in raw_tags][:8] if isinstance(raw_tags, list) else []

        timestamp = current_tick
        raw_timestamp = raw_entry.get("timestamp")
        if raw_timestamp is not None:
            try:
                timestamp = float(raw_timestamp)
            except (TypeError, ValueError):
                timestamp = current_tick

        return {
            "source": "gm",
            "content": content,
            "visibility": visibility,
            "audience": audience,
            "tags": tags,
            "timestamp": timestamp,
        }

    @staticmethod
    def _noop_metadata(reason: str = "noop") -> dict[str, Any]:
        return {
            "status": "noop",
            "evaluated": False,
            "reason": reason,
            "input_change_count": 0,
            "generated_entry_count": 0,
            "public_entry_count": 0,
            "private_entry_count": 0,
            "truncated_count": 0,
            "skipped_invalid_count": 0,
            "changed_slices": [],
            "narrator_metadata": {},
        }

    @staticmethod
    def _normalize_mapping(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        return {str(key): raw_value for key, raw_value in value.items()}
