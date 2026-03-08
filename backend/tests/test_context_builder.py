"""Tests for AgentContextBuilder — 7-layer context assembly and system prompts."""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.bootstrap import build_default_world, build_runtime_for_world
from app.game_core.content import WorldInstance
from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.context_builder import (
    AgentContextBuilder,
    NpcFullContext,
    TeammateFull,
    _build_npc_prompt_text,
    _build_teammate_prompt_text,
)
from app.game_core.state import StateContainer
from app.game_core.narrative.companion_runtime import TickRecord


class _FakeCompanionInstance:
    def __init__(self, events: list[TickRecord]) -> None:
        self._events = events

    def get_recent_events(self, n: int = 10) -> list[TickRecord]:
        return list(self._events[-n:])


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _test_world_tags() -> dict[str, Any]:
    return {
        "profession": {"id": "profession", "tags": ["merchant", "warrior"]},
        "ancestry": {"id": "ancestry", "tags": ["human"]},
        "affinity": {"id": "affinity", "tags": ["holy"]},
    }


def _world_with_characters() -> WorldInstance:
    return build_default_world(
        "test_world",
        world_data={
            "tags": _test_world_tags(),
            "characters": {
                "merchant_tom": {
                    "id": "merchant_tom",
                    "name": "Merchant Tom",
                    "personality": "A shrewd but fair merchant.",
                    "dialogue_style": "Speaks with a slight drawl.",
                    "tags": ["merchant", "human"],
                },
                "paladin_aria": {
                    "id": "paladin_aria",
                    "name": "Paladin Aria",
                    "personality": "A devout and brave paladin.",
                    "tags": ["holy", "warrior"],
                },
            },
        },
    )


def _state_with_relations(world: WorldInstance) -> StateContainer:
    runtime = build_runtime_for_world(world)
    state = runtime.state

    state.player.restore({
        "character_name": "Hero",
        "character_class": "warrior",
        "current_area": "town",
        "current_location": "market",
    })
    state.relations.restore({
        "npc_dispositions": {
            "merchant_tom": {"approval": 25, "trust": 15, "fear": 0, "romance": 0},
            "paladin_aria": {"approval": 40, "trust": 50, "fear": 0, "romance": 5},
        },
        "relationship_stages": {
            "merchant_tom": "acquaintance",
            "paladin_aria": "friend",
        },
        "npc_impressions": {
            "merchant_tom": ["Bought a sword last time", "Seemed trustworthy"],
        },
    })
    return state


def _builder(world: WorldInstance | None = None, state: StateContainer | None = None):
    if world is None:
        world = _world_with_characters()
    if state is None:
        state = _state_with_relations(world)
    return AgentContextBuilder(world, state)


# ------------------------------------------------------------------
# TestLayerBuilders — verify 7-layer structure and role visibility
# ------------------------------------------------------------------


