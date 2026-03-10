"""Read-only state panel routes (inventory, map, quests)."""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import APIRouter

from app.api_models import (
    InventoryPanelResponse,
    MapAreaSummary,
    MapPanelResponse,
    QuestPanelResponse,
)
from app.deps import _load_session_or_404
from app.game_core import ManagedSession
from app.quest_views import normalize_dynamic_quest_panel
from app.scene_views import build_location_overview

router = APIRouter()


def _inventory_response(session: ManagedSession) -> InventoryPanelResponse:
    """Build the inventory panel payload from the player slice."""

    player_payload = session.runtime.state.player.snapshot()
    inventory = player_payload.get("inventory", [])
    equipment = player_payload.get("equipment", {})
    return InventoryPanelResponse(
        gold=int(player_payload.get("gold", 0)),
        inventory=list(inventory) if isinstance(inventory, list) else [],
        equipment=dict(equipment) if isinstance(equipment, Mapping) else {},
    )


def _map_response(session: ManagedSession) -> MapPanelResponse:
    """Build the map panel payload from world templates and runtime state."""

    player = session.runtime.state.player
    area_snapshot = session.runtime.state.areas.snapshot()
    raw_areas = area_snapshot.get("areas", {})
    state_areas = raw_areas if isinstance(raw_areas, Mapping) else {}
    summaries: list[MapAreaSummary] = []
    discovered_area_ids: list[str] = []
    world_areas = (
        session.runtime.world.maps.list_all()
        if session.runtime.world.has_registry("maps")
        else []
    )
    for template in world_areas:
        area_id = template.id.strip() if template.id else ""
        if not area_id:
            continue
        state_area = state_areas.get(area_id, {})
        if not isinstance(state_area, Mapping):
            state_area = {}
        exploration = str(state_area.get("exploration", "undiscovered"))
        if exploration != "undiscovered":
            discovered_area_ids.append(area_id)
        sub_locations: list[dict[str, str]] = []
        for key, raw_location in template.sub_locations.items():
            location_id = str(key).strip()
            if not location_id:
                continue
            location_name = location_id
            raw_name = getattr(raw_location, "name", None)
            if raw_name is None and isinstance(raw_location, Mapping):
                raw_name = raw_location.get("name", "")
            if raw_name:
                location_name = str(raw_name).strip() or location_name
            sub_locations.append({"id": location_id, "name": location_name})
        tags = [str(tag) for tag in template.tags if str(tag).strip()]
        raw_danger = state_area.get(
            "danger_level",
            template.base_danger,
        )
        try:
            danger_level = float(raw_danger) if raw_danger is not None else None
        except (TypeError, ValueError):
            danger_level = None
        summaries.append(
            MapAreaSummary(
                id=area_id,
                name=template.name or area_id,
                danger_level=danger_level,
                exploration=exploration,
                tags=tags,
                sub_locations=sub_locations,
            )
        )
    return MapPanelResponse(
        current_area=player.current_area,
        current_location=player.current_location,
        discovered_area_ids=discovered_area_ids,
        areas=summaries,
    )


def _quest_response(session: ManagedSession) -> QuestPanelResponse:
    """Build the quest panel payload from the quest slice.

    Merges content-layer titles/descriptions into runtime milestone states
    so the frontend can display meaningful milestone information.
    """

    quest_payload = session.runtime.state.quests.snapshot()
    raw_milestones = quest_payload.get("milestone_states", {})

    # Enrich milestone states with title/description from content registry
    enriched_milestones: dict[str, Any] = {}
    has_quest_registry = session.runtime.world.has_registry("quests")
    for ms_id, ms_state in raw_milestones.items():
        entry: dict[str, Any] = dict(ms_state) if isinstance(ms_state, Mapping) else {"state": str(ms_state)}
        if has_quest_registry:
            template = session.runtime.world.quests.get_milestone(ms_id)
            if template is not None:
                entry["title"] = template.title or ms_id
                entry["description"] = template.description or ""
                entry["chapter_id"] = template.chapter_id or ""
        if "title" not in entry:
            entry["title"] = ms_id
        enriched_milestones[ms_id] = entry

    return QuestPanelResponse(
        milestone_states=enriched_milestones,
        dynamic_quests=normalize_dynamic_quest_panel(
            quest_payload.get("dynamic_quests", {})
            if isinstance(quest_payload.get("dynamic_quests", {}), Mapping)
            else {}
        ),
        chapter_completion=dict(quest_payload.get("chapter_completion", {})),
    )


@router.get(
    "/api/game/{world_id}/sessions/{session_id}/inventory",
    response_model=InventoryPanelResponse,
)
async def get_inventory_panel(
    world_id: str,
    session_id: str,
) -> InventoryPanelResponse:
    """Return the current inventory and equipment panel."""

    session = await _load_session_or_404(world_id, session_id)
    return _inventory_response(session)


@router.get(
    "/api/game/{world_id}/sessions/{session_id}/map",
    response_model=MapPanelResponse,
)
async def get_map_panel(world_id: str, session_id: str) -> MapPanelResponse:
    """Return the current map panel."""

    session = await _load_session_or_404(world_id, session_id)
    return _map_response(session)


@router.get(
    "/api/game/{world_id}/sessions/{session_id}/quests",
    response_model=QuestPanelResponse,
)
async def get_quest_panel(world_id: str, session_id: str) -> QuestPanelResponse:
    """Return the current quest panel."""

    session = await _load_session_or_404(world_id, session_id)
    return _quest_response(session)


@router.get("/api/game/{world_id}/sessions/{session_id}/scene")
async def get_scene(world_id: str, session_id: str) -> dict[str, Any]:
    """Return current location_overview for initial game load after resume."""

    session = await _load_session_or_404(world_id, session_id)
    return build_location_overview(session)
