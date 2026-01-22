"""
Game master loop foundation service.
"""
from typing import Optional

from app.combat.combat_engine import CombatEngine
from app.models.event import Event, EventContent, EventType, GMEventIngestRequest
from app.models.game import (
    CombatResolveRequest,
    CombatResolveResponse,
    CombatStartRequest,
    CombatStartResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    GameSessionState,
    UpdateSceneRequest,
)
from app.services.gm_flash_service import GMFlashService
from app.services.game_session_store import GameSessionStore


class GameLoopService:
    """Orchestrates session, scene, and combat flow."""

    def __init__(
        self,
        session_store: Optional[GameSessionStore] = None,
        gm_service: Optional[GMFlashService] = None,
        combat_engine: Optional[CombatEngine] = None,
    ) -> None:
        self.session_store = session_store or GameSessionStore()
        self.gm_service = gm_service or GMFlashService()
        self.combat_engine = combat_engine or CombatEngine()

    async def create_session(self, world_id: str, request: CreateSessionRequest) -> CreateSessionResponse:
        state = await self.session_store.create_session(world_id, request.session_id, request.participants)
        return CreateSessionResponse(session=state)

    async def get_session(self, world_id: str, session_id: str) -> Optional[GameSessionState]:
        return await self.session_store.get_session(world_id, session_id)

    async def update_scene(self, world_id: str, session_id: str, request: UpdateSceneRequest) -> GameSessionState:
        await self.session_store.set_scene(world_id, session_id, request.scene)
        state = await self.session_store.get_session(world_id, session_id)
        if not state:
            raise ValueError("session not found")
        return state

    async def start_combat(
        self,
        world_id: str,
        session_id: str,
        request: CombatStartRequest,
    ) -> CombatStartResponse:
        session = self.combat_engine.start_combat(
            enemies=request.enemies,
            player_state=request.player_state,
            environment=request.environment or None,
            allies=request.allies or None,
        )
        await self.session_store.set_combat(world_id, session_id, session.combat_id, request.combat_context)

        combat_state = {
            "combat_id": session.combat_id,
            "state": session.state.value,
            "turn_order": session.turn_order,
            "current_round": session.current_round,
        }

        state = await self.session_store.get_session(world_id, session_id)
        if not state:
            raise ValueError("session not found")

        return CombatStartResponse(combat_id=session.combat_id, combat_state=combat_state, session=state)

    async def resolve_combat(
        self,
        world_id: str,
        session_id: str,
        request: CombatResolveRequest,
    ) -> CombatResolveResponse:
        session_state = await self.session_store.get_session(world_id, session_id)
        if not session_state:
            raise ValueError("session not found")

        combat_id = request.combat_id or session_state.active_combat_id
        if not combat_id:
            raise ValueError("combat_id is required")

        if request.use_engine:
            result = self.combat_engine.get_combat_result(combat_id)
            result_payload = result.to_dict()
            summary = request.summary_override or result.summary
        else:
            if not request.result_override:
                raise ValueError("result_override is required when use_engine=false")
            result_payload = request.result_override
            summary = request.summary_override or result_payload.get("summary", "")

        combat_context = session_state.combat_context
        location = combat_context.location if combat_context else None
        participants = combat_context.participants if combat_context else []
        witnesses = combat_context.witnesses if combat_context else []

        event = Event(
            type=EventType.COMBAT,
            game_day=None,
            location=location,
            participants=participants,
            witnesses=witnesses,
            content=EventContent(raw=summary, structured=result_payload),
        )

        dispatch_request = GMEventIngestRequest(
            event=event,
            distribute=request.dispatch,
            recipients=request.recipients,
            known_characters=combat_context.known_characters if combat_context else [],
            character_locations=combat_context.character_locations if combat_context else {},
            per_character=request.per_character,
            write_indexes=request.write_indexes,
            validate=request.validate,
            strict=request.strict,
        )

        response = await self.gm_service.ingest_event(world_id, dispatch_request)
        await self.session_store.clear_combat(world_id, session_id)

        return CombatResolveResponse(
            combat_id=combat_id,
            event_id=response.event_id,
            dispatched=response.dispatched,
        )