class TestLayerBuilders:
    def test_build_gm_context_has_all_layers(self) -> None:
        builder = _builder()
        ctx = builder.build_gm_context()

        assert set(ctx.keys()) == {
            "l0_world_constants",
            "l1_chapter_state",
            "l2_area_environment",
            "l3_location_details",
            "l4_dynamic_state",
            "l5_scene_bus",
            "l6_memory_recall",
            "l7_engine_result",
        }
        assert ctx["l6_memory_recall"] is None   # GM has no personal memory
        assert ctx["l1_chapter_state"] is not None
        assert ctx["l7_engine_result"] is not None

    def test_build_npc_context_l1_is_none(self) -> None:
        """NPC should not see chapter state (L1)."""
        builder = _builder()
        ctx = asyncio.run(builder.build_npc_context("merchant_tom"))

        assert ctx["l1_chapter_state"] is None
        assert ctx["l7_engine_result"] is None
        assert ctx["l6_memory_recall"] == {"hits": [], "source": "null"}

    def test_build_npc_context_l4_only_self_disposition(self) -> None:
        """NPC L4 should only contain self relationship data."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        builder = AgentContextBuilder(world, state)
        ctx = asyncio.run(builder.build_npc_context("merchant_tom"))

        l4 = ctx["l4_dynamic_state"]
        assert {"disposition", "stage", "impressions"}.issubset(l4.keys())
        assert l4["disposition"]["approval"] == 25
        assert l4["disposition"]["trust"] == 15
        assert l4["stage"] == "acquaintance"
        assert "Bought a sword last time" in l4["impressions"]
        # Must not expose other NPCs or global party data
        assert "party" not in l4
        assert "player" not in l4

    def test_build_teammate_context_l1_partial(self) -> None:
        """Teammate L1 should be partial (only available milestones)."""
        builder = _builder()
        ctx = asyncio.run(builder.build_teammate_context("paladin_aria"))

        l1 = ctx["l1_chapter_state"]
        assert l1 is not None
        assert "available_milestones" in l1
        # Must NOT include chapter_completion, strategy_notes etc.
        assert "chapter_completion" not in l1
        assert "strategy_notes" not in l1

    def test_build_teammate_context_l4_self_and_party(self) -> None:
        """Teammate L4 should have self_disposition + party_members."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        builder = AgentContextBuilder(world, state)
        ctx = asyncio.run(builder.build_teammate_context("paladin_aria"))

        l4 = ctx["l4_dynamic_state"]
        assert "self_disposition" in l4
        assert "party_members" in l4
        assert "companion_approval" in l4
        assert l4["self_disposition"]["approval"] == 40
        assert l4["self_disposition"]["trust"] == 50

    def test_build_gm_context_l7_includes_hints(self) -> None:
        builder = _builder()
        ctx = builder.build_gm_context(hints=["Player entered a dangerous area."])

        l7 = ctx["l7_engine_result"]
        assert l7["narrative_hints"] == ["Player entered a dangerous area."]

    def test_build_npc_context_l7_is_none(self) -> None:
        builder = _builder()
        ctx = asyncio.run(builder.build_npc_context("merchant_tom"))
        assert ctx["l7_engine_result"] is None

    def test_build_gm_context_l7_empty_without_hints(self) -> None:
        builder = _builder()
        ctx = builder.build_gm_context()
        assert ctx["l7_engine_result"]["narrative_hints"] == []

    def test_npc_unknown_defaults(self) -> None:
        """Unknown NPC id returns zero disposition, stranger stage."""
        builder = _builder()
        ctx = asyncio.run(builder.build_npc_context("unknown_npc"))

        l4 = ctx["l4_dynamic_state"]
        assert l4["disposition"] == {"approval": 0, "trust": 0, "fear": 0, "romance": 0}
        assert l4["stage"] == "stranger"
        assert l4["impressions"] == []


# ------------------------------------------------------------------
# TestVisibilityFiltering — L5 scene bus role filtering
# ------------------------------------------------------------------


class TestVisibilityFiltering:
    def _state_with_scene(self, world: WorldInstance) -> StateContainer:
        runtime = build_runtime_for_world(world)
        state = runtime.state
        state.player.restore({"current_area": "town"})
        # Add entries with different visibility levels
        from app.game_core.state.slices.scene import SceneEntry
        state.scene.add_entry(SceneEntry(
            source="player",
            content="Public action",
            visibility="public",
        ))
        state.scene.add_entry(SceneEntry(
            source="system",
            content="System event",
            visibility="system",
        ))
        state.scene.add_entry(SceneEntry(
            source="npc",
            content="Private to NPC",
            visibility="private",
            audience=["npc:merchant_tom"],
        ))
        return state

    def test_l5_gm_excludes_system_entries(self) -> None:
        world = _world_with_characters()
        state = self._state_with_scene(world)
        builder = AgentContextBuilder(world, state)
        ctx = builder.build_gm_context()

        contents = [e["content"] for e in ctx["l5_scene_bus"]["entries"]]
        assert "Public action" in contents
        assert "Private to NPC" in contents
        assert "System event" not in contents

    def test_l5_npc_filters_by_audience(self) -> None:
        """NPC should see public entries and private entries addressed to them."""
        world = _world_with_characters()
        state = self._state_with_scene(world)
        builder = AgentContextBuilder(world, state)
        ctx = asyncio.run(builder.build_npc_context("merchant_tom"))

        contents = [e["content"] for e in ctx["l5_scene_bus"]["entries"]]
        assert "Public action" in contents
        assert "Private to NPC" in contents  # addressed to npc:merchant_tom
        assert "System event" not in contents

    def test_l5_npc_cannot_see_others_private(self) -> None:
        """NPC should not see private entries addressed to other characters."""
        world = _world_with_characters()
        state = self._state_with_scene(world)
        builder = AgentContextBuilder(world, state)
        # paladin_aria is NOT the audience for the private entry
        ctx = asyncio.run(builder.build_npc_context("paladin_aria"))

        contents = [e["content"] for e in ctx["l5_scene_bus"]["entries"]]
        assert "Public action" in contents
        assert "Private to NPC" not in contents

    def test_l5_viewer_metadata(self) -> None:
        world = _world_with_characters()
        state = build_runtime_for_world(world).state
        builder = AgentContextBuilder(world, state)

        gm_ctx = builder.build_gm_context()
        assert gm_ctx["l5_scene_bus"]["viewer_role"] == "gm"
        assert gm_ctx["l5_scene_bus"]["viewer_id"] is None

        npc_ctx = asyncio.run(builder.build_npc_context("merchant_tom"))
        assert npc_ctx["l5_scene_bus"]["viewer_role"] == "npc"
        assert npc_ctx["l5_scene_bus"]["viewer_id"] == "merchant_tom"


