"""Combat and encounter routes."""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api_models import StructuredActionRequest
from app.deps import (
    _api_error,
    _execute_structured_action,
    _load_session_or_404,
    get_admin_coordinator,
)
from app.game_core import ManagedSession
from app.game_core.orchestration.models import PipelineResult, SSEEvent
from app.game_core.rules.models import Command, DiceRoll
from app.routers.gameplay import (
    _build_action_result_event,
    _build_stream_end_event,
    _stream_with_lock,
)
from app.scene_views import build_location_overview, build_scene_change


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


class CombatActionRequest(BaseModel):
    action_type: str
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

        before_player_hp = int(session.runtime.state.player.hp)
        structured = StructuredActionRequest(
            action_type=action_type,
            params=params,
        )
        result = await _execute_structured_action(session, structured)

        after_payload = _get_hostile_payload(session, sub_area_id)
        if int(session.runtime.state.player.hp) <= 0 and after_payload is not None:
            await _deactivate_combat(
                session,
                sub_area_id=sub_area_id,
                payload=after_payload,
                status="player_defeated",
            )
            after_payload = _get_hostile_payload(session, sub_area_id)

        if (
            result.success
            and after_payload is not None
            and bool(after_payload.get("combat_active", False))
        ):
            round_result = await _execute_command(
                session,
                Command(
                    type="advance_combat_round",
                    params={"sub_area_id": sub_area_id},
                    source="system",
                ),
            )
            if round_result.success:
                refreshed_payload = _get_hostile_payload(session, sub_area_id)
                if refreshed_payload is not None:
                    after_payload = refreshed_payload

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

        if not result.success:
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
                loot_payload = await _generate_loot(session, payload=after_payload or before_payload)
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
        await queue.put(_build_stream_end_event("completed", result.success))

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
                    success=False,
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
                    success=False,
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
                        success=True,
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
                        success=False,
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
            if enter_result.success:
                await queue.put(SSEEvent("scene_change", build_scene_change(session)))
                await queue.put(SSEEvent("location_overview", build_location_overview(session)))
                await _emit_hostile_entry_events(queue, session, sub_area_id=sub_area_id)
            else:
                await queue.put(SSEEvent("location_overview", build_location_overview(session)))
            await queue.put(_build_stream_end_event("completed", enter_result.success))
            return

        stealth = payload.get("last_stealth_result")
        if status != "stealth_resolved" or not isinstance(stealth, Mapping) or not bool(
            stealth.get("success", False)
        ):
            await queue.put(
                _manual_action_result(
                    choice,
                    success=False,
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
                    success=start_result.success,
                    errors=list(start_result.errors),
                    metadata={"sub_area_id": sub_area_id},
                )
            )
            if start_result.success:
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
            await queue.put(_build_stream_end_event("completed", start_result.success))
            return

        if choice == "sneak_through":
            if bool(payload.get("blocking", False)):
                await queue.put(
                    _manual_action_result(
                        choice,
                        success=False,
                        errors=["blocking encounters cannot be bypassed"],
                        metadata={"sub_area_id": sub_area_id},
                    )
                )
                await queue.put(SSEEvent("location_overview", build_location_overview(session)))
                await queue.put(_build_stream_end_event("completed", False))
                return
            updated = session.runtime.state.areas.copy_hostile_state(payload)
            updated["last_stealth_choice"] = choice
            session.runtime.state.areas.upsert_hostile(sub_area_id, updated)
            await get_admin_coordinator().save_session(session)
            await queue.put(
                _manual_action_result(
                    choice,
                    success=True,
                    metadata={"sub_area_id": sub_area_id},
                )
            )
            await queue.put(SSEEvent("location_overview", build_location_overview(session)))
            await queue.put(_build_stream_end_event("completed", True))
            return

        if choice == "retreat":
            leave_result = await _execute_structured_action(
                session,
                StructuredActionRequest(
                    action_type="leave_sub_location",
                    params={},
                ),
            )
            if leave_result.success:
                await _reset_hostile_to_spotted(
                    session,
                    sub_area_id=sub_area_id,
                    last_choice=choice,
                )
            await queue.put(
                _manual_action_result(
                    choice,
                    success=leave_result.success,
                    errors=list(leave_result.errors),
                    metadata={"sub_area_id": sub_area_id},
                )
            )
            if leave_result.success:
                await queue.put(SSEEvent("scene_change", build_scene_change(session)))
            await queue.put(SSEEvent("location_overview", build_location_overview(session)))
            await queue.put(_build_stream_end_event("completed", leave_result.success))
            return

        await queue.put(
            _manual_action_result(
                choice,
                success=False,
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
    if not result.success:
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
    if bool(stealth_meta.get("success", False)):
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
    if not start_result.success:
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
) -> None:
    payload = _get_hostile_payload(session, sub_area_id)
    if payload is None or bool(payload.get("cleared", False)):
        return
    if bool(payload.get("combat_active", False)):
        return
    updated = session.runtime.state.areas.copy_hostile_state(payload)
    updated["status"] = "spotted"
    updated["entry_mode"] = None
    updated["last_stealth_result"] = None
    updated["last_stealth_choice"] = last_choice
    session.runtime.state.areas.upsert_hostile(sub_area_id, updated)
    await get_admin_coordinator().save_session(session)


async def _deactivate_combat(
    session: ManagedSession,
    *,
    sub_area_id: str,
    payload: Mapping[str, Any],
    status: str,
) -> None:
    updated = session.runtime.state.areas.copy_hostile_state(payload)
    updated["status"] = status
    updated["combat_active"] = False
    session.runtime.state.areas.upsert_hostile(sub_area_id, updated)
    await get_admin_coordinator().save_session(session)


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
        success = roll.total >= dc
        return {
            "type": roll.dice,
            "result": roll.result,
            "modifier": modifier,
            "total": roll.total,
            "dc": dc,
            "success": success,
            "skill": "monster_attack",
            "roller": "enemy",
            "roller_name": purpose.split(":", 1)[1] or f"Enemy {index + 1}",
        }

    if purpose == "attack":
        dc = int(result.metadata.get("target_ac", 0))
        success = bool(result.metadata.get("hit", False))
        skill = "attack"
    elif purpose == "shove":
        dc = int(result.metadata.get("resist_dc", 0))
        success = bool(result.metadata.get("passed", False))
        skill = "shove"
    elif purpose == "flee":
        dc = int(result.metadata.get("escape_dc", 0))
        success = bool(result.metadata.get("passed", False))
        skill = "flee"
    else:
        dc = 0
        success = bool(roll.critical)
        skill = purpose

    return {
        "type": roll.dice,
        "result": roll.result,
        "modifier": modifier,
        "total": roll.total,
        "dc": dc,
        "success": success,
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
        "success": bool(metadata.get("success", False)),
        "skill": "stealth",
        "roller": "player",
        "roller_name": session.runtime.state.player.character_name or "Player",
    }


def _stealth_result_payload(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "success": bool(metadata.get("success", False)),
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
    success: bool,
    errors: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> SSEEvent:
    return SSEEvent(
        "action_result",
        {
            "success": success,
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
    if action_type == "flee" and not bool(result.metadata.get("passed", False)):
        return _manual_action_result(
            action_type,
            success=False,
            errors=["failed to flee"],
            metadata=dict(result.metadata),
        )
    return _build_action_result_event(result, action_type)


def _non_empty_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
