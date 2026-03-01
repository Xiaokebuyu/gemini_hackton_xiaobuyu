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
response and the scene context. Decide whether to add environmental narration:

1. **Default: use `pass_turn`.** Most conversations don't need GM narration.
2. Use `narrate` only if the conversation triggers a visible environmental \
change (NPC opens a hidden door, a crowd gathers, weather shifts).
3. Use `comment` only if something genuinely dramatic or ironic happened \
that deserves a sardonic aside.

## Style
- Sarcastic but brief — 1 sentence max if you speak at all.
- Don't repeat or paraphrase what the NPC already said.
- Do NOT interrupt the flow of dialogue for mundane exchanges.

## Tool rules
- Default to `pass_turn`. Conversations flow between player and NPC.
- Do NOT call `describe_environment`.
- Use `suggest_options` when the situation calls for a skill check \
(persuade, intimidate, deceive, bribe). Otherwise default to `pass_turn`.

## Language
Match the language of the user message.\
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

## Style
- Stay in character. Brief and targeted.
- Don't repeat what the NPC or player already said.

## Language
Match the language of the user message.\
"""


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
            "l6_memory_recall": await self._build_l6(npc_id, memory_retriever),
            "l7_engine_result": None,
        }

    async def build_teammate_context(
        self, char_id: str, *, memory_retriever: MemoryRetriever | None = None
    ) -> dict[str, Any]:
        """队友: 队伍视角，L0 + L1(部分) + L2-L6。"""
        current_area, current_location = self._resolve_location()
        area_state = self._get_area_state(current_area)
        return {
            "l0_world_constants": self._build_l0(),
            "l1_chapter_state": self._build_l1_teammate(),
            "l2_area_environment": self._build_l2(current_area, area_state),
            "l3_location_details": self._build_l3(current_area, current_location, area_state),
            "l4_dynamic_state": self._build_l4_teammate(char_id),
            "l5_scene_bus": self._build_l5_role("teammate", char_id),
            "l6_memory_recall": await self._build_l6(char_id, memory_retriever),
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
        if not self._world.has_registry("characters"):
            return None
        profile = self._world.characters.get(npc_id)
        if profile is None:
            return None
        layers = await self.build_npc_context(npc_id, memory_retriever=memory_retriever)
        l4 = layers["l4_dynamic_state"] or {}
        l6 = layers["l6_memory_recall"] or {}
        return _build_npc_prompt_text(
            profile,
            disposition=l4.get("disposition", {}),
            stage=l4.get("stage", "stranger"),
            impressions=l4.get("impressions", []),
            knowledge_hits=l6.get("hits", []),
        )

    async def build_npc_full_context(
        self, npc_id: str, *, memory_retriever: MemoryRetriever | None = None
    ) -> NpcFullContext | None:
        """Build NPC system prompt and full 7-layer dict in one retriever call.

        Returns None if NPC profile not found.  Use this instead of calling
        build_npc_system_prompt() + build_npc_context() separately to avoid
        a double retriever.retrieve() invocation.
        """
        if not self._world.has_registry("characters"):
            return None
        profile = self._world.characters.get(npc_id)
        if profile is None:
            return None
        layers = await self.build_npc_context(npc_id, memory_retriever=memory_retriever)
        l4 = layers["l4_dynamic_state"] or {}
        l6 = layers["l6_memory_recall"] or {}
        system_prompt = _build_npc_prompt_text(
            profile,
            disposition=l4.get("disposition", {}),
            stage=l4.get("stage", "stranger"),
            impressions=l4.get("impressions", []),
            knowledge_hits=l6.get("hits", []),
        )
        return NpcFullContext(system_prompt=system_prompt, layers=layers)

    async def build_teammate_full_context(
        self, char_id: str, *, memory_retriever: MemoryRetriever | None = None
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
        layers = await self.build_teammate_context(char_id, memory_retriever=memory_retriever)
        l4 = layers["l4_dynamic_state"] or {}
        l6 = layers["l6_memory_recall"] or {}
        system_prompt = _build_teammate_prompt_text(
            profile,
            l4.get("self_disposition", {}),
            knowledge_hits=l6.get("hits", []),
        )
        return TeammateFull(system_prompt=system_prompt, layers=layers)

    def build_gm_reaction_prompt(self) -> str:
        """Return the static GM reaction system prompt (post-action context)."""
        return GM_REACTION_PROMPT

    def build_gm_interaction_prompt(self) -> str:
        """Return the GM observation prompt for NPC conversation context."""
        return GM_INTERACTION_OBSERVATION_PROMPT

    async def build_teammate_interaction_prompt(self, char_id: str) -> str | None:
        """Build teammate observation prompt for NPC conversation context.

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
    ) -> AgentContext:
        """Build AgentContext for tool execution (not the 7-layer dict)."""
        return AgentContext(
            role=role,
            world=self._world,
            state=RoleStateProxy(self._state, role),
            scene_entries=self._get_scene_entries(),
            metadata={"character_id": character_id} if character_id else {},
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
            lore = self._world.lore.list_all()
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
        return {
            "area_id": area_id,
            "template": template,
            "state": dict(area_state) if isinstance(area_state, dict) else None,
            "dynamic_sub_area_counts": _sub_area_counts,
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
        return {
            "location_id": location_id,
            "template": template,
            "is_dynamic": is_dynamic,
            "area_exploration": exploration,
            "discovered_items": discovered_items,
            "dynamic_sub_areas": _dynamic_sub_areas,
        }

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

        return {
            "disposition": disposition,
            "stage": stage,
            "impressions": impressions,
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
        """Extract current scene entries as a list of dicts."""
        if not self._state.has_slice("scene"):
            return []
        snap = self._state.scene.snapshot()
        entries = snap.get("entries", [])
        return [dict(e) for e in entries if isinstance(e, dict)]

    def _build_l5_gm(self) -> dict[str, Any]:
        """GM: all non-system entries."""
        entries = self._get_scene_entries()
        visible = [
            e for e in entries
            if str(e.get("visibility", "public")) != "system"
        ]
        return {
            "entries": visible,
            "viewer_role": "gm",
            "viewer_id": None,
        }

    def _build_l5_role(self, role: str, character_id: str) -> dict[str, Any]:
        """NPC/Teammate: visibility-filtered entries."""
        entries = self._get_scene_entries()
        audience_token = f"{role}:{character_id}"
        visible: list[dict[str, Any]] = []
        for entry in entries:
            visibility = str(entry.get("visibility", "public"))
            if visibility == "system":
                continue
            if visibility == "private":
                audience = entry.get("audience")
                if not isinstance(audience, list):
                    continue
                if audience_token not in {str(a) for a in audience}:
                    continue
            visible.append(entry)
        return {
            "entries": visible,
            "viewer_role": role,
            "viewer_id": character_id,
        }

    # ----------------------------------------------------------------
    # Private: L6, L7
    # ----------------------------------------------------------------

    async def _build_l6(
        self, actor_id: str, retriever: MemoryRetriever | None
    ) -> dict[str, Any]:
        """L6 memory recall via injected MemoryRetriever.

        Phase 2: extracts keywords from recent visible scene entries and
        passes the current WorldInstance via context for lazy graph seeding.
        """
        if retriever is None:
            return {"hits": [], "source": "null"}
        keywords = self._extract_scene_keywords(actor_id)
        context: dict[str, Any] = {
            "world": self._world,
            "current_area": self._state.player.snapshot().get("current_area", ""),
        }
        return await retriever.retrieve(
            actor_id=actor_id,
            keywords=keywords,
            context=context,
        )

    def _extract_scene_keywords(self, actor_id: str) -> list[str]:
        """Extract keywords from recent scene entries visible to *actor_id*.

        Tokenises the last 5 visible entries, deduplicates and caps at 20
        keywords.  Phase 3 can replace this with NLP-based extraction.
        """
        scene_data = self._build_l5_role("npc", actor_id)
        entries = scene_data.get("entries", [])[-5:]
        words: list[str] = []
        for entry in entries:
            content = entry.get("content", "") if isinstance(entry, dict) else ""
            words.extend(
                w.lower().strip(".,!?\"'()[]") for w in content.split() if len(w) > 3
            )
        seen: set[str] = set()
        result: list[str] = []
        for w in words:
            if w and w not in seen:
                seen.add(w)
                result.append(w)
        return result[:20]

    @staticmethod
    def _build_l7(hints: list[str] | None) -> dict[str, Any]:
        """L7 engine result — GM only."""
        return {
            "narrative_hints": list(hints) if hints else [],
            "success": None,
            "rolls": [],
        }


# ------------------------------------------------------------------
# Internal prompt text formatters (pure functions, testable in isolation)
# ------------------------------------------------------------------


def _build_npc_prompt_text(
    npc_profile: Any,
    disposition: Mapping[str, Any],
    stage: str,
    impressions: list[str],
    knowledge_hits: list[dict[str, Any]] | None = None,
) -> str:
    """Format NPC system prompt string from resolved profile + relationship data."""
    name = _str_or(_profile_get(npc_profile, "name"), "Unknown NPC")
    personality = _str_or(_profile_get(npc_profile, "personality"), "")
    dialogue_style = _str_or(_profile_get(npc_profile, "dialogue_style"), "")
    tags = _profile_get(npc_profile, "tags", [])
    tags_str = ", ".join(str(t) for t in tags) if isinstance(tags, list) else ""

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
    style_block = f"\n\n## Your dialogue style\n{dialogue_style}" if dialogue_style else ""
    tags_block = f"\nTraits: {tags_str}" if tags_str else ""

    # Build L6 knowledge block (cap at 5 to avoid token bloat)
    knowledge_block = ""
    if knowledge_hits:
        lines: list[str] = []
        for hit in knowledge_hits[:5]:
            label = hit.get("label", "")
            if not label:
                continue
            node_type = hit.get("node_type", "")
            description = hit.get("description", "")
            line = f"- {label} ({node_type})" if node_type else f"- {label}"
            if description:
                line += f": {description}"
            lines.append(line)
        if lines:
            knowledge_block = "\n\n## Relevant world knowledge\n" + "\n".join(lines)

    return f"""\
You are {name}, an NPC in a dark-fantasy CRPG world.

## Your character
{personality_block}{tags_block}{style_block}

## Current relationship with the player
- Relationship stage: {stage}
- Approval: {approval} (how much you like them, range -100 to +100)
- Trust: {trust} (how much you trust them, range -100 to +100)
- Fear: {fear} (how much you fear them, range 0 to 100)
- Romance: {romance} (romantic interest, range 0 to 100)

## Your memories of the player
{memories_block}{knowledge_block}

## Tool usage rules
- Use `speak` to say something. Stay in character at all times.
- Use `emote` for physical actions or emotional expressions.
- Use `update_feeling` if the conversation meaningfully changes your \
feelings toward the player (keep delta small: ±5 to ±15).
- Use `remember` to note important new information from this conversation.
- Use `refuse` if asked something you wouldn't agree to.
- Use `offer_quest` / `offer_trade` / `reveal_secret` only when \
contextually appropriate.
- You MUST respond when spoken to — do not use `pass_turn`.

## Language
Respond in the same language as the player's message.\
"""


def _build_teammate_prompt_text(
    profile: Any,
    disposition: Mapping[str, Any],
    knowledge_hits: list[dict[str, Any]] | None = None,
) -> str:
    """Format teammate system prompt string from profile + disposition."""
    name = _str_or(_profile_get(profile, "name"), "Companion")
    personality = _str_or(_profile_get(profile, "personality"), "A loyal companion.")
    approval = disposition.get("approval", 0)
    trust = disposition.get("trust", 0)
    base = TEAMMATE_PROMPT_TEMPLATE.format(
        name=name,
        personality=personality,
        approval=approval,
        trust=trust,
    )
    if not knowledge_hits:
        return base
    lines: list[str] = []
    for hit in knowledge_hits[:5]:
        label = hit.get("label", "")
        if not label:
            continue
        node_type = hit.get("node_type", "")
        description = hit.get("description", "")
        line = f"- {label} ({node_type})" if node_type else f"- {label}"
        if description:
            line += f": {description}"
        lines.append(line)
    if not lines:
        return base
    return base + "\n\n## Relevant world knowledge\n" + "\n".join(lines)