# ------------------------------------------------------------------
# TestPromptBuilders — system prompt integration
# ------------------------------------------------------------------


class TestPromptBuilders:
    def test_build_npc_system_prompt_integrates_l4_data(self) -> None:
        """build_npc_system_prompt resolves disposition/stage/impressions from L4."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        builder = AgentContextBuilder(world, state)

        prompt = asyncio.run(builder.build_npc_system_prompt("merchant_tom"))

        assert prompt is not None
        assert "Merchant Tom" in prompt
        assert "shrewd" in prompt.lower()
        assert "acquaintance" in prompt
        assert "Approval: 25" in prompt
        assert "Trust: 15" in prompt
        assert "Bought a sword last time" in prompt

    def test_build_npc_system_prompt_none_for_unknown_npc(self) -> None:
        builder = _builder()
        assert asyncio.run(builder.build_npc_system_prompt("nonexistent")) is None

    def test_build_npc_system_prompt_uses_temporary_npc_profile(self) -> None:
        world = _world_with_characters()
        state = _state_with_relations(world)
        state.narrative_plan.add_temporary_npc(
            "temp_messenger",
            {
                "name": "Temp Messenger",
                "personality": "calm and direct.",
                "dialogue_hook": "I can guide you through the gate.",
                "tags": ["temporary"],
            },
        )
        builder = AgentContextBuilder(world, state)
        prompt = asyncio.run(builder.build_npc_system_prompt("temp_messenger"))

        assert prompt is not None
        assert "Temp Messenger" in prompt
        assert "calm and direct" in prompt
        assert "## Dialogue hook" in prompt
        assert "I can guide you through the gate." in prompt

    def test_build_npc_full_context_uses_temporary_npc_profile(self) -> None:
        world = _world_with_characters()
        state = _state_with_relations(world)
        state.narrative_plan.add_temporary_npc(
            "temp_guide",
            {
                "name": "Temp Guide",
                "personality": "old and patient.",
                "dialogue_hook": "Follow the old trail.",
                "tags": ["quest_giver"],
            },
        )
        builder = AgentContextBuilder(world, state)
        npc_full = asyncio.run(builder.build_npc_full_context("temp_guide"))

        assert npc_full is not None
        assert "Temp Guide" in npc_full.system_prompt
        assert "old and patient" in npc_full.system_prompt
        assert "Follow the old trail." in npc_full.system_prompt

    def test_build_teammate_system_prompt_integrates_disposition(self) -> None:
        world = _world_with_characters()
        state = _state_with_relations(world)
        builder = AgentContextBuilder(world, state)

        prompt = asyncio.run(builder.build_teammate_system_prompt("paladin_aria"))

        assert prompt is not None
        assert "Paladin Aria" in prompt
        assert "brave paladin" in prompt.lower()
        assert "40" in prompt   # approval
        assert "50" in prompt   # trust

    def test_build_teammate_system_prompt_none_for_unknown(self) -> None:
        builder = _builder()
        assert asyncio.run(builder.build_teammate_system_prompt("unknown_char")) is None

    def test_build_gm_reaction_prompt_returns_constant(self) -> None:
        from app.game_core.narrative.context_builder import GM_REACTION_PROMPT
        builder = _builder()
        assert builder.build_gm_reaction_prompt() is GM_REACTION_PROMPT


# ------------------------------------------------------------------
# TestAgentContextBuilder — AgentContext construction
# ------------------------------------------------------------------


class TestAgentContextConstructor:
    def test_build_agent_context_npc_role_and_metadata(self) -> None:
        builder = _builder()
        ctx = builder.build_agent_context("npc", "merchant_tom")

        assert isinstance(ctx, AgentContext)
        assert ctx.role == "npc"
        assert ctx.metadata["character_id"] == "merchant_tom"

    def test_build_agent_context_gm_no_metadata(self) -> None:
        builder = _builder()
        ctx = builder.build_agent_context("gm")

        assert ctx.role == "gm"
        assert ctx.metadata == {}
        assert ctx.execute_command is None

    def test_build_agent_context_teammate_role(self) -> None:
        builder = _builder()
        ctx = builder.build_agent_context("teammate", "paladin_aria")

        assert ctx.role == "teammate"
        assert ctx.metadata["character_id"] == "paladin_aria"

    def test_build_agent_context_with_execute_command(self) -> None:
        builder = _builder()
        sentinel = object()
        ctx = builder.build_agent_context("npc", "merchant_tom", execute_command=sentinel)
        assert ctx.execute_command is sentinel

    def test_build_agent_context_scene_entries_populated(self) -> None:
        world = _world_with_characters()
        state = build_runtime_for_world(world).state
        from app.game_core.state.slices.scene import SceneEntry
        state.scene.add_entry(SceneEntry(
            source="player", content="Hello!", visibility="public",
        ))
        builder = AgentContextBuilder(world, state)
        ctx = builder.build_agent_context("gm")

        assert len(ctx.scene_entries) == 1
        assert ctx.scene_entries[0]["content"] == "Hello!"


# ------------------------------------------------------------------
# TestPureFunctions — _build_npc_prompt_text, _build_teammate_prompt_text
# ------------------------------------------------------------------


class TestPurePromptFormatters:
    def test_npc_prompt_text_full_profile(self) -> None:
        prompt = _build_npc_prompt_text(
            npc_profile={"name": "Tom", "personality": "Shrewd merchant"},
            disposition={"approval": 30, "trust": 10, "fear": 0, "romance": 0},
            stage="friend",
            impressions=["Bought a sword", "Kind person"],
        )
        assert "Tom" in prompt
        assert "Shrewd merchant" in prompt
        assert "friend" in prompt
        assert "Approval: 30" in prompt
        assert "Bought a sword" in prompt
        assert "Kind person" in prompt

    def test_npc_prompt_text_empty_profile(self) -> None:
        prompt = _build_npc_prompt_text(
            npc_profile={},
            disposition={},
            stage="stranger",
            impressions=[],
        )
        assert "Unknown NPC" in prompt
        assert "stranger" in prompt
        assert "No previous memories" in prompt

    def test_npc_prompt_text_respects_passive_flag(self) -> None:
        prompt = _build_npc_prompt_text(
            npc_profile={"name": "Tom"},
            disposition={},
            stage="stranger",
            impressions=[],
            is_passive=True,
        )

        assert "You just witnessed a player action. You are NOT being spoken to directly." in prompt
        assert "MUST respond when spoken to" not in prompt

    def test_npc_prompt_mentions_join_party_tool_for_clear_invites(self) -> None:
        prompt = _build_npc_prompt_text(
            npc_profile={"name": "Tom"},
            disposition={"approval": 25, "trust": 20},
            stage="acquaintance",
            impressions=[],
        )

        assert "join_party" in prompt
        assert "Do not verbally agree to join the party unless you also call `join_party`." in prompt

    def test_teammate_prompt_text_includes_personality(self) -> None:
        prompt = _build_teammate_prompt_text(
            profile={"name": "Aria", "personality": "A brave paladin."},
            disposition={"approval": 50, "trust": 60},
        )
        assert "Aria" in prompt
        assert "brave paladin" in prompt
        assert "50" in prompt
        assert "60" in prompt
        assert "leave_party" in prompt


# ------------------------------------------------------------------
# TestL6Injection — L6 memory hits → system prompt (Phase 4)
# ------------------------------------------------------------------


class _StubRetriever:
    """Controllable memory retriever stub returning preset hits."""

    def __init__(self, hits: list[dict] | None = None) -> None:
        self.hits = hits or []
        self.call_count = 0

    async def retrieve(self, actor_id: str, keywords: list, context: dict) -> dict:
        self.call_count += 1
        return {"hits": list(self.hits), "source": "stub"}


def _one_hit(
    label: str = "Iron Sword",
    node_type: str = "item",
    description: str = "",
) -> dict:
    return {
        "node_id": label.lower().replace(" ", "_"),
        "node_type": node_type,
        "label": label,
        "tags": [],
        "activation": 0.8,
        "description": description,
        "metadata": {},
    }


class TestL6Injection:
    def test_npc_prompt_includes_knowledge_block_when_hits_present(self) -> None:
        """build_npc_system_prompt with a retriever returning hits → prompt contains block."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        builder = AgentContextBuilder(world, state)
        retriever = _StubRetriever(hits=[_one_hit("Iron Sword", "item")])

        prompt = asyncio.run(builder.build_npc_system_prompt(
            "merchant_tom", memory_retriever=retriever,
        ))

        assert prompt is not None
        assert "## Relevant world knowledge" in prompt
        assert "Iron Sword" in prompt

    def test_npc_prompt_no_knowledge_block_when_hits_empty(self) -> None:
        """Empty hits → no knowledge block in prompt."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        builder = AgentContextBuilder(world, state)
        retriever = _StubRetriever(hits=[])

        prompt = asyncio.run(builder.build_npc_system_prompt(
            "merchant_tom", memory_retriever=retriever,
        ))

        assert prompt is not None
        assert "## Relevant world knowledge" not in prompt

    def test_npc_prompt_hits_capped_at_5(self) -> None:
        """8 hits → only 5 rendered in prompt."""
        hits = [_one_hit(f"Item {i}", "item") for i in range(8)]
        world = _world_with_characters()
        state = _state_with_relations(world)
        builder = AgentContextBuilder(world, state)
        retriever = _StubRetriever(hits=hits)

        prompt = asyncio.run(builder.build_npc_system_prompt(
            "merchant_tom", memory_retriever=retriever,
        ))

        assert prompt is not None
        assert "Item 4" in prompt    # 5th item (0-indexed) → included
        assert "Item 5" not in prompt  # 6th item → capped out

    def test_npc_prompt_hit_with_description_formatted_correctly(self) -> None:
        """Hit with non-empty description → 'label (type): description' format."""
        hit = _one_hit("Merchant Tom", "character", description="A shrewd trader.")
        world = _world_with_characters()
        state = _state_with_relations(world)
        builder = AgentContextBuilder(world, state)
        retriever = _StubRetriever(hits=[hit])

        prompt = asyncio.run(builder.build_npc_system_prompt(
            "merchant_tom", memory_retriever=retriever,
        ))

        assert "Merchant Tom (character): A shrewd trader." in prompt

    def test_npc_prompt_hit_without_description_formatted_correctly(self) -> None:
        """Hit with empty description → 'label (type)' format (no colon)."""
        hit = _one_hit("Iron Sword", "item", description="")
        world = _world_with_characters()
        state = _state_with_relations(world)
        builder = AgentContextBuilder(world, state)
        retriever = _StubRetriever(hits=[hit])

        prompt = asyncio.run(builder.build_npc_system_prompt(
            "merchant_tom", memory_retriever=retriever,
        ))

        assert "Iron Sword (item)" in prompt
        assert "Iron Sword (item):" not in prompt  # no colon when no description

    def test_teammate_prompt_includes_knowledge_block_when_hits_present(self) -> None:
        """build_teammate_system_prompt with hits → prompt contains knowledge block."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        builder = AgentContextBuilder(world, state)
        retriever = _StubRetriever(hits=[_one_hit("Dark Dungeon", "area")])

        prompt = asyncio.run(builder.build_teammate_system_prompt(
            "paladin_aria", memory_retriever=retriever,
        ))

        assert prompt is not None
        assert "## Relevant world knowledge" in prompt
        assert "Dark Dungeon" in prompt

    def test_teammate_prompt_no_knowledge_block_when_hits_empty(self) -> None:
        """Teammate prompt with empty hits → no knowledge block."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        builder = AgentContextBuilder(world, state)
        retriever = _StubRetriever(hits=[])

        prompt = asyncio.run(builder.build_teammate_system_prompt(
            "paladin_aria", memory_retriever=retriever,
        ))

        assert prompt is not None
        assert "## Relevant world knowledge" not in prompt

    def test_build_npc_prompt_text_backward_compatible_no_hits_arg(self) -> None:
        """_build_npc_prompt_text without knowledge_hits → no error, no knowledge block."""
        prompt = _build_npc_prompt_text(
            npc_profile={"name": "Tom", "personality": "A merchant."},
            disposition={"approval": 0, "trust": 0, "fear": 0, "romance": 0},
            stage="stranger",
            impressions=[],
            # knowledge_hits deliberately omitted → default None
        )
        assert "Tom" in prompt
        assert "## Relevant world knowledge" not in prompt


# ------------------------------------------------------------------
# TestL1RoleFiltering — N-4 verification: NPC/Teammate cannot see L1
# ------------------------------------------------------------------


class TestL1RoleFiltering:
    """Verify that L1 (chapter state) is filtered per role.

    Design rule (设计规范 §2.2):
      - GM: receives full L1 (chapter completion, milestones, strategy notes)
      - NPC: l1_chapter_state=None (not visible)
      - Teammate: l1_chapter_state=None (not visible)
    """

    def _state_with_quest_data(self, world: WorldInstance) -> StateContainer:
        runtime = build_runtime_for_world(world)
        state = runtime.state
        state.player.restore({"character_name": "Hero", "current_area": "town"})
        state.quests.restore({
            "milestone_states": {"chapter_1_start": "completed"},
            "chapter_completion": {"chapter_1": 0.6},
            "dynamic_quests": {"q1": {"title": "Secret Quest", "status": "active"}},
        })
        return state

    def test_npc_context_l1_is_none(self) -> None:
        """build_npc_context() must return l1_chapter_state=None."""
        world = _world_with_characters()
        state = self._state_with_quest_data(world)
        builder = AgentContextBuilder(world, state)
        ctx = asyncio.run(builder.build_npc_context("merchant_tom"))
        assert ctx["l1_chapter_state"] is None

    def test_teammate_context_l1_is_partial(self) -> None:
        """build_teammate_context() returns partial L1 (only available_milestones).

        Design (§2.2): Teammates see available milestones but NOT
        chapter_completion, milestone_states, strategy_notes, or active quests.
        """
        world = _world_with_characters()
        state = self._state_with_quest_data(world)
        builder = AgentContextBuilder(world, state)
        ctx = asyncio.run(builder.build_teammate_context("paladin_aria"))
        l1 = ctx["l1_chapter_state"]
        # Partial L1 must exist
        assert l1 is not None
        assert "available_milestones" in l1
        # Full GM fields must NOT be present
        assert "chapter_completion" not in l1
        assert "milestone_states" not in l1
        assert "strategy_notes" not in l1
        assert "active_dynamic_quests" not in l1

    def test_gm_context_l1_is_populated(self) -> None:
        """GM context must include L1 data (positive control)."""
        world = _world_with_characters()
        state = self._state_with_quest_data(world)
        builder = AgentContextBuilder(world, state)
        ctx = builder.build_gm_context()
        assert ctx["l1_chapter_state"] is not None

    def test_npc_system_prompt_excludes_chapter_completion(self) -> None:
        """NPC system prompt must not contain chapter completion data."""
        world = _world_with_characters()
        state = self._state_with_quest_data(world)
        state.relations.restore({
            "npc_dispositions": {"merchant_tom": {"approval": 20, "trust": 10}},
            "relationship_stages": {"merchant_tom": "acquaintance"},
        })
        builder = AgentContextBuilder(world, state)
        prompt = asyncio.run(builder.build_npc_system_prompt("merchant_tom"))
        assert prompt is not None
        # chapter_completion and milestone data must not leak into NPC prompt
        assert "chapter_completion" not in prompt
        assert "milestone_states" not in prompt
        assert "strategy_notes" not in prompt

    def test_l1_visibility_differs_by_role(self) -> None:
        """NPC, Teammate, and GM must receive different L1 content.

        NPC: None | Teammate: partial (available_milestones only) | GM: full
        """
        world = _world_with_characters()
        state = self._state_with_quest_data(world)
        builder = AgentContextBuilder(world, state)
        npc_ctx = asyncio.run(builder.build_npc_context("merchant_tom"))
        tm_ctx = asyncio.run(builder.build_teammate_context("paladin_aria"))
        gm_ctx = builder.build_gm_context()

        # NPC sees nothing
        assert npc_ctx["l1_chapter_state"] is None
        # Teammate sees only milestones (no sensitive chapter data)
        tm_l1 = tm_ctx["l1_chapter_state"]
        assert tm_l1 is not None
        assert set(tm_l1.keys()) == {"available_milestones"}
        # GM sees everything
        gm_l1 = gm_ctx["l1_chapter_state"]
        assert gm_l1 is not None
        assert "chapter_completion" in gm_l1
        assert "strategy_notes" in gm_l1


# ------------------------------------------------------------------
# TestNpcFullContext — N-7: single-retriever NpcFullContext (D-N7)
# ------------------------------------------------------------------


class TestNpcFullContext:
    """Verify build_npc_full_context() returns correct data without double retrieve."""

    def test_returns_nonfull_object_with_seven_layer_keys(self) -> None:
        """NpcFullContext must expose system_prompt + all 7 layer keys."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        builder = AgentContextBuilder(world, state)

        npc_full = asyncio.run(builder.build_npc_full_context("merchant_tom"))

        assert npc_full is not None
        assert isinstance(npc_full, NpcFullContext)
        assert npc_full.system_prompt
        assert set(npc_full.layers.keys()) == {
            "l0_world_constants",
            "l1_chapter_state",
            "l2_area_environment",
            "l3_location_details",
            "l4_dynamic_state",
            "l5_scene_bus",
            "l6_memory_recall",
            "l7_engine_result",
        }

    def test_unknown_npc_returns_none(self) -> None:
        """Unknown NPC id → build_npc_full_context returns None."""
        builder = _builder()
        assert asyncio.run(builder.build_npc_full_context("nonexistent")) is None

    def test_no_double_retriever_call(self) -> None:
        """build_npc_full_context must call retriever exactly once (no double-call)."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        builder = AgentContextBuilder(world, state)
        retriever = _StubRetriever(hits=[_one_hit("Iron Sword", "item")])

        asyncio.run(builder.build_npc_full_context(
            "merchant_tom", memory_retriever=retriever,
        ))

        assert retriever.call_count == 1

    def test_system_prompt_matches_expected_data(self) -> None:
        """system_prompt integrates L4 relationship data exactly as build_npc_system_prompt."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        builder = AgentContextBuilder(world, state)

        npc_full = asyncio.run(builder.build_npc_full_context("merchant_tom"))

        assert npc_full is not None
        assert "Merchant Tom" in npc_full.system_prompt
        assert "acquaintance" in npc_full.system_prompt
        assert "Approval: 25" in npc_full.system_prompt
        assert "Trust: 15" in npc_full.system_prompt
        assert "Bought a sword last time" in npc_full.system_prompt

    def test_build_npc_full_context_passive_uses_passive_prompt_rules(self) -> None:
        world = _world_with_characters()
        state = _state_with_relations(world)
        builder = AgentContextBuilder(world, state)

        npc_full = asyncio.run(builder.build_npc_full_context(
            "merchant_tom",
            is_passive=True,
        ))

        assert npc_full is not None
        assert "You just witnessed a player action. You are NOT being spoken to directly." in npc_full.system_prompt
        assert "You MUST respond when spoken to" not in npc_full.system_prompt


