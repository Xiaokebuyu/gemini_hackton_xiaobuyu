"""EncounterHook implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Mapping, Protocol

from app.game_core.orchestration.event_engine import _normalize_mapping
from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules.models import Command
from app.game_core.state import StateChange


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EncounterProbe:
    should_check: bool = False
    command_params: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class EncounterDetector(Protocol):
    def plan(self, context: dict[str, Any]) -> EncounterProbe | Mapping[str, Any]:
        ...


class NullEncounterDetector:
    def plan(self, context: dict[str, Any]) -> EncounterProbe:
        del context
        return EncounterProbe(metadata={"status": "noop"})


class BasicEncounterDetector:
    _PROBE_THRESHOLD = 0.75
    _PERIOD_MULTIPLIERS: dict[str, float] = {
        "dawn": 0.8,
        "day": 0.5,
        "dusk": 1.0,
        "night": 1.5,
    }

    def plan(self, context: dict[str, Any] | Mapping[str, Any]) -> EncounterProbe:
        if not isinstance(context, Mapping):
            return self._noop("invalid_context")

        area_id = self._coerce_non_empty_string(context.get("area_id"))
        period = self._coerce_non_empty_string(context.get("period"))
        absolute_tick = self._coerce_int(context.get("absolute_tick"))
        danger_level = self._coerce_float(context.get("danger_level"))
        if (
            area_id is None
            or period is None
            or absolute_tick is None
            or danger_level is None
        ):
            return self._noop("invalid_context")

        if not bool(context.get("player_on_world_map")):
            return self._noop("off_world_map")

        area_state = context.get("area_state")
        active_hostile_count = self._active_hostile_count(area_state)
        if active_hostile_count > 0:
            return self._noop("active_hostile_present")

        period_multiplier = self._PERIOD_MULTIPLIERS.get(period)
        if period_multiplier is None:
            return self._noop("unsupported_period")

        trigger_score = danger_level * period_multiplier
        if trigger_score < self._PROBE_THRESHOLD:
            return self._noop("below_probe_threshold")
        available_slot_count = self._available_slot_count(area_state)
        if available_slot_count <= 0:
            return self._noop("slot_capacity_reached")

        selected_template = self._select_template(
            period=period,
            encounter_profile=context.get("encounter_profile"),
            area_state=area_state,
        )
        if selected_template is None:
            return self._noop("template_cooldown")

        return EncounterProbe(
            should_check=True,
            command_params={
                "template_id": selected_template["id"],
                "source": selected_template["source"],
            },
            metadata={
                "status": "deterministic",
                "provider": "default_detector",
                "branch": "template_probe_window",
                "probe_threshold": self._PROBE_THRESHOLD,
                "selected_template_id": selected_template["id"],
                "selected_template_source": selected_template["source"],
                "trigger_score": trigger_score,
                "period_multiplier": period_multiplier,
                "available_slot_count": available_slot_count,
            },
        )

    @staticmethod
    def _noop(reason: str) -> EncounterProbe:
        return EncounterProbe(
            metadata={
                "status": "noop",
                "provider": "default_detector",
                "reason": reason,
            }
        )

    @staticmethod
    def _coerce_non_empty_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _active_hostile_count(cls, area_state: Any) -> int:
        if not isinstance(area_state, Mapping):
            return 0
        value = cls._coerce_int(area_state.get("active_hostile_count"))
        return value if value is not None and value > 0 else 0

    @classmethod
    def _available_slot_count(cls, area_state: Any) -> int:
        if not isinstance(area_state, Mapping):
            return 0
        value = cls._coerce_int(area_state.get("available_slot_count"))
        return value if value is not None and value > 0 else 0

    @classmethod
    def _template_ids(cls, raw_ids: Any) -> set[str]:
        if not isinstance(raw_ids, list):
            return set()
        normalized: set[str] = set()
        for value in raw_ids:
            template_id = cls._coerce_non_empty_string(value)
            if template_id is not None:
                normalized.add(template_id)
        return normalized

    @classmethod
    def _select_template(
        cls,
        *,
        period: str,
        encounter_profile: Any,
        area_state: Any,
    ) -> dict[str, str] | None:
        if not isinstance(encounter_profile, Mapping):
            return None
        templates = encounter_profile.get("templates")
        if not isinstance(templates, list):
            return None

        active_template_ids = cls._template_ids(
            area_state.get("active_template_ids") if isinstance(area_state, Mapping) else None
        )
        cooling_template_ids = cls._template_ids(
            area_state.get("cooling_template_ids") if isinstance(area_state, Mapping) else None
        )

        for template in templates:
            if not isinstance(template, Mapping):
                continue
            template_id = cls._coerce_non_empty_string(template.get("id"))
            if template_id is None:
                continue
            periods: list[str] = []
            raw_periods = template.get("periods")
            if isinstance(raw_periods, list):
                for value in raw_periods:
                    normalized = cls._coerce_non_empty_string(value)
                    if normalized is not None:
                        periods.append(normalized)
            if periods and period not in periods:
                continue
            if template_id in active_template_ids or template_id in cooling_template_ids:
                continue
            source = cls._coerce_non_empty_string(template.get("source")) or "encounter"
            return {"id": template_id, "source": source}
        return None


class EncounterHook(NoOpSettlementHook):
    HOOK_PRIORITY = 40
    HOOK_NAME = "encounter"
    DEFAULT_SLOT_CAPACITY = 1
    PROBE_REFRESH_DELAY = 6
    CLEAR_REFRESH_DELAY = 12

    def __init__(self, detector: EncounterDetector | None = None) -> None:
        self._detector = detector or BasicEncounterDetector()

    def should_skip(self, change_log: list[Any]) -> bool:
        for change in change_log:
            slice_name = getattr(change, "slice", getattr(change, "slice_name", ""))
            if slice_name != "player":
                continue
            if getattr(change, "path", "") in {"current_area", "current_location"}:
                return False
        return True

    async def execute(self, context: SettlementContext) -> HookResult:
        can_evaluate, area_id, period, danger_level = self._can_evaluate(context)
        if not can_evaluate:
            return HookResult(
                metadata=self._noop_metadata(
                    area_id=area_id,
                    period=period,
                    danger_level=danger_level,
                )
            )

        current_tick = context.state.time.absolute_tick()
        encounter_profile = self._resolve_encounter_profile(context, area_id)
        slot_capacity = self._coerce_tick(encounter_profile.get("slot_capacity"))
        if slot_capacity is None:
            slot_capacity = self.DEFAULT_SLOT_CAPACITY
        slot_bucket = context.state.areas.sync_permanent_hostile_slots(
            area_id,
            current_tick=current_tick,
            default_max_slots=slot_capacity,
            clear_refresh_delay=self.CLEAR_REFRESH_DELAY,
        )
        detector_context = self._build_detector_context(
            context,
            area_id=area_id,
            period=period,
            danger_level=danger_level,
            slot_bucket=slot_bucket,
            encounter_profile=encounter_profile,
        )
        try:
            raw_probe = self._detector.plan(detector_context)
        except Exception as exc:
            logger.exception(
                "hook failed: encounter",
                extra={
                    "hook_name": self.HOOK_NAME,
                    "area_id": area_id,
                    "period": period,
                },
            )
            return HookResult(
                sse_events=[
                    SSEEvent(
                        event_type="encounter_error",
                        payload={"error": str(exc)},
                    )
                ],
                metadata={
                    "status": "detector_error",
                    "evaluated": False,
                    "checked": False,
                    "area_id": area_id,
                    "period": period,
                    "danger_level": danger_level,
                    "detector_metadata": {},
                    "encounter_result": {},
                },
            )

        probe = self._normalize_probe(raw_probe)
        if not probe.should_check:
            return HookResult(
                metadata={
                    "status": "noop",
                    "evaluated": True,
                    "checked": False,
                    "area_id": area_id,
                    "period": period,
                    "danger_level": danger_level,
                    "detector_metadata": dict(probe.metadata),
                    "encounter_result": {},
                }
            )

        command_params = dict(probe.command_params)
        command_params["area_id"] = area_id
        command_params["period"] = period
        result = context.execute_command(
            Command(
                type="encounter_check",
                params=command_params,
                source="system",
            )
        )

        encounter_result = dict(result.metadata)
        status = "checked"
        sse_events: list[SSEEvent] = []
        if not result.success:
            status = "command_failed"
        else:
            if bool(encounter_result.get("triggered")):
                sub_area_id = self._coerce_non_empty_string(
                    encounter_result.get("sub_area_id")
                )
                if sub_area_id is not None:
                    context.state.areas.occupy_permanent_hostile_slot(
                        area_id,
                        sub_area_id,
                        default_max_slots=slot_capacity,
                    )
                    context.record_change(StateChange(slice="areas", operation="set", path=f"{area_id}.permanent_hostile_slots.{sub_area_id}", value="occupied"))
                status = "triggered"
                sse_events.append(
                    SSEEvent(
                        event_type="encounter_spotted",
                        payload={
                            "area_id": encounter_result.get("area_id", area_id),
                            "sub_area_id": encounter_result.get("sub_area_id"),
                            "blocking": bool(encounter_result.get("blocking", False)),
                            "source": encounter_result.get("source", "encounter"),
                        },
                    )
                )
            else:
                selected_template_id = self._coerce_non_empty_string(
                    probe.metadata.get("selected_template_id")
                )
                context.state.areas.cooldown_permanent_hostile_slot(
                    area_id,
                    refresh_at_tick=current_tick + self.PROBE_REFRESH_DELAY,
                    used_ids=(
                        [selected_template_id]
                        if selected_template_id is not None
                        else []
                    ),
                    default_max_slots=slot_capacity,
                )
                context.record_change(StateChange(slice="areas", operation="set", path=f"{area_id}.permanent_hostile_slots", value="cooldown"))

        return HookResult(
            sse_events=sse_events,
            metadata={
                "status": status,
                "evaluated": True,
                "checked": True,
                "area_id": area_id,
                "period": period,
                "danger_level": danger_level,
                "detector_metadata": dict(probe.metadata),
                "encounter_result": encounter_result,
            },
        )

    @staticmethod
    def _can_evaluate(
        context: SettlementContext,
    ) -> tuple[bool, str, str, float]:
        if not context.state.has_slice("player"):
            return False, "", "", 0.0
        if not context.state.has_slice("areas"):
            return False, "", "", 0.0
        if not context.state.has_slice("time"):
            return False, "", "", 0.0

        area_id = context.state.player.current_area
        period = context.state.time.period
        if not area_id:
            return False, "", period, 0.0
        if context.state.player.current_location is not None:
            return False, area_id, period, 0.0

        if context.world.has_registry("maps"):
            if context.world.maps.get(area_id) is None:
                return False, area_id, period, 0.0
        elif area_id not in context.state.areas.areas:
            return False, area_id, period, 0.0

        danger_level = float(context.state.areas.get_danger(area_id))
        if danger_level <= 0.0:
            return False, area_id, period, danger_level
        return True, area_id, period, danger_level

    @classmethod
    def _build_detector_context(
        cls,
        context: SettlementContext,
        *,
        area_id: str,
        period: str,
        danger_level: float,
        slot_bucket: Mapping[str, Any],
        encounter_profile: Mapping[str, Any],
    ) -> dict[str, Any]:
        area_state = context.state.areas.get_area(area_id)
        hostile_count = len(area_state.hostile_tracking)
        active_hostile_count = cls._count_active_hostiles(area_state.hostile_tracking)
        slot_capacity = cls._coerce_tick(slot_bucket.get("max_slots")) or 0
        raw_active_ids = slot_bucket.get("active_ids", [])
        active_hostile_ids: list[str] = []
        if isinstance(raw_active_ids, list):
            for value in raw_active_ids:
                hostile_id = cls._coerce_non_empty_string(value)
                if hostile_id is not None:
                    active_hostile_ids.append(hostile_id)
        active_slot_count = len(active_hostile_ids)
        available_slot_count = max(0, slot_capacity - active_slot_count)
        active_template_ids: list[str] = []
        seen_active_templates: set[str] = set()
        for hostile_id in active_hostile_ids:
            hostile_state = context.state.areas.get_hostile_state(hostile_id)
            if not isinstance(hostile_state, Mapping):
                continue
            template_id = cls._coerce_non_empty_string(hostile_state.get("template_id"))
            if template_id is None or template_id in seen_active_templates:
                continue
            seen_active_templates.add(template_id)
            active_template_ids.append(template_id)

        cooling_template_ids: list[str] = []
        seen_cooling_templates: set[str] = set()
        next_refresh_tick = None
        refresh_entries = slot_bucket.get("refresh_queue", [])
        if isinstance(refresh_entries, list):
            refresh_ticks: list[int] = []
            for entry in refresh_entries:
                if not isinstance(entry, Mapping):
                    continue
                refresh_at_tick = cls._coerce_tick(entry.get("refresh_at_tick"))
                if refresh_at_tick is not None:
                    refresh_ticks.append(refresh_at_tick)
                used_template_ids = entry.get("used_template_ids")
                if not isinstance(used_template_ids, list):
                    continue
                for value in used_template_ids:
                    template_id = cls._coerce_non_empty_string(value)
                    if (
                        template_id is None
                        or template_id in seen_cooling_templates
                    ):
                        continue
                    seen_cooling_templates.add(template_id)
                    cooling_template_ids.append(template_id)
            if refresh_ticks:
                next_refresh_tick = min(refresh_ticks)
        return {
            "area_id": area_id,
            "period": period,
            "absolute_tick": context.state.time.absolute_tick(),
            "danger_level": danger_level,
            "player_on_world_map": True,
            "encounter_profile": dict(encounter_profile),
            "area_state": {
                "danger_level": danger_level,
                "hostile_count": hostile_count,
                "active_hostile_count": active_hostile_count,
                "slot_capacity": slot_capacity,
                "active_slot_count": active_slot_count,
                "available_slot_count": available_slot_count,
                "active_template_ids": active_template_ids,
                "cooling_template_ids": cooling_template_ids,
                "next_refresh_tick": next_refresh_tick,
            },
        }

    @classmethod
    def _resolve_encounter_profile(
        cls,
        context: SettlementContext,
        area_id: str,
    ) -> dict[str, Any]:
        fallback = {
            "slot_capacity": cls.DEFAULT_SLOT_CAPACITY,
            "templates": [
                {
                    "id": f"{area_id}:ambient",
                    "periods": [],
                    "source": "encounter",
                }
            ],
        }
        if not context.world.has_registry("maps"):
            return fallback

        raw_map = context.world.maps.get(area_id)
        if raw_map is None:
            return fallback
        raw_profile = raw_map.encounter_profile
        if not isinstance(raw_profile, Mapping):
            return fallback

        slot_capacity = cls._coerce_tick(raw_profile.get("slot_capacity"))
        if slot_capacity is None:
            slot_capacity = cls.DEFAULT_SLOT_CAPACITY
        slot_capacity = max(0, slot_capacity)

        normalized_templates: list[dict[str, Any]] = []
        raw_templates = raw_profile.get("templates")
        if isinstance(raw_templates, list):
            for raw_template in raw_templates:
                if not isinstance(raw_template, Mapping):
                    continue
                template_id = cls._coerce_non_empty_string(raw_template.get("id"))
                if template_id is None:
                    continue
                periods: list[str] = []
                raw_periods = raw_template.get("periods")
                if isinstance(raw_periods, list):
                    for value in raw_periods:
                        period = cls._coerce_non_empty_string(value)
                        if period is not None:
                            periods.append(period)
                source = cls._coerce_non_empty_string(raw_template.get("source")) or "encounter"
                normalized_templates.append(
                    {
                        "id": template_id,
                        "periods": periods,
                        "source": source,
                    }
                )

        if not normalized_templates:
            return fallback
        return {
            "slot_capacity": slot_capacity,
            "templates": normalized_templates,
        }

    @classmethod
    def _normalize_probe(
        cls,
        raw_probe: EncounterProbe | Mapping[str, Any],
    ) -> EncounterProbe:
        if isinstance(raw_probe, EncounterProbe):
            return EncounterProbe(
                should_check=bool(raw_probe.should_check),
                command_params=dict(raw_probe.command_params),
                metadata=_normalize_mapping(raw_probe.metadata),
            )
        if not isinstance(raw_probe, Mapping):
            return EncounterProbe(metadata={"status": "invalid_response"})
        return EncounterProbe(
            should_check=bool(raw_probe.get("should_check", False)),
            command_params=_normalize_mapping(raw_probe.get("command_params")),
            metadata=_normalize_mapping(raw_probe.get("metadata")),
        )

    @staticmethod
    def _count_active_hostiles(hostile_tracking: Mapping[str, Any]) -> int:
        count = 0
        for hostile_state in hostile_tracking.values():
            if not isinstance(hostile_state, Mapping):
                continue
            status = str(hostile_state.get("status", "active")).strip().lower()
            cleared = bool(hostile_state.get("cleared", False))
            if status == "cleared" or cleared:
                continue
            count += 1
        return count

    @staticmethod
    def _noop_metadata(
        *,
        area_id: str,
        period: str,
        danger_level: float,
    ) -> dict[str, Any]:
        return {
            "status": "noop",
            "evaluated": False,
            "checked": False,
            "area_id": area_id,
            "period": period,
            "danger_level": danger_level,
            "detector_metadata": {},
            "encounter_result": {},
        }

    @staticmethod
    def _coerce_tick(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_non_empty_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
