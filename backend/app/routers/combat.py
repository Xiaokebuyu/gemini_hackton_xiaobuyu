"""Combat and encounter routes."""

from __future__ import annotations

import logging
from typing import Any, Mapping

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api_models import StructuredActionRequest
from app.deps import (
    _api_error,
    _execute_structured_action,
    _load_session_or_404,
    get_admin_coordinator,
    get_llm_provider,
)
from app.game_core import ManagedSession
from app.game_core.orchestration.models import PipelineResult, SSEEvent
from app.game_core.rules.models import Command, DiceRoll
from app.routers.gameplay import (
    _build_action_result_event,
    _build_stream_end_event,
    _stream_with_lock,
)
from app.combat_helpers import (
    COMBAT_ACTION_TO_COMMAND as _COMBAT_ACTION_TO_COMMAND,
    compute_companion_decision as _compute_companion_decision_helper,
    compute_enemy_decision as _compute_enemy_decision_helper,
    get_current_unit as _get_current_unit,
    resolve_enemy_ai_tier as _resolve_enemy_ai_tier,
    restore_fallen_companions as _restore_fallen_companions_helper,
)
from app.scene_views import build_location_overview, build_scene_change

logger = logging.getLogger(__name__)

router = APIRouter()

_ALLOWED_COMBAT_ACTIONS = {
    "attack",
    "defend",
    "disengage",
    "dash",
    "shove",
    "flee",
    "use_combat_item",
    "offhand_attack",
    "stand_up",
}
_TARGETED_COMBAT_ACTIONS = {"attack", "shove", "offhand_attack"}
_ALLOWED_ENCOUNTER_CHOICES = {"enter", "surprise_attack", "sneak_through", "retreat"}
_ENCOUNTER_CHOICE_ALIASES = {
    "ambush": "surprise_attack",
    "stealth_pass": "sneak_through",
}

_MAX_NPC_ADVANCE = 20  # 安全上限，防止无限循环


class CombatActionRequest(BaseModel):
    action_type: str
    params: dict[str, Any] = Field(default_factory=dict)


class CombatMoveRequest(BaseModel):
    """Request body for /combat/move."""
    target: list[int]  # [col, row]
    params: dict[str, Any] = Field(default_factory=dict)


class EncounterActionRequest(BaseModel):
    choice: str
    sub_area_id: str


@router.get("/api/game/{world_id}/sessions/{session_id}/combat")
async def get_combat_state(
    world_id: str,
    session_id: str,
) -> dict[str, Any]:
    """Return the current combat snapshot, if any."""
    session = await _load_session_or_404(world_id, session_id)
    active = _find_active_combat(session)
    if active is None:
        return {"active": False}
    sub_area_id, payload = active
    return {
        "active": True,
        **_build_combat_snapshot(session, sub_area_id=sub_area_id, payload=payload),
    }