# ------------------------------------------------------------------
# TestTeammateFull — N-7 Phase 2: single-retriever TeammateFull
# ------------------------------------------------------------------


class TestTeammateFull:
    """Verify build_teammate_full_context() returns correct data without double retrieve."""

    def test_returns_object_with_seven_layer_keys(self) -> None:
        """TeammateFull must expose system_prompt + all 7 layer keys."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        builder = AgentContextBuilder(world, state)

        tm_full = asyncio.run(builder.build_teammate_full_context("paladin_aria"))

        assert tm_full is not None
        assert isinstance(tm_full, TeammateFull)
        assert tm_full.system_prompt
        assert set(tm_full.layers.keys()) == {
            "l0_world_constants",
            "l1_chapter_state",
            "l2_area_environment",
            "l3_location_details",
            "l4_dynamic_state",
            "l5_scene_bus",
            "l6_memory_recall",
            "l7_engine_result",
        }

    def test_unknown_char_returns_none(self) -> None:
        """Unknown char_id → build_teammate_full_context returns None."""
        builder = _builder()
        assert asyncio.run(builder.build_teammate_full_context("nonexistent")) is None

    def test_no_double_retriever_call(self) -> None:
        """build_teammate_full_context must call retriever exactly once."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        builder = AgentContextBuilder(world, state)
        retriever = _StubRetriever(hits=[_one_hit("Holy Sword", "item")])

        asyncio.run(builder.build_teammate_full_context(
            "paladin_aria", memory_retriever=retriever,
        ))

        assert retriever.call_count == 1

    def test_system_prompt_contains_teammate_data(self) -> None:
        """system_prompt integrates L4 disposition data (approval/trust)."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        builder = AgentContextBuilder(world, state)

        tm_full = asyncio.run(builder.build_teammate_full_context("paladin_aria"))

        assert tm_full is not None
        assert "Paladin Aria" in tm_full.system_prompt
        assert "40" in tm_full.system_prompt   # approval
        assert "50" in tm_full.system_prompt   # trust

    def test_system_prompt_includes_companion_observation_context(self) -> None:
        """build_teammate_full_context injects recent companion observations from event log."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        builder = AgentContextBuilder(world, state)
        companion = _FakeCompanionInstance([
            TickRecord(
                tick=1,
                action_type="navigate",
                executed=True,
                summary="Scouted the northern ridge.",
                tags=["NAVIGATION"],
                involved_npcs=["paladin_aria"],
                has_rolls=False,
                event_transitions=[],
            )
        ])

        tm_full = asyncio.run(
            builder.build_teammate_full_context(
                "paladin_aria",
                companion_instance=companion,
            )
        )

        assert tm_full is not None
        assert "Recent observations" in tm_full.system_prompt


