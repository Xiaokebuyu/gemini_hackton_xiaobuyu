"""
Pro service foundation (no LLM).
"""
from typing import Optional

from app.models.pro import CharacterProfile, ProContextRequest, ProContextResponse
from app.services.flash_service import FlashService
from app.services.graph_store import GraphStore
from app.services.pro_context_builder import ProContextBuilder


class ProService:
    """Pro service for assembling character context."""

    def __init__(
        self,
        graph_store: Optional[GraphStore] = None,
        flash_service: Optional[FlashService] = None,
        context_builder: Optional[ProContextBuilder] = None,
    ) -> None:
        self.graph_store = graph_store or GraphStore()
        self.flash_service = flash_service or FlashService(self.graph_store)
        self.context_builder = context_builder or ProContextBuilder()

    async def get_profile(self, world_id: str, character_id: str) -> CharacterProfile:
        data = await self.graph_store.get_character_profile(world_id, character_id)
        return CharacterProfile(**data) if data else CharacterProfile()

    async def set_profile(
        self,
        world_id: str,
        character_id: str,
        profile: CharacterProfile,
        merge: bool = True,
    ) -> CharacterProfile:
        await self.graph_store.set_character_profile(
            world_id,
            character_id,
            profile.model_dump(),
            merge=merge,
        )
        return profile

    async def build_context(
        self,
        world_id: str,
        character_id: str,
        request: ProContextRequest,
    ) -> ProContextResponse:
        profile = await self.get_profile(world_id, character_id)
        state = await self.graph_store.get_character_state(world_id, character_id)

        memory = None
        if request.recall:
            memory = await self.flash_service.recall_memory(world_id, character_id, request.recall)

        prompt = None
        if request.include_prompt:
            prompt = self.context_builder.build_prompt(
                profile=profile,
                state=state,
                scene=request.scene,
                memory=memory,
                recent_conversation=request.recent_conversation,
            )

        return ProContextResponse(
            profile=profile,
            state=state,
            scene=request.scene,
            memory=memory,
            assembled_prompt=prompt,
        )