@router.post("/api/game/{world_id}/sessions/{session_id}/combat/action")
async def combat_action(
    world_id: str,
    session_id: str,
    request: CombatActionRequest,
):
    """Execute one combat action and stream synthesized combat SSE events."""
    action_type = request.action_type.strip()
    if action_type not in _ALLOWED_COMBAT_ACTIONS:
        raise _api_error(400, "invalid_combat_action", "unknown combat action")

    session = await _load_session_or_404(world_id, session_id)
    if _find_active_combat(session) is None:
        raise _api_error(409, "combat_not_active", "no active combat")

    async def _execute(
        session: ManagedSession,
        queue: Any,
    ) -> None:
        active = _find_active_combat(session)
        if active is None:
            raise _api_error(409, "combat_not_active", "no active combat")
        sub_area_id, before_payload = active
        params = dict(request.params)
        params.setdefault("sub_area_id", sub_area_id)
        if action_type in _TARGETED_COMBAT_ACTIONS:
            target = _non_empty_string(params.get("target"))
            if target is None:
                target = _first_alive_target_id(before_payload)
            if target is None:
                raise _api_error(409, "combat_not_active", "no available combat target")
            params["target"] = target

        # D-1: Translate frontend action_type to v2 command type
        v2_command = _COMBAT_ACTION_TO_COMMAND.get(action_type, action_type)

        before_player_hp = int(session.runtime.state.player.hp)
        structured = StructuredActionRequest(
            action_type=v2_command,
            params=params,
        )
        result = await _execute_structured_action(session, structured)

        after_payload = _get_hostile_payload(session, sub_area_id)
        if int(session.runtime.state.player.hp) <= 0 and after_payload is not None:
            await _deactivate_combat(
                session,
                sub_area_id=sub_area_id,
                status="player_defeated",
            )
            after_payload = _get_hostile_payload(session, sub_area_id)

        # D-3: Auto-advance NPC turns (replaces dead advance_combat_round call)
        if (
            result.executed
            and after_payload is not None
            and bool(after_payload.get("combat_active", False))
            and int(session.runtime.state.player.hp) > 0
        ):
            await _auto_advance_npc_turns(session, sub_area_id, queue)
            after_payload = _get_hostile_payload(session, sub_area_id)

        await _emit_roll_events(
            queue,
            result,
            session,
            fallback_player_ac=max(10, int(session.runtime.state.player.ac)),
        )
        await queue.put(_combat_action_result_event(action_type, result))
        await _emit_status_updates(
            queue,
            before_payload=before_payload,
            after_payload=after_payload,
            before_player_hp=before_player_hp,
            after_player_hp=int(session.runtime.state.player.hp),
            player_max_hp=int(session.runtime.state.player.max_hp),
            cause=action_type,
        )

        if not result.executed:
            pass
        elif after_payload is not None and bool(after_payload.get("combat_active", False)):
            await queue.put(
                SSEEvent(
                    "combat_update",
                    _build_combat_update_payload(
                        session,
                        sub_area_id=sub_area_id,
                        payload=after_payload,
                    ),
                )
            )
        else:
            end_result = _combat_end_result(
                action_type=action_type,
                result=result,
                player_hp=int(session.runtime.state.player.hp),
                final_payload=after_payload,
            )
            loot_payload = None
            if end_result == "victory":
                # D-9: Restore fallen companions to 50% HP after victory
                before_payload_for_restore = after_payload or before_payload
                await _restore_fallen_companions(session, before_payload_for_restore)
                loot_payload = await _generate_loot(session, payload=before_payload_for_restore)
            await queue.put(
                SSEEvent(
                    "combat_end",
                    {
                        "result": end_result,
                        "sub_area_id": sub_area_id,
                        "xp_gained": int(result.metadata.get("xp_awarded", 0)),
                        "gold_gained": int((loot_payload or {}).get("gold", 0)),
                        "rounds_fought": _rounds_fought(after_payload or before_payload),
                    },
                )
            )
            if loot_payload is not None:
                await queue.put(SSEEvent("loot_display", loot_payload))

        await queue.put(SSEEvent("location_overview", build_location_overview(session)))
        await queue.put(_build_stream_end_event("completed", result.executed))

    return await _stream_with_lock(world_id, session_id, _execute)


# D-2: /combat/move endpoint (W3-2)
@router.post("/api/game/{world_id}/sessions/{session_id}/combat/move")
async def combat_move(
    world_id: str,
    session_id: str,
    request: CombatMoveRequest,
):
    """Move a unit on the combat grid."""
    session = await _load_session_or_404(world_id, session_id)
    if _find_active_combat(session) is None:
        raise _api_error(409, "combat_not_active", "no active combat")

    async def _execute(
        session: ManagedSession,
        queue: Any,
    ) -> None:
        active = _find_active_combat(session)
        if active is None:
            raise _api_error(409, "combat_not_active", "no active combat")
        sub_area_id, before_payload = active
        params = dict(request.params)
        params.setdefault("sub_area_id", sub_area_id)
        params["target"] = list(request.target)

        before_player_hp = int(session.runtime.state.player.hp)
        structured = StructuredActionRequest(
            action_type="combat_move",
            params=params,
        )
        result = await _execute_structured_action(session, structured)
        after_payload = _get_hostile_payload(session, sub_area_id)

        # D-3: Auto-advance NPC turns after player move
        if (
            result.executed
            and after_payload is not None
            and bool(after_payload.get("combat_active", False))
            and int(session.runtime.state.player.hp) > 0
        ):
            await _auto_advance_npc_turns(session, sub_area_id, queue)
            after_payload = _get_hostile_payload(session, sub_area_id)

        await _emit_roll_events(
            queue,
            result,
            session,
            fallback_player_ac=max(10, int(session.runtime.state.player.ac)),
        )
        await queue.put(_combat_action_result_event("combat_move", result))
        await _emit_status_updates(
            queue,
            before_payload=before_payload,
            after_payload=after_payload,
            before_player_hp=before_player_hp,
            after_player_hp=int(session.runtime.state.player.hp),
            player_max_hp=int(session.runtime.state.player.max_hp),
            cause="move",
        )
        if result.executed and after_payload is not None and bool(after_payload.get("combat_active", False)):
            await queue.put(
                SSEEvent(
                    "combat_update",
                    _build_combat_update_payload(
                        session,
                        sub_area_id=sub_area_id,
                        payload=after_payload,
                    ),
                )
            )
        await queue.put(SSEEvent("location_overview", build_location_overview(session)))
        await queue.put(_build_stream_end_event("completed", result.executed))

    return await _stream_with_lock(world_id, session_id, _execute)


