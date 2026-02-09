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
    async def add_item(
        world_id: str,
        session_id: str,
        item_id: str,
        item_name: str,
        quantity: int = 1,
    ) -> str:
        """Add an item to the player's inventory.

        Args:
            world_id: World ID
            session_id: Session ID
            item_id: Unique item identifier (e.g. "healing_potion")
            item_name: Display name (e.g. "治疗药水")
            quantity: Number to add (default 1)
        """
        character = await _store.get_character(world_id, session_id)
        if not character:
            return json.dumps({"error": "no player character"}, ensure_ascii=False)

        # Try to get properties from item registry
        properties = None
        registry_item = item_registry.get_item(item_id)
        if registry_item:
            properties = registry_item.get("properties")
            if not item_name or item_name == item_id:
                item_name = registry_item.get("name", item_name)

        character.add_item(item_id, item_name, quantity, properties)
        await _store.save_character(world_id, session_id, character)
        return json.dumps(
            {"success": True, "item_id": item_id, "item_name": item_name, "quantity": quantity},
            ensure_ascii=False,
        )

    @game_mcp.tool()
    async def remove_item(
        world_id: str,
        session_id: str,
        item_id: str,
        quantity: int = 1,
    ) -> str:
        """Remove an item from the player's inventory.

        Args:
            world_id: World ID
            session_id: Session ID
            item_id: Item identifier to remove
            quantity: Number to remove (default 1)
        """
        character = await _store.get_character(world_id, session_id)
        if not character:
            return json.dumps({"error": "no player character"}, ensure_ascii=False)
        removed = character.remove_item(item_id, quantity)
        if removed:
            await _store.save_character(world_id, session_id, character)
        return json.dumps(
            {"success": removed, "item_id": item_id},
            ensure_ascii=False,
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
    async def use_item(
        world_id: str,
        session_id: str,
        item_id: str,
    ) -> str:
        """Use a consumable item (potion, scroll, etc).

        Applies the item's effect and removes it from inventory.
        Currently supports healing potions (restores HP).

        Args:
            world_id: World ID
            session_id: Session ID
            item_id: Item to use
        """
        character = await _store.get_character(world_id, session_id)
        if not character:
            return json.dumps({"error": "no player character"}, ensure_ascii=False)
        if not character.has_item(item_id):
            return json.dumps({"error": f"item {item_id} not in inventory"}, ensure_ascii=False)

        registry_item = item_registry.get_item(item_id)
        item_type = registry_item.get("type", "") if registry_item else ""
        item_subtype = registry_item.get("subtype", "") if registry_item else ""

        effect_description = "物品已使用"

        # Handle healing items
        if item_type == "potion" and item_subtype == "healing":
            # Default healing: 1d6+5 (~8 HP)
            import random
            heal_amount = random.randint(1, 6) + 5
            old_hp = character.current_hp
            character.current_hp = min(character.current_hp + heal_amount, character.max_hp)
            actual_heal = character.current_hp - old_hp
            effect_description = f"恢复了 {actual_heal} 点生命值 (HP: {character.current_hp}/{character.max_hp})"

        # Handle antidote
        elif item_type == "potion" and item_subtype == "curative":
            character.conditions = [c for c in character.conditions if c.get("name") != "中毒"]
            effect_description = "解除了中毒状态"

        character.remove_item(item_id, 1)
        await _store.save_character(world_id, session_id, character)
        return json.dumps(
            {"success": True, "item_id": item_id, "effect": effect_description},
            ensure_ascii=False,
        )

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
