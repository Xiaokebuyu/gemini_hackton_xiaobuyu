"""
CRPG 图谱预填充编排器 (Phase 4)

从 structured/ 产物（maps.json, characters.json, mainlines.json, world_graph.json）
生成 v2 架构的 MemoryNode/MemoryEdge 数据，按 GraphScope 层级组织。

输出：
- prefilled_graph.json — 所有节点和边，按 scope 标记
- chapters_v2.json — 章节元数据（新格式）

用法：
    python -m app.tools.worldbook_graphizer.graph_prefill data/goblin_slayer/structured
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.models.graph import MemoryEdge, MemoryNode
from app.models.graph_nodes import (
    RELATION_BASE_WEIGHT,
    AreaNode,
    CharacterNode,
    ChapterNode,
    CRPGNodeType,
    CRPGRelationType,
    LocationNode,
    default_importance,
)


# ==================== Relationship Mapping ====================


# Chinese relationship text -> list of (relation_type, weight) pairs
# Some relationships produce multiple edges (e.g. friend -> knows + trusts)
_RELATION_KEYWORDS: List[Tuple[List[str], List[Tuple[str, float]]]] = [
    (["同伴", "队友", "战友", "伙伴", "搭档"],
     [(CRPGRelationType.COMPANION_OF, 0.9)]),
    (["敌人", "仇敌", "对手", "敌对"],
     [(CRPGRelationType.ENEMY_OF, 0.8)]),
    (["好友", "朋友", "友人"],
     [(CRPGRelationType.KNOWS, 0.7), (CRPGRelationType.TRUSTS, 0.6)]),
    (["崇敬", "师傅", "导师", "崇拜", "敬仰", "尊敬"],
     [(CRPGRelationType.KNOWS, 0.8), (CRPGRelationType.TRUSTS, 0.8)]),
    (["竞争", "较量"],
     [(CRPGRelationType.RIVALS, 0.7)]),
]


def _classify_relationship(description: str) -> List[Tuple[str, float]]:
    """Classify a Chinese relationship description into relation type(s) + weight(s)."""
    for keywords, relations in _RELATION_KEYWORDS:
        for kw in keywords:
            if kw in description:
                return relations
    return [(CRPGRelationType.KNOWS, 0.5)]


# ==================== Prefill Result ====================


@dataclass
class PrefillResult:
    """Prefill pipeline output."""

    nodes: List[MemoryNode] = field(default_factory=list)
    edges: List[MemoryEdge] = field(default_factory=list)
    chapters_v2: List[Dict[str, Any]] = field(default_factory=list)

    # Stats
    area_count: int = 0
    location_count: int = 0
    character_count: int = 0
    chapter_count: int = 0
    camp_count: int = 0
    world_node_count: int = 0
    edge_count: int = 0

    def summary(self) -> str:
        return (
            f"Prefill: {len(self.nodes)} nodes, {len(self.edges)} edges\n"
            f"  Areas: {self.area_count}, Locations: {self.location_count}\n"
            f"  Characters: {self.character_count}, Chapters: {self.chapter_count}\n"
            f"  Camp locations: {self.camp_count}\n"
            f"  World-level nodes (faction/deity/race/monster/item/concept): "
            f"{self.world_node_count}"
        )


# ==================== GraphPrefiller ====================


class GraphPrefiller:
    """Reads structured data and emits typed MemoryNode/MemoryEdge."""

    # World-graph node types to keep (drop character and location — 它们由专门的处理器负责)
    _WORLD_KEEP_TYPES: Set[str] = {
        "faction", "deity", "race", "monster", "item", "concept", "knowledge",
        "event",
    }

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._node_ids: Set[str] = set()
        self._edge_ids: Set[str] = set()

    # ---- public entry point ----

    def run(self, verbose: bool = False) -> PrefillResult:
        """Execute the full prefill pipeline."""
        result = PrefillResult()

        maps_data = self._load_json("maps.json")
        chars_data = self._load_json("characters.json")
        mainlines_data = self._load_json("mainlines.json")
        world_graph = self._load_json("world_graph.json")

        # 1) Maps -> AreaNode + LocationNode + edges
        self._process_maps(maps_data, result)
        if verbose:
            print(f"[maps] {result.area_count} areas, {result.location_count} locations")

        # 2) Characters -> CharacterNode + default_area/hosts_npc + relationship edges
        self._process_characters(chars_data, result)
        if verbose:
            print(f"[characters] {result.character_count} characters")

        # 3) Mainlines -> ChapterNode + opens_area edges + chapters_v2
        self._process_mainlines(mainlines_data, result)
        if verbose:
            print(f"[mainlines] {result.chapter_count} chapters")

        # 4) Camp auto-generation
        self._generate_camp(maps_data, result)
        if verbose:
            print(f"[camp] {result.camp_count} camp locations")

        # 5) World graph -> faction/deity/race/monster/item/concept nodes + edges
        self._process_world_graph(world_graph, result)
        if verbose:
            print(f"[world_graph] {result.world_node_count} world-level nodes")

        result.edge_count = len(result.edges)
        if verbose:
            print(result.summary())

        return result

    def save(self, result: PrefillResult, output_dir: Optional[Path] = None) -> None:
        """Save prefill results to JSON files."""
        out = output_dir or self.data_dir
        out.mkdir(parents=True, exist_ok=True)

        # prefilled_graph.json
        graph_data = {
            "nodes": [n.model_dump(mode="json") for n in result.nodes],
            "edges": [e.model_dump(mode="json") for e in result.edges],
        }
        (out / "prefilled_graph.json").write_text(
            json.dumps(graph_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # chapters_v2.json
        (out / "chapters_v2.json").write_text(
            json.dumps(result.chapters_v2, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ---- internal: maps ----

    def _process_maps(self, maps_data: Dict, result: PrefillResult) -> None:
        maps_list = maps_data.get("maps", [])
        for m in maps_list:
            area_id = m["id"]
            area_node = AreaNode(
                id=area_id,
                name=m["name"],
                danger_level=_danger_to_int(m.get("danger_level", "low")),
                atmosphere=m.get("atmosphere"),
                description=m.get("description"),
                scope_type="world",
            )
            self._add_node(area_node.to_memory_node(), result)
            result.area_count += 1

            # Sub-locations
            for sub in m.get("sub_locations", []):
                loc_id = f"{area_id}__{sub['id']}"
                loc_node = LocationNode(
                    id=loc_id,
                    name=sub["name"],
                    description=sub.get("description"),
                    resident_npcs=sub.get("resident_npcs", []),
                    scope_type="area",
                    area_id=area_id,
                )
                self._add_node(loc_node.to_memory_node(), result)
                result.location_count += 1

                # area -> has_location -> location
                self._add_edge(
                    source=area_id,
                    target=loc_id,
                    relation=CRPGRelationType.HAS_LOCATION,
                    props={"created_by": "worldbook"},
                    result=result,
                )

                # hosts_npc edges for resident NPCs
                for npc_id in sub.get("resident_npcs", []):
                    self._add_edge(
                        source=loc_id,
                        target=npc_id,
                        relation=CRPGRelationType.HOSTS_NPC,
                        props={"created_by": "worldbook"},
                        result=result,
                    )

            # Map connections (area -> connects_to -> area)
            for conn in m.get("connections", []):
                target_id = conn.get("target_map_id")
                if target_id:
                    self._add_edge(
                        source=area_id,
                        target=target_id,
                        relation=CRPGRelationType.CONNECTS_TO,
                        props={
                            "travel_time": conn.get("travel_time"),
                            "created_by": "worldbook",
                        },
                        result=result,
                    )

    # ---- internal: characters ----

    def _process_characters(self, chars_data: Dict, result: PrefillResult) -> None:
        seen_ids: Set[str] = set()
        characters = chars_data.get("characters", [])
        for c in characters:
            char_id = c["id"]
            if char_id in seen_ids:
                continue  # skip duplicates

            # 设计约束：passerby 不进入持久图谱，走 passerby_pool。
            if c.get("tier") == "passerby":
                continue

            seen_ids.add(char_id)

            tier = c.get("tier", "secondary")
            char_node = CharacterNode(
                id=char_id,
                name=c["name"],
                role=tier,
                personality=c.get("personality"),
                background=c.get("backstory"),
                scope_type="character",
                character_id=char_id,
            )
            self._add_node(char_node.to_memory_node(), result)
            result.character_count += 1

            # default_area edge
            default_map = c.get("default_map")
            if default_map:
                self._add_edge(
                    source=char_id,
                    target=default_map,
                    relation=CRPGRelationType.DEFAULT_AREA,
                    props={"created_by": "worldbook"},
                    result=result,
                )

            # default_sub_location -> hosts_npc edge
            default_sub = c.get("default_sub_location")
            if default_sub and default_map:
                loc_id = f"{default_map}__{default_sub}"
                self._add_edge(
                    source=loc_id,
                    target=char_id,
                    relation=CRPGRelationType.HOSTS_NPC,
                    props={"created_by": "worldbook"},
                    result=result,
                )

            # Relationship edges (may produce multiple edges per relationship)
            relationships = c.get("relationships", {})
            for target_id, desc in relationships.items():
                rel_pairs = _classify_relationship(desc)
                for rel_type, weight in rel_pairs:
                    self._add_edge(
                        source=char_id,
                        target=target_id,
                        relation=rel_type,
                        weight=weight,
                        props={
                            "evidence_text": desc,
                            "created_by": "worldbook",
                        },
                        result=result,
                    )

    # ---- internal: mainlines ----

    def _process_mainlines(self, mainlines_data: Dict, result: PrefillResult) -> None:
        chapters = mainlines_data.get("chapters", [])

        # 无 mainlines 时自动生成默认章节，打开所有已处理的区域
        if not chapters:
            area_ids = [n.id for n in result.nodes if n.type == "area"]
            if area_ids:
                chapters = [{
                    "id": "ch_default",
                    "name": "序章",
                    "description": "默认初始章节",
                    "available_maps": area_ids,
                }]

        for i, ch in enumerate(chapters):
            ch_id = ch["id"]
            chapter_node = ChapterNode(
                id=ch_id,
                name=ch["name"],
                order=i,
                status="locked" if i > 0 else "active",
                description=ch.get("description"),
                scope_type="chapter",
                chapter_id=ch_id,
            )
            self._add_node(chapter_node.to_memory_node(), result)
            result.chapter_count += 1

            # opens_area edges
            for area_id in ch.get("available_maps", []):
                self._add_edge(
                    source=ch_id,
                    target=area_id,
                    relation=CRPGRelationType.OPENS_AREA,
                    props={"created_by": "worldbook"},
                    result=result,
                )

            # chapters_v2 output format
            result.chapters_v2.append({
                "id": ch_id,
                "mainline_id": ch.get("mainline_id"),
                "name": ch["name"],
                "order": i,
                "status": "locked" if i > 0 else "active",
                "description": ch.get("description"),
                "available_areas": ch.get("available_maps", []),
                "objectives": ch.get("objectives", []),
                "trigger_conditions": ch.get("trigger_conditions", {}),
                "completion_conditions": ch.get("completion_conditions", {}),
            })

    # ---- internal: camp ----

    def _generate_camp(self, maps_data: Dict, result: PrefillResult) -> None:
        """Generate camp locations from safe areas (danger_level low or medium)."""
        safe_areas = []
        for m in maps_data.get("maps", []):
            dl = m.get("danger_level", "low")
            if dl in ("low", "medium"):
                safe_areas.append(m)

        if not safe_areas:
            return

        # Create a camp meta-node
        camp_node = MemoryNode(
            id="camp",
            type=CRPGNodeType.LOCATION,
            name="营地",
            importance=0.7,
            properties={
                "scope_type": "camp",
                "description": "冒险者的营地，可在安全区域扎营休息",
                "unlocked_features": ["rest", "conversation", "inventory"],
            },
        )
        self._add_node(camp_node, result)
        result.camp_count += 1

        # Link camp to each safe area
        for m in safe_areas:
            self._add_edge(
                source="camp",
                target=m["id"],
                relation=CRPGRelationType.CONNECTS_TO,
                props={
                    "created_by": "worldbook",
                    "description": f"可在{m['name']}附近扎营",
                },
                result=result,
            )

    # ---- internal: world graph ----

    def _normalize_endpoint(self, raw_id: str) -> Optional[str]:
        """尝试规范化 world_graph 中的端点 ID 到已注册节点"""
        if raw_id in self._node_ids:
            return raw_id
        # 尝试 strip type prefix
        for prefix in ("character_", "location_"):
            stripped = raw_id.removeprefix(prefix)
            if stripped != raw_id and stripped in self._node_ids:
                return stripped
        return None

    def _process_world_graph(self, world_graph: Dict, result: PrefillResult) -> None:
        """Import faction/deity/race/monster/item/concept nodes and their edges."""
        nodes = world_graph.get("nodes", [])
        kept_ids: Set[str] = set()

        for n in nodes:
            ntype = n.get("type", "")
            if ntype not in self._WORLD_KEEP_TYPES:
                continue

            node_id = n["id"]
            kept_ids.add(node_id)

            # Map type to CRPGNodeType value (they should match)
            importance = default_importance(ntype)
            props = dict(n.get("properties", {}))
            props["scope_type"] = "world"
            props["created_by"] = "worldbook"

            mn = MemoryNode(
                id=node_id,
                type=ntype,
                name=n["name"],
                importance=importance,
                properties=props,
            )
            self._add_node(mn, result)
            result.world_node_count += 1

        # Edges: keep edges where both endpoints exist (with ID normalization)
        for e in world_graph.get("edges", []):
            src = self._normalize_endpoint(e["source"])
            tgt = self._normalize_endpoint(e["target"])
            if src is None or tgt is None:
                continue

            relation = e.get("relation", "knows")
            weight = e.get("weight", RELATION_BASE_WEIGHT.get(relation, 0.5))
            props = dict(e.get("properties", {}))
            props["created_by"] = "worldbook"

            self._add_edge(
                source=src,
                target=tgt,
                relation=relation,
                weight=weight,
                props=props,
                result=result,
                edge_id=e.get("id"),
            )

    # ---- helpers ----

    def _load_json(self, filename: str) -> Dict:
        path = self.data_dir / filename
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _add_node(self, node: MemoryNode, result: PrefillResult) -> None:
        if node.id not in self._node_ids:
            self._node_ids.add(node.id)
            result.nodes.append(node)

    def _add_edge(
        self,
        source: str,
        target: str,
        relation: str,
        result: PrefillResult,
        weight: Optional[float] = None,
        props: Optional[Dict] = None,
        edge_id: Optional[str] = None,
    ) -> None:
        eid = edge_id or f"edge_{source}__{relation}__{target}"
        if eid in self._edge_ids:
            return
        self._edge_ids.add(eid)

        if weight is None:
            weight = RELATION_BASE_WEIGHT.get(relation, 0.5)

        edge = MemoryEdge(
            id=eid,
            source=source,
            target=target,
            relation=relation,
            weight=weight,
            properties=props or {},
        )
        result.edges.append(edge)


# ==================== Utility ====================


def _danger_to_int(level: str) -> int:
    return {"low": 1, "medium": 2, "high": 3, "extreme": 4}.get(level, 1)


# ==================== CLI ====================


def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m app.tools.worldbook_graphizer.graph_prefill <data_dir>")
        sys.exit(1)

    data_dir = Path(sys.argv[1])
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else data_dir

    prefiller = GraphPrefiller(data_dir)
    result = prefiller.run(verbose=True)
    prefiller.save(result, output_dir)
    print(f"\nSaved to {output_dir}/")


if __name__ == "__main__":
    main()
