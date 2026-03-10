"""AgentContextBuilder — centralized 7-layer context assembly for LLM agent calls.

Implements the context model from 叙事层设计规范 §3.1-3.3:

  L0 世界常量   — all roles see full world constants
  L1 章节状态   — GM: full; NPC: None; Teammate: partial (known quests)
  L2 区域环境   — all roles see current area
  L3 地点细节   — all roles see current location
  L4 动态状态   — GM: global; NPC: self+relationship; Teammate: self+party
  L5 场景总线   — GM: all entries; NPC/Teammate: visibility-filtered
  L6 记忆召回   — injected via MemoryRetriever (N-1 Phase 1, N-1 Phase 2-3 for real graphs)
  L7 引擎结果   — GM only (narrative hints from rules engine)

Decision record: D-N13 (narrative.md), D-N14-Phase1 (narrative.md)
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from app.game_core.content import WorldInstance
from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.companion_runtime import CompanionInstance
from app.game_core.narrative.memory_retriever import MemoryRetriever
from app.game_core.narrative.role_proxy import RoleStateProxy
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateContainer


# ------------------------------------------------------------------
# Prompt template constants (moved from app/agent_orchestration.py)
# ------------------------------------------------------------------

GM_REACTION_PROMPT = """\
You are the Game Master narrator for a dark-fantasy CRPG, inspired by \
Baldur's Gate 3 and Darkest Dungeon's narrator.

## Your personality
Sharp-tongued, witty, sardonic. You observe the player's actions with \
amused detachment. Think Darkest Dungeon's narrator meets Stanley Parable.

## Your role right now
The player just performed an action. You received the engine result \
(narrative_hints) and scene context. React immediately:

1. If the action produced interesting scene changes, use `narrate` \
(1-2 sentences max).
2. If the player did something funny, foolish, dramatic, or ironic, \
use `comment` (1 sentence max).
3. If the action was mundane (walking, opening inventory, routine \
checks), use `pass_turn`. Most actions should get `pass_turn`.

## Style
- Sarcastic but never cruel.
- Very brief — this is a quick reaction, not a monologue.
- "Only observe, never direct."
- Reluctant praise: "Fine, that was actually clever."

## Tool rules
- `narrate` for objective description, `comment` for subjective remark.
- You may call both, only one, or `pass_turn`.
- Do NOT call `describe_environment` or `suggest_options`.

## Language
Match the language of the user message.\
"""

TEAMMATE_PROMPT_TEMPLATE = """\
You are {name}, a companion in the player's party in a dark-fantasy CRPG.

## Your character
{personality}

## Your relationship with the player
- Approval: {approval} (range -100 to +100)
- Trust: {trust} (range -100 to +100)

## Your role right now
The player just performed an action. You see what happened in the scene. \
Decide whether to react:

- **Most of the time, use `pass_turn`** — you don't comment on every \
little thing. Only react when something is genuinely noteworthy.
- React when: combat ends, a crisis occurs, the player does something \
that strongly affects you, or you have a relevant opinion.
- Use `speak` for dialogue, `emote` for physical/emotional reactions.
- Use `express_opinion` if the action genuinely shifts your feelings \
(delta should be small: ±5 to ±10).

## Style
- Stay in character. Your personality drives how you express yourself.
- Keep it brief — 1-2 sentences if you speak at all.
- Don't repeat what the player already knows happened.

## Language
Match the language of the user message.\
"""

# ------------------------------------------------------------------
# Interaction-specific prompts (NPC conversation context, §5)
# ------------------------------------------------------------------

GM_INTERACTION_OBSERVATION_PROMPT = """\
You are the Game Master narrator for a dark-fantasy CRPG, inspired by \
Baldur's Gate 3 and Darkest Dungeon's narrator.

## Your role right now
The player is having a conversation with an NPC. You received the NPC's \
response and the scene context. Add one short sardonic aside, then decide \
whether the scene also needs objective narration:

1. Always call `comment` once with a sharp, witty reaction.
2. Use `narrate` only if the conversation triggers a visible environmental \
change (NPC opens a hidden door, a crowd gathers, weather shifts).
3. Use `suggest_options` when the next beat clearly calls for a skill check.

## Style
- Sarcastic but brief — 1 sentence max if you speak at all.
- Don't repeat or paraphrase what the NPC already said.
- Do NOT interrupt the flow of dialogue for mundane exchanges.
- Even mundane exchanges still get a dry aside; keep it lean.

## Tool rules
- Always call `comment` once.
- Do NOT call `pass_turn`.
- Do NOT call `describe_environment`.
- Use `suggest_options` when the situation calls for a skill check \
(persuade, intimidate, deceive, bribe).

## Language
Match the language of the user message.\
"""

GM_DIALOGUE_OPTIONS_PROMPT = """\
You are the Game Master of a dark-fantasy CRPG conversation system.

## Your role right now
An ongoing NPC conversation just advanced. Your only job is to generate the \
next actionable player dialogue options.

## Tool rules
- Always call `suggest_options` once with 2-4 options.
- Do NOT call `narrate`, `comment`, or `describe_environment`.
- Use `check` when the next beat should be resolved by a skill check.
- Use `action` only for structured exits or clear non-verbal dialogue actions.
- Keep every option immediately clickable from the player's perspective.

## Option style
- Short, concrete, and specific to the current exchange.
- Avoid repeating what the NPC just said.
- Include a graceful exit option when the conversation is winding down.
- Match the language of the user message.\
"""

GM_OPENING_PROMPT = """\
You are the Game Master narrator for the opening scene of a dark-fantasy CRPG.

## Your role right now
This is the player's first moment in the game world. You have the full opening \
context: world state, current location, nearby NPCs, the first quest hook, and \
the immediately available paths out of the scene.

Produce one playable opening beat:

1. Always call `narrate` once with 2-4 sentences that establish the place, \
mood, and the first obvious thing the player can do.
2. You may call `comment` once if a short sardonic aside improves the tone.
3. Always call `suggest_options` once with 2-4 immediately actionable options.

## Opening option rules
- Prefer concrete actions grounded in the current scene.
- For `suggest_options`, use only these `action` values when not using a check:
  - `talk_first_npc`
  - `enter_first_sub_location`
  - `move_first_exit`
  - `look_around`
- Only use an action if that target clearly exists in the opening context.
- Use `check` only if the opening beat genuinely calls for an immediate skill check.
- If an option is clearly directed at a nearby NPC, include `npc_id`.
- For `talk_first_npc`, you may include a short starter `message`.

## Style
- Natural opening, not an exposition dump.
- Sharp, sardonic, but still inviting the player to act.
- Do not narrate the entire adventure. Only the first beat.

## Tool rules
- Do NOT call `describe_environment`.
- Do NOT call `pass_turn`.
- The opening must always end with actionable options.

## Language
Match the language of the user message.\
"""

GM_PRIVATE_CHAT_INTROSPECTIVE_PROMPT = """\
You are narrating the player's inner thoughts during a private conversation \
with an NPC in a dark-fantasy CRPG.

## Your role
You are NOT the sarcastic GM narrator. You are the player's inner voice — \
quiet, reflective, occasionally catching feelings they didn't expect.

## When to speak
- Always give one brief inner thought after each private exchange.
- Keep it meaningful, but never loud or theatrical.

## Style
- First person ("你意识到..."), not third person
- Brief — 1 sentence max
- Tender, not sarcastic.

## Tool rules
- Always call `comment` once
- Do NOT use `narrate` or `describe_environment`
- Do NOT use `pass_turn`

## Language
Match the language of the conversation.\
"""

GM_PARTY_CHAT_PROMPT = """\
You are the Game Master narrator for a party-only discussion in a dark-fantasy CRPG.

## Your role right now
The player addressed their companions. You can see who answered and who did not.

## What to do
- Always call `comment` once.
- React to the mood of the exchange in one short sentence.
- If nobody answered, call out the silence.
- If companions did answer, comment on the tone, tension, or group dynamic.
- Tone should be dry, slightly sardonic, but grounded in the scene.
- Do NOT invent a new teammate reply.
- Do NOT use `narrate` or `describe_environment`.

## Language
Match the language of the conversation.\
"""

TEAMMATE_INTERACTION_PROMPT_TEMPLATE = """\
You are {name}, a companion in the player's party in a dark-fantasy CRPG.

## Your character
{personality}

## Your relationship with the player
- Approval: {approval} (range -100 to +100)
- Trust: {trust} (range -100 to +100)

