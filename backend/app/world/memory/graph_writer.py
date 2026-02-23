"""L3 graph-write operations extracted from MemoryGraphizer.

Pure WorldGraph manipulation — no LLM, no Firestore.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models.graph_elements import ExtractedElements, MergeResult
from app.world.graph.models import WorldNode


def _extract_metadata(extraction: ExtractedElements):
    """Extract story_event_ids, transition_target, chapter_id from transcript metadata."""
    story_event_ids = []
    transition_target = ""
    chapter_id = ""
    if extraction.event_group and extraction.event_group.transcript:
        for msg in extraction.event_group.transcript:
            metadata = getattr(msg, "metadata", {}) or {}
            raw_story_events = metadata.get("story_events")
            if isinstance(raw_story_events, list):
                for event_id in raw_story_events:
                    if isinstance(event_id, str) and event_id.strip():
                        story_event_ids.append(event_id.strip())
            if not transition_target:
                raw_transition = metadata.get("transition")
                if isinstance(raw_transition, str) and raw_transition.strip():
                    transition_target = raw_transition.strip()
            if not chapter_id:
                raw_chapter_id = metadata.get("chapter_id")
                if isinstance(raw_chapter_id, str) and raw_chapter_id.strip():
                    chapter_id = raw_chapter_id.strip()
    story_event_ids = sorted(set(story_event_ids))
    return story_event_ids, transition_target, chapter_id


def merge_extraction(
    world_graph,
    npc_id: str,
    extraction: ExtractedElements,
) -> MergeResult:
    """Merge extracted graph elements into WorldGraph (synchronous, in-memory)."""
    result = MergeResult()

    story_event_ids, transition_target, chapter_id = _extract_metadata(extraction)

    # 0. Ensure owner node exists
    if not world_graph.has_node(npc_id):
        world_graph.add_node(WorldNode(
            id=npc_id, type="npc", name=npc_id,
            importance=0.2,
            properties={"character_id": npc_id, "owner": npc_id},
        ))

    # 1. event_group
    if extraction.event_group:
        eg = extraction.event_group
        transcript_snippet = [
            {"role": t.role, "content": t.content}
            for t in (eg.transcript or [])[:8]
        ]
        node = WorldNode(
            id=eg.id, type="event_group", name=eg.name,
            importance=eg.importance,
            properties={
                "day": eg.day, "location": eg.location,
                "summary": eg.summary, "emotion": eg.emotion,
                "participants": eg.participants,
                "story_events": story_event_ids,
                "transition_target": transition_target,
                "chapter_id": chapter_id,
                "source": "session_history_graphizer",
                "transcript_ref": f"event_group:{eg.id}",
                "transcript_snippet": transcript_snippet,
                "message_count": eg.message_count,
                "token_count": eg.token_count,
                "owner": npc_id,
            },
        )
        world_graph.add_node(node)
        world_graph.add_edge(npc_id, eg.id, "has_memory",
                             key=f"edge_{npc_id}_has_{eg.id}")
        result.new_nodes += 1
        result.new_node_ids.append(eg.id)

    # 2. sub_events
    for ev in extraction.sub_events:
        node = WorldNode(
            id=ev.id, type="memory_event", name=ev.name,
            importance=ev.importance,
            properties={
                "day": ev.day, "summary": ev.summary,
                "emotion": ev.emotion, "participants": ev.participants,
                "story_events": story_event_ids,
                "source": "session_history_graphizer",
                "transcript_range": ev.transcript_range.model_dump() if ev.transcript_range else None,
                "transcript_snippet": [
                    {"role": t.role, "content": t.content}
                    for t in ev.transcript_snippet
                ] if ev.transcript_snippet else None,
                "owner": npc_id,
            },
        )
        world_graph.add_node(node)
        if extraction.event_group:
            world_graph.add_edge(
                extraction.event_group.id, ev.id, "contains",
                key=f"edge_{extraction.event_group.id}_contains_{ev.id}",
            )
        result.new_nodes += 1
        result.new_node_ids.append(ev.id)

    # 3. new_nodes
    for nd in extraction.new_nodes:
        props = dict(nd.get("properties", {}))
        props["owner"] = npc_id
        node = WorldNode(
            id=nd.get("id"), type=nd.get("type", "memory"),
            name=nd.get("name", ""),
            importance=float(nd.get("importance", 0.5)),
            properties=props,
        )
        world_graph.add_node(node)
        result.new_nodes += 1
        result.new_node_ids.append(node.id)

    # 3.5. Anchor edges (guarded by has_node)
    if extraction.event_group:
        eg_id = extraction.event_group.id
        eg_loc = extraction.event_group.location
        if eg_loc and world_graph.has_node(eg_loc):
            world_graph.add_edge(eg_id, eg_loc, "located_in",
                                 key=f"edge_{eg_id}_at_{eg_loc}", weight=0.8)
            result.new_edges += 1
        world_graph.add_edge(eg_id, npc_id, "participated",
                             key=f"edge_{eg_id}_owner_{npc_id}", weight=0.9)
        result.new_edges += 1
        if world_graph.has_node("player"):
            world_graph.add_edge(eg_id, "player", "participated",
                                 key=f"edge_{eg_id}_player", weight=0.9)
            result.new_edges += 1
        for p in (extraction.event_group.participants or []):
            if p == "player":
                continue
            if world_graph.has_node(p):
                world_graph.add_edge(eg_id, p, "participated",
                                     key=f"edge_{eg_id}_part_{p}", weight=0.8)
                result.new_edges += 1

    # 4. LLM edges
    for es in extraction.edges:
        src, tgt = es.source, es.target
        if world_graph.has_node(src) and world_graph.has_node(tgt):
            world_graph.add_edge(src, tgt, es.relation,
                                 key=es.id, weight=es.weight,
                                 **(es.properties or {}))
            result.new_edges += 1
            result.new_edge_ids.append(es.id)

    # 5. State update
    if extraction.state_updates:
        world_graph.merge_state(npc_id, extraction.state_updates)

    return result


def get_context_nodes(
    world_graph,
    npc_id: str,
    limit: int = 50,
    current_scene: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get important nodes from WorldGraph for LLM context (synchronous, in-memory)."""
    char_limit = min(limit, 30)
    world_limit = limit - char_limit

    # 1. Character's own memory nodes (via _owner_index)
    char_nodes_raw = world_graph.find_memories_of(npc_id)
    char_nodes_sorted = sorted(char_nodes_raw, key=lambda n: n.importance, reverse=True)
    char_nodes = [
        {
            "id": n.id, "type": n.type, "name": n.name,
            "importance": n.importance, "properties": n.properties,
            "_scope": "character",
        }
        for n in char_nodes_sorted[:char_limit]
    ]

    # 2. World-level nodes
    world_nodes: List[Dict[str, Any]] = []
    seen_ids = {n["id"] for n in char_nodes}
    if world_limit > 0:
        # 2a. Scene neighbors first
        if current_scene and world_graph.has_node(current_scene):
            for nid, _ in world_graph.get_neighbors(current_scene):
                if nid not in seen_ids and len(world_nodes) < min(10, world_limit):
                    node = world_graph.get_node(nid)
                    if node:
                        world_nodes.append({
                            "id": node.id, "type": node.type, "name": node.name,
                            "importance": node.importance,
                            "properties": node.properties, "_scope": "world",
                        })
                        seen_ids.add(nid)

        # 2b. Fill by importance
        remaining = world_limit - len(world_nodes)
        if remaining > 0:
            all_world = []
            for ntype in ("location", "npc", "area", "item"):
                for node_id in world_graph.get_by_type(ntype):
                    if node_id not in seen_ids:
                        node = world_graph.get_node(node_id)
                        if node:
                            all_world.append(node)
                            seen_ids.add(node_id)
            all_world.sort(key=lambda n: n.importance, reverse=True)
            for node in all_world[:remaining]:
                world_nodes.append({
                    "id": node.id, "type": node.type, "name": node.name,
                    "importance": node.importance,
                    "properties": node.properties, "_scope": "world",
                })

    return char_nodes + world_nodes
