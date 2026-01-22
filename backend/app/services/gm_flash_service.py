"""
GM Flash service for event recording and dispatch.
"""
import uuid
from datetime import datetime
from typing import List, Optional, Set

from app.models.event import GMEventIngestRequest, GMEventIngestResponse
from app.models.flash import EventIngestRequest
from app.models.graph import MemoryNode
from app.services.event_bus import EventBus
from app.services.flash_service import FlashService
from app.services.graph_schema import GraphSchemaOptions, validate_edge, validate_node
from app.services.graph_store import GraphStore


class GMFlashService:
    """GM-level event service."""

    def __init__(
        self,
        graph_store: Optional[GraphStore] = None,
        flash_service: Optional[FlashService] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self.graph_store = graph_store or GraphStore()
        self.flash_service = flash_service or FlashService(self.graph_store)
        self.event_bus = event_bus or EventBus()

    async def ingest_event(
        self,
        world_id: str,
        request: GMEventIngestRequest,
    ) -> GMEventIngestResponse:
        event = request.event
        event_id = event.id or f"event_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        gm_nodes = list(event.nodes)
        gm_edges = list(event.edges)

        if not gm_nodes:
            gm_nodes = [
                MemoryNode(
                    id=event_id,
                    type="event",
                    name=event.type.value,
                    importance=0.5,
                    properties={
                        "day": event.game_day,
                        "summary": event.content.raw or "",
                        "location": event.location,
                        "participants": event.participants,
                        "witnesses": event.witnesses,
                    },
                )
            ]

        if request.validate:
            options = GraphSchemaOptions(
                allow_unknown_node_types=not request.strict,
                allow_unknown_relations=not request.strict,
                validate_event_properties=request.strict,
            )
            for node in gm_nodes:
                errors = validate_node(node, options)
                if errors:
                    raise ValueError(f"Invalid node {node.id}: {errors}")
            for edge in gm_edges:
                errors = validate_edge(edge, None, options)
                if errors:
                    raise ValueError(f"Invalid edge {edge.id}: {errors}")

        for node in gm_nodes:
            await self.graph_store.upsert_node(
                world_id,
                "gm",
                node,
                character_id=None,
                index=request.write_indexes,
            )
        for edge in gm_edges:
            await self.graph_store.upsert_edge(world_id, "gm", edge, character_id=None)

        if self.event_bus:
            await self.event_bus.publish(event)

        recipients: List[str] = []
        if request.distribute:
            recipients = sorted(self._resolve_recipients(request))
            for character_id in recipients:
                override = request.per_character.get(character_id)
                if override:
                    ingest_request = EventIngestRequest(
                        event_id=event_id,
                        description=event.content.raw,
                        timestamp=event.timestamp,
                        game_day=event.game_day,
                        location=event.location,
                        perspective="gm_dispatch",
                        participants=event.participants,
                        witnesses=event.witnesses,
                        visibility_public=event.visibility.public,
                        nodes=override.nodes,
                        edges=override.edges,
                        state_updates=override.state_updates,
                        write_indexes=override.write_indexes,
                        validate=override.validate,
                        strict=override.strict,
                    )
                elif request.default_dispatch:
                    ingest_request = EventIngestRequest(
                        event_id=event_id,
                        description=event.content.raw,
                        timestamp=event.timestamp,
                        game_day=event.game_day,
                        location=event.location,
                        perspective="gm_dispatch",
                        participants=event.participants,
                        witnesses=event.witnesses,
                        visibility_public=event.visibility.public,
                        nodes=gm_nodes,
                        edges=gm_edges,
                        write_indexes=request.write_indexes,
                        validate=request.validate,
                        strict=request.strict,
                    )
                else:
                    continue

                await self.flash_service.ingest_event(
                    world_id=world_id,
                    character_id=character_id,
                    request=ingest_request,
                )

        return GMEventIngestResponse(
            event_id=event_id,
            gm_node_count=len(gm_nodes),
            gm_edge_count=len(gm_edges),
            dispatched=request.distribute,
            recipients=recipients,
        )

    def _resolve_recipients(self, request: GMEventIngestRequest) -> Set[str]:
        event = request.event
        if request.recipients is not None:
            return set(request.recipients)

        recipients = set(event.participants) | set(event.witnesses) | set(event.visibility.known_to)

        if event.visibility.public and request.known_characters:
            recipients |= set(request.known_characters)

        if event.location and request.character_locations:
            for char_id, location in request.character_locations.items():
                if location == event.location:
                    recipients.add(char_id)

        return recipients