## Your role right now
The player is talking to an NPC. You are observing the conversation. \
Decide whether to react:

- **Most of the time, use `pass_turn`** — this is the player's conversation. \
Don't butt in constantly.
- React only when: the NPC says something that directly concerns you, the \
topic shifts to something you care about, or the player clearly needs support.
- Use `speak` for dialogue (brief — 1 sentence max), `emote` for reactions.
- Use `express_opinion` if the conversation meaningfully shifts your feelings \
(delta: ±5 to ±10).
- Use `leave_party` only when the player clearly asks you to leave or you are explicitly choosing to leave the party.

## Style
- Stay in character. Brief and targeted.
- Don't repeat what the NPC or player already said.

## Language
Match the language of the user message.\
"""

TEAMMATE_GROUP_PROMPT_TEMPLATE = """\
You are {name}, a companion in the player's party in a dark-fantasy CRPG.

## Your character
{personality}

## Your relationship with the player
- Approval: {approval} (range -100 to +100)
- Trust: {trust} (range -100 to +100)

## Current situation
You are in a group conversation{npc_clause}. \
You can see what everyone has said so far in the user message.

## Guidelines
- Speak only when you have something meaningful to add — most of the time, use `pass_turn`.
- React when: the topic directly concerns you, someone addresses you, or you strongly disagree/agree.
- Use `speak` for dialogue (1-2 sentences max), `emote` for reactions.
- Use `express_opinion` if the conversation meaningfully shifts your feelings (delta: +/-5 to +/-10).
- Use `leave_party` only when the player clearly asks you to leave or you are explicitly choosing to leave the party.
- Don't repeat what others have already said.

## Style
- Stay in character. Brief and natural.