# D-2: /combat/end_turn endpoint (W3-2)
@router.post("/api/game/{world_id}/sessions/{session_id}/combat/end_turn")
async def combat_end_turn(
    world_id: str,
    session_id: str,
):
    """End the current unit's turn."""
    session = await _load_session_or_404(world_id, session_id)
    if _find_active_combat(session) is None:
        raise _api_error(409, "combat_not_active", "no active combat")

    async def _execute(
        session: ManagedSession,
        queue: Any,
    ) -> None:
        active = _find_active_combat(session)
        if active is None:
            raise _api_error(409, "combat_not_active", "no active combat")
        sub_area_id, before_payload = active

        before_player_hp = int(session.runtime.state.player.hp)
        result = await _execute_command(
            session,
            Command(
                type="combat_end_turn",
                params={"sub_area_id": sub_area_id},
                source="system",
            ),
        )
        after_payload = _get_hostile_payload(session, sub_area_id)

        # D-3: Auto-advance NPC turns after player ends turn
        if (
            result.executed
            and after_payload is not None
            and bool(after_payload.get("combat_active", False))
            and int(session.runtime.state.player.hp) > 0
        ):
            await _auto_advance_npc_turns(session, sub_area_id, queue)
            after_payload = _get_hostile_payload(session, sub_area_id)

        await queue.put(_combat_action_result_event("combat_end_turn", result))
        await _emit_status_updates(
            queue,
            before_payload=before_payload,
            after_payload=after_payload,
            before_player_hp=before_player_hp,
            after_player_hp=int(session.runtime.state.player.hp),
            player_max_hp=int(session.runtime.state.player.max_hp),
            cause="end_turn",
        )
        if result.executed and after_payload is not None and bool(after_payload.get("combat_active", False)):
            await queue.put(
                SSEEvent(
                    "combat_update",
                    _build_combat_update_payload(
                        session,
                        sub_area_id=sub_area_id,
                        payload=after_payload,
                    ),
                )
            )
        await queue.put(SSEEvent("location_overview", build_location_overview(session)))
        await queue.put(_build_stream_end_event("completed", result.executed))

    return await _stream_with_lock(world_id, session_id, _execute)