class TestLoreScopeFilter:
    """P3-7a: L0 lore is filtered by scope (global/area only)."""

    @staticmethod
    def _world_with_lore(entries: list[dict]) -> WorldInstance:
        return build_default_world(
            "test_world",
            world_data={
                "lore": {e["id"]: e for e in entries},
            },
        )

    def test_global_lore_always_included(self) -> None:
        world = self._world_with_lore([
            {"id": "rule_global", "content": "Global rule", "scope": "global"},
        ])
        runtime = build_runtime_for_world(world)
        runtime.state.player.restore({"current_area": "dungeon"})
        builder = AgentContextBuilder(world, runtime.state)
        l0 = builder._build_l0()
        ids = [e.id for e in l0["lore"]]
        assert "rule_global" in ids

    def test_area_lore_included_when_matching_area(self) -> None:
        world = self._world_with_lore([
            {"id": "dungeon_lore", "content": "Dungeon secret", "scope": "area", "scope_id": "dungeon"},
        ])
        runtime = build_runtime_for_world(world)
        runtime.state.player.restore({"current_area": "dungeon"})
        builder = AgentContextBuilder(world, runtime.state)
        l0 = builder._build_l0()
        ids = [e.id for e in l0["lore"]]
        assert "dungeon_lore" in ids

    def test_area_lore_excluded_when_different_area(self) -> None:
        world = self._world_with_lore([
            {"id": "dungeon_lore", "content": "Dungeon secret", "scope": "area", "scope_id": "dungeon"},
        ])
        runtime = build_runtime_for_world(world)
        runtime.state.player.restore({"current_area": "town"})
        builder = AgentContextBuilder(world, runtime.state)
        l0 = builder._build_l0()
        ids = [e.id for e in l0["lore"]]
        assert "dungeon_lore" not in ids