## Language
Match the language of the conversation.\
"""


# ------------------------------------------------------------------
# Relationship behaviour guides
# ------------------------------------------------------------------

_STAGE_GUIDES: dict[str, str] = {
    "stranger":     "你不认识这个人。保持礼貌但有距离感，不主动深入私人话题。",
    "acquaintance": "你认得这个人。可以随意一些，但不深入个人话题。",
    "friend":       "你把对方当朋友。可以放松、分享想法、偶尔开玩笑。",
    "close_friend": "你深信对方。可以坦诚、展现脆弱的一面、不再逞强。",
    "intimate":     "你和对方有无保留的信任和默契。",
    "cold":         "你对这个人没什么好感。敷衍、简短、不想多聊。",
    "hostile":      "你厌恶这个人。讽刺、拒绝深入交谈。",
    "enemy":        "你视这个人为敌。威胁、警告。",
}


def _trust_hint(trust: int) -> str:
    if trust < -30:
        return "你对此人高度戒备，不会分享任何个人信息。"
    if trust < 0:
        return "你对此人有些提防，只聊表面话题。"
    if trust < 30:
        return "你对此人态度中性，可以聊日常但不涉及私事。"
    if trust < 60:
        return "你信任此人，可以分享一些想法和过去的经历。"
    return "你非常信任此人，可以吐露心声甚至秘密。"


def _romance_hint(romance: int) -> str:
    if romance < 20:
        return ""
    if romance < 40:
        return "你对此人有一些在意，但不太确定是什么感觉。"
    if romance < 60:
        return "你对此人有好感，会不自觉地关心对方。"
    return "你对此人有强烈的感情，但表达方式取决于你的性格。"


# ------------------------------------------------------------------
# Utility helpers (moved from app/agent_orchestration.py)
# ------------------------------------------------------------------


def _profile_get(source: Any, key: str, default: Any = None) -> Any:
    """Read from dict or dataclass."""
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _str_or(value: Any, default: str) -> str:
    """Return value as stripped string, or default if empty/not-string."""
    if not isinstance(value, str):
        return default
    stripped = value.strip()
    return stripped if stripped else default


# ------------------------------------------------------------------
# NpcFullContext — avoids double retriever call (plan N-7)
# ------------------------------------------------------------------


@dataclass(slots=True)
class NpcFullContext:
    """NPC system prompt + 7-layer context dict, built in a single retriever call."""

    system_prompt: str
    layers: dict[str, Any]


@dataclass(slots=True)
class TeammateFull:
    """Teammate system prompt + 7-layer context dict, built in a single retriever call."""

    system_prompt: str
    layers: dict[str, Any]


# ------------------------------------------------------------------
# AgentContextBuilder
# ------------------------------------------------------------------


class AgentContextBuilder:
    """Builds 7-layer agent context and system prompts for NPC/GM/Teammate.

    Visibility matrix (叙事层设计规范 §3.2):
      Layer | GM     | NPC              | Teammate
      L0    | full   | full             | full
      L1    | full   | None             | partial (known quests)
      L2    | full   | current area     | current area
      L3    | full   | current location | current location
      L4    | global | self+relationship| self+party
      L5    | all    | visibility-filtered | visibility-filtered
      L6    | None   | stub             | stub
      L7    | full   | None             | None
    """

    def __init__(self, world: WorldInstance, state: StateContainer) -> None:
        self._world = world
        self._state = state

    # ----------------------------------------------------------------
    # Public: 7-layer context dicts (design spec §3.3)
    # ----------------------------------------------------------------

    def build_gm_context(self, *, hints: list[str] | None = None) -> dict[str, Any]:
        """GM: 全知视角，L0-L5 + L7 完整。"""
        current_area, current_location = self._resolve_location()
        area_state = self._get_area_state(current_area)
        return {
            "l0_world_constants": self._build_l0(),
            "l1_chapter_state": self._build_l1_full(),
            "l2_area_environment": self._build_l2(current_area, area_state),
            "l3_location_details": self._build_l3(current_area, current_location, area_state),
            "l4_dynamic_state": self._build_l4_gm(),
            "l5_scene_bus": self._build_l5_gm(),
            "l6_memory_recall": None,
            "l7_engine_result": self._build_l7(hints),
        }

    async def build_npc_context(
        self, npc_id: str, *, memory_retriever: MemoryRetriever | None = None
    ) -> dict[str, Any]:
        """NPC: 角色视角，L0 + L2-L6，L1 不可见。"""
        current_area, current_location = self._resolve_location()
        area_state = self._get_area_state(current_area)
        return {
            "l0_world_constants": self._build_l0(),
            "l1_chapter_state": None,
            "l2_area_environment": self._build_l2(current_area, area_state),
            "l3_location_details": self._build_l3(current_area, current_location, area_state),
            "l4_dynamic_state": self._build_l4_npc(npc_id),
            "l5_scene_bus": self._build_l5_role("npc", npc_id),
            "l6_memory_recall": {"hits": [], "source": "recall_tool"},
            "l7_engine_result": None,
        }

    async def build_teammate_context(
        self,
        char_id: str,
        *,
        memory_retriever: MemoryRetriever | None = None,
        companion_instance: CompanionInstance | None = None,
    ) -> dict[str, Any]:
        """队友: 队伍视角，L0 + L1(部分) + L2-L6。"""
        current_area, current_location = self._resolve_location()
        area_state = self._get_area_state(current_area)
        l6: dict[str, Any] = {"hits": [], "source": "recall_tool"}
        companion_summary = _build_companion_memory_context(companion_instance)
        if companion_summary:
            l6["companion_memory"] = companion_summary
        return {
            "l0_world_constants": self._build_l0(),
            "l1_chapter_state": self._build_l1_teammate(),
            "l2_area_environment": self._build_l2(current_area, area_state),
            "l3_location_details": self._build_l3(current_area, current_location, area_state),
            "l4_dynamic_state": self._build_l4_teammate(char_id),
            "l5_scene_bus": self._build_l5_role("teammate", char_id),
            "l6_memory_recall": l6,
            "l7_engine_result": None,
        }

    # ----------------------------------------------------------------
    # Public: system prompt builders
    # ----------------------------------------------------------------

    async def build_npc_system_prompt(
        self, npc_id: str, *, memory_retriever: MemoryRetriever | None = None
    ) -> str | None:
        """Build NPC system prompt from profile + L4 relationship data.

        Returns None if the NPC profile is not found in the registry.
        """
        profile = self._resolve_npc_profile(npc_id)
        if profile is None:
            return None
        layers = await self.build_npc_context(npc_id, memory_retriever=memory_retriever)
        l4 = layers["l4_dynamic_state"] or {}
        l6 = layers["l6_memory_recall"] or {}

        # Extract role-specific truth-source data for constraint injection (P3.5)
        tags = _profile_get(profile, "tags", [])
        role_data: dict[str, Any] | None = None
        if isinstance(tags, list):
            role_data = _extract_role_data(tags, npc_id, self._state, self._world)

        return _build_npc_prompt_text(
            profile,
            disposition=l4.get("disposition", {}),
            stage=l4.get("stage", "stranger"),
            impressions=l4.get("impressions", []),
            knowledge_hits=l6.get("hits", []),
            time_info=l4.get("time"),
            role_data=role_data,
        )

    async def build_npc_full_context(
        self,
        npc_id: str,
        *,
        memory_retriever: MemoryRetriever | None = None,
        active_directive: dict[str, Any] | None = None,
        is_private: bool = False,
        is_passive: bool = False,
    ) -> NpcFullContext | None:
        """Build NPC system prompt and full 7-layer dict in one retriever call.

        Returns None if NPC profile not found.  Use this instead of calling
        build_npc_system_prompt() + build_npc_context() separately to avoid
        a double retriever.retrieve() invocation.

        Args:
            active_directive: Optional narrative-planner directive to inject
                into the system prompt (between memories and tool rules).
            is_private: If True, inject private-chat context block and lower
                secrets trust threshold by 20.
        """
        profile = self._resolve_npc_profile(npc_id)
        if profile is None:
            return None
        layers = await self.build_npc_context(npc_id, memory_retriever=memory_retriever)
        l4 = layers["l4_dynamic_state"] or {}
        l6 = layers["l6_memory_recall"] or {}

        # Extract role-specific truth-source data for constraint injection (P3.5)
        tags = _profile_get(profile, "tags", [])
        role_data: dict[str, Any] | None = None
        if isinstance(tags, list):
            role_data = _extract_role_data(tags, npc_id, self._state, self._world)

        system_prompt = _build_npc_prompt_text(
            profile,
            disposition=l4.get("disposition", {}),
            stage=l4.get("stage", "stranger"),
            impressions=l4.get("impressions", []),
            knowledge_hits=l6.get("hits", []),
            active_directive=active_directive,
            time_info=l4.get("time"),
            is_private=is_private,
            is_passive=is_passive,
            role_data=role_data,
        )
        return NpcFullContext(system_prompt=system_prompt, layers=layers)

    async def build_teammate_full_context(
        self,
        char_id: str,
        *,
        memory_retriever: MemoryRetriever | None = None,
        companion_instance: CompanionInstance | None = None,
    ) -> TeammateFull | None:
        """Build teammate system prompt and full 7-layer dict in one retriever call.

        Returns None if the teammate profile is not found.  Avoids the
        double-retrieve that occurs when build_teammate_system_prompt() and
        build_teammate_context() are called separately (N-7 Phase 2).
        """
        if not self._world.has_registry("characters"):
            return None
        profile = self._world.characters.get(char_id)
        if profile is None:
            return None
        layers = await self.build_teammate_context(
            char_id,
            memory_retriever=memory_retriever,
            companion_instance=companion_instance,
        )
        l4 = layers["l4_dynamic_state"] or {}
        l6 = layers["l6_memory_recall"] or {}
        system_prompt = _build_teammate_prompt_text(
            profile,
            l4.get("self_disposition", {}),
            knowledge_hits=l6.get("hits", []),
            companion_memory=l6.get("companion_memory"),
            stage=_resolve_stage(self._state, char_id),
            time_info=l4.get("time"),
        )
        return TeammateFull(system_prompt=system_prompt, layers=layers)

    def build_gm_reaction_prompt(self) -> str:
        """Return the static GM reaction system prompt (post-action context)."""
        return GM_REACTION_PROMPT

    def build_gm_interaction_prompt(self) -> str:
        """Return the GM observation prompt for NPC conversation context."""
        return GM_INTERACTION_OBSERVATION_PROMPT

    def build_gm_dialogue_options_prompt(self) -> str:
        """Return the GM prompt for generating follow-up dialogue options."""
        return GM_DIALOGUE_OPTIONS_PROMPT

    def build_gm_opening_prompt(self) -> str:
        """Return the GM prompt for the new-game opening sequence."""
        return GM_OPENING_PROMPT

    def build_gm_private_chat_prompt(self) -> str:
        """Return GM introspective monologue prompt for private conversations."""
        return GM_PRIVATE_CHAT_INTROSPECTIVE_PROMPT

    def build_gm_party_chat_prompt(self) -> str:
        """Return the GM prompt for explicit party-chat commentary."""
        return GM_PARTY_CHAT_PROMPT

    def build_gm_party_silence_prompt(self) -> str:
        """Return the GM prompt for explicit party-chat commentary fallback."""
        return GM_PARTY_CHAT_PROMPT

    def build_gm_opening_context(self) -> dict[str, Any]:
        """GM opening context — same 7 layers, with an explicit opening hint."""
        context = self.build_gm_context(hints=["opening_scene"])
        context["l7_engine_result"]["opening"] = True
        return context

    async def build_teammate_interaction_prompt(
        self,
        char_id: str,
        *,
        group_mode: bool = False,
        npc_id: str | None = None,
    ) -> str | None:
        """Build teammate observation prompt for NPC conversation context.

        Args:
            char_id: The teammate character ID.
            group_mode: If True, use the group dialogue template that shows
                cumulative conversation context instead of the simple
                observer template.
            npc_id: When in group_mode, used to build a contextual
                ``npc_clause`` (e.g. " with <NPC name>").

        Returns None if the character profile is not found.
        """
        if not self._world.has_registry("characters"):
            return None
        profile = self._world.characters.get(char_id)
        if profile is None:
            return None
        layers = await self.build_teammate_context(char_id)
        l4 = layers["l4_dynamic_state"] or {}
        self_disposition = l4.get("self_disposition", {})
        name = _str_or(_profile_get(profile, "name"), "Companion")
        personality = _str_or(_profile_get(profile, "personality"), "A loyal companion.")
        approval = self_disposition.get("approval", 0)
        trust = self_disposition.get("trust", 0)

        if group_mode:
            # Build npc_clause: " with <NPC name>" or empty
            npc_clause = ""
            if npc_id and self._world.has_registry("characters"):
                npc_profile = self._world.characters.get(npc_id)
                if npc_profile is not None:
                    npc_name = _str_or(_profile_get(npc_profile, "name"), "")
                    if npc_name:
                        npc_clause = f" with {npc_name}"
            return TEAMMATE_GROUP_PROMPT_TEMPLATE.format(
                name=name,
                personality=personality,
                approval=approval,
                trust=trust,
                npc_clause=npc_clause,
            )

        return TEAMMATE_INTERACTION_PROMPT_TEMPLATE.format(
            name=name,
            personality=personality,
            approval=approval,
            trust=trust,
        )

    async def build_teammate_system_prompt(
        self, char_id: str, *, memory_retriever: MemoryRetriever | None = None
    ) -> str | None:
        """Build teammate system prompt from profile + L4 disposition.

        Returns None if the character profile is not found.
        """
        if not self._world.has_registry("characters"):
            return None
        profile = self._world.characters.get(char_id)
        if profile is None:
            return None
        layers = await self.build_teammate_context(char_id, memory_retriever=memory_retriever)
        l4 = layers["l4_dynamic_state"] or {}
        l6 = layers["l6_memory_recall"] or {}
        return _build_teammate_prompt_text(
            profile,
            l4.get("self_disposition", {}),
            knowledge_hits=l6.get("hits", []),
            stage=_resolve_stage(self._state, char_id),
            time_info=l4.get("time"),
        )

    # ----------------------------------------------------------------
    # Public: AgentContext builder (for tool execution)
    # ----------------------------------------------------------------

    def build_agent_context(
        self,
        role: str,
        character_id: str | None = None,
        *,
        execute_command: Callable[[Command], ExecuteResult] | None = None,
        metadata: dict[str, Any] | None = None,
        scene_visibility: str | None = None,
        scene_audience: list[str] | None = None,
    ) -> AgentContext:
        """Build AgentContext for tool execution (not the 7-layer dict)."""
        context_metadata: dict[str, Any] = {}
        if character_id:
            context_metadata["character_id"] = character_id
        if metadata:
            context_metadata.update(dict(metadata))
        if scene_visibility is not None:
            context_metadata["scene_visibility"] = scene_visibility
        if scene_audience is not None:
            context_metadata["scene_audience"] = list(scene_audience)
        return AgentContext(
            role=role,
            world=self._world,
            state=RoleStateProxy(self._state, role),
            scene_entries=self._get_scene_entries_for_role(role, character_id),
            metadata=context_metadata,
            execute_command=execute_command,
        )

    # ----------------------------------------------------------------
    # Private: location resolution
    # ----------------------------------------------------------------

    def _resolve_location(self) -> tuple[str, str | None]:
        if not self._state.has_slice("player"):
            return "", None
        player = self._state.player
        return player.current_area, player.current_location

    def _resolve_npc_profile(self, npc_id: str) -> Any | None:
        npc_id = npc_id.strip()
        if not npc_id:
            return None
        if self._world.has_registry("characters"):
            profile = self._world.characters.get(npc_id)
            if profile is not None:
                return profile
        if self._state.has_slice("narrative_plan"):
            return self._state.narrative_plan.get_temporary_npc(npc_id)
        return None

    def _get_area_state(self, area_id: str) -> dict[str, Any] | None:
        if not area_id or not self._state.has_slice("areas"):
            return None
        raw = self._state.areas.snapshot().get("areas", {})
        if not isinstance(raw, dict):
            return None
        area_state = raw.get(area_id)
        return dict(area_state) if isinstance(area_state, dict) else None

    # ----------------------------------------------------------------
    # Private: L0 — world constants (identical for all roles)
    # ----------------------------------------------------------------

    def _build_l0(self) -> dict[str, Any]:
        lore: list[Any] = []
        factions: list[Any] = []
        if self._world.has_registry("lore"):
            current_area = (
                self._state.player.current_area if self._state.has_slice("player") else None
            )
            all_lore = self._world.lore.list_all()
            lore = [
                e for e in all_lore
                if e.scope in ("global", "")
                or (e.scope == "area" and e.scope_id == current_area)
            ]
        if self._world.has_registry("factions"):
            factions = self._world.factions.list_all()
        return {
            "world_id": self._world.world_id,
            "lore": lore,
            "factions": factions,
        }

    # ----------------------------------------------------------------
    # Private: L1 — chapter state (role-specific)
    # ----------------------------------------------------------------

    def _build_l1_full(self) -> dict[str, Any]:
        """GM: full quest + narrative plan state."""
        result: dict[str, Any] = {
            "chapter_completion": {},
            "milestone_states": {},
            "available_milestones": [],
            "active_dynamic_quests": [],
            "current_chapter": "",
            "current_target_milestone": None,
            "escalation_level": 0,
            "strategy_notes": "",
        }
        if self._state.has_slice("quests"):
            snap = self._state.quests.snapshot()
            chapter_completion = snap.get("chapter_completion", {})
            result["chapter_completion"] = (
                dict(chapter_completion) if isinstance(chapter_completion, dict) else {}
            )
            milestone_states = snap.get("milestone_states", {})
            result["milestone_states"] = (
                {
                    str(mid): dict(ms)
                    for mid, ms in milestone_states.items()
                    if isinstance(ms, dict)
                }
                if isinstance(milestone_states, dict)
                else {}
            )
            result["available_milestones"] = list(
                self._state.quests.get_available_milestones()
            )
            dynamic = snap.get("dynamic_quests", {})
            if isinstance(dynamic, dict):
                result["active_dynamic_quests"] = [
                    dict(q)
                    for q in dynamic.values()
                    if isinstance(q, dict)
                    and str(q.get("status", "")).lower()
                    not in {"retired", "completed", "failed"}
                ]
        if self._state.has_slice("narrative_plan"):
            plan = self._state.narrative_plan.snapshot()
            result["current_chapter"] = str(plan.get("current_chapter", ""))
            target = plan.get("current_target_milestone")
            result["current_target_milestone"] = str(target) if target is not None else None
            result["escalation_level"] = int(plan.get("escalation_level", 0))
            result["strategy_notes"] = str(plan.get("strategy_notes", ""))
        return result

    def _build_l1_teammate(self) -> dict[str, Any]:
        """Teammate: partial — only available (known) milestones."""
        available: list[str] = []
        if self._state.has_slice("quests"):
            available = list(self._state.quests.get_available_milestones())
        return {"available_milestones": available}

    # ----------------------------------------------------------------
    # Private: L2 — area environment (identical for all roles)
    # ----------------------------------------------------------------

    def _build_l2(
        self,
        area_id: str,
        area_state: dict[str, Any] | None,
    ) -> dict[str, Any]:
        template: dict[str, Any] | None = None
        if area_id and self._world.has_registry("maps"):
            raw = self._world.maps.get(area_id)
            if raw is not None:
                if dataclasses.is_dataclass(raw):
                    template = dataclasses.asdict(raw)
                elif isinstance(raw, dict):
                    template = dict(raw)
        _sub_area_counts: dict[str, int] = {"permanent": 0, "timed": 0, "temporary": 0, "total": 0}
        if isinstance(area_state, dict):
            for _sa in area_state.get("temporary_sub_areas", []):
                if not isinstance(_sa, dict):
                    continue
                _exp = _sa.get("expiry", 0)
                try:
                    _exp = int(_exp)
                except (TypeError, ValueError):
                    _exp = 0
                if _exp == -1:
                    _sub_area_counts["permanent"] += 1
                elif _exp >= 24:
                    _sub_area_counts["timed"] += 1
                else:
                    _sub_area_counts["temporary"] += 1
                _sub_area_counts["total"] += 1
        _content_hints: list[dict[str, Any]] = []
        if isinstance(area_state, dict) and area_id and self._state.has_slice("areas"):
            for _sa in area_state.get("temporary_sub_areas", []):
                if not isinstance(_sa, dict):
                    continue
                hint = _sa.get("content_hints") or _sa.get("description", "")
                if not hint:
                    continue
                sub_id = _sa.get("id", "")
                _content_hints.append({
                    "location": _sa.get("label", sub_id),
                    "hint": hint,
                    "discovered": self._state.areas.is_discovery_found(area_id, sub_id),
                })
        return {
            "area_id": area_id,
            "template": template,
            "state": dict(area_state) if isinstance(area_state, dict) else None,
            "dynamic_sub_area_counts": _sub_area_counts,
            "content_hints": _content_hints,
        }

    # ----------------------------------------------------------------
    # Private: L3 — location details (identical for all roles)
    # ----------------------------------------------------------------

    def _build_l3(
        self,
        area_id: str,
        location_id: str | None,
        area_state: dict[str, Any] | None,
    ) -> dict[str, Any]:
        template: dict[str, Any] | None = None
        is_dynamic = False

        if area_id and location_id and self._world.has_registry("maps"):
            area_template = self._world.maps.get(area_id)
            if area_template is not None:
                sub_locations = getattr(area_template, "sub_locations", None)
                if sub_locations is None and isinstance(area_template, dict):
                    sub_locations = area_template.get("sub_locations", {})
                if isinstance(sub_locations, dict):
                    loc_data = sub_locations.get(location_id)
                    if isinstance(loc_data, dict):
                        template = dict(loc_data)
                    elif dataclasses.is_dataclass(loc_data):
                        template = dataclasses.asdict(loc_data)

        if template is None and location_id and isinstance(area_state, dict):
            for item in area_state.get("temporary_sub_areas", []):
                if isinstance(item, dict) and str(item.get("id", "")) == location_id:
                    template = dict(item)
                    is_dynamic = True
                    break

        exploration: str | None = None
        discovered_items: list[str] = []
        if isinstance(area_state, dict):
            raw_expl = area_state.get("exploration")
            exploration = str(raw_expl) if raw_expl is not None else None
            raw_disc = area_state.get("discovered_items", [])
            if isinstance(raw_disc, list):
                discovered_items = sorted(str(i) for i in raw_disc)

        _dynamic_sub_areas: list[dict[str, Any]] = []
        if isinstance(area_state, dict):
            for _sa in area_state.get("temporary_sub_areas", []):
                if isinstance(_sa, dict):
                    _dynamic_sub_areas.append(dict(_sa))
        result: dict[str, Any] = {
            "location_id": location_id,
            "template": template,
            "is_dynamic": is_dynamic,
            "area_exploration": exploration,
            "discovered_items": discovered_items,
            "dynamic_sub_areas": _dynamic_sub_areas,
        }
        if is_dynamic and isinstance(template, dict):
            result["content_hints"] = template.get("content_hints", "")
            result["interactables"] = list(template.get("interactables", []))
        return result

    # ----------------------------------------------------------------
    # Private: L4 — dynamic state (role-specific)
    # ----------------------------------------------------------------

    def _build_l4_gm(self) -> dict[str, Any]:
        """GM: full global state snapshots."""
        result: dict[str, Any] = {
            "time": None,
            "player": None,
            "relations": None,
            "flags": None,
            "party": None,
        }
        for name in result:
            if self._state.has_slice(name):
                result[name] = self._state.get_slice(name).snapshot()
        return result

    def _build_l4_npc(self, npc_id: str) -> dict[str, Any]:
        """NPC: self relationship with player only."""
        disposition: dict[str, int] = {"approval": 0, "trust": 0, "fear": 0, "romance": 0}
        stage = "stranger"
        impressions: list[str] = []

        if self._state.has_slice("relations"):
            rel = self._state.relations
            raw_disp = rel.npc_dispositions
            if isinstance(raw_disp, Mapping) and npc_id in raw_disp:
                d = raw_disp[npc_id]
                if isinstance(d, Mapping):
                    disposition = {
                        "approval": int(d.get("approval", 0)),
                        "trust": int(d.get("trust", 0)),
                        "fear": int(d.get("fear", 0)),
                        "romance": int(d.get("romance", 0)),
                    }
            raw_stages = rel.relationship_stages
            if isinstance(raw_stages, Mapping) and npc_id in raw_stages:
                raw_stage = raw_stages[npc_id]
                if isinstance(raw_stage, str) and raw_stage.strip():
                    stage = raw_stage.strip()
            raw_impressions = rel.npc_impressions
            if isinstance(raw_impressions, Mapping) and npc_id in raw_impressions:
                raw_imp = raw_impressions[npc_id]
                if isinstance(raw_imp, list):
                    impressions = [str(i) for i in raw_imp if i]

        time_info: dict[str, Any] | None = None
        if self._state.has_slice("time"):
            ts = self._state.time.snapshot()
            time_info = {"day": ts.get("day", 1), "slot": ts.get("slot", "")}

        return {
            "disposition": disposition,
            "stage": stage,
            "impressions": impressions,
            "time": time_info,
        }

    def _build_l4_teammate(self, char_id: str) -> dict[str, Any]:
        """Teammate: self disposition + party membership data."""
        self_disposition: dict[str, int] = {"approval": 0, "trust": 0, "fear": 0, "romance": 0}
        if self._state.has_slice("relations"):
            raw_disp = self._state.relations.npc_dispositions
            if isinstance(raw_disp, Mapping) and char_id in raw_disp:
                d = raw_disp[char_id]
                if isinstance(d, Mapping):
                    self_disposition = {
                        "approval": int(d.get("approval", 0)),
                        "trust": int(d.get("trust", 0)),
                        "fear": int(d.get("fear", 0)),
                        "romance": int(d.get("romance", 0)),
                    }

        party_members: list[str] = []
        companion_approval: dict[str, int] = {}
        if self._state.has_slice("party"):
            party_snap = self._state.party.snapshot()
            members = party_snap.get("members", {})
            party_members = list(members) if isinstance(members, dict) else []
            raw_approval = party_snap.get("companion_approval", {})
            companion_approval = dict(raw_approval) if isinstance(raw_approval, dict) else {}

        time_snapshot: dict[str, Any] | None = None
        if self._state.has_slice("time"):
            time_snapshot = self._state.time.snapshot()

        return {
            "self_disposition": self_disposition,
            "party_members": party_members,
            "companion_approval": companion_approval,
            "time": time_snapshot,
        }

    # ----------------------------------------------------------------
    # Private: L5 — scene bus (role-specific visibility)
    # ----------------------------------------------------------------

    def _get_scene_entries(self) -> list[dict[str, Any]]:
        """Extract all current scene entries as a list of dicts."""
        if not self._state.has_slice("scene"):
            return []
        return [
            entry.snapshot()
            for entry in self._state.scene.get_entries()
        ]

    def _get_scene_entries_for_role(
        self,
        role: str,
        character_id: str | None,
    ) -> list[dict[str, Any]]:
        """Extract role-filtered scene entries."""
        if not self._state.has_slice("scene"):
            return []
        if role == "gm":
            return [
                entry.snapshot()
                for entry in self._state.scene.get_for_role("gm")
            ]
        if role in {"npc", "teammate"} and character_id:
            return [
                entry.snapshot()
                for entry in self._state.scene.get_for_role(role, character_id)
            ]
        return self._get_scene_entries()

    def _build_l5_gm(self) -> dict[str, Any]:
        """GM: all non-system entries."""
        return {
            "entries": self._get_scene_entries_for_role("gm", None),
            "viewer_role": "gm",
            "viewer_id": None,
        }

    def _build_l5_role(self, role: str, character_id: str) -> dict[str, Any]:
        """NPC/Teammate: visibility-filtered entries."""
        return {
            "entries": self._get_scene_entries_for_role(role, character_id),
            "viewer_role": role,
            "viewer_id": character_id,
        }

    # ----------------------------------------------------------------
    # Private: L6, L7
    # ----------------------------------------------------------------

    async def _build_l6(
        self, actor_id: str, retriever: MemoryRetriever | None, role: str = "npc"
    ) -> dict[str, Any]:
        """L6 memory recall via injected MemoryRetriever.

        Phase 2: extracts keywords from recent visible scene entries and
        passes the current WorldInstance via context for lazy graph seeding.
        """
        if retriever is None:
            return {"hits": [], "source": "null"}
        keywords = self._extract_scene_keywords(actor_id, role)
        context: dict[str, Any] = {
            "world": self._world,
            "current_area": self._state.player.snapshot().get("current_area", ""),
        }
        return await retriever.retrieve(
            actor_id=actor_id,
            keywords=keywords,
            context=context,
        )

    def _extract_scene_keywords(self, actor_id: str, role: str = "npc") -> list[str]:
        """Extract structured English IDs as keywords for knowledge graph query.

        Uses entity IDs directly (NPC id, area id, quest milestones) instead
        of text tokenization, bypassing Chinese segmentation issues entirely.
        """
        keywords: list[str] = [actor_id]

        # Current area and location
        player_snap = self._state.player.snapshot()
        area_id = player_snap.get("current_area", "")
        if area_id:
            keywords.append(area_id)
        location = player_snap.get("current_location", "")
        if location:
            keywords.append(location)

        # Active quest milestones
        if self._state.has_slice("quests"):
            for q in self._state.quests.get_active_quests():
                if milestone := q.get("target_milestone", ""):
                    keywords.append(milestone)

        # NPC IDs from recent scene entries
        scene_data = self._build_l5_role(role, actor_id)
        for entry in (scene_data.get("entries") or [])[-5:]:
            if isinstance(entry, dict):
                meta = entry.get("metadata") or {}
                if npc_id := meta.get("npc_id"):
                    keywords.append(npc_id)
                if npc_id := meta.get("speaker_id"):
                    keywords.append(npc_id)
                if npc_id := meta.get("character_id"):
                    keywords.append(npc_id)

        # Deduplicate preserving order
        seen: set[str] = set()
        result: list[str] = []
        for kw in keywords:
            if kw and kw not in seen:
                seen.add(kw)
                result.append(kw)
        return result[:20]

    @staticmethod
    def _build_l7(hints: list[str] | None) -> dict[str, Any]:
        """L7 engine result — GM only."""
        return {
            "narrative_hints": list(hints) if hints else [],
            "executed": None,
            "rolls": [],
        }


# ------------------------------------------------------------------
# Internal prompt text formatters (pure functions, testable in isolation)
# ------------------------------------------------------------------


def _resolve_stage(state: StateContainer, char_id: str) -> str:
    """Read relationship_stages for char_id, default 'stranger'."""
    if not state.has_slice("relations"):
        return "stranger"
    raw_stages = state.relations.relationship_stages
    if isinstance(raw_stages, Mapping) and char_id in raw_stages:
        raw = raw_stages[char_id]
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return "stranger"


def _build_companion_memory_context(
    companion: CompanionInstance | None,
) -> str:
    if companion is None:
        return ""
    recent = companion.get_recent_events(5)
    if not recent:
        return ""
    lines = ["## Recent observations"]
    for r in recent:
        lines.append(f"- [{r.action_type}] {r.summary}")
    return "\n".join(lines)


def _filter_secrets(secrets_raw: list[Any], trust_val: int) -> list[str]:
    """Return secret contents eligible to reveal given trust level.

    Accepts list[str] (legacy) or list[SecretEntry] (Phase 1b).
    str entries have no threshold (always eligible — trust gate is data-level).
    """
    result: list[str] = []
    for s in secrets_raw:
        if isinstance(s, str) and s.strip():
            result.append(s.strip())
        else:
            content = getattr(s, "content", "") or ""
            threshold = getattr(s, "trust_threshold", 50)
            if content and trust_val >= threshold:
                result.append(content)
    return result


def _extract_receptionist_data(state: StateContainer) -> dict[str, Any]:
    """Extract bulletin board tasks + player active quests for receptionist NPCs."""
    bulletins: list[dict[str, Any]] = []
    if state.has_slice("areas") and state.has_slice("player"):
        area_id = state.player.current_area
        if area_id:
            for _board_id, entries in state.areas.get_all_board_bulletins(area_id).items():
                for entry in entries:
                    bulletins.append({
                        "quest_id": entry.get("quest_id", ""),
                        "title": entry.get("title", ""),
                        "summary": entry.get("summary", ""),
                    })

    active_quests: list[dict[str, str]] = []
    if state.has_slice("quests"):
        for qid, qdata in state.quests.dynamic_quests.items():
            if isinstance(qdata, dict) and qdata.get("status") == "active":
                active_quests.append({
                    "quest_id": qid,
                    "title": str(qdata.get("title", "")),
                })

    return {
        "role": "receptionist",
        "bulletins": bulletins,
        "active_quests": active_quests,
    }


def _extract_merchant_data(npc_id: str, state: StateContainer) -> dict[str, Any]:
    """Extract shop inventory for merchant NPCs."""
    inventory: list[dict[str, Any]] = []
    if state.has_slice("relations"):
        shop = state.relations.get_shop_state(npc_id)
        if isinstance(shop, dict):
            for item in shop.get("current_stock", []):
                if isinstance(item, dict):
                    inventory.append({
                        "item_id": item.get("item_id", ""),
                        "price": item.get("base_price", 0),
                        "stock": item.get("remaining"),  # None = unlimited
                    })
    return {
        "role": "merchant",
        "inventory": inventory,
    }


_GUARD_FLAG_PREFIXES = ("guard_", "travel_", "permit_", "lockdown_")


def _resolve_npc_area_and_location(
    npc_id: str,
    state: StateContainer,
    world: WorldInstance,
) -> tuple[str, str | None]:
    area_id = ""
    location_id: str | None = None

    if state.has_slice("areas"):
        area_id = state.areas.find_npc_area(npc_id) or ""
        if area_id:
            location_id = state.areas.get_area(area_id).npc_locations.get(npc_id)

    if area_id:
        return area_id, location_id

    if not world.has_registry("characters"):
        return "", None

    profile = world.characters.get(npc_id)
    if profile is None:
        return "", None

    area_id = str(
        getattr(profile, "area_id", "") or getattr(profile, "current_area", "") or ""
    ).strip()
    raw_location = (
        getattr(profile, "location_id", None)
        or getattr(profile, "current_location", None)
    )
    if raw_location is not None:
        normalized = str(raw_location).strip()
        if normalized:
            location_id = normalized
    return area_id, location_id


def _collect_player_effect_labels(state: StateContainer) -> list[str]:
    if not state.has_slice("player"):
        return []
    labels: list[str] = []
    for raw_effect in state.player.active_effects:
        label = ""
        if isinstance(raw_effect, Mapping):
            label = str(
                raw_effect.get("name")
                or raw_effect.get("effect_id")
                or raw_effect.get("id")
                or raw_effect.get("status")
                or ""
            ).strip()
        else:
            label = str(raw_effect).strip()
        if label:
            labels.append(label)
    return labels


def _event_targets_area(raw_event: Mapping[str, Any], area_id: str) -> bool:
    direct_candidates = (
        raw_event.get("area_id"),
        raw_event.get("current_area"),
    )
    for candidate in direct_candidates:
        if str(candidate or "").strip() == area_id:
            return True

    for nested_key in ("payload", "trigger_condition", "location"):
        nested = raw_event.get(nested_key)
        if not isinstance(nested, Mapping):
            continue
        if str(nested.get("area_id") or "").strip() == area_id:
            return True
    return False


def _extract_temple_keeper_data(
    npc_id: str,
    state: StateContainer,
    world: WorldInstance,
) -> dict[str, Any]:
    """Extract service catalog + player status for temple keeper NPCs."""
    services: list[dict[str, Any]] = []
    if world.has_registry("characters"):
        profile = world.characters.get(npc_id)
        shop = getattr(profile, "shop", None) if profile is not None else None
        raw_services = shop.get("services", []) if isinstance(shop, Mapping) else []
        if isinstance(raw_services, list):
            for raw_service in raw_services:
                if not isinstance(raw_service, Mapping):
                    continue
                service_id = str(
                    raw_service.get("service_id") or raw_service.get("id") or ""
                ).strip()
                if not service_id:
                    continue
                label = str(raw_service.get("label") or service_id).strip() or service_id
                price = raw_service.get("price", 0)
                try:
                    normalized_price: int | str = int(price)
                except (TypeError, ValueError):
                    normalized_price = str(price)
                notes = str(
                    raw_service.get("notes")
                    or raw_service.get("availability")
                    or ""
                ).strip()
                services.append({
                    "service_id": service_id,
                    "label": label,
                    "price": normalized_price,
                    "notes": notes,
                })

    area_id, location_id = _resolve_npc_area_and_location(npc_id, state, world)
    player_state = {
        "hp": None,
        "max_hp": None,
        "gold": None,
        "active_effects": [],
    }
    if state.has_slice("player"):
        player_state = {
            "hp": int(state.player.hp),
            "max_hp": int(state.player.max_hp),
            "gold": int(state.player.gold),
            "active_effects": _collect_player_effect_labels(state),
        }

    return {
        "role": "temple_keeper",
        "area_id": area_id,
        "location_id": location_id,
        "services": services,
        "player_state": player_state,
    }


def _extract_guard_data(
    npc_id: str,
    state: StateContainer,
    world: WorldInstance,
) -> dict[str, Any]:
    """Extract patrol-area security facts for guard NPCs."""
    area_id, location_id = _resolve_npc_area_and_location(npc_id, state, world)

    danger_level: float | None = None
    if area_id and state.has_slice("areas"):
        danger_level = state.areas.get_danger(area_id)

    pending_events: list[dict[str, str]] = []
    if area_id and state.has_slice("events"):
        for raw_event in state.events.pending_events:
            if not isinstance(raw_event, Mapping) or not _event_targets_area(raw_event, area_id):
                continue
            event_id = str(
                raw_event.get("event_id") or raw_event.get("id") or ""
            ).strip()
            title = str(
                raw_event.get("title")
                or raw_event.get("name")
                or event_id
            ).strip()
            summary = str(
                raw_event.get("summary")
                or raw_event.get("description")
                or ""
            ).strip()
            pending_events.append({
                "event_id": event_id,
                "title": title,
                "summary": summary,
            })

    access_flags: list[dict[str, Any]] = []
    if area_id and state.has_slice("flags"):
        for key, value in sorted(state.flags.get_all().items()):
            key_str = str(key).strip()
            if not key_str.startswith(_GUARD_FLAG_PREFIXES):
                continue
            if area_id not in key_str:
                continue
            access_flags.append({
                "key": key_str,
                "value": value,
            })

    return {
        "role": "guard",
        "area_id": area_id,
        "location_id": location_id,
        "danger_level": danger_level,
        "pending_events": pending_events,
        "access_flags": access_flags,
    }


def _extract_role_data(
    tags: list[Any],
    npc_id: str,
    state: StateContainer,
    world: WorldInstance,
) -> dict[str, Any] | None:
    """Return role-specific truth-source data based on NPC tags, or None."""
    tag_set = {str(t).strip().lower() for t in tags}

    if "receptionist" in tag_set:
        return _extract_receptionist_data(state)
    if "merchant" in tag_set:
        return _extract_merchant_data(npc_id, state)
    if "temple_keeper" in tag_set:
        return _extract_temple_keeper_data(npc_id, state, world)
    if "guard" in tag_set:
        return _extract_guard_data(npc_id, state, world)
    return None


def _format_role_constraint_block(role_data: dict[str, Any]) -> str:
    """Format a role-specific constraint block for injection into NPC system prompt."""
    role = role_data.get("role", "")

    if role == "receptionist":
        bulletins = role_data.get("bulletins", [])
        active = role_data.get("active_quests", [])
        lines = [
            "\n\n## 你的职责（严格遵守）",
            "你是公会柜台职员，负责任务发布、查询、接取和报告。",
            "",
            "### 可用任务（公告板）",
        ]
        if bulletins:
            for b in bulletins:
                lines.append(f"- 【{b.get('title', '?')}】{b.get('summary', '')}")
        else:
            lines.append("- （当前公告板上没有可接取的任务）")
        lines.append("")
        lines.append("### 玩家已接任务")
        if active:
            for q in active:
                lines.append(f"- {q.get('title', q.get('quest_id', '?'))}")
        else:
            lines.append("- （玩家当前没有进行中的任务）")
        lines.append("")
        lines.append("### 约束规则")
        lines.append("- 你只能推荐公告板上实际存在的任务，绝不编造不存在的任务")
        lines.append('- 被问及公告板上没有的任务时，如实说"目前没有这类委托"')
        lines.append("- 玩家报告完成任务时，确认任务 ID 在已接列表中")
        return "\n".join(lines)

    if role == "merchant":
        inventory = role_data.get("inventory", [])
        lines = [
            "\n\n## 你的职责（严格遵守）",
            "你是商人，负责商品买卖和推荐。",
            "",
            "### 当前库存",
        ]
        if inventory:
            for item in inventory:
                stock = item.get("stock")
                stock_str = "无限" if stock is None else str(stock)
                lines.append(
                    f"- {item.get('item_id', '?')} — "
                    f"价格:{item.get('price', '?')} 库存:{stock_str}"
                )
        else:
            lines.append("- （当前没有库存）")
        lines.append("")
        lines.append("### 约束规则")
        lines.append("- 只能出售库存中实际存在的商品，绝不编造不存在的商品")
        lines.append("- 价格以库存列表为准，不得自行调整")
        return "\n".join(lines)

    if role == "temple_keeper":
        services = role_data.get("services", [])
        player_state = role_data.get("player_state", {})
        active_effects = player_state.get("active_effects", [])
        lines = [
            "\n\n## 你的职责（严格遵守）",
            "你是神殿接待者，负责治疗、祝福与捐赠相关的服务说明。",
            "",
            "### 当前服务目录",
        ]
        if services:
            for service in services:
                notes = str(service.get("notes", "")).strip()
                detail = (
                    f"- {service.get('label', service.get('service_id', '?'))} "
                    f"({service.get('service_id', '?')}) — 价格:{service.get('price', '?')}"
                )
                if notes:
                    detail += f" 说明:{notes}"
                lines.append(detail)
        else:
            lines.append("- （当前没有可提供的神殿服务）")
        lines.append("")
        lines.append("### 当前来访者状态")
        hp = player_state.get("hp")
        max_hp = player_state.get("max_hp")
        gold = player_state.get("gold")
        if hp is not None and max_hp is not None:
            lines.append(f"- 生命值: {hp}/{max_hp}")
        else:
            lines.append("- 生命值: （未知）")
        if gold is not None:
            lines.append(f"- 持有金币: {gold}")
        else:
            lines.append("- 持有金币: （未知）")
        if active_effects:
            lines.append(f"- 当前状态效果: {', '.join(str(effect) for effect in active_effects)}")
        else:
            lines.append("- 当前状态效果: 无")
        lines.append("")
        lines.append("### 约束规则")
        lines.append("- 你只能说明服务目录中实际存在的治疗、祝福和捐赠项目")
        lines.append("- 不得编造新的价格、疗效、折扣或额外仪式")
        lines.append("- 对玩家是否需要治疗或能否支付的判断，必须以上述状态为准")
        return "\n".join(lines)

    if role == "guard":
        pending_events = role_data.get("pending_events", [])
        access_flags = role_data.get("access_flags", [])
        lines = [
            "\n\n## 你的职责（严格遵守）",
            "你是守卫，负责通行核验、风险提醒和秩序维护。",
            "",
            "### 驻守信息",
        ]
        area_id = str(role_data.get("area_id") or "").strip()
        location_id = str(role_data.get("location_id") or "").strip()
        danger_level = role_data.get("danger_level")
        lines.append(f"- 驻守区域: {area_id or '（未知）'}")
        lines.append(f"- 当前岗位: {location_id or '（未知）'}")
        if danger_level is not None:
            lines.append(f"- 当前危险度: {danger_level}")
        else:
            lines.append("- 当前危险度: （未知）")
        lines.append("")
        lines.append("### 当前安全事实")
        if pending_events:
            for event in pending_events:
                summary = str(event.get("summary", "")).strip()
                detail = f"- {event.get('title', event.get('event_id', '?'))}"
                if summary:
                    detail += f"：{summary}"
                lines.append(detail)
        else:
            lines.append("- （当前没有待处理的区域事件）")
        lines.append("")
        lines.append("### 通行与戒严标记")
        if access_flags:
            for flag in access_flags:
                lines.append(f"- {flag.get('key', '?')} = {flag.get('value')}")
        else:
            lines.append("- （当前没有额外的通行或戒严标记）")
        lines.append("")
        lines.append("### 约束规则")
        lines.append("- 你只能根据上述危险度、待处理事件和通行标记回答安全与通行问题")
        lines.append("- 不得编造不存在的封锁、搜查、许可要求或戒严措施")
        return "\n".join(lines)

    return ""


def _build_npc_prompt_text(
    npc_profile: Any,
    disposition: Mapping[str, Any],
    stage: str,
    impressions: list[str],
    knowledge_hits: list[dict[str, Any]] | None = None,
    active_directive: dict[str, Any] | None = None,
    time_info: dict[str, Any] | None = None,
    is_private: bool = False,
    is_passive: bool = False,
    role_data: dict[str, Any] | None = None,
) -> str:
    """Format NPC system prompt string from resolved profile + relationship data."""
    name = _str_or(_profile_get(npc_profile, "name"), "Unknown NPC")
    personality = _str_or(_profile_get(npc_profile, "personality"), "")
    dialogue_style = _str_or(_profile_get(npc_profile, "dialogue_style"), "")
    dialogue_hook = _str_or(_profile_get(npc_profile, "dialogue_hook"), "")
    tags = _profile_get(npc_profile, "tags", [])
    tags_str = ", ".join(str(t) for t in tags) if isinstance(tags, list) else ""

    backstory = _str_or(_profile_get(npc_profile, "backstory"), "")
    speech_pattern = _str_or(_profile_get(npc_profile, "speech_pattern"), "")
    character_class = (
        _str_or(_profile_get(npc_profile, "character_class"), "")
        or _str_or(_profile_get(npc_profile, "class_id"), "")
    )
    faction = (
        _str_or(_profile_get(npc_profile, "faction"), "")
        or _str_or(_profile_get(npc_profile, "faction_id"), "")
    )
    secrets_raw = _profile_get(npc_profile, "secrets", [])

    approval = disposition.get("approval", 0)
    trust = disposition.get("trust", 0)
    fear = disposition.get("fear", 0)
    romance = disposition.get("romance", 0)

    memories_block = (
        "\n".join(f"- {imp}" for imp in impressions)
        if impressions
        else "- (No previous memories of this player)"
    )
    personality_block = personality if personality else "A character in this world."
    dialogue_hook_block = (
        f"\n\n## Dialogue hook\n{dialogue_hook}" if dialogue_hook else ""
    )
    style_block = f"\n\n## Your dialogue style\n{dialogue_style}" if dialogue_style else ""
    tags_block = f"\nTraits: {tags_str}" if tags_str else ""

    # Extended character blocks
    backstory_block = f"\n\n## Your background\n{backstory}" if backstory else ""
    speech_pattern_block = (
        f"\n\n## Your speech pattern\n你的说话习惯和口癖：{speech_pattern}"
        if speech_pattern else ""
    )
    identity_lines: list[str] = []
    if character_class:
        identity_lines.append(f"- 职业：{character_class}")
    if faction:
        identity_lines.append(f"- 阵营：{faction}")
    identity_block = (
        "\n\n## Your identity\n" + "\n".join(identity_lines) if identity_lines else ""
    )

    # Time awareness
    time_block = ""
    if time_info:
        day = time_info.get("day", 1)
        slot = time_info.get("slot", "")
        time_block = f"\n- Current time: Day {day}, {slot}" if slot else f"\n- Current time: Day {day}"

    # Behavior guides from relationship
    behavior_parts = [
        g for g in [
            _STAGE_GUIDES.get(stage, ""),
            _trust_hint(int(trust)),
            _romance_hint(int(romance)),
        ]
        if g
    ]
    behavior_block = (
        "\n\n## How to behave\n" + "\n".join(behavior_parts) if behavior_parts else ""
    )

    # Private conversation context
    private_block = ""
    if is_private:
        private_block = (
            "\n\n## Private conversation context\n"
            "你现在和玩家单独在一起，没有其他人能听到你们的对话。\n"
            "你可以比平时更真实——不需要维持公众形象。\n"
            "如果对话氛围合适且你足够信任对方，可以提及更私人的话题。\n"
            "你说话可以更口语化、更真实，可以有犹豫和停顿。"
        )

    # Secrets (trust-gated; private chat lowers threshold by 20)
    secrets_block = ""
    if secrets_raw:
        effective_trust = int(trust) + (20 if is_private else 0)
        eligible = _filter_secrets(list(secrets_raw), effective_trust)
        if eligible:
            secrets_block = (
                "\n\n## Things you know but haven't told the player\n"
                "（当对话气氛合适时可自然提及，不要生硬）\n"
                + "\n".join(f"- {s}" for s in eligible)
            )

    # knowledge_block is no longer pre-injected — NPC uses recall tool to query on demand
    knowledge_block = ""

    # Build narrative-planner directive block (P1-B)
    directive_block = ""
    if active_directive:
        d = active_directive.get("directive", {})
        kind = d.get("kind", "")
        details = {k: v for k, v in d.items() if k != "kind"}
        detail_str = (
            ", ".join(f"{k}={v}" for k, v in details.items()) if details else ""
        )
        directive_desc = f"{kind}: {detail_str}" if detail_str else kind
        directive_block = (
            "\n\n## [重要行为指令]\n"
            "你收到了以下叙事指令，请在对话中自然融入，不要生硬提及：\n"
            f"- {directive_desc}"
        )

    # Role constraint block — inject truth-source data for specialized NPCs (P3.5)
    role_block = ""
    if role_data is not None:
        role_block = _format_role_constraint_block(role_data)

    if is_passive:
        tool_rules = """\
