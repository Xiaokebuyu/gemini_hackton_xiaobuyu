"""NpcAutonomyHook — deterministic NPC idle tick between schedule moves.

Runs at P61 (after NpcScheduleHook=60, before SharedExperienceHook=62).
For every NPC that is co-located with the player in the same room, and for every
party companion (regardless of room), this hook updates their blackboard from
area_events and active Planner directives.

Three-tier memory architecture:
  observations (short-term, cap=10, injected into system prompt)
    ↓ overflow
  pending_graphize (mid-term buffer, cap=100, persisted but not injected)
    ↓ threshold reached
  LLM extraction → WorldKnowledgeGraph triples (permanent, L6 semantic retrieval)

Design ref: known_issues.md §4-A.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult
from app.game_core.orchestration.settlement import SettlementContext

if TYPE_CHECKING:
    from app.game_core.adapters.llm import LlmPort

logger = logging.getLogger(__name__)

# Maximum observations per blackboard (short-term active window).
_OBS_CAP = 10
# Threshold at which pending_graphize triggers LLM extraction.
_GRAPHIZE_THRESHOLD = 100
# System prompt for LLM observation → triple extraction.
_OBSERVATIONS_EXTRACTION_PROMPT = (
    "You are a knowledge extractor for a fantasy RPG game.\n"
    "Given a list of NPC observations (things an NPC witnessed or heard),\n"
    "call record_triple for each significant factual relationship:\n"
    "1. What the NPC learned about specific named entities (people, places, items)\n"
    "2. Interactions or events that occurred between named entities\n"
    "3. Attitudes, opinions, or commitments formed\n"
    "Use entity names exactly as they appear. Call record_triple once per fact. "
    "Skip vague or non-relational observations."
)


class NpcAutonomyHook(NoOpSettlementHook):
    """NPC idle tick — blackboard maintenance with optional LLM triple extraction.

    For each co-located NPC:
      - Pulls the 3 most recent area_events into blackboard.observations.
      - Copies any active Planner directive's npc_goal into blackboard.goals.
      - Stamps updated_tick with the current slot/tick counter.
      - Overflow observations go into pending_graphize buffer.

    For each party companion:
      - Same as above.
      - Additionally records undiscovered scoped-interactable clues as
        potential observations ("teammate notices clue: <clue_id>").

    When pending_graphize reaches _GRAPHIZE_THRESHOLD (100 entries):
      - With LLM: extracts semantic triples via LLM → writes to knowledge_graph.
      - Without LLM: writes raw fallback triples (npc_id, "观察到", text).
      - Clears pending_graphize in both cases.

    Priority = 61: runs after NpcScheduleHook (60), before SharedExperienceHook (62).
    """

    HOOK_PRIORITY = 61
    HOOK_NAME = "npc_autonomy"

    def __init__(self, llm_provider: LlmPort | None = None) -> None:
        """Construct the hook.

        Args:
            llm_provider: Optional LLM port for observation → triple extraction.
                          If None, falls back to raw (npc_id, "观察到", text) triples.
        """
        self._llm_provider = llm_provider

    async def execute(self, context: SettlementContext) -> HookResult:  # noqa: C901
        if not context.state.has_slice("player"):
            return HookResult(metadata={"status": "noop", "reason": "no_player_slice"})
        if not context.state.has_slice("relations"):
            return HookResult(metadata={"status": "noop", "reason": "no_relations_slice"})

        player_area: str = context.state.player.current_area
        player_location: str | None = context.state.player.current_location
        player_room: str | None = context.state.player.current_room

        # Gather party members so we can exclude them from colocated-NPC logic.
        companion_ids: set[str] = set()
        if context.state.has_slice("party"):
            members = context.state.party.members
            if isinstance(members, dict):
                companion_ids = set(members.keys())

        # Find NPCs co-located with the player (same area/location/room) that
        # are NOT party companions.
        colocated_npcs: list[str] = []
        if context.state.has_slice("areas") and player_area:
            colocated_npcs = self._find_colocated_npcs(
                context,
                player_area,
                player_location,
                player_room,
                exclude_ids=companion_ids,
            )

        updated_npcs: list[str] = []

        for npc_id in colocated_npcs:
            try:
                self._run_npc_idle(npc_id, context)
                updated_npcs.append(npc_id)
            except Exception:  # noqa: BLE001
                logger.debug("npc_autonomy: idle update failed for npc=%s", npc_id, exc_info=True)

        for companion_id in companion_ids:
            try:
                self._run_companion_idle(companion_id, context)
                updated_npcs.append(companion_id)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "npc_autonomy: idle update failed for companion=%s",
                    companion_id,
                    exc_info=True,
                )

        # Check if any processed NPC's pending_graphize has hit the threshold.
        graphized: list[str] = []
        for npc_id in updated_npcs:
            try:
                board = context.state.relations.get_blackboard(npc_id)
                pending = board.get("pending_graphize", [])
                if isinstance(pending, list) and len(pending) >= _GRAPHIZE_THRESHOLD:
                    await self._graphize_pending(npc_id, pending, context)
                    graphized.append(npc_id)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "npc_autonomy: graphize failed for npc=%s", npc_id, exc_info=True
                )

        return HookResult(
            metadata={
                "status": "ok",
                "updated_npcs": updated_npcs,
                "colocated_count": len(colocated_npcs),
                "companion_count": len(companion_ids),
                "graphized_npcs": graphized,
            }
        )

    # ------------------------------------------------------------------
    # Core idle logic
    # ------------------------------------------------------------------

    def _run_npc_idle(
        self,
        npc_id: str,
        context: SettlementContext,
    ) -> None:
        """Update a non-companion NPC's blackboard deterministically.

        Steps:
        1. Pull the 3 most recent area_events for the player's current area.
        2. If the NPC has an active Planner directive with a npc_goal, add it
           to goals (deduplicated).
        3. Stamp updated_tick with the current slot/tick counter.
        4. If the blackboard is empty or lacks updated_tick (first run / old
           save), seed initial observations and attitude from npc_impressions.
        5. If merged_obs exceeds _OBS_CAP, overflow → pending_graphize buffer.
        """
        if not context.state.has_slice("areas"):
            return

        player_area = context.state.player.current_area

        # 1. Observations from recent area_events.
        recent_events = context.state.areas.get_area_events(player_area)
        observations: list[str] = [
            self._event_to_observation(e)
            for e in recent_events[-3:]
            if isinstance(e, dict)
        ]

        # 2. Active directive goal.
        goals = self._extract_directive_goals(npc_id, context)

        # 3. Current tick.
        current_tick = self._current_tick(context)

        # Check if this is a first-run / old-save situation (no updated_tick).
        existing_board = context.state.relations.get_blackboard(npc_id)
        is_first_run = "updated_tick" not in existing_board

        # 4. Seed from impressions on first run.
        if is_first_run:
            seed_observations, seed_attitude = self._seed_from_impressions(npc_id, context)
            # Prepend seeded observations so they form the base layer.
            if seed_observations:
                observations = seed_observations + observations
            if seed_attitude and "attitude_towards_player" not in existing_board:
                context.state.relations.update_blackboard(
                    npc_id, {"attitude_towards_player": seed_attitude}
                )
                # Re-read to include this initial write.
                existing_board = context.state.relations.get_blackboard(npc_id)

        # Build update dict — only include keys we have data for.
        updates: dict[str, Any] = {"updated_tick": current_tick}
        if observations:
            existing_obs = existing_board.get("observations", [])
            if isinstance(existing_obs, list):
                merged_obs = list(existing_obs) + [
                    o for o in observations if o not in existing_obs
                ]
            else:
                merged_obs = observations
            # 5. Overflow: entries displaced beyond cap → pending_graphize buffer.
            if len(merged_obs) > _OBS_CAP:
                overflow = merged_obs[:-_OBS_CAP]
                existing_pending = existing_board.get("pending_graphize", [])
                if isinstance(existing_pending, list):
                    existing_pending = list(existing_pending)
                else:
                    existing_pending = []
                existing_pending.extend(overflow)
                updates["pending_graphize"] = existing_pending
                updates["observations"] = merged_obs[-_OBS_CAP:]
            else:
                updates["observations"] = merged_obs

        if goals:
            existing_goals = existing_board.get("goals", [])
            if isinstance(existing_goals, list):
                merged_goals = list(existing_goals)
                for goal in goals:
                    if goal not in merged_goals:
                        merged_goals.append(goal)
            else:
                merged_goals = goals
            updates["goals"] = merged_goals

        context.state.relations.update_blackboard(npc_id, updates)

    def _run_companion_idle(
        self,
        companion_id: str,
        context: SettlementContext,
    ) -> None:
        """Update a party companion's blackboard deterministically.

        Same as _run_npc_idle, plus: checks the current area's
        scoped_interactable_overlays for undiscovered clues and appends them as
        observations.  Also seeds from impressions on first run.
        Overflow observations beyond _OBS_CAP go into pending_graphize buffer.
        """
        if not context.state.has_slice("areas"):
            return

        player_area = context.state.player.current_area
        player_location = context.state.player.current_location

        # 1. Observations from recent area_events (same as NPC).
        recent_events = context.state.areas.get_area_events(player_area)
        observations: list[str] = [
            self._event_to_observation(e)
            for e in recent_events[-3:]
            if isinstance(e, dict)
        ]

        # 2. Companion additionally notices undiscovered clues.
        clue_obs = self._find_unresolved_clues(
            context, player_area, player_location
        )
        observations.extend(clue_obs)

        # 3. Active directive goal.
        goals = self._extract_directive_goals(companion_id, context)

        # 4. Current tick.
        current_tick = self._current_tick(context)

        # Check if this is a first-run / old-save situation (no updated_tick).
        existing_board = context.state.relations.get_blackboard(companion_id)
        is_first_run = "updated_tick" not in existing_board

        # 5. Seed from impressions on first run.
        if is_first_run:
            seed_observations, seed_attitude = self._seed_from_impressions(companion_id, context)
            if seed_observations:
                observations = seed_observations + observations
            if seed_attitude and "attitude_towards_player" not in existing_board:
                context.state.relations.update_blackboard(
                    companion_id, {"attitude_towards_player": seed_attitude}
                )
                existing_board = context.state.relations.get_blackboard(companion_id)

        updates: dict[str, Any] = {"updated_tick": current_tick}
        if observations:
            existing_obs = existing_board.get("observations", [])
            if isinstance(existing_obs, list):
                merged_obs = list(existing_obs) + [
                    o for o in observations if o not in existing_obs
                ]
            else:
                merged_obs = observations
            # Overflow: entries displaced beyond cap → pending_graphize buffer.
            if len(merged_obs) > _OBS_CAP:
                overflow = merged_obs[:-_OBS_CAP]
                existing_pending = existing_board.get("pending_graphize", [])
                if isinstance(existing_pending, list):
                    existing_pending = list(existing_pending)
                else:
                    existing_pending = []
                existing_pending.extend(overflow)
                updates["pending_graphize"] = existing_pending
                updates["observations"] = merged_obs[-_OBS_CAP:]
            else:
                updates["observations"] = merged_obs

        if goals:
            existing_goals = existing_board.get("goals", [])
            if isinstance(existing_goals, list):
                merged_goals = list(existing_goals)
                for goal in goals:
                    if goal not in merged_goals:
                        merged_goals.append(goal)
            else:
                merged_goals = goals
            updates["goals"] = merged_goals

        context.state.relations.update_blackboard(companion_id, updates)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_colocated_npcs(
        context: SettlementContext,
        area_id: str,
        location_id: str | None,
        room_id: str | None,
        *,
        exclude_ids: set[str],
    ) -> list[str]:
        """Return NPC IDs in the same area[/location[/room]] as the player.

        Granularity:
        - If player has a room → match room within the area.
        - If player has a location but no room → match location within area.
        - Otherwise → match any NPC in the same area.
        """
        area_state = context.state.areas.areas.get(area_id)
        if area_state is None:
            return []

        result: list[str] = []
        for npc_id, npc_loc in area_state.npc_locations.items():
            if npc_id in exclude_ids:
                continue
            if location_id is not None:
                if npc_loc != location_id:
                    continue
                if room_id is not None:
                    npc_room = area_state.npc_rooms.get(npc_id)
                    if npc_room != room_id:
                        continue
            result.append(npc_id)
        return result

    @staticmethod
    def _event_to_observation(event: dict[str, Any]) -> str:
        """Format an area_event dict into a human-readable observation string."""
        text = str(event.get("event", "")).strip()
        tick = event.get("tick")
        source = event.get("source", "")
        if tick is not None:
            return f"[tick {tick}] {text}" if not source else f"[tick {tick}|{source}] {text}"
        return text or "(unknown event)"

    @staticmethod
    def _extract_directive_goals(
        npc_id: str, context: SettlementContext
    ) -> list[str]:
        """Return active Planner npc_goal strings for a given NPC."""
        if not context.state.has_slice("narrative_plan"):
            return []
        goals: list[str] = []
        for directive in context.state.narrative_plan.npc_directives:
            if not isinstance(directive, dict):
                continue
            if directive.get("npc_id") != npc_id:
                continue
            if directive.get("consumed"):
                continue
            inner = directive.get("directive", {})
            if not isinstance(inner, dict):
                continue
            goal = inner.get("topic") or inner.get("npc_goal")
            if isinstance(goal, str) and goal.strip():
                goals.append(goal.strip())
        return goals

    @staticmethod
    def _find_unresolved_clues(
        context: SettlementContext,
        area_id: str,
        location_id: str | None,
    ) -> list[str]:
        """Return observation strings for unresolved clues in the current location."""
        if not area_id or location_id is None:
            return []

        area_state = context.state.areas.areas.get(area_id)
        if area_state is None:
            return []

        # scoped_interactable_overlays keys are either "location_id" or
        # "location_id__room_id".  We match by prefix to cover any room.
        resolved_ids: set[str] = set(area_state.interactable_states.keys())

        clue_observations: list[str] = []
        for scope_key, overlays in area_state.scoped_interactable_overlays.items():
            # Accept overlays for the current location (any room).
            if scope_key != location_id and not scope_key.startswith(f"{location_id}__"):
                continue
            for overlay in overlays:
                if not isinstance(overlay, dict):
                    continue
                clue_id = overlay.get("id", "")
                if not clue_id or clue_id in resolved_ids:
                    continue
                # Only highlight clue-type interactables.
                overlay_type = overlay.get("type", "")
                if overlay_type and overlay_type not in ("clue", "investigate", "inspect"):
                    continue
                name = overlay.get("name", clue_id)
                clue_observations.append(f"teammate notices clue: {name} ({clue_id})")
        return clue_observations

    @staticmethod
    def _seed_from_impressions(
        npc_id: str, context: SettlementContext,
    ) -> tuple[list[str], str]:
        """Build seed observations and attitude from stored npc_impressions.

        Returns:
            (observations, attitude_towards_player)
            observations: up to 5 most recent impressions formatted as strings.
            attitude_towards_player: last impression as initial attitude, or "".
        """
        if not context.state.has_slice("relations"):
            return [], ""
        impressions = context.state.relations.get_impressions(npc_id)
        if not impressions:
            return [], ""
        # Take the 5 most recent impressions as initial observations.
        recent = impressions[-5:]
        observations = [f"[memory] {imp}" for imp in recent]
        # Use the most recent impression as the initial attitude.
        attitude = impressions[-1]
        return observations, attitude

    @staticmethod
    def _current_tick(context: SettlementContext) -> int:
        """Return the current game tick (slot), or 0 if unavailable."""
        if not context.state.has_slice("time"):
            return 0
        snap = context.state.time.snapshot()
        return int(snap.get("slot", snap.get("tick", 0)))

    # ------------------------------------------------------------------
    # Graphize: pending_graphize → knowledge graph
    # ------------------------------------------------------------------

    async def _graphize_pending(
        self,
        npc_id: str,
        pending: list[Any],
        context: SettlementContext,
    ) -> None:
        """Extract triples from pending observations and write to knowledge_graph.

        With LLM:   calls LLM with record_triple tool → parses triples → add_triple.
        Without LLM: writes raw fallback triples (npc_id, "观察到", text) via add_raw_triple.
        In both cases, clears pending_graphize from the NPC's blackboard.

        Failures are logged and silently swallowed — this is best-effort enrichment.
        """
        knowledge_graph = context.knowledge_graph
        if knowledge_graph is None:
            # No knowledge graph injected — still clear the buffer to avoid unbounded growth.
            context.state.relations.update_blackboard(npc_id, {"pending_graphize": []})
            return

        obs_text = "\n".join(str(o) for o in pending if o)
        if not obs_text.strip():
            context.state.relations.update_blackboard(npc_id, {"pending_graphize": []})
            return

        session_id = context.session_id
        if self._llm_provider is not None:
            await self._graphize_with_llm(npc_id, obs_text, knowledge_graph, session_id=session_id)
        else:
            self._graphize_fallback(npc_id, pending, knowledge_graph, session_id=session_id)

        # Clear the buffer regardless of extraction outcome.
        context.state.relations.update_blackboard(npc_id, {"pending_graphize": []})
        logger.debug("npc_autonomy: graphized %d observations for npc=%s", len(pending), npc_id)

    async def _graphize_with_llm(
        self,
        npc_id: str,
        obs_text: str,
        knowledge_graph: Any,
        session_id: str = "",
    ) -> None:
        """Use LLM to extract semantic triples from observation text."""
        # Inline tool declaration — avoids importing app-layer world_knowledge_graph.
        record_triple_tool: dict[str, Any] = {
            "name": "record_triple",
            "description": (
                "Record a semantic triple extracted from NPC observations. "
                "Call once per meaningful fact."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "Name of the subject entity."},
                    "relation": {
                        "type": "string",
                        "enum": [
                            "knows_about", "interacted_with", "made_promise",
                            "related_to", "has_opinion_of",
                        ],
                        "description": "Type of relationship.",
                    },
                    "object": {"type": "string", "description": "Name of the object entity."},
                    "weight": {
                        "type": "number",
                        "description": "Relationship strength 0.0–1.0 (default 1.0).",
                    },
                },
                "required": ["subject", "relation", "object"],
            },
        }
        prompt_text = (
            f"NPC ID: {npc_id}\n\n"
            f"## Observations\n{obs_text}"
        )
        history: list[dict[str, Any]] = [{"role": "user", "parts": [{"text": prompt_text}]}]
        try:
            response = await self._llm_provider.generate(
                _OBSERVATIONS_EXTRACTION_PROMPT,
                history,
                [record_triple_tool],
            )
        except Exception:  # noqa: BLE001
            logger.warning("npc_autonomy: LLM extraction failed for npc=%s", npc_id, exc_info=True)
            return

        for tc in (response.tool_calls or []):
            if tc.get("name") != "record_triple":
                continue
            args = tc.get("args", {})
            subj = str(args.get("subject", "")).strip()
            rel = str(args.get("relation", "related_to")).strip()
            obj = str(args.get("object", "")).strip()
            weight = float(args.get("weight", 1.0))
            if subj and obj:
                try:
                    knowledge_graph.add_triple(subj, rel, obj, weight=weight, session_id=session_id)
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "npc_autonomy: add_triple failed: %s %s %s", subj, rel, obj, exc_info=True
                    )

    @staticmethod
    def _graphize_fallback(
        npc_id: str,
        pending: list[Any],
        knowledge_graph: Any,
        session_id: str = "",
    ) -> None:
        """Fallback: write raw (npc_id, "观察到", text) triples without LLM."""
        for obs in pending:
            text = str(obs).strip()
            if not text:
                continue
            try:
                knowledge_graph.add_raw_triple(npc_id, "观察到", text, node_type="memory_note", session_id=session_id)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "npc_autonomy: add_raw_triple failed for npc=%s", npc_id, exc_info=True
                )
