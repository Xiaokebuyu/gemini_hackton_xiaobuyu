"""
图谱预填充 Firestore 写入器

读取 prefilled_graph.json + chapters_v2.json，按 GraphScope 分发到 Firestore。

路由规则：
  area nodes     -> chapters/{cid}/areas/{aid}/graph/  (uses chapter->area mapping)
  location nodes -> chapters/{cid}/areas/{aid}/graph/  (grouped with parent area)
  chapter nodes  -> chapters/{cid}/graph/
  faction/deity/race/monster/item/concept -> graphs/world/
  character nodes -> characters/{char_id}/
  camp node      -> camp/graph/

边路由：跟随源节点的 scope，跨 scope 则放入 world。
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from google.cloud import firestore

from app.config import settings
from app.models.graph import GraphData, MemoryEdge, MemoryNode
from app.models.graph_scope import GraphScope
from app.services.graph_store import GraphStore


class GraphPrefillLoader:
    """Loads prefilled graph data into Firestore using v2 scope addressing."""

    def __init__(self, firestore_client: Optional[firestore.Client] = None):
        self.db = firestore_client or firestore.Client(
            database=settings.firestore_database
        )
        self.graph_store = GraphStore(firestore_client=self.db)

    async def load_prefilled_graph(
        self,
        world_id: str,
        data_dir: Path,
        dry_run: bool = False,
        verbose: bool = True,
    ) -> Dict[str, Any]:
        """Load prefilled_graph.json + chapters_v2.json into Firestore.

        Returns stats dict.
        """
        stats: Dict[str, Any] = {
            "nodes_written": 0,
            "edges_written": 0,
            "chapters_meta_written": 0,
            "mainlines_meta_written": 0,
            "dispositions_written": 0,
            "scopes_used": [],
            "errors": [],
        }

        # Load data files
        graph_path = data_dir / "prefilled_graph.json"
        chapters_path = data_dir / "chapters_v2.json"
        mainlines_path = data_dir / "mainlines.json"

        if not graph_path.exists():
            stats["errors"].append(f"prefilled_graph.json not found in {data_dir}")
            return stats

        graph_raw = json.loads(graph_path.read_text(encoding="utf-8"))
        nodes = [MemoryNode(**n) for n in graph_raw.get("nodes", [])]
        edges = [MemoryEdge(**e) for e in graph_raw.get("edges", [])]

        chapters_v2: List[Dict[str, Any]] = []
        if chapters_path.exists():
            chapters_v2 = json.loads(chapters_path.read_text(encoding="utf-8"))

        mainlines_raw: List[Dict[str, Any]] = []
        if mainlines_path.exists():
            mainlines_payload = json.loads(mainlines_path.read_text(encoding="utf-8"))
            if isinstance(mainlines_payload, dict):
                raw_mainlines = mainlines_payload.get("mainlines")
                if isinstance(raw_mainlines, list):
                    mainlines_raw = raw_mainlines

        # strict-v2：写入 Firestore 前强校验，避免线上导入 legacy 结构
        if settings.narrative_v2_strict_mode:
            chapters_v2, mainlines_raw = self.upgrade_narrative_v2_artifacts(
                chapters_v2=chapters_v2,
                mainlines_raw=mainlines_raw,
            )
            self.validate_narrative_v2_artifacts(
                chapters_v2=chapters_v2,
                mainlines_raw=mainlines_raw,
                data_dir=data_dir,
            )

        if verbose:
            print(f"  Loaded {len(nodes)} nodes, {len(edges)} edges")
            if chapters_v2:
                print(f"  Loaded {len(chapters_v2)} chapter definitions")
            if mainlines_raw:
                print(f"  Loaded {len(mainlines_raw)} mainline definitions")

        # Build area -> first chapter mapping from chapters_v2
        # Each area is assigned to the earliest chapter that references it
        self._area_to_chapter: Dict[str, str] = {}
        for ch in chapters_v2:
            ch_id = ch["id"]
            for area_id in ch.get("available_areas", []):
                if area_id not in self._area_to_chapter:
                    self._area_to_chapter[area_id] = ch_id

        # Build node lookup for scope routing
        node_by_id: Dict[str, MemoryNode] = {n.id: n for n in nodes}

        # Route nodes by scope
        scope_nodes: Dict[str, List[MemoryNode]] = defaultdict(list)
        for node in nodes:
            scope_key = self._node_scope_key(node)
            scope_nodes[scope_key].append(node)

        if verbose:
            print(f"  Routing to {len(scope_nodes)} scopes:")
            for sk, ns in sorted(scope_nodes.items()):
                print(f"    {sk}: {len(ns)} nodes")

        # Route edges: follow source node scope, cross-scope goes to world
        scope_edges: Dict[str, List[MemoryEdge]] = defaultdict(list)
        for edge in edges:
            scope_key = self._edge_scope_key(edge, node_by_id)
            scope_edges[scope_key].append(edge)

        # Write nodes + edges per scope
        all_scope_keys = set(scope_nodes.keys()) | set(scope_edges.keys())
        for scope_key in sorted(all_scope_keys):
            scope = self._scope_key_to_scope(scope_key)
            if scope is None:
                stats["errors"].append(f"Cannot resolve scope key: {scope_key}")
                continue

            scope_node_list = scope_nodes.get(scope_key, [])
            scope_edge_list = scope_edges.get(scope_key, [])

            if not scope_node_list and not scope_edge_list:
                continue

            graph_data = GraphData(nodes=scope_node_list, edges=scope_edge_list)

            if not dry_run:
                await self.graph_store.save_graph_v2(
                    world_id=world_id,
                    scope=scope,
                    graph=graph_data,
                    merge=True,
                )

            stats["nodes_written"] += len(scope_node_list)
            stats["edges_written"] += len(scope_edge_list)
            if scope_key not in stats["scopes_used"]:
                stats["scopes_used"].append(scope_key)

        # Write chapter meta documents
        if chapters_v2:
            for ch in chapters_v2:
                ch_id = ch["id"]
                meta = {
                    "id": ch_id,
                    "mainline_id": ch.get("mainline_id"),
                    "name": ch["name"],
                    "type": ch.get("type", "story"),
                    "order": ch.get("order", 0),
                    "status": ch.get("status", "locked"),
                    "description": ch.get("description"),
                    "available_areas": ch.get("available_areas", []),
                    "objectives": ch.get("objectives", []),
                    "trigger_conditions": ch.get("trigger_conditions", {}),
                    "completion_conditions": ch.get("completion_conditions", {}),
                    # v2 剧情编排字段
                    "events": ch.get("events", []),
                    "transitions": ch.get("transitions", []),
                    "pacing": ch.get("pacing", {}),
                    "entry_conditions": ch.get("entry_conditions"),
                    "tags": ch.get("tags", []),
                }
                if not dry_run:
                    self._write_chapter_meta(world_id, ch_id, meta)
                stats["chapters_meta_written"] += 1
                if verbose:
                    print(f"  Chapter meta: {ch_id} ({ch['name']})")

        # Write mainline meta documents (for chapter_graph / DAG navigation)
        if mainlines_raw:
            for ml in mainlines_raw:
                if not isinstance(ml, dict):
                    continue
                mainline_id = str(ml.get("id", "")).strip()
                if not mainline_id:
                    continue
                meta = {
                    "id": mainline_id,
                    "name": ml.get("name", mainline_id),
                    "description": ml.get("description", ""),
                    "chapters": ml.get("chapters", []),
                    "chapter_graph": ml.get("chapter_graph", {}),
                }
                if not dry_run:
                    self._write_mainline_meta(world_id, mainline_id, meta)
                stats["mainlines_meta_written"] += 1
                if verbose:
                    print(f"  Mainline meta: {mainline_id} ({meta['name']})")

        # Write initial dispositions from companion/friend relationships
        disposition_count = self._write_initial_dispositions(
            world_id, edges, node_by_id, dry_run, verbose,
        )
        stats["dispositions_written"] = disposition_count

        if verbose:
            print(f"  Written {stats['nodes_written']} nodes, "
                  f"{stats['edges_written']} edges across "
                  f"{len(stats['scopes_used'])} scopes")
            if disposition_count:
                print(f"  Initial dispositions: {disposition_count}")

        return stats

    @staticmethod
    def _build_linear_chapter_graph(chapter_ids: List[str]) -> Dict[str, List[str]]:
        graph: Dict[str, List[str]] = {}
        for i in range(len(chapter_ids) - 1):
            graph[chapter_ids[i]] = [chapter_ids[i + 1]]
        return graph

    @classmethod
    def upgrade_narrative_v2_artifacts(
        cls,
        chapters_v2: List[Dict[str, Any]],
        mainlines_raw: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Upgrade legacy-like narrative artifacts to strict-v2 in memory."""
        if not isinstance(chapters_v2, list):
            chapters_v2 = []
        if not isinstance(mainlines_raw, list):
            mainlines_raw = []

        valid_chapter_ids: List[str] = []
        chapters_by_mainline: Dict[str, List[str]] = {}
        for chapter in chapters_v2:
            if not isinstance(chapter, dict):
                continue
            chapter_id = str(chapter.get("id") or "").strip()
            if chapter_id:
                valid_chapter_ids.append(chapter_id)
            mainline_id = str(chapter.get("mainline_id") or "").strip()
            if mainline_id and chapter_id:
                chapters_by_mainline.setdefault(mainline_id, []).append(chapter_id)

            chapter_type = str(chapter.get("type") or "story").strip().lower()
            if chapter_type != "story":
                continue

            completion = chapter.get("completion_conditions")
            if not isinstance(completion, dict):
                completion = {}
                chapter["completion_conditions"] = completion

            events_raw = chapter.get("events")
            if not isinstance(events_raw, list) or not events_raw:
                required_raw = completion.get("events_required")
                required_ids: List[str] = []
                if isinstance(required_raw, list):
                    for event_id in required_raw:
                        if isinstance(event_id, str) and event_id.strip():
                            required_ids.append(event_id.strip())
                if not required_ids:
                    fallback_id = f"{chapter_id or 'unknown'}_event_1"
                    required_ids = [fallback_id]

                synthesized_events: List[Dict[str, Any]] = []
                prev_event_id: Optional[str] = None
                for event_id in required_ids:
                    conditions: List[Dict[str, Any]] = []
                    if prev_event_id:
                        conditions.append({
                            "type": "event_triggered",
                            "params": {"event_id": prev_event_id},
                        })
                    else:
                        conditions.append({
                            "type": "rounds_elapsed",
                            "params": {"min_rounds": 0},
                        })
                    synthesized_events.append({
                        "id": event_id,
                        "name": event_id,
                        "description": "Auto-synthesized v2 event from legacy completion_conditions",
                        "is_required": True,
                        "is_repeatable": False,
                        "cooldown_rounds": 0,
                        "trigger_conditions": {"operator": "and", "conditions": conditions},
                        "narrative_directive": "",
                        "side_effects": [],
                    })
                    prev_event_id = event_id
                chapter["events"] = synthesized_events
                completion["events_required"] = required_ids

                tags = chapter.get("tags")
                if not isinstance(tags, list):
                    tags = []
                if "auto_migrated_v2" not in tags:
                    tags.append("auto_migrated_v2")
                chapter["tags"] = tags

            if not isinstance(chapter.get("transitions"), list):
                chapter["transitions"] = []
            if not isinstance(chapter.get("pacing"), dict):
                chapter["pacing"] = {
                    "min_rounds": 3,
                    "ideal_rounds": 10,
                    "max_rounds": 30,
                    "stall_threshold": 5,
                    "hint_escalation": [
                        "subtle_environmental",
                        "npc_reminder",
                        "direct_prompt",
                        "forced_event",
                    ],
                }
            if "entry_conditions" not in chapter:
                chapter["entry_conditions"] = None
            if not isinstance(chapter.get("tags"), list):
                chapter["tags"] = []

            required = completion.get("events_required")
            if not isinstance(required, list) or not required:
                required_ids: List[str] = []
                for ev in chapter.get("events", []):
                    if not isinstance(ev, dict):
                        continue
                    event_id = str(ev.get("id") or "").strip()
                    if event_id:
                        required_ids.append(event_id)
                if required_ids:
                    completion["events_required"] = required_ids

        valid_set = set(valid_chapter_ids)
        for mainline in mainlines_raw:
            if not isinstance(mainline, dict):
                continue
            mainline_id = str(mainline.get("id") or "").strip()
            chapters_raw = mainline.get("chapters")
            normalized_chapters: List[str] = []
            if isinstance(chapters_raw, list):
                for chapter_id in chapters_raw:
                    if (
                        isinstance(chapter_id, str)
                        and chapter_id.strip()
                        and chapter_id.strip() in valid_set
                    ):
                        normalized_chapters.append(chapter_id.strip())
            if not normalized_chapters and mainline_id in chapters_by_mainline:
                normalized_chapters = list(chapters_by_mainline[mainline_id])
            normalized_chapters = list(dict.fromkeys(normalized_chapters))
            mainline["chapters"] = normalized_chapters

            chapter_graph = mainline.get("chapter_graph")
            if not isinstance(chapter_graph, dict) or not chapter_graph:
                mainline["chapter_graph"] = cls._build_linear_chapter_graph(normalized_chapters)
            else:
                sanitized_graph: Dict[str, List[str]] = {}
                for src, targets in chapter_graph.items():
                    src_id = str(src).strip()
                    if not src_id or src_id not in valid_set or not isinstance(targets, list):
                        continue
                    valid_targets = []
                    for target in targets:
                        if isinstance(target, str) and target.strip() in valid_set:
                            valid_targets.append(target.strip())
                    if valid_targets:
                        sanitized_graph[src_id] = list(dict.fromkeys(valid_targets))
                if not sanitized_graph and normalized_chapters:
                    sanitized_graph = cls._build_linear_chapter_graph(normalized_chapters)
                mainline["chapter_graph"] = sanitized_graph

        return chapters_v2, mainlines_raw

    @staticmethod
    def validate_narrative_v2_artifacts(
        chapters_v2: List[Dict[str, Any]],
        mainlines_raw: List[Dict[str, Any]],
        data_dir: Optional[Path] = None,
    ) -> None:
        """Validate strict-v2 narrative artifacts before Firestore import."""
        if not isinstance(chapters_v2, list) or not chapters_v2:
            base = f" ({data_dir})" if data_dir else ""
            raise ValueError(f"strict-v2 导入失败: 缺少有效 chapters_v2.json{base}")
        if not isinstance(mainlines_raw, list) or not mainlines_raw:
            base = f" ({data_dir})" if data_dir else ""
            raise ValueError(f"strict-v2 导入失败: 缺少有效 mainlines.json{base}")

        invalid_events: List[str] = []
        invalid_transitions: List[str] = []
        invalid_pacing: List[str] = []
        for chapter in chapters_v2:
            if not isinstance(chapter, dict):
                continue
            chapter_type = str(chapter.get("type") or "story").strip().lower()
            if chapter_type != "story":
                continue
            chapter_id = str(chapter.get("id") or "unknown")
            if not isinstance(chapter.get("events"), list) or not chapter.get("events"):
                invalid_events.append(chapter_id)
            if not isinstance(chapter.get("transitions"), list):
                invalid_transitions.append(chapter_id)
            if not isinstance(chapter.get("pacing"), dict):
                invalid_pacing.append(chapter_id)

        invalid_graph: List[str] = []
        for mainline in mainlines_raw:
            if not isinstance(mainline, dict):
                continue
            mainline_id = str(mainline.get("id") or "unknown")
            if "chapter_graph" not in mainline or not isinstance(mainline.get("chapter_graph"), dict):
                invalid_graph.append(mainline_id)

        problems: List[str] = []
        if invalid_events:
            problems.append(f"缺少 events: {', '.join(invalid_events[:10])}")
        if invalid_transitions:
            problems.append(f"transitions 非列表: {', '.join(invalid_transitions[:10])}")
        if invalid_pacing:
            problems.append(f"pacing 非对象: {', '.join(invalid_pacing[:10])}")
        if invalid_graph:
            problems.append(f"mainline 缺少/错误 chapter_graph: {', '.join(invalid_graph[:10])}")

        if problems:
            base = f" ({data_dir})" if data_dir else ""
            raise ValueError(
                "strict-v2 导入失败" + base + ": " + " | ".join(problems)
            )

    # ---- Scope routing ----

    def _node_scope_key(self, node: MemoryNode) -> str:
        """Determine scope key for a node based on its properties.

        area nodes (type=area)    -> area:{chapter_id}:{area_id}
        location nodes under area -> area:{chapter_id}:{area_id}
        chapter nodes             -> chapter:{chapter_id}
        character nodes           -> character:{character_id}
        camp nodes                -> camp
        everything else           -> world
        """
        props = node.properties or {}
        scope_type = props.get("scope_type", "world")
        node_type = node.type

        # Area nodes: route to chapters/{cid}/areas/{aid}/graph/
        if node_type == "area":
            area_id = node.id
            chapter_id = self._area_to_chapter.get(area_id)
            if chapter_id:
                return f"area:{chapter_id}:{area_id}"
            return "world"  # fallback if no chapter mapping

        # Location nodes (scope_type=area): route to same area scope as parent
        if scope_type == "area":
            area_id = props.get("area_id")
            if area_id:
                chapter_id = self._area_to_chapter.get(area_id)
                if chapter_id:
                    return f"area:{chapter_id}:{area_id}"
            return "world"

        if scope_type == "chapter":
            return f"chapter:{props.get('chapter_id', node.id)}"
        if scope_type == "character":
            return f"character:{props.get('character_id', node.id)}"
        if scope_type == "camp":
            return "camp"
        return "world"

    def _edge_scope_key(
        self, edge: MemoryEdge, node_by_id: Dict[str, MemoryNode]
    ) -> str:
        """Route edge to same scope as its source node.

        Cross-scope edges go to 'world' scope.
        """
        src_node = node_by_id.get(edge.source)
        tgt_node = node_by_id.get(edge.target)

        if not src_node:
            return "world"

        src_key = self._node_scope_key(src_node)
        if tgt_node:
            tgt_key = self._node_scope_key(tgt_node)
            if src_key != tgt_key:
                # Cross-scope edge -> store at world level
                return "world"

        return src_key

    def _scope_key_to_scope(self, key: str) -> Optional[GraphScope]:
        """Convert a scope key string back to a GraphScope."""
        if key == "world":
            return GraphScope.world()
        if key == "camp":
            return GraphScope.camp()
        if key.startswith("chapter:"):
            chapter_id = key.split(":", 1)[1]
            return GraphScope.chapter(chapter_id)
        if key.startswith("character:"):
            char_id = key.split(":", 1)[1]
            return GraphScope.character(char_id)
        if key.startswith("area:"):
            # area:{chapter_id}:{area_id}
            parts = key.split(":", 2)
            if len(parts) == 3:
                return GraphScope.area(chapter_id=parts[1], area_id=parts[2])
            return None
        return None

    # ---- Chapter meta ----

    def _write_chapter_meta(
        self, world_id: str, chapter_id: str, meta: Dict[str, Any]
    ) -> None:
        """Write chapter meta document to chapters/{chapter_id}."""
        ref = (
            self.db.collection("worlds")
            .document(world_id)
            .collection("chapters")
            .document(chapter_id)
        )
        ref.set(meta, merge=True)

    def _write_mainline_meta(
        self, world_id: str, mainline_id: str, meta: Dict[str, Any]
    ) -> None:
        """Write mainline meta document to mainlines/{mainline_id}."""
        ref = (
            self.db.collection("worlds")
            .document(world_id)
            .collection("mainlines")
            .document(mainline_id)
        )
        ref.set(meta, merge=True)

    # ---- Dispositions ----

    def _write_initial_dispositions(
        self,
        world_id: str,
        edges: List[MemoryEdge],
        node_by_id: Dict[str, MemoryNode],
        dry_run: bool,
        verbose: bool,
    ) -> int:
        """Write initial disposition documents from relationship edges.

        Companion relationships -> approval=20, trust=20
        Friend/knows with trusts -> approval=10, trust=15
        Enemy relationships -> approval=-20, trust=-20
        """
        # Collect character IDs
        char_ids: Set[str] = set()
        for node in node_by_id.values():
            if node.type == "character":
                char_ids.add(node.id)

        # Disposition mapping by (source, target)
        dispositions: Dict[Tuple[str, str], Dict[str, Any]] = {}

        _RELATION_DISPOSITIONS = {
            "companion_of": {"approval": 20, "trust": 20},
            "knows": {"approval": 5, "trust": 5},
            "trusts": {"approval": 10, "trust": 15},
            "enemy_of": {"approval": -20, "trust": -20},
            "rivals": {"approval": -5, "trust": 0},
            "fears": {"approval": 0, "trust": -10, "fear": 20},
        }

        for edge in edges:
            if edge.source not in char_ids or edge.target not in char_ids:
                continue

            rel_disp = _RELATION_DISPOSITIONS.get(edge.relation)
            if not rel_disp:
                continue

            key = (edge.source, edge.target)
            if key not in dispositions:
                dispositions[key] = {
                    "approval": 0,
                    "trust": 0,
                    "fear": 0,
                    "romance": 0,
                    "history": [],
                }

            d = dispositions[key]
            for field in ("approval", "trust", "fear", "romance"):
                d[field] = max(-100, min(100, d[field] + rel_disp.get(field, 0)))

            evidence = (edge.properties or {}).get("evidence_text", edge.relation)
            d["history"].append({
                "delta_approval": rel_disp.get("approval", 0),
                "delta_trust": rel_disp.get("trust", 0),
                "reason": f"worldbook:{evidence}",
                "day": 0,
            })

        # Write to Firestore
        count = 0
        batch = self.db.batch()
        for (char_id, target_id), disp_data in dispositions.items():
            ref = (
                self.db.collection("worlds")
                .document(world_id)
                .collection("characters")
                .document(char_id)
                .collection("dispositions")
                .document(target_id)
            )
            if not dry_run:
                batch.set(ref, disp_data, merge=True)
            count += 1

            if count % 450 == 0 and not dry_run:
                batch.commit()
                batch = self.db.batch()

        if count % 450 != 0 and not dry_run:
            batch.commit()

        if verbose and count:
            print(f"  Dispositions: {count} initial relationships")

        return count