@router.post("/api/game/{world_id}/sessions/{session_id}/encounter/action")
async def encounter_action(
    world_id: str,
    session_id: str,
    request: EncounterActionRequest,
):
    """Resolve one encounter choice across spotted and stealth-resolved phases."""
    choice = _normalize_encounter_choice(request.choice)
    sub_area_id = request.sub_area_id.strip()
    if choice not in _ALLOWED_ENCOUNTER_CHOICES:
        raise _api_error(400, "invalid_encounter_choice", "unknown encounter choice")
    if not sub_area_id:
        raise _api_error(400, "invalid_encounter_choice", "sub_area_id is required")

    session = await _load_session_or_404(world_id, session_id)
    payload = _get_hostile_payload(session, sub_area_id)
    if payload is None:
        raise _api_error(404, "encounter_not_found", "encounter not found")

    async def _execute(
        session: ManagedSession,
        queue: Any,
    ) -> None:
        payload = _get_hostile_payload(session, sub_area_id)
        if payload is None:
            raise _api_error(404, "encounter_not_found", "encounter not found")
        status = _payload_status(payload)
        if bool(payload.get("cleared", False)):
            await queue.put(
                _manual_action_result(
                    choice,
                    executed=False,
                    errors=["encounter already cleared"],
                    metadata={"sub_area_id": sub_area_id},
                )
            )
            await queue.put(SSEEvent("location_overview", build_location_overview(session)))
            await queue.put(_build_stream_end_event("completed", False))
            return
        if bool(payload.get("combat_active", False)):
            await queue.put(
                _manual_action_result(
                    choice,
                    executed=False,
                    errors=["combat already started"],
                    metadata={"sub_area_id": sub_area_id},
                )
            )
            await queue.put(SSEEvent("location_overview", build_location_overview(session)))
            await queue.put(_build_stream_end_event("completed", False))
            return

        if status == "spotted":
            if choice == "retreat":
                await queue.put(
                    _manual_action_result(
                        choice,
                        executed=True,
                        metadata={"sub_area_id": sub_area_id},
                    )
                )
                await queue.put(SSEEvent("location_overview", build_location_overview(session)))
                await queue.put(_build_stream_end_event("completed", True))
                return
            if choice != "enter":
                await queue.put(
                    _manual_action_result(
                        choice,
                        executed=False,
                        errors=["encounter must be entered before this action"],
                        metadata={"sub_area_id": sub_area_id},
                    )
                )
                await queue.put(SSEEvent("location_overview", build_location_overview(session)))
                await queue.put(_build_stream_end_event("completed", False))
                return

            enter_result = await _execute_structured_action(
                session,
                StructuredActionRequest(
                    action_type="enter_sub_location",
                    params={"location_id": sub_area_id},
                ),
            )
            await queue.put(_build_action_result_event(enter_result, "enter"))
            if enter_result.executed:
                await queue.put(SSEEvent("scene_change", build_scene_change(session)))
                await queue.put(SSEEvent("location_overview", build_location_overview(session)))
                await _emit_hostile_entry_events(queue, session, sub_area_id=sub_area_id)
            else:
                await queue.put(SSEEvent("location_overview", build_location_overview(session)))
            await queue.put(_build_stream_end_event("completed", enter_result.executed))
            return

        stealth = payload.get("last_stealth_result")
        if status != "stealth_resolved" or not isinstance(stealth, Mapping) or not bool(
            stealth.get("passed", False)
        ):
            await queue.put(
                _manual_action_result(
                    choice,
                    executed=False,
                    errors=["encounter is not ready for follow-up actions"],
                    metadata={"sub_area_id": sub_area_id},
                )
            )
            await queue.put(SSEEvent("location_overview", build_location_overview(session)))
            await queue.put(_build_stream_end_event("completed", False))
            return

        if choice == "surprise_attack":
            start_result = await _execute_command(
                session,
                Command(
                    type="start_combat",
                    params={
                        "sub_area_id": sub_area_id,
                        "surprise_state": str(stealth.get("surprise_state", "player_surprise")),
                    },
                    source="system",
                ),
            )
            await queue.put(
                _manual_action_result(
                    choice,
                    executed=start_result.executed,
                    errors=list(start_result.errors),
                    metadata={"sub_area_id": sub_area_id},
                )
            )
            if start_result.executed:
                payload = _get_hostile_payload(session, sub_area_id)
                if payload is not None:
                    await queue.put(
                        SSEEvent(
                            "combat_start",
                            _build_combat_start_payload(
                                session,
                                sub_area_id=sub_area_id,
                                payload=payload,
                            ),
                        )
                    )
            await queue.put(SSEEvent("location_overview", build_location_overview(session)))
            await queue.put(_build_stream_end_event("completed", start_result.executed))
            return

        if choice == "sneak_through":
            if bool(payload.get("blocking", False)):
                await queue.put(
                    _manual_action_result(
                        choice,
                        executed=False,
                        errors=["blocking encounters cannot be bypassed"],
                        metadata={"sub_area_id": sub_area_id},
                    )
                )
                await queue.put(SSEEvent("location_overview", build_location_overview(session)))
                await queue.put(_build_stream_end_event("completed", False))
                return
            mark_result = await _execute_command(
                session,
                Command(
                    type="record_hostile_stealth_choice",
                    params={"sub_area_id": sub_area_id, "choice": choice},
                    source="system",
                ),
            )
            await queue.put(
                _manual_action_result(
                    choice,
                    executed=mark_result.executed,
                    errors=list(mark_result.errors),
                    metadata={"sub_area_id": sub_area_id},
                )
            )
            await queue.put(SSEEvent("location_overview", build_location_overview(session)))
            await queue.put(_build_stream_end_event("completed", mark_result.executed))
            return

        if choice == "retreat":
            leave_result = await _execute_structured_action(
                session,
                StructuredActionRequest(
                    action_type="leave_sub_location",
                    params={},
                ),
            )
            if leave_result.executed:
                reset_result = await _reset_hostile_to_spotted(
                    session,
                    sub_area_id=sub_area_id,
                    last_choice=choice,
                )
                if not reset_result.executed:
                    logger.warning(
                        "failed to reset hostile %s after retreat: %s",
                        sub_area_id,
                        list(reset_result.errors),
                    )
            await queue.put(
                _manual_action_result(
                    choice,
                    executed=leave_result.executed,
                    errors=list(leave_result.errors),
                    metadata={"sub_area_id": sub_area_id},
                )
            )
            if leave_result.executed:
                await queue.put(SSEEvent("scene_change", build_scene_change(session)))
            await queue.put(SSEEvent("location_overview", build_location_overview(session)))
            await queue.put(_build_stream_end_event("completed", leave_result.executed))
            return

        await queue.put(
            _manual_action_result(
                choice,
                executed=False,
                errors=["unsupported encounter action"],
                metadata={"sub_area_id": sub_area_id},
            )
        )
        await queue.put(SSEEvent("location_overview", build_location_overview(session)))
        await queue.put(_build_stream_end_event("completed", False))

    return await _stream_with_lock(world_id, session_id, _execute)


