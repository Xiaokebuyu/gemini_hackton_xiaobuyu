"""Party tools for MCP server."""
import json

from app.services.graph_store import GraphStore
from app.services.party_service import PartyService
from app.services.party_store import PartyStore

_party_service = PartyService(
    graph_store=GraphStore(),
    party_store=PartyStore(),
)


def register(game_mcp) -> None:
    @game_mcp.tool()
    async def get_party_info(world_id: str, session_id: str) -> str:
        """Get current party information including all members.

        Returns JSON with:
        - has_party: whether a party exists
        - party_id: party identifier
        - leader_id: party leader (usually 'player')
        - current_location: party's current location
        - members: list of party members with character_id, name, role,
          personality, is_active, current_mood, response_tendency
        """
        party = await _party_service.get_party(world_id, session_id)
        if not party:
            return json.dumps(
                {"has_party": False, "party_id": None, "members": []},
                ensure_ascii=False,
            )

        return json.dumps(
            {
                "has_party": True,
                "party_id": party.party_id,
                "leader_id": party.leader_id,
                "current_location": party.current_location,
                "members": [
                    {
                        "character_id": m.character_id,
                        "name": m.name,
                        "role": m.role.value,
                        "personality": m.personality,
                        "is_active": m.is_active,
                        "current_mood": m.current_mood,
                        "response_tendency": m.response_tendency,
                    }
                    for m in party.members
                ],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
