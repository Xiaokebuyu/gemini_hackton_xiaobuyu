"""WorldKnowledgeGraph — static world knowledge graph with spreading activation.

Implements MemoryGraphPort using NetworkX DiGraph.  Nodes are seeded from
content registries (characters, factions, areas, items, monsters, skills,
quests) on first use for each WorldInstance (lazy + idempotent).

Spreading activation uses BFS on the undirected view of the graph so that
activation flows in both directions along each edge.

Decision record: D-N15 (narrative.md)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import networkx as nx

if TYPE_CHECKING:
    from app.game_core.adapters.llm import LlmPort
    from app.game_core.content import WorldInstance

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Node types
# ------------------------------------------------------------------

NODE_CHARACTER = "character"
NODE_FACTION = "faction"
NODE_AREA = "area"
NODE_LOCATION = "location"
NODE_ITEM = "item"
NODE_MONSTER = "monster"
NODE_SKILL = "skill"
NODE_MILESTONE = "milestone"
NODE_LORE_CONCEPT = "lore_concept"

# ------------------------------------------------------------------
# Edge relation types
# ------------------------------------------------------------------


class EdgeType:
    LOCATED_IN = "located_in"       # character → area
    BELONGS_TO = "belongs_to"       # character → faction
    HAS_CLASS = "has_class"         # character → class (id only)
    CARRIES = "carries"             # character → item
    SELLS = "sells"                 # character → item
    FACTION_REL = "faction_relation"  # faction ↔ faction
    DROPS = "drops"                 # monster → item  (weight = chance)
    ADJACENT_TO = "adjacent_to"     # area ↔ area
    CONTAINS = "contains"           # area → sub_location
    REQUIRES = "requires"           # milestone → milestone (prerequisite)
    LEADS_TO = "leads_to"           # milestone → milestone (next)
    # Dynamic edges — inserted by write_episode (Phase 3b)
    KNOWS_ABOUT     = "knows_about"       # entity learned about another entity
    INTERACTED_WITH = "interacted_with"   # interaction occurred (weight = quality)
    MADE_PROMISE    = "made_promise"      # commitment made in dialogue
    RELATED_TO      = "related_to"        # semantic relation from lore text
    HAS_OPINION_OF  = "has_opinion_of"    # disposition/attitude (weight = strength)


# ------------------------------------------------------------------
# LLM triple extraction constants (Phase 3b)
# ------------------------------------------------------------------

RECORD_TRIPLE_TOOL: dict[str, Any] = {
    "name": "record_triple",
    "description": (
        "Record a semantic triple extracted from dialogue or lore text. "
        "Call once per meaningful fact."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "description": "Name of the subject entity (character, place, item, etc.)",
            },
            "relation": {
                "type": "string",
                "enum": [
                    "knows_about", "interacted_with", "made_promise",
                    "related_to", "has_opinion_of",
                ],
                "description": "Type of relationship.",
            },
            "object": {
                "type": "string",
                "description": "Name of the object entity or concept.",
            },
            "weight": {
                "type": "number",
                "description": "Relationship strength 0.0–1.0 (default 1.0).",
            },
        },
        "required": ["subject", "relation", "object"],
    },
}

_DIALOGUE_EXTRACTION_PROMPT = (
    "You are a knowledge extractor for a fantasy RPG game.\n"
    "Given NPC dialogue, call record_triple for each significant fact:\n"
    "1. What the NPC learned about the player or world\n"
    "2. Agreements, promises, or commitments made\n"
    "3. Emotional reactions or relationship changes\n"
    "4. References to specific named people, places, or items\n"
    "Use entity names exactly as they appear. Call record_triple once per fact. "
    "If there are no significant facts, call nothing."
)

_LORE_ENRICHMENT_PROMPT = (
    "You are a knowledge extractor for a fantasy RPG world.\n"
    "Given world lore descriptions, call record_triple for each "
    "semantic relationship between named entities:\n"
    "history, alliances, conflicts, locations of legend, notable events.\n"
    "Use entity names exactly as they appear. "
    "If there are no relationships, call nothing."
)

# ------------------------------------------------------------------
# WorldKnowledgeGraph
# ------------------------------------------------------------------


@dataclass(slots=True)
class _NodeData:
    """Internal node payload stored in NetworkX node attributes."""

    node_type: str
    label: str
    tags: list[str] = field(default_factory=list)
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class WorldKnowledgeGraph:
    """NetworkX-backed world knowledge graph with BFS spreading activation.

    Thread-safety: not thread-safe.  All access is expected within a
    single async event loop without concurrent seeding calls.
    """

    def __init__(self, llm: LlmPort | None = None) -> None:
        self._graph: nx.DiGraph = nx.DiGraph()
        self._seeded: set[str] = set()
        self._lore_graphized: set[str] = set()
        self._llm = llm

    # ------------------------------------------------------------------
    # Public interface (MemoryGraphPort)
    # ------------------------------------------------------------------

    async def query_spread(
        self,
        actor_id: str,
        keywords: list[str],
        context: dict[str, Any],
        *,
        max_depth: int = 2,
        decay: float = 0.8,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Spreading activation retrieval.

        1. Lazy-seed the graph from context["world"] if provided.
        2. Find seed nodes matching keywords.
        3. BFS-propagate activation with decay.
        4. Return top_k hits sorted by activation (descending).
        """
        world = context.get("world")
        if world is not None:
            self.ensure_seeded(world)
            await self.ensure_lore_enriched(world)

        if not keywords:
            return []

        seeds = self._find_seed_nodes(keywords)
        if not seeds:
            return []

        activation = self._spread_activation(seeds, max_depth, decay)

        # Exclude seed nodes themselves — return only *activated neighbours*
        seed_set = set(seeds)
        ranked = sorted(
            ((nid, act) for nid, act in activation.items() if nid not in seed_set),
            key=lambda x: x[1],
            reverse=True,
        )[:top_k]

        return [self._node_to_hit(nid, act) for nid, act in ranked]

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------

    def ensure_seeded(self, world: WorldInstance) -> None:
        """Seed graph from *world*'s content registries (idempotent per world_id)."""
        world_id = world.world_id
        if world_id in self._seeded:
            return
        self._seed_from_world(world)
        self._seeded.add(world_id)

    async def ensure_lore_enriched(self, world: WorldInstance) -> None:
        """Lazily enrich the graph with semantic triples extracted from lore text.

        Idempotent per world_id.  Requires an LLM — no-op when unavailable.
        Makes a single batched LLM call over the top lore entries (capped at 10).
        """
        if world.world_id in self._lore_graphized:
            return
        # Mark immediately to prevent re-entry (even if LLM fails, we don't retry)
        self._lore_graphized.add(world.world_id)

        if self._llm is None:
            return

        texts = self._collect_lore_texts(world)
        if not texts:
            return

        combined = "\n\n---\n\n".join(texts[:10])
        try:
            triples = await self._extract_triples_via_llm(combined, _LORE_ENRICHMENT_PROMPT)
        except Exception:
            logger.warning(
                "ensure_lore_enriched: LLM extraction failed for %s", world.world_id
            )
            return

        for triple in triples:
            self._apply_triple(triple)

    def _seed_from_world(self, world: WorldInstance) -> None:
        self._seed_items(world)       # items first — others reference them
        self._seed_skills(world)
        self._seed_factions(world)
        self._seed_areas(world)
        self._seed_characters(world)
        self._seed_monsters(world)
        self._seed_quests(world)

    def _seed_items(self, world: WorldInstance) -> None:
        if not world.has_registry("items"):
            return
        for item in world.items.list_all():
            if not item.id:
                continue
            self._add_node(
                item.id,
                node_type=NODE_ITEM,
                label=item.name or item.id,
                tags=item.tags or [],
                description="",
                metadata={k: v for k, v in {
                    "type": item.type,
                    "rarity": item.rarity,
                    "slot": item.slot,
                }.items() if v},
            )

    def _seed_skills(self, world: WorldInstance) -> None:
        if not world.has_registry("skills"):
            return
        for skill in world.skills.list_all():
            if not skill.id:
                continue
            self._add_node(
                skill.id,
                node_type=NODE_SKILL,
                label=skill.name or skill.id,
                tags=getattr(skill, "tags", []) or [],
                description="",
                metadata={k: v for k, v in {
                    "category": skill.category,
                    "school": skill.school,
                    "spell_level": skill.spell_level,
                }.items() if v},
            )

    def _seed_factions(self, world: WorldInstance) -> None:
        if not world.has_registry("factions"):
            return
        for faction in world.factions.list_all():
            if not faction.id:
                continue
            self._add_node(
                faction.id,
                node_type=NODE_FACTION,
                label=faction.name or faction.id,
                tags=faction.tags or [],
                description=faction.description or "",
                metadata={k: v for k, v in {
                    "alignment": faction.alignment,
                }.items() if v},
            )
        # Add faction relation edges after all faction nodes exist
        for faction in world.factions.list_all():
            if not faction.id or not faction.relations:
                continue
            for other_id, rel_data in faction.relations.items():
                if not other_id:
                    continue
                weight = 1.0
                if isinstance(rel_data, dict):
                    w = rel_data.get("strength") or rel_data.get("weight")
                    if isinstance(w, (int, float)):
                        weight = float(w)
                self._add_edge_if_nodes_exist(
                    faction.id, other_id, EdgeType.FACTION_REL, weight=weight,
                )

    def _seed_areas(self, world: WorldInstance) -> None:
        if not world.has_registry("maps"):
            return
        for area in world.maps.list_all():
            if not area.id:
                continue
            self._add_node(
                area.id,
                node_type=NODE_AREA,
                label=area.name or area.id,
                tags=area.tags or [],
                description="",
                metadata={k: v for k, v in {
                    "region": area.region,
                    "base_danger": area.base_danger,
                }.items() if v is not None},
            )
            # Sub-locations as location nodes
            for sub_id in (area.sub_locations or {}):
                loc_node_id = f"{area.id}/{sub_id}"
                self._add_node(
                    loc_node_id,
                    node_type=NODE_LOCATION,
                    label=sub_id,
                    tags=[],
                    description="",
                    metadata={"parent_area": area.id},
                )
                self._add_edge_if_nodes_exist(
                    area.id, loc_node_id, EdgeType.CONTAINS, weight=1.0,
                )

        # Adjacent_to edges after all area nodes exist
        for area in world.maps.list_all():
            if not area.id:
                continue
            for connected_id in (area.connections or []):
                if not connected_id:
                    continue
                self._add_edge_if_nodes_exist(
                    area.id, connected_id, EdgeType.ADJACENT_TO, weight=1.0,
                )

    def _seed_characters(self, world: WorldInstance) -> None:
        if not world.has_registry("characters"):
            return
        for char in world.characters.list_all():
            if not char.id:
                continue
            self._add_node(
                char.id,
                node_type=NODE_CHARACTER,
                label=char.name or char.id,
                tags=char.tags or [],
                description=char.personality or char.backstory or "",
                metadata={k: v for k, v in {
                    "dialogue_style": char.dialogue_style,
                    "appearance": char.appearance,
                }.items() if v},
            )
            # located_in — prefer area_id, fall back to current_area
            area_ref = char.area_id or char.current_area
            if area_ref:
                self._add_edge_if_nodes_exist(
                    char.id, area_ref, EdgeType.LOCATED_IN, weight=1.0,
                )
            # belongs_to — prefer faction_id, fall back to faction
            faction_ref = char.faction_id or char.faction
            if faction_ref:
                self._add_edge_if_nodes_exist(
                    char.id, faction_ref, EdgeType.BELONGS_TO, weight=1.0,
                )
            # sells — from shop_inventory base_pool + rotating_pool
            if char.shop_inventory is not None:
                for pool in (
                    char.shop_inventory.base_pool,
                    char.shop_inventory.rotating_pool,
                ):
                    for entry in (pool or []):
                        item_id = entry.get("item_id") if isinstance(entry, dict) else None
                        if item_id:
                            self._add_edge_if_nodes_exist(
                                char.id, item_id, EdgeType.SELLS, weight=1.0,
                            )
            # also check legacy shop dict
            if isinstance(char.shop, dict):
                for entry in char.shop.get("inventory") or []:
                    if isinstance(entry, dict):
                        item_id = entry.get("item_id")
                        if item_id:
                            self._add_edge_if_nodes_exist(
                                char.id, item_id, EdgeType.SELLS, weight=1.0,
                            )

    def _seed_monsters(self, world: WorldInstance) -> None:
        if not world.has_registry("monsters"):
            return
        for monster in world.monsters.list_all():
            if not monster.id:
                continue
            self._add_node(
                monster.id,
                node_type=NODE_MONSTER,
                label=monster.name or monster.id,
                tags=monster.tags or [],
                description="",
                metadata={k: v for k, v in {
                    "cr": monster.cr,
                    "creature_type": monster.creature_type,
                }.items() if v is not None},
            )
            # drops edges (weight = loot chance)
            for loot in (monster.loot_table or []):
                item_id = loot.item_id if hasattr(loot, "item_id") else loot.get("item_id", "")
                chance = loot.chance if hasattr(loot, "chance") else loot.get("chance", 1.0)
                if item_id:
                    self._add_edge_if_nodes_exist(
                        monster.id, item_id, EdgeType.DROPS,
                        weight=float(chance),
                    )

    def _seed_quests(self, world: WorldInstance) -> None:
        if not world.has_registry("quests"):
            return
        for milestone in world.quests.list_all():
            if not milestone.id:
                continue
            self._add_node(
                milestone.id,
                node_type=NODE_MILESTONE,
                label=milestone.title or milestone.id,
                tags=milestone.tags or [],
                description=milestone.description or "",
                metadata={k: v for k, v in {
                    "chapter_id": milestone.chapter_id,
                }.items() if v},
            )
        # Edges after all milestone nodes exist
        for milestone in world.quests.list_all():
            if not milestone.id:
                continue
            for prereq_id in (milestone.prerequisites or []):
                if prereq_id:
                    self._add_edge_if_nodes_exist(
                        milestone.id, prereq_id, EdgeType.REQUIRES, weight=1.0,
                    )
            for next_id in (milestone.next_milestones or []):
                if next_id:
                    self._add_edge_if_nodes_exist(
                        milestone.id, next_id, EdgeType.LEADS_TO, weight=1.0,
                    )

    # ------------------------------------------------------------------
    # Graph primitives
    # ------------------------------------------------------------------

    def _add_node(
        self,
        node_id: str,
        *,
        node_type: str,
        label: str,
        tags: list[str],
        description: str,
        metadata: dict[str, Any],
    ) -> None:
        self._graph.add_node(
            node_id,
            node_type=node_type,
            label=label,
            tags=list(tags),
            description=description,
            metadata=dict(metadata),
        )

    def _add_edge_if_nodes_exist(
        self, src: str, dst: str, relation: str, *, weight: float = 1.0,
    ) -> None:
        """Add a directed edge only if both endpoints already exist as nodes."""
        if src in self._graph and dst in self._graph:
            self._graph.add_edge(src, dst, relation=relation, weight=weight)

    # ------------------------------------------------------------------
    # Spreading activation
    # ------------------------------------------------------------------

    def _find_seed_nodes(self, keywords: list[str]) -> list[str]:
        """Return node IDs matching any keyword (case-insensitive).

        Matches against: node_id, label, and tags.
        """
        lower_kws = [kw.lower() for kw in keywords if kw]
        if not lower_kws:
            return []
        seeds: list[str] = []
        for node_id, data in self._graph.nodes(data=True):
            label_lower = (data.get("label") or "").lower()
            tags_lower = [t.lower() for t in (data.get("tags") or [])]
            node_id_lower = node_id.lower()
            for kw in lower_kws:
                if (
                    kw in node_id_lower
                    or kw in label_lower
                    or any(kw in tag for tag in tags_lower)
                ):
                    seeds.append(node_id)
                    break
        return seeds

    def _spread_activation(
        self,
        seeds: list[str],
        max_depth: int,
        decay: float,
    ) -> dict[str, float]:
        """BFS spreading activation on the undirected view of the graph.

        Each seed starts with activation=1.0.  Activation spreads to
        neighbours multiplied by *decay* × edge_weight per hop.
        Only the highest activation value is kept per node.
        """
        undirected = self._graph.to_undirected()
        activation: dict[str, float] = {s: 1.0 for s in seeds}
        # frontier: (node_id, current_activation, depth)
        frontier: list[tuple[str, float, int]] = [
            (s, 1.0, 0) for s in seeds
        ]
        visited: set[str] = set(seeds)

        while frontier:
            node, act, depth = frontier.pop(0)
            if depth >= max_depth:
                continue
            for neighbour in undirected.neighbors(node):
                edge_data = undirected[node][neighbour]
                weight = float(edge_data.get("weight", 1.0))
                new_act = act * decay * weight
                if new_act > activation.get(neighbour, 0.0):
                    activation[neighbour] = new_act
                if neighbour not in visited:
                    visited.add(neighbour)
                    frontier.append((neighbour, new_act, depth + 1))

        return activation

    def _node_to_hit(self, node_id: str, activation: float) -> dict[str, Any]:
        """Convert a graph node to the L6 hit dict format."""
        data = self._graph.nodes[node_id]
        return {
            "node_id": node_id,
            "node_type": data.get("node_type", "unknown"),
            "label": data.get("label", node_id),
            "tags": list(data.get("tags") or []),
            "activation": round(activation, 4),
            "description": data.get("description", ""),
            "metadata": dict(data.get("metadata") or {}),
        }

    # ------------------------------------------------------------------
    # Inspection helpers (tests / debugging)
    # ------------------------------------------------------------------

    def node_count(self) -> int:
        """Return total number of nodes in the graph."""
        return self._graph.number_of_nodes()

    def edge_count(self) -> int:
        """Return total number of directed edges in the graph."""
        return self._graph.number_of_edges()

    def has_node(self, node_id: str) -> bool:
        return node_id in self._graph

    def has_edge(self, src: str, dst: str) -> bool:
        return self._graph.has_edge(src, dst)

    # ------------------------------------------------------------------
    # write_episode — Phase 3b: LLM triple extraction
    # ------------------------------------------------------------------

    async def write_episode(
        self,
        actor_id: str,
        messages: list[Any],
        context: dict[str, Any],
    ) -> None:
        """Write a compressed dialogue episode to the graph via LLM triple extraction.

        Called when a ContextWindow overflows.  Extracts semantic triples from
        the evicted messages and inserts dynamic edges into the knowledge graph.
        No-op when LLM is unavailable or messages is empty.
        """
        if self._llm is None or not messages:
            return

        world = context.get("world")
        if world is not None:
            self.ensure_seeded(world)

        dialogue = _format_dialogue(messages)
        if not dialogue.strip():
            return

        try:
            triples = await self._extract_triples_via_llm(
                f"NPC ID: {actor_id}\n\n{dialogue}",
                _DIALOGUE_EXTRACTION_PROMPT,
            )
        except Exception:
            logger.warning("write_episode: LLM extraction failed for %s", actor_id)
            return

        for triple in triples:
            self._apply_triple(triple)

    # ------------------------------------------------------------------
    # Phase 3b: LLM triple extraction helpers
    # ------------------------------------------------------------------

    async def _extract_triples_via_llm(
        self, text: str, system_prompt: str
    ) -> list[dict[str, Any]]:
        """Call LLM with record_triple tool and collect returned triples."""
        history = [{"role": "user", "parts": [{"text": text}]}]
        response = await self._llm.generate(system_prompt, history, [RECORD_TRIPLE_TOOL])
        triples: list[dict[str, Any]] = []
        for tc in (response.tool_calls or []):
            if tc.get("name") == "record_triple":
                triples.append(tc.get("args", {}))
        return triples

    def _apply_triple(self, triple: dict[str, Any]) -> None:
        """Map a {subject, relation, object, weight} triple to a graph edge.

        Uses _find_seed_nodes to resolve natural-language names to node IDs.
        Silently skips triples where either endpoint cannot be resolved,
        and skips self-loops (subject == object resolved to the same node).
        """
        subject_name = str(triple.get("subject", "")).strip()
        relation = str(triple.get("relation", EdgeType.RELATED_TO)).strip()
        object_name = str(triple.get("object", "")).strip()
        weight = float(triple.get("weight", 1.0))

        if not subject_name or not object_name:
            return

        subject_ids = self._find_seed_nodes([subject_name])
        object_ids = self._find_seed_nodes([object_name])

        if not subject_ids or not object_ids:
            return

        src = subject_ids[0]
        dst = object_ids[0]
        if src != dst:
            self._graph.add_edge(src, dst, relation=relation, weight=weight)

    def _collect_lore_texts(self, world: WorldInstance) -> list[str]:
        """Collect lore and character description texts for LLM enrichment."""
        texts: list[str] = []
        if world.has_registry("lore"):
            for entry in world.lore.list_all():
                desc = getattr(entry, "description", "")
                if desc:
                    name = getattr(entry, "name", None) or getattr(entry, "id", "?")
                    texts.append(f"[Lore: {name}]\n{desc}")
        if world.has_registry("characters"):
            for char in world.characters.list_all():
                desc = getattr(char, "description", "") or getattr(char, "backstory", "")
                if desc:
                    texts.append(f"[Character: {char.name}]\n{desc}")
        return texts


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _format_dialogue(messages: list[Any]) -> str:
    """Format a list of WindowMessage objects into a readable dialogue string."""
    lines: list[str] = []
    for msg in messages:
        role = getattr(msg, "role", "?")
        content = getattr(msg, "content", "")
        if content:
            lines.append(f"[{role}] {content}")
    return "\n".join(lines)
