"""EncounterHook implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import random
from typing import TYPE_CHECKING, Any, Mapping, Protocol

from app.game_core.orchestration.event_engine import _normalize_mapping
from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult, PhaseResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules.models import Command
from app.game_core.state import StateChange

if TYPE_CHECKING:
    from app.game_core.content.registries.map_types import EncounterEntry


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
            encounter_table=context.get("encounter_table", []),
            danger_level=danger_level,
            area_state=area_state,
        )
        if selected_template is None:
            return self._noop("template_cooldown")

        return EncounterProbe(
            should_check=True,
            command_params={
                "template_id": selected_template["id"],
                "source": selected_template["source"],
                "monster_ids": list(selected_template["monster_ids"]),
                "name": selected_template["description"] or "Hostile Encounter",
                "description": (
                    selected_template["description"]
                    or "A hostile group appears nearby."
                ),
                "map_category": selected_template.get("map_category"),
                "threat_level": self._threat_level_for(
                    danger_level,
                    len(selected_template["monster_ids"]),
                ),
                "stealth_dc": self._stealth_dc_for(
                    self._threat_level_for(
                        danger_level,
                        len(selected_template["monster_ids"]),
                    )
                ),
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
        encounter_table: list[dict[str, Any]],
        danger_level: float,
        area_state: Any,
    ) -> dict[str, Any] | None:
        active_template_ids = cls._template_ids(
            area_state.get("active_template_ids") if isinstance(area_state, Mapping) else None
        )
        cooling_template_ids = cls._template_ids(
            area_state.get("cooling_template_ids") if isinstance(area_state, Mapping) else None
        )

        candidates: list[dict[str, Any]] = []
        weights: list[float] = []
        for entry in encounter_table:
            if not isinstance(entry, Mapping):
                continue
            entry_id = cls._coerce_non_empty_string(entry.get("id"))
            if entry_id is None:
                continue
            if entry_id in active_template_ids or entry_id in cooling_template_ids:
                continue
            try:
                min_danger = float(entry.get("min_danger", 0.0))
            except (TypeError, ValueError):
                min_danger = 0.0
            if danger_level < min_danger:
                continue
            try:
                weight = max(0.0, float(entry.get("weight", 1.0)))
            except (TypeError, ValueError):
                weight = 1.0
            if weight <= 0.0:
                continue
            candidates.append(entry)
            weights.append(weight)

        if not candidates:
            return None

        selected = random.choices(candidates, weights=weights, k=1)[0]
        raw_monster_ids = selected.get("monster_ids", [])
        monster_ids = list(raw_monster_ids) if isinstance(raw_monster_ids, list) else []
        return {
            "id": cls._coerce_non_empty_string(selected.get("id")) or "",
            "monster_ids": monster_ids,
            "description": cls._coerce_non_empty_string(selected.get("description")) or "",
            "map_category": cls._coerce_non_empty_string(selected.get("map_category")),
            "source": "encounter",
        }

    @staticmethod
    def _threat_level_for(danger_level: float, monster_count: int) -> str:
        score = max(float(danger_level), monster_count * 0.35)
        if score >= 1.45:
            return "deadly"
        if score >= 1.0:
            return "hard"
        if score >= 0.55:
            return "moderate"
        return "easy"

    @staticmethod
    def _stealth_dc_for(threat_level: str) -> int:
        return {
            "easy": 10,
            "moderate": 12,
            "hard": 14,
            "deadly": 16,
        }.get(threat_level, 12)


class EncounterPhase:
    """Core encounter logic extracted for Osiris coordination.

    Encapsulates all encounter detection and spawn logic previously in
    EncounterHook.execute().  AIOsirisHook calls run() as Phase 3.
    """

    DEFAULT_SLOT_CAPACITY = 1
    PROBE_REFRESH_DELAY = 6
    CLEAR_REFRESH_DELAY = 12

    def __init__(self, detector: EncounterDetector | None = None) -> None:
        self._detector = detector or BasicEncounterDetector()

    async def run(self, context: SettlementContext) -> PhaseResult:
        """Run encounter detection logic.  Returns PhaseResult (sse_events + metadata)."""
        can_evaluate, area_id, period, danger_level = EncounterHook._can_evaluate(
            context,
            action_log=context.action_log,
        )
        if not can_evaluate:
            return PhaseResult(
                metadata=EncounterHook._noop_metadata(
                    area_id=area_id,
                    period=period,
                    danger_level=danger_level,
                )
            )

        current_tick = context.state.time.absolute_tick()
        area_map = context.world.maps.get(area_id) if context.world.has_registry("maps") else None
        slot_capacity = area_map.encounter_slot_capacity if area_map is not None else self.DEFAULT_SLOT_CAPACITY
        encounter_table = area_map.encounter_table if area_map is not None else []
        slot_bucket = context.state.areas.sync_permanent_hostile_slots(
            area_id,
            current_tick=current_tick,
            default_max_slots=slot_capacity,
            clear_refresh_delay=self.CLEAR_REFRESH_DELAY,
        )
        detector_context = EncounterHook._build_detector_context(
            context,
            area_id=area_id,
            period=period,
            danger_level=danger_level,
            slot_bucket=slot_bucket,
            encounter_table=encounter_table,
        )
        try:
            raw_probe = self._detector.plan(detector_context)
        except Exception as exc:
            logger.exception(
                "phase failed: encounter",
                extra={
                    "area_id": area_id,
                    "period": period,
                },
            )
            return PhaseResult(
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

        probe = EncounterHook._normalize_probe(raw_probe)
        if not probe.should_check:
            return PhaseResult(
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
        if not result.executed:
            status = "command_failed"
        else:
            if bool(encounter_result.get("triggered")):
                sub_area_id = EncounterHook._coerce_non_empty_string(
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
                            "name": encounter_result.get("name", "Hostile Encounter"),
                            "description": encounter_result.get(
                                "description",
                                "A hostile group appears nearby.",
                            ),
                            "blocking": bool(encounter_result.get("blocking", False)),
                            "threat_level": encounter_result.get(
                                "threat_level",
                                "moderate",
                            ),
                            "monster_count": int(encounter_result.get("monster_count", 0)),
                            "map_category": encounter_result.get("map_category"),
                            "options": [
                                {"action": "enter", "label": "接近（进入战斗区域）"},
                                {"action": "retreat", "label": "原路返回"},
                            ],
                            "source": encounter_result.get("source", "encounter"),
                        },
                    )
                )
            else:
                selected_template_id = EncounterHook._coerce_non_empty_string(
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

        return PhaseResult(
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


class EncounterHook(NoOpSettlementHook):
    HOOK_PRIORITY = 40
    HOOK_NAME = "encounter"
    DEFAULT_SLOT_CAPACITY = 1
    PROBE_REFRESH_DELAY = 6
    CLEAR_REFRESH_DELAY = 12

    def __init__(self, detector: EncounterDetector | None = None) -> None:
        self._detector = detector or BasicEncounterDetector()
        self._phase = EncounterPhase(detector=self._detector)

    def should_skip(
        self,
        change_log: list[Any],
        action_log: list[dict[str, Any]] | None = None,
    ) -> bool:
        del action_log
        for change in change_log:
            slice_name = getattr(change, "slice", getattr(change, "slice_name", ""))
            if slice_name != "player":
                continue
            if getattr(change, "path", "") in {"current_area", "current_location"}:
                return False
        return True

    async def execute(self, context: SettlementContext) -> HookResult:
        """Delegate to EncounterPhase.run() — kept for backward compatibility."""
        phase_result = await self._phase.run(context)
        return HookResult(sse_events=phase_result.sse_events, metadata=phase_result.metadata)

    @staticmethod
    def _can_evaluate(
        context: SettlementContext,
        *,
        action_log: list[dict[str, Any]] | None = None,
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
        current_location = context.state.player.current_location
        if current_location is not None and not EncounterHook._is_area_entry_bridge(
            context,
            area_id=area_id,
            current_location=str(current_location),
            action_log=action_log,
        ):
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
    def _is_area_entry_bridge(
        cls,
        context: SettlementContext,
        *,
        area_id: str,
        current_location: str,
        action_log: list[dict[str, Any]] | None,
    ) -> bool:
        if not context.world.has_registry("maps"):
            return False
        auto_location = context.world.maps.resolve_auto_sub_location(area_id)
        if auto_location is None or current_location != auto_location:
            return False
        latest_action = cls._latest_action_record(action_log)
        if not isinstance(latest_action, Mapping):
            return False
        if cls._coerce_non_empty_string(latest_action.get("type")) != "move_area":
            return False
        raw_params = latest_action.get("params")
        if not isinstance(raw_params, Mapping):
            return False
        return cls._coerce_non_empty_string(raw_params.get("area_id")) == area_id

    @staticmethod
    def _latest_action_record(
        action_log: list[dict[str, Any]] | None,
    ) -> Mapping[str, Any] | None:
        if not isinstance(action_log, list):
            return None
        for candidate in reversed(action_log):
            if isinstance(candidate, Mapping):
                return candidate
        return None

    @classmethod
    def _build_detector_context(
        cls,
        context: SettlementContext,
        *,
        area_id: str,
        period: str,
        danger_level: float,
        slot_bucket: Mapping[str, Any],
        encounter_table: list[EncounterEntry],
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
            "encounter_table": [
                {
                    "id": e.id,
                    "monster_ids": list(e.monster_ids),
                    "weight": e.weight,
                    "min_danger": e.min_danger,
                    "description": e.description,
                    "map_category": (
                        context.world.maps.resolve_encounter_map_category(area_id, e)
                        if context.world.has_registry("maps")
                        else None
                    ),
                }
                for e in encounter_table
            ],
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
    def _build_combat_start_payload(
        context: SettlementContext,
        *,
        sub_area_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        participants = context.state.areas.participant_snapshots(payload)
        return {
            "sub_area_id": sub_area_id,
            "round": int(payload.get("combat_round", 1)),
            "surprise_state": str(payload.get("surprise_state", "none")),
            "blocking": bool(payload.get("blocking", False)),
            "participants": [
                {
                    "id": str(item.get("id", "")),
                    "name": str(item.get("name") or item.get("monster_id") or "Unknown"),
                    "hp": int(item.get("hp", 0)),
                    "max_hp": int(item.get("max_hp", 0)),
                    "ac": int(item.get("ac", 10)),
                    "is_player": False,
                    "status_effects": [
                        str(effect.get("effect_name") or effect.get("effect_id") or "")
                        for effect in item.get("active_effects", [])
                        if isinstance(effect, Mapping)
                    ],
                }
                for item in participants
            ],
            "player": {
                "hp": int(context.state.player.hp),
                "max_hp": int(context.state.player.max_hp),
                "ac": int(context.state.player.ac),
                "active_effects": [
                    str(effect.get("effect_name") or effect.get("effect_id") or "")
                    for effect in context.state.player.active_effects
                    if isinstance(effect, Mapping)
                ],
            },
        }

    @staticmethod
    def _coerce_non_empty_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
