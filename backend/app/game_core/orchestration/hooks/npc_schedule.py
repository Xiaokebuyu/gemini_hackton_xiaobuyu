"""NpcScheduleHook implementation."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
import logging
from typing import Any, Mapping, Protocol

from app.game_core.orchestration.event_engine import _normalize_mapping
from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.presence import room_exists
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules.models import Command


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class NpcScheduleMove:
    character_id: str
    area_id: str | None = None
    location_id: str | None = None
    room_id: str | None = None


@dataclass(slots=True)
class NpcScheduleDecision:
    moves: list[NpcScheduleMove | Mapping[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class NpcScheduleProvider(Protocol):
    def plan(
        self,
        context: dict[str, Any],
    ) -> NpcScheduleDecision | Mapping[str, Any]:
        ...


class NullNpcScheduleProvider:
    def plan(self, context: dict[str, Any]) -> NpcScheduleDecision:
        del context
        return NpcScheduleDecision(metadata={"status": "noop"})


class BasicNpcScheduleProvider:
    """Deterministic default NPC schedule provider for the runtime skeleton."""

    def plan(self, context: dict[str, Any]) -> NpcScheduleDecision:
        if not isinstance(context, Mapping):
            return self._noop(reason="invalid_context")

        if not bool(context.get("period_change_pending")):
            return self._noop(reason="stable")

        next_period = self._normalize_string(context.get("next_period"))
        raw_areas = context.get("areas", {})
        areas = raw_areas if isinstance(raw_areas, Mapping) else {}
        raw_characters = context.get("characters", [])
        characters = raw_characters if isinstance(raw_characters, list) else []
        raw_directives = context.get("npc_directives")
        npc_directives: list[Any] | None = (
            raw_directives if isinstance(raw_directives, list) else None
        )
        placements = self._placements(areas)
        valid_areas: set[str] = set(areas.keys())

        if not next_period:
            return self._noop(reason="stable")

        moves = self._collect_moves(
            characters, areas, placements, next_period, valid_areas,
            npc_directives=npc_directives,
        )
        if not moves:
            return self._noop(reason="stable")
        return NpcScheduleDecision(
            moves=moves,
            metadata={
                "status": "deterministic",
                "provider": "default_provider",
                "branch": "schedule",
                "planned_move_count": len(moves),
            },
        )

    def _collect_moves(
        self,
        characters: list[Any],
        areas: Mapping[str, Any],
        placements: dict[str, tuple[str, str | None, str | None]],
        next_period: str,
        valid_areas: set[str],
        npc_directives: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        moves: list[dict[str, Any]] = []
        for character in self._sorted_characters(characters):
            character_id = self._normalize_string(character.get("id"))
            if character_id is None:
                continue
            sched_area, sched_loc, sched_room = self._scheduled_destination(
                character, next_period, valid_areas, npc_directives=npc_directives
            )
            if sched_area is None and sched_loc is None and sched_room is None:
                continue  # no schedule entry → don't move

            # determine target area
            if sched_area is not None:
                target_area = sched_area
            else:
                target_area = (
                    self._normalize_string(character.get("area_id"))
                    or self._normalize_string(character.get("current_area"))
                )
            if target_area is None or target_area not in valid_areas:
                continue

            # check if already at destination
            existing = placements.get(character_id)
            current_area = existing[0] if existing is not None else None
            current_loc = existing[1] if existing is not None else None
            current_room = existing[2] if existing is not None else None
            if (
                current_area == target_area
                and current_loc == sched_loc
                and (sched_room is None or current_room == sched_room)
            ):
                continue

            moves.append(
                {
                    "character_id": character_id,
                    "area_id": target_area,
                    "location_id": sched_loc,
                    "room_id": sched_room,
                }
            )
        return moves

    @staticmethod
    def _scheduled_destination(
        char_data: Any,
        next_period: str,
        valid_area_ids: set[str],
        npc_directives: list[Any] | None = None,
    ) -> tuple[str | None, str | None, str | None]:
        """Return (area_id, location_id, room_id) from directives or character schedule.

        Priority: active NPC directive with destination > character schedule > global rules.
        """
        del valid_area_ids
        # Check NarrativePlanSlice.npc_directives for an active destination directive
        npc_id = char_data.get("id") if isinstance(char_data, dict) else None
        if npc_id and isinstance(npc_directives, list):
            for directive in npc_directives:
                if not isinstance(directive, Mapping):
                    continue
                if directive.get("npc_id") != npc_id or directive.get("consumed"):
                    continue
                dest = directive.get("directive", {}).get("destination")
                if isinstance(dest, Mapping) and dest.get("area_id"):
                    area_id = str(dest["area_id"]).strip() or None
                    location_id_raw = dest.get("location_id")
                    room_id_raw = dest.get("room_id")
                    loc = (
                        str(location_id_raw).strip()
                        if isinstance(location_id_raw, str) and str(location_id_raw).strip()
                        else None
                    )
                    room = (
                        str(room_id_raw).strip()
                        if isinstance(room_id_raw, str) and str(room_id_raw).strip()
                        else None
                    )
                    if area_id:
                        return (area_id, loc, room)

        # Fallback: character schedule
        sched = char_data.get("schedule") if isinstance(char_data, dict) else None
        if not isinstance(sched, dict):
            return (None, None, None)
        dest = sched.get(next_period)
        if isinstance(dest, str):
            stripped = dest.strip()
            if stripped:
                return (None, stripped, None)  # sub-location in home area
            return (None, None, None)
        if isinstance(dest, Mapping):
            raw_area = dest.get("area")
            raw_loc = dest.get("location")
            raw_room = dest.get("room")
            area = raw_area.strip() if isinstance(raw_area, str) and raw_area.strip() else None
            loc = raw_loc.strip() if isinstance(raw_loc, str) and raw_loc.strip() else None
            room = (
                raw_room.strip()
                if isinstance(raw_room, str) and raw_room.strip()
                else None
            )
            if room is not None and loc is None:
                return (None, None, None)
            if area or loc or room:
                return (area, loc, room)
            return (None, None, None)
        return (None, None, None)

    @classmethod
    def _placements(
        cls,
        areas: Mapping[str, Any],
    ) -> dict[str, tuple[str, str | None, str | None]]:
        placements: dict[str, tuple[str, str | None, str | None]] = {}
        for area_id, raw_area in areas.items():
            normalized_area_id = cls._normalize_string(area_id)
            if normalized_area_id is None or not isinstance(raw_area, Mapping):
                continue
            raw_locations = raw_area.get("npc_locations", {})
            raw_rooms = raw_area.get("npc_rooms", {})
            if not isinstance(raw_locations, Mapping):
                continue
            for character_id, raw_location in raw_locations.items():
                normalized_character_id = cls._normalize_string(character_id)
                if normalized_character_id is None:
                    continue
                location_id = (
                    cls._normalize_string(raw_location)
                    if raw_location is not None
                    else None
                )
                room_id = (
                    cls._normalize_string(
                        raw_rooms.get(character_id, raw_rooms.get(normalized_character_id))
                    )
                    if isinstance(raw_rooms, Mapping)
                    else None
                )
                placements[normalized_character_id] = (
                    normalized_area_id,
                    location_id,
                    room_id,
                )
        return placements

    @classmethod
    def _sorted_characters(cls, characters: list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = [
            {
                str(key): value
                for key, value in character.items()
            }
            for character in characters
            if isinstance(character, Mapping)
            and cls._normalize_string(character.get("id")) is not None
        ]
        normalized.sort(key=lambda item: cls._normalize_string(item.get("id")) or "")
        return normalized

    @staticmethod
    def _normalize_string(value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _noop(*, reason: str) -> NpcScheduleDecision:
        return NpcScheduleDecision(
            metadata={
                "status": "noop",
                "provider": "default_provider",
                "reason": reason,
            }
        )


class NpcScheduleHook(NoOpSettlementHook):
    HOOK_PRIORITY = 60
    HOOK_NAME = "npc_schedule"

    def __init__(self, provider: NpcScheduleProvider | None = None) -> None:
        self._provider = provider or BasicNpcScheduleProvider()

    def should_skip(
        self,
        change_log: list[Any],
        action_log: list[dict[str, Any]] | None = None,
    ) -> bool:
        del action_log
        del change_log
        return False

    async def execute(self, context: SettlementContext) -> HookResult:
        if not context.state.has_slice("time"):
            return HookResult(
                metadata=self._build_noop_metadata(
                    current_period="",
                    next_period="",
                    period_change_pending=False,
                )
            )
        if not context.state.has_slice("areas"):
            current_period = context.state.time.period
            next_period = self._predict_next_period(context.state.time.slot)
            return HookResult(
                metadata=self._build_noop_metadata(
                    current_period=current_period,
                    next_period=next_period,
                    period_change_pending=current_period != next_period,
                )
            )

        current_period = context.state.time.period
        next_period = self._predict_next_period(context.state.time.slot)
        period_change_pending = current_period != next_period
        if not period_change_pending:
            return HookResult(
                metadata=self._build_noop_metadata(
                    current_period=current_period,
                    next_period=next_period,
                    period_change_pending=False,
                )
            )
        if not context.world.has_registry("characters"):
            return HookResult(
                metadata=self._build_noop_metadata(
                    current_period=current_period,
                    next_period=next_period,
                    period_change_pending=True,
                )
            )

        candidate_templates = self._candidate_npcs(context)
        provider_context = self._build_provider_context(
            context,
            current_period=current_period,
            next_period=next_period,
            candidate_templates=candidate_templates,
        )

        try:
            raw_decision = self._provider.plan(provider_context)
        except Exception as exc:
            logger.exception(
                "hook failed: npc_schedule",
                extra={
                    "hook_name": self.HOOK_NAME,
                    "current_period": current_period,
                    "next_period": next_period,
                    "candidate_count": len(candidate_templates),
                },
            )
            return HookResult(
                sse_events=[
                    SSEEvent(
                        event_type="npc_schedule_error",
                        payload={"error": str(exc)},
                    )
                ],
                metadata={
                    "status": "provider_error",
                    "evaluated": False,
                    "current_period": current_period,
                    "next_period": next_period,
                    "period_change_pending": True,
                    "candidate_count": len(candidate_templates),
                    "requested_move_count": 0,
                    "moved_npc_count": 0,
                    "updated_area_count": 0,
                    "skipped_invalid_count": 0,
                    "moved_npc_ids": [],
                    "provider_metadata": {},
                },
            )

        decision = self._normalize_decision(raw_decision)
        requested_move_count = len(decision.moves)

        moved_npc_ids: list[str] = []
        updated_areas: set[str] = set()
        skipped_invalid_count = 0
        accepted_characters: set[str] = set()
        for raw_move in decision.moves:
            normalized_move = self._normalize_move(
                raw_move,
                context,
                candidate_templates,
                accepted_characters,
            )
            if normalized_move is None:
                skipped_invalid_count += 1
                continue
            accepted_characters.add(normalized_move.character_id)
            npc_id = normalized_move.character_id
            target_area = normalized_move.area_id or ""
            move_result = context.execute_command(Command(
                type="schedule_npc_move",
                params={
                    "npc_id": npc_id,
                    "area_id": target_area,
                    "location_id": normalized_move.location_id,
                    "room_id": normalized_move.room_id,
                    "source": "schedule",
                },
                source="system",
            ))
            if not move_result.executed:
                skipped_invalid_count += 1
                continue
            moved_npc_ids.append(npc_id)
            updated_areas.add(normalized_move.area_id or "")

        # Notify party members whose schedule says they should be elsewhere
        if context.state.has_slice("party") and context.state.has_slice("relations"):
            party_ids = set(context.state.party.members.keys())
            valid_areas = set(context.state.areas.areas.keys())
            npc_directives = (
                context.state.narrative_plan.npc_directives
                if context.state.has_slice("narrative_plan")
                else []
            )
            for member_id in party_ids:
                char_data = candidate_templates.get(member_id)
                if char_data is None:
                    continue
                sched_area, sched_loc, _ = self._provider._scheduled_destination(
                    char_data, next_period, valid_areas, npc_directives=npc_directives,
                )
                if sched_area or sched_loc:
                    dest_name = sched_loc or sched_area or "住处"
                    context.state.relations.update_blackboard(member_id, {
                        "pending_topic": f"时候不早了，{dest_name}那边还有事，我得回去了。",
                        "wants_to_leave": True,
                    })

        sse_events: list[SSEEvent] = []
        if moved_npc_ids:
            sse_events.append(
                SSEEvent(
                    event_type="npc_schedule_updated",
                    payload={
                        "next_period": next_period,
                        "moved_npc_count": len(moved_npc_ids),
                        "updated_area_count": len(updated_areas),
                        "npc_ids": list(moved_npc_ids),
                    },
                )
            )

        status = "applied" if moved_npc_ids else "noop"
        return HookResult(
            sse_events=sse_events,
            metadata={
                "status": status,
                "evaluated": True,
                "current_period": current_period,
                "next_period": next_period,
                "period_change_pending": True,
                "candidate_count": len(candidate_templates),
                "requested_move_count": requested_move_count,
                "moved_npc_count": len(moved_npc_ids),
                "updated_area_count": len(updated_areas),
                "skipped_invalid_count": skipped_invalid_count,
                "moved_npc_ids": moved_npc_ids,
                "provider_metadata": dict(decision.metadata),
            },
        )

    @staticmethod
    def _build_noop_metadata(
        *,
        current_period: str,
        next_period: str,
        period_change_pending: bool,
    ) -> dict[str, Any]:
        return {
            "status": "noop",
            "evaluated": False,
            "current_period": current_period,
            "next_period": next_period,
            "period_change_pending": period_change_pending,
            "candidate_count": 0,
            "requested_move_count": 0,
            "moved_npc_count": 0,
            "updated_area_count": 0,
            "skipped_invalid_count": 0,
            "moved_npc_ids": [],
            "provider_metadata": {},
        }

    @staticmethod
    def _predict_next_period(slot: int) -> str:
        next_slot = int(slot) + 1
        if next_slot > 24:
            next_slot = 1
        if 5 <= next_slot <= 7:
            return "dawn"
        if 8 <= next_slot <= 17:
            return "day"
        if 18 <= next_slot <= 19:
            return "dusk"
        return "night"

    @classmethod
    def _build_provider_context(
        cls,
        context: SettlementContext,
        *,
        current_period: str,
        next_period: str,
        candidate_templates: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        party_members: list[str] = []
        if context.state.has_slice("party"):
            party_members = list(context.state.party.members.keys())

        areas = {
            area_id: {
                "npc_locations": dict(area.npc_locations),
                "npc_rooms": dict(area.npc_rooms),
            }
            for area_id, area in context.state.areas.areas.items()
        }
        current_tick = context.state.time.absolute_tick()

        # Include active NPC directives so BasicNpcScheduleProvider can honor
        # destination-based directive movement (3-D).
        npc_directives: list[Any] = []
        if context.state.has_slice("narrative_plan"):
            raw = getattr(context.state.narrative_plan, "npc_directives", None)
            if isinstance(raw, list):
                npc_directives = list(raw)

        return {
            "current_period": current_period,
            "next_period": next_period,
            "period_change_pending": True,
            "absolute_tick": current_tick,
            "next_tick": current_tick + 1,
            "areas": areas,
            "characters": [dict(template) for template in candidate_templates.values()],
            "party_members": party_members,
            "npc_directives": npc_directives,
        }

    @classmethod
    def _candidate_npcs(
        cls,
        context: SettlementContext,
    ) -> dict[str, dict[str, Any]]:
        if not context.world.has_registry("characters"):
            return {}

        party_members: set[str] = set()
        if context.state.has_slice("party"):
            party_members = set(context.state.party.members.keys())

        candidates: dict[str, dict[str, Any]] = {}
        for item in context.world.characters.list_all():
            character_id = cls._coerce_non_empty_string(item.id)
            if character_id is None or character_id in party_members:
                continue
            candidates[character_id] = dataclasses.asdict(item)
        return candidates

    @classmethod
    def _normalize_decision(
        cls,
        raw_decision: NpcScheduleDecision | Mapping[str, Any],
    ) -> NpcScheduleDecision:
        if isinstance(raw_decision, NpcScheduleDecision):
            return NpcScheduleDecision(
                moves=list(raw_decision.moves),
                metadata=_normalize_mapping(raw_decision.metadata),
            )
        if not isinstance(raw_decision, Mapping):
            return NpcScheduleDecision(metadata={"status": "invalid_response"})

        raw_moves = raw_decision.get("moves", [])
        moves = list(raw_moves) if isinstance(raw_moves, list) else []
        return NpcScheduleDecision(
            moves=moves,
            metadata=_normalize_mapping(raw_decision.get("metadata")),
        )

    @classmethod
    def _normalize_move(
        cls,
        raw_move: NpcScheduleMove | Mapping[str, Any],
        context: SettlementContext,
        candidate_templates: dict[str, dict[str, Any]],
        accepted_characters: set[str],
    ) -> NpcScheduleMove | None:
        if isinstance(raw_move, NpcScheduleMove):
            character_id = cls._coerce_non_empty_string(raw_move.character_id)
            requested_area = cls._coerce_non_empty_string(raw_move.area_id)
            raw_location_id: Any = raw_move.location_id
            raw_room_id: Any = raw_move.room_id
        elif isinstance(raw_move, Mapping):
            character_id = cls._coerce_non_empty_string(raw_move.get("character_id"))
            requested_area = cls._coerce_non_empty_string(raw_move.get("area_id"))
            raw_location_id = raw_move.get("location_id")
            raw_room_id = raw_move.get("room_id")
        else:
            return None

        if character_id is None or character_id in accepted_characters:
            return None

        template = candidate_templates.get(character_id)
        if template is None:
            return None

        area_id = cls._resolve_target_area(
            requested_area,
            character_id,
            template,
            context,
        )
        if area_id is None or not cls._is_valid_area(context, area_id):
            return None

        if raw_location_id is None:
            location_id = None
        elif isinstance(raw_location_id, str):
            location_id = raw_location_id.strip() or None
            if location_id is None:
                return None
        else:
            return None

        if raw_room_id is None:
            room_id = None
        elif isinstance(raw_room_id, str):
            room_id = raw_room_id.strip() or None
            if room_id is None:
                return None
        else:
            return None

        if room_id is not None and location_id is None:
            return None

        if not cls._is_valid_location(context, area_id, location_id):
            return None
        if not cls._is_valid_room(context, area_id, location_id, room_id):
            return None

        return NpcScheduleMove(
            character_id=character_id,
            area_id=area_id,
            location_id=location_id,
            room_id=room_id,
        )

    @classmethod
    def _resolve_target_area(
        cls,
        requested_area: str | None,
        character_id: str,
        template: Mapping[str, Any],
        context: SettlementContext,
    ) -> str | None:
        if requested_area is not None:
            return requested_area

        existing_area = context.state.areas.find_npc_area(character_id)
        if existing_area is not None:
            return existing_area

        template_area = cls._coerce_non_empty_string(template.get("area_id"))
        if template_area is not None:
            return template_area

        return cls._coerce_non_empty_string(template.get("current_area"))

    @staticmethod
    def _is_valid_area(
        context: SettlementContext,
        area_id: str,
    ) -> bool:
        if context.world.has_registry("maps"):
            return context.world.maps.get(area_id) is not None
        return area_id in context.state.areas.areas

    @classmethod
    def _is_valid_location(
        cls,
        context: SettlementContext,
        area_id: str,
        location_id: str | None,
    ) -> bool:
        if location_id is None:
            return True
        if not context.world.has_registry("maps"):
            return True

        area_template = context.world.maps.get(area_id)
        if area_template is not None:
            recognized_ids = cls._recognized_location_ids(area_template.sub_locations)
            if recognized_ids is None or location_id in recognized_ids:
                return True

        area_state = context.state.areas.areas.get(area_id)
        if area_state is None:
            return False
        return any(
            cls._coerce_non_empty_string(item.get("id")) == location_id
            for item in area_state.temporary_sub_areas
            if isinstance(item, Mapping)
        )

    @staticmethod
    def _is_valid_room(
        context: SettlementContext,
        area_id: str,
        location_id: str | None,
        room_id: str | None,
    ) -> bool:
        if room_id is None:
            return True
        return room_exists(context.state, context.world, area_id, location_id, room_id)

    @classmethod
    def _recognized_location_ids(
        cls,
        raw_sub_locations: Any,
    ) -> set[str] | None:
        if raw_sub_locations is None:
            return None
        if isinstance(raw_sub_locations, Mapping):
            return {
                key
                for key in (str(item).strip() for item in raw_sub_locations.keys())
                if key
            }
        if not isinstance(raw_sub_locations, list):
            return None

        location_ids: set[str] = set()
        for item in raw_sub_locations:
            if isinstance(item, Mapping):
                location_id = cls._coerce_non_empty_string(item.get("id"))
                if location_id is not None:
                    location_ids.add(location_id)
            elif isinstance(item, str):
                location_id = item.strip()
                if location_id:
                    location_ids.add(location_id)
        return location_ids

    @staticmethod
    def _coerce_non_empty_string(value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None
