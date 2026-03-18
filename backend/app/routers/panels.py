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
    raw_inventory = player_payload.get("inventory", [])
    raw_equipment = player_payload.get("equipment", {})

    has_items = session.runtime.world.has_registry("items")
    items_reg = session.runtime.world.items if has_items else None

    def _enrich_item(item: dict[str, Any]) -> dict[str, Any]:
        """Add name/type/base_price from ItemRegistry if available."""
        enriched = dict(item)
        item_id = str(item.get("item_id", "")).strip()
        if items_reg and item_id:
            template = items_reg.get(item_id)
            if template is not None:
                enriched.setdefault("name", getattr(template, "name", item_id))
                enriched.setdefault("type", getattr(template, "type", ""))
                enriched.setdefault("base_price", getattr(template, "base_price", 0))
                desc = getattr(template, "description", "")
                if desc:
                    enriched.setdefault("description", desc)
        return enriched

    inventory = [
        _enrich_item(item) for item in (raw_inventory if isinstance(raw_inventory, list) else [])
        if isinstance(item, Mapping)
    ]
    equipment: dict[str, Any] = {}
    if isinstance(raw_equipment, Mapping):
        for slot, item in raw_equipment.items():
            if item is None:
                equipment[slot] = None
            elif isinstance(item, Mapping):
                equipment[slot] = _enrich_item(dict(item))
            else:
                equipment[slot] = item

    return InventoryPanelResponse(
        gold=int(player_payload.get("gold", 0)),
        inventory=inventory,
        equipment=equipment,
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

    milestone_states are internal engine state and are not exposed through the
    quest panel API (P25-12). Only active/available/completed dynamic quests
    are shown; retired and expired quests are filtered out.
    """

    quest_payload = session.runtime.state.quests.snapshot()

    # Filter dynamic_quests — hide retired/expired from the frontend
    raw_dynamic = quest_payload.get("dynamic_quests", {})
    if not isinstance(raw_dynamic, Mapping):
        raw_dynamic = {}
    visible_quests: dict[str, Any] = {}
    for qid, quest in raw_dynamic.items():
        status = str(quest.get("status", "")).strip().lower() if isinstance(quest, Mapping) else ""
        if status in {"retired", "expired"}:
            continue
        visible_quests[qid] = quest

    # Build pending NPC invites + ambient chatter from unconsumed directives
    pending_invites: list[dict[str, Any]] = []
    ambient_chatter: list[dict[str, Any]] = []
    if session.runtime.state.has_slice("narrative_plan"):
        _INVITE_PRIORITIES = {"high", "medium"}
        for directive in session.runtime.state.narrative_plan.npc_directives:
            if not isinstance(directive, Mapping):
                continue
            if directive.get("consumed"):
                continue
            npc_id = str(directive.get("npc_id", "")).strip()
            if not npc_id:
                continue
            inner = directive.get("directive", {})
            topic = ""
            if isinstance(inner, Mapping):
                topic = str(inner.get("topic", "")).strip()
            npc_name = npc_id
            if session.runtime.world.has_registry("characters"):
                tmpl = session.runtime.world.characters.get(npc_id)
                if tmpl is not None:
                    npc_name = getattr(tmpl, "name", npc_id) or npc_id
            entry = {
                "npc_id": npc_id,
                "npc_name": str(npc_name),
                "topic": topic[:50],
            }
            priority = str(directive.get("priority", "low")).strip().lower()
            if priority in _INVITE_PRIORITIES and len(pending_invites) < 3:
                pending_invites.append(entry)
            elif len(ambient_chatter) < 5:
                ambient_chatter.append(entry)

    return QuestPanelResponse(
        dynamic_quests=normalize_dynamic_quest_panel(visible_quests),
        chapter_completion=dict(quest_payload.get("chapter_completion", {})),
        pending_npc_invites=pending_invites,
        ambient_chatter=ambient_chatter,
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
