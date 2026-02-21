"""Inventory and equipment MCP tools."""
import json
from typing import Optional

from app.services.character_service import CharacterService
from app.services.character_store import CharacterStore
from app.services import item_registry

_store = CharacterStore()
_service = CharacterService(store=_store)


def register(game_mcp) -> None:
    @game_mcp.tool()
    async def get_inventory(world_id: str, session_id: str) -> str:
        """Get the player's inventory and equipment.

        Returns JSON with inventory list and equipment slots.
        """
        character = await _store.get_character(world_id, session_id)
        if not character:
            return json.dumps({"error": "no player character"}, ensure_ascii=False)
        return json.dumps(
            {
                "inventory": character.inventory,
                "equipment": character.equipment,
                "gold": character.gold,
            },
            ensure_ascii=False,
            indent=2,
        )

    @game_mcp.tool()
    async def equip_item(
        world_id: str,
        session_id: str,
        item_id: str,
        slot: str,
    ) -> str:
        """Equip an item from inventory to a slot.

        Args:
            world_id: World ID
            session_id: Session ID
            item_id: Item to equip (must be in inventory)
            slot: Equipment slot (main_hand, off_hand, armor, head, etc.)
        """
        try:
            result = await _service.equip_item(world_id, session_id, item_id, slot)
            return json.dumps({"success": True, **result}, ensure_ascii=False)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @game_mcp.tool()
    async def unequip_item(
        world_id: str,
        session_id: str,
        slot: str,
    ) -> str:
        """Unequip item from a slot back to inventory.

        Args:
            world_id: World ID
            session_id: Session ID
            slot: Equipment slot to unequip
        """
        try:
            result = await _service.unequip_item(world_id, session_id, slot)
            return json.dumps({"success": True, **result}, ensure_ascii=False)
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @game_mcp.tool()
    async def lookup_item(item_id: str) -> str:
        """Look up item details from the item registry.

        Args:
            item_id: Item ID to look up
        """
        item = item_registry.get_item(item_id)
        if not item:
            return json.dumps({"error": f"item {item_id} not found"}, ensure_ascii=False)
        return json.dumps(item, ensure_ascii=False, indent=2)

    @game_mcp.tool()
    async def search_items(query: str, item_type: Optional[str] = None) -> str:
        """Search for items by name/description.

        Args:
            query: Search query (matches name and description)
            item_type: Optional filter by type (weapon, armor, potion, tool, etc.)
        """
        results = item_registry.search_items(query)
        if item_type:
            results = [r for r in results if r.get("type") == item_type]
        # Limit results
        return json.dumps(
            {"items": results[:20], "total": len(results)},
            ensure_ascii=False,
            indent=2,
        )