def _find_active_combat(
    session: ManagedSession,
) -> tuple[str, dict[str, Any]] | None:
    current_area = _non_empty_string(session.runtime.state.player.current_area)
    if current_area is None:
        return None
    area_state = session.runtime.state.areas.areas.get(current_area)
    if area_state is None:
        return None
    for sub_area_id, raw_payload in area_state.hostile_tracking.items():
        if not isinstance(raw_payload, Mapping):
            continue
        if not bool(raw_payload.get("combat_active", False)):
            continue
        return str(sub_area_id), session.runtime.state.areas.copy_hostile_state(raw_payload)
    return None


def _get_hostile_payload(
    session: ManagedSession,
    sub_area_id: str,
) -> dict[str, Any] | None:
    payload = session.runtime.state.areas.get_hostile_state(sub_area_id)
    if not isinstance(payload, Mapping):
        return None
    return session.runtime.state.areas.copy_hostile_state(payload)


def _payload_status(payload: Mapping[str, Any]) -> str:
    status = _non_empty_string(payload.get("status"))
    return status or "spotted"


def _normalize_encounter_choice(choice: str) -> str:
    normalized = choice.strip()
    return _ENCOUNTER_CHOICE_ALIASES.get(normalized, normalized)


def _build_combat_snapshot(
    session: ManagedSession,
    *,
    sub_area_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "sub_area_id": sub_area_id,
        "round": int(payload.get("combat_round", 1)),
        "surprise_state": str(payload.get("surprise_state", "none")),
        "blocking": bool(payload.get("blocking", False)),
        "participants": _participant_cards(payload, include_is_dead=True),
        "player": _player_card(session),
    }


def _build_combat_start_payload(
    session: ManagedSession,
    *,
    sub_area_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "sub_area_id": sub_area_id,
        "round": int(payload.get("combat_round", 1)),
        "surprise_state": str(payload.get("surprise_state", "none")),
        "blocking": bool(payload.get("blocking", False)),
        "participants": _participant_cards(payload, include_is_dead=False),
        "player": _player_card(session),
    }


def _build_combat_update_payload(
    session: ManagedSession,
    *,
    sub_area_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "sub_area_id": sub_area_id,
        "round": int(payload.get("combat_round", 1)),
        "participants": _participant_cards(payload, include_is_dead=True),
        "player": _player_card(session),
        "combat_cleared": bool(payload.get("cleared", False)),
        "fled": False,
    }