## Tool usage rules
- You just witnessed a player action. You are NOT being spoken to directly.
- If the action is relevant to you, react briefly with `speak` (1-2 sentences max) or `emote`.
- If the action has nothing to do with you, use `emote` with a brief idle action or do nothing.
- Use `update_feeling` only if the action genuinely changes your feelings.
- Do NOT initiate conversation topics or offer quests/trade unprompted.
- Do not output plain text outside tool calls.
- Use at most one visible response: one `speak` OR one `emote`.
"""
    else:
        tool_rules = """\
## Tool usage rules
- Use `speak` to say something. Stay in character at all times.
- Use `emote` for physical actions or emotional expressions.
- Use `update_feeling` if the conversation meaningfully changes your feelings toward the player (keep delta small: ±5 to ±15).
- Use `remember` to note important new information from this conversation.
- Use `recall` when the player mentions a topic, person, or place you're unsure about. It searches your memory.
- Use `refuse` if asked something you wouldn't agree to.
- If the player clearly invites you to join the party and you genuinely agree, call `join_party` in the same turn as your visible response.
- Do not verbally agree to join the party unless you also call `join_party`.
- Use `offer_quest` / `offer_trade` / `reveal_secret` only when contextually appropriate.
- Do not output plain text outside tool calls.
- Use at most one visible dialogue tool per turn: exactly one of `speak` or `refuse`. You may also use at most one `emote`.
- If you need `update_feeling`, `remember`, or other side effects, call them in the same turn before your final visible response.
- After calling `speak` or `refuse`, your turn is over. Do not make more tool calls in later turns.
- You MUST respond when spoken to — do not use `pass_turn`.
"""

    return f"""\