def _participant_cards(
    payload: Mapping[str, Any],
    *,
    include_is_dead: bool,
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    raw_participants = payload.get("participants", [])
    if not isinstance(raw_participants, list):
        return cards
    for item in raw_participants:
        if not isinstance(item, Mapping):
            continue
        card = {
            "id": str(item.get("id", "")),
            "name": str(item.get("name") or item.get("monster_id") or "Unknown"),
            "hp": int(item.get("hp", 0)),
            "max_hp": int(item.get("max_hp", 0)),
            "status_effects": _effect_names(item.get("active_effects", [])),
        }
        if include_is_dead:
            card["is_dead"] = not bool(item.get("alive", False))
        else:
            card["ac"] = int(item.get("ac", 10))
            card["is_player"] = False
        cards.append(card)
    return cards


def _player_card(session: ManagedSession) -> dict[str, Any]:
    player = session.runtime.state.player
    return {
        "hp": int(player.hp),
        "max_hp": int(player.max_hp),
        "ac": int(player.ac),
        "active_effects": _effect_names(player.active_effects),
    }


def _effect_names(raw_effects: Any) -> list[str]:
    if not isinstance(raw_effects, list):
        return []
    return [
        str(effect.get("effect_name") or effect.get("effect_id") or "")
        for effect in raw_effects
        if isinstance(effect, Mapping)
        and str(effect.get("effect_name") or effect.get("effect_id") or "")
    ]


def _first_alive_target_id(payload: Mapping[str, Any]) -> str | None:
    raw_participants = payload.get("participants", [])
    if not isinstance(raw_participants, list):
        return None
    for item in raw_participants:
        if not isinstance(item, Mapping):
            continue
        if not bool(item.get("alive", False)):
            continue
        target_id = _non_empty_string(item.get("id"))
        if target_id is not None:
            return target_id
    return None


async def _emit_hostile_entry_events(
    queue: Any,
    session: ManagedSession,
    *,
    sub_area_id: str,
) -> None:
    payload = _get_hostile_payload(session, sub_area_id)
    if payload is None or _payload_status(payload) != "spotted":
        return
    result = await _execute_command(
        session,
        Command(
            type="enter_hostile",
            params={"sub_area_id": sub_area_id},
            source="system",
        ),
    )
    if not result.executed:
        return
    stealth_meta = dict(result.metadata)
    for roll in result.rolls:
        await queue.put(
            SSEEvent(
                "dice_roll",
                _stealth_roll_payload(
                    roll=roll,
                    session=session,
                    metadata=stealth_meta,
                ),
            )
        )
    await queue.put(SSEEvent("stealth_result", _stealth_result_payload(stealth_meta)))
    if bool(stealth_meta.get("passed", False)):
        return
    start_result = await _execute_command(
        session,
        Command(
            type="start_combat",
            params={
                "sub_area_id": sub_area_id,
                "surprise_state": str(stealth_meta.get("surprise_state", "none")),
            },
            source="system",
        ),
    )
    if not start_result.executed:
        return
    combat_payload = _get_hostile_payload(session, sub_area_id)
    if combat_payload is None:
        return
    await queue.put(
        SSEEvent(
            "combat_start",
            _build_combat_start_payload(
                session,
                sub_area_id=sub_area_id,
                payload=combat_payload,
            ),
        )
    )


async def _reset_hostile_to_spotted(
    session: ManagedSession,
    *,
    sub_area_id: str,
    last_choice: str | None = None,
) -> PipelineResult:
    payload = _get_hostile_payload(session, sub_area_id)
    if payload is None or bool(payload.get("cleared", False)):
        return PipelineResult(executed=True, errors=[], metadata={"status": "noop_cleared"})
    if bool(payload.get("combat_active", False)):
        return PipelineResult(executed=True, errors=[], metadata={"status": "noop_active"})
    return await _execute_command(
        session,
        Command(
            type="mark_hostile_spotted",
            params={"sub_area_id": sub_area_id, "last_choice": last_choice},
            source="system",
        ),
    )


async def _deactivate_combat(
    session: ManagedSession,
    *,
    sub_area_id: str,
    status: str,
) -> PipelineResult:
    return await _execute_command(
        session,
        Command(
            type="combat_finalize_status",
            params={"sub_area_id": sub_area_id, "status": status},
            source="system",
        ),
    )


async def _generate_loot(
    session: ManagedSession,
    *,
    payload: Mapping[str, Any],
) -> dict[str, Any] | None:
    monster_ids: list[str] = []
    raw_participants = payload.get("participants", [])
    if isinstance(raw_participants, list):
        for item in raw_participants:
            if not isinstance(item, Mapping):
                continue
            if bool(item.get("alive", True)):
                continue
            if bool(item.get("fled", False)):
                continue
            monster_id = _non_empty_string(item.get("monster_id"))
            if monster_id is not None:
                monster_ids.append(monster_id)
    if not monster_ids:
        return None

    loot_result = await _execute_command(
        session,
        Command(
            type="generate_loot",
            params={
                "monster_ids": monster_ids,
                "area_id": session.runtime.state.player.current_area,
            },
            source="system",
        ),
    )
    loot = loot_result.metadata.get("loot")
    if not isinstance(loot, Mapping):
        return None
    items = loot.get("items")
    normalized_items = list(items) if isinstance(items, list) else []
    return {
        "items": normalized_items,
        "gold": int(loot.get("gold", 0)),
    }


async def _execute_command(
    session: ManagedSession,
    command: Command,
) -> PipelineResult:
    result = await session.runtime.tick_coordinator.process(command)
    await get_admin_coordinator().save_session(session)
    return result


async def _emit_roll_events(
    queue: Any,
    result: PipelineResult,
    session: ManagedSession,
    *,
    fallback_player_ac: int,
) -> None:
    for index, roll in enumerate(result.rolls):
        await queue.put(
            SSEEvent(
                "dice_roll",
                _roll_payload(
                    result=result,
                    roll=roll,
                    session=session,
                    index=index,
                    fallback_player_ac=fallback_player_ac,
                ),
            )
        )


def _roll_payload(
    *,
    result: PipelineResult,
    roll: DiceRoll,
    session: ManagedSession,
    index: int,
    fallback_player_ac: int,
) -> dict[str, Any]:
    modifier = 0
    for item in roll.modifiers:
        try:
            modifier += int(item.get("value", 0))
        except (AttributeError, TypeError, ValueError):
            continue

    purpose = str(roll.purpose)
    if purpose.startswith("monster_attack:"):
        dc = fallback_player_ac
        passed = roll.total >= dc
        return {
            "type": roll.dice,
            "result": roll.result,
            "modifier": modifier,
            "total": roll.total,
            "dc": dc,
            "passed": passed,
            "skill": "monster_attack",
            "roller": "enemy",
            "roller_name": purpose.split(":", 1)[1] or f"Enemy {index + 1}",
        }

    if purpose == "attack":
        dc = int(result.metadata.get("target_ac", 0))
        passed = bool(result.metadata.get("hit", False))
        skill = "attack"
    elif purpose == "shove":
        dc = int(result.metadata.get("resist_dc", 0))
        passed = bool(result.metadata.get("passed", False))
        skill = "shove"
    elif purpose == "flee":
        dc = int(result.metadata.get("escape_dc", 0))
        passed = bool(result.metadata.get("passed", False))
        skill = "flee"
    else:
        dc = 0
        passed = bool(roll.critical)
        skill = purpose

    return {
        "type": roll.dice,
        "result": roll.result,
        "modifier": modifier,
        "total": roll.total,
        "dc": dc,
        "passed": passed,
        "skill": skill,
        "roller": "player",
        "roller_name": session.runtime.state.player.character_name or "Player",
    }


async def _emit_status_updates(
    queue: Any,
    *,
    before_payload: Mapping[str, Any],
    after_payload: Mapping[str, Any] | None,
    before_player_hp: int,
    after_player_hp: int,
    player_max_hp: int,
    cause: str,
) -> None:
    if before_player_hp != after_player_hp:
        await queue.put(
            SSEEvent(
                "status_update",
                {
                    "target_id": "player",
                    "hp_delta": after_player_hp - before_player_hp,
                    "new_hp": after_player_hp,
                    "max_hp": player_max_hp,
                    "cause": cause,
                },
            )
        )

    before_map = _participant_map(before_payload)
    after_map = _participant_map(after_payload or {})
    for target_id, before_entry in before_map.items():
        after_entry = after_map.get(target_id)
        before_hp = int(before_entry.get("hp", 0))
        after_hp = int((after_entry or before_entry).get("hp", 0))
        if before_hp == after_hp:
            continue
        await queue.put(
            SSEEvent(
                "status_update",
                {
                    "target_id": target_id,
                    "hp_delta": after_hp - before_hp,
                    "new_hp": after_hp,
                    "max_hp": int((after_entry or before_entry).get("max_hp", 0)),
                    "cause": cause,
                },
            )
        )


def _participant_map(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw_participants = payload.get("participants", [])
    if not isinstance(raw_participants, list):
        return {}
    mapped: dict[str, dict[str, Any]] = {}
    for item in raw_participants:
        if not isinstance(item, Mapping):
            continue
        participant_id = _non_empty_string(item.get("id"))
        if participant_id is None:
            continue
        mapped[participant_id] = dict(item)
    return mapped


def _combat_end_result(
    *,
    action_type: str,
    result: PipelineResult,
    player_hp: int,
    final_payload: Mapping[str, Any] | None,
) -> str:
    if action_type == "flee" and bool(result.metadata.get("passed", False)):
        return "fled"
    if player_hp <= 0:
        return "defeat"
    if final_payload is not None and bool(final_payload.get("cleared", False)):
        return "victory"
    if bool(result.metadata.get("combat_cleared", False)):
        return "victory"
    return "fled"


def _rounds_fought(payload: Mapping[str, Any]) -> int:
    current = int(payload.get("combat_round", 1))
    return max(1, current)


def _stealth_roll_payload(
    *,
    roll: DiceRoll,
    session: ManagedSession,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    modifier = 0
    for item in roll.modifiers:
        try:
            modifier += int(item.get("value", 0))
        except (AttributeError, TypeError, ValueError):
            continue
    return {
        "type": roll.dice,
        "result": roll.result,
        "modifier": modifier,
        "total": roll.total,
        "dc": int(metadata.get("dc", 0)),
        "passed": bool(metadata.get("passed", False)),
        "skill": "stealth",
        "roller": "player",
        "roller_name": session.runtime.state.player.character_name or "Player",
    }


def _stealth_result_payload(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "passed": bool(metadata.get("passed", False)),
        "roll": int(metadata.get("roll", 0)),
        "dc": int(metadata.get("dc", 0)),
        "modifier": int(metadata.get("modifier", 0)),
        "advantage": bool(metadata.get("advantage", False)),
        "disadvantage": bool(metadata.get("disadvantage", False)),
        "narrative": str(metadata.get("narrative", "")),
        "options": list(metadata.get("options", [])),
        "surprise_state": str(metadata.get("surprise_state", "none")),
    }


def _manual_action_result(
    action_type: str,
    *,
    executed: bool,
    errors: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> SSEEvent:
    return SSEEvent(
        "action_result",
        {
            "executed": executed,
            "action_type": action_type,
            "time_cost": 0.0,
            "errors": list(errors or []),
            "metadata": dict(metadata or {}),
            "narrative_hints": [],
        },
    )


def _combat_action_result_event(
    action_type: str,
    result: PipelineResult,
) -> SSEEvent:
    return _build_action_result_event(result, action_type)


def _non_empty_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


# ---------------------------------------------------------------------------
# D-3: NPC auto-advance helpers (W3-3, W4-1)
# ---------------------------------------------------------------------------
# Pure logic delegated to combat_helpers (no FastAPI dependency there).
# _get_current_unit is imported from combat_helpers above.


async def _compute_companion_decision(
    session: ManagedSession,
    unit: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Pre-compute a companion combat decision via LLM AI (D-6).

    Thin router wrapper — injects LLM provider from deps, then delegates to
    combat_helpers.compute_companion_decision for the actual AI call.
    """
    return await _compute_companion_decision_helper(
        session=session,
        unit=unit,
        payload=payload,
        llm_provider=get_llm_provider(),
    )


async def _compute_enemy_decision(
    session: ManagedSession,
    unit: dict[str, Any],
    payload: dict[str, Any],
    *,
    decision_tier: str,
) -> dict[str, Any] | None:
    """Pre-compute one elite/boss enemy combat decision via LLM AI."""
    return await _compute_enemy_decision_helper(
        session=session,
        unit=unit,
        payload=payload,
        decision_tier=decision_tier,
        llm_provider=get_llm_provider(),
    )


async def _emit_npc_turn_events(
    queue: Any,
    npc_result: PipelineResult,
    session: ManagedSession,
    sub_area_id: str,
    before_hp: int,
) -> None:
    """Emit SSE events generated by one NPC turn (rolls + status updates)."""
    after_payload = _get_hostile_payload(session, sub_area_id)
    await _emit_roll_events(
        queue,
        npc_result,
        session,
        fallback_player_ac=max(10, int(session.runtime.state.player.ac)),
    )
    await _emit_status_updates(
        queue,
        before_payload=after_payload or {},
        after_payload=after_payload,
        before_player_hp=before_hp,
        after_player_hp=int(session.runtime.state.player.hp),
        player_max_hp=int(session.runtime.state.player.max_hp),
        cause="npc_action",
    )


async def _auto_advance_npc_turns(
    session: ManagedSession,
    sub_area_id: str,
    queue: Any,
) -> None:
    """Auto-execute all NPC turns until it's the player's turn or combat ends.

    Called after each player action (D-3 / W3-3).
    """
    for _ in range(_MAX_NPC_ADVANCE):
        payload = _get_hostile_payload(session, sub_area_id)
        if payload is None or not bool(payload.get("combat_active", False)):
            break

        current_unit = _get_current_unit(payload)
        if current_unit is None:
            break
        unit_source = str(current_unit.get("source", ""))
        if unit_source == "player":
            break  # player's turn — stop advancing

        # Pre-compute companion decision if it's a companion's turn
        decision_params: dict[str, Any] = {"sub_area_id": sub_area_id}
        if unit_source == "companion":
            companion_decision = await _compute_companion_decision(
                session, current_unit, payload
            )
            if companion_decision is not None:
                decision_params["decision"] = companion_decision
                decision_params["decision_provider"] = "companion_llm"
        elif unit_source == "monster":
            decision_tier = _resolve_enemy_ai_tier(
                session=session,
                unit=current_unit,
                payload=payload,
                sub_area_id=sub_area_id,
            )
            if decision_tier is not None:
                enemy_decision = await _compute_enemy_decision(
                    session,
                    current_unit,
                    payload,
                    decision_tier=decision_tier,
                )
                if enemy_decision is not None:
                    decision_params["decision"] = enemy_decision
                    decision_params["decision_provider"] = "enemy_llm"
                    decision_params["decision_tier"] = decision_tier

        before_hp = int(session.runtime.state.player.hp)
        npc_result = await _execute_command(
            session,
            Command(type="combat_npc_turn", params=decision_params, source="system"),
        )

        await _emit_npc_turn_events(queue, npc_result, session, sub_area_id, before_hp)

        if not npc_result.executed:
            break

        # D-4: Detect player death from NPC attack
        if int(session.runtime.state.player.hp) <= 0:
            updated_payload = _get_hostile_payload(session, sub_area_id)
            if updated_payload is not None:
                await _deactivate_combat(
                    session,
                    sub_area_id=sub_area_id,
                    status="player_defeated",
                )
            await queue.put(SSEEvent("combat_end", {
                "result": "defeat",
                "sub_area_id": sub_area_id,
            }))
            break


# ---------------------------------------------------------------------------
# D-9: Fallen companion restoration after victory (W3-6)
# ---------------------------------------------------------------------------


async def _restore_fallen_companions(
    session: ManagedSession,
    payload: Mapping[str, Any],
) -> list[str]:
    """Restore fallen companions to 50% max HP after a combat victory.

    Thin router wrapper — injects the internal command executor, then delegates to
    combat_helpers.restore_fallen_companions.
    """
    return await _restore_fallen_companions_helper(
        session,
        payload,
        execute_command_fn=lambda cmd: _execute_command(session, cmd),
    )