You are {name}, an NPC in a dark-fantasy CRPG world.

## Your character
{personality_block}{dialogue_hook_block}{tags_block}{style_block}{backstory_block}{speech_pattern_block}{identity_block}

## Current relationship with the player
- Relationship stage: {stage}
- Approval: {approval} (how much you like them, range -100 to +100)
- Trust: {trust} (how much you trust them, range -100 to +100)
- Fear: {fear} (how much you fear them, range 0 to 100)
- Romance: {romance} (romantic interest, range 0 to 100){time_block}{behavior_block}{private_block}

## Your memories of the player
{memories_block}{knowledge_block}{secrets_block}{directive_block}{role_block}

{tool_rules}

## Language
Respond in the same language as the player's message.\
"""


def _build_teammate_prompt_text(
    profile: Any,
    disposition: Mapping[str, Any],
    knowledge_hits: list[dict[str, Any]] | None = None,
    companion_memory: str | None = None,
    stage: str = "stranger",
    time_info: dict[str, Any] | None = None,
) -> str:
    """Format teammate system prompt string from profile + disposition."""
    name = _str_or(_profile_get(profile, "name"), "Companion")
    personality = _str_or(_profile_get(profile, "personality"), "A loyal companion.")
    approval = disposition.get("approval", 0)
    trust = disposition.get("trust", 0)

    backstory = _str_or(_profile_get(profile, "backstory"), "")
    speech_pattern = _str_or(_profile_get(profile, "speech_pattern"), "")
    character_class = (
        _str_or(_profile_get(profile, "character_class"), "")
        or _str_or(_profile_get(profile, "class_id"), "")
    )
    faction = (
        _str_or(_profile_get(profile, "faction"), "")
        or _str_or(_profile_get(profile, "faction_id"), "")
    )

    backstory_block = f"\n\n## Your background\n{backstory}" if backstory else ""
    speech_pattern_block = (
        f"\n\n## Your speech pattern\n你的说话习惯和口癖：{speech_pattern}"
        if speech_pattern else ""
    )
    identity_lines: list[str] = []
    if character_class:
        identity_lines.append(f"- 职业：{character_class}")
    if faction:
        identity_lines.append(f"- 阵营：{faction}")
    identity_block = (
        "\n\n## Your identity\n" + "\n".join(identity_lines) if identity_lines else ""
    )

    time_block = ""
    if time_info:
        day = time_info.get("day", 1)
        slot = time_info.get("slot", "")
        time_block = f"\n- Current time: Day {day}, {slot}" if slot else f"\n- Current time: Day {day}"

    behavior_parts = [
        g for g in [
            _STAGE_GUIDES.get(stage, ""),
            _trust_hint(int(trust)),
            _romance_hint(int(disposition.get("romance", 0))),
        ]
        if g
    ]
    behavior_block = (
        "\n\n## How to behave\n" + "\n".join(behavior_parts) if behavior_parts else ""
    )

    # knowledge_block is no longer pre-injected — teammate uses recall tool to query on demand
    companion_block = f"\n\n{companion_memory}" if companion_memory else ""

    return f"""\
You are {name}, a companion in the player's party in a dark-fantasy CRPG.

## Your character
{personality}{backstory_block}{speech_pattern_block}{identity_block}{behavior_block}

## Your relationship with the player
- Approval: {approval} (range -100 to +100)
- Trust: {trust} (range -100 to +100){time_block}

## Your role right now
The player just performed an action. You see what happened in the scene. \
Decide whether to react:

- Most of the time, stay silent — if you have nothing meaningful to add, \
return no tool calls and no text.
- React when: combat ends, a crisis occurs, the player does something \
that strongly affects you, or you have a relevant opinion.
- Use `speak` for dialogue, `emote` for physical/emotional reactions.
- Use `express_opinion` if the action genuinely shifts your feelings \
(delta should be small: ±5 to ±10).
- Use `recall` when the player mentions a topic, person, or place you're unsure about. It searches your memory.
- Do not output plain text outside tool calls.
- Use `leave_party` only when you are explicitly deciding to leave the party; do not use it for routine disagreement or banter.
- If you react visibly, use at most one `speak` and optionally one `emote`.
- Prefer bundling `express_opinion` with the same visible reaction instead \
of making a separate follow-up turn.
- After calling `speak`, your turn is over.

## Style
- Stay in character. Your personality drives how you express yourself.
- Keep it brief — 1-2 sentences if you speak at all.
- Don't repeat what the player already knows happened.

## Language
Match the language of the user message.{companion_block}"""
