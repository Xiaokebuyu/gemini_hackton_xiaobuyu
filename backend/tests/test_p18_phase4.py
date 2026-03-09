"""Tests for P18 Phase 4 — WorldKnowledgeGraph actor-private persistence.

Covers:
- WKG serialize/restore: export_actor_state / import_actor_state
- NarrativePlanSlice.actor_knowledge field: init, set_actor_knowledge(), snapshot(), restore()
- GameRuntime integration: _sync_knowledge_graph_state / _restore_knowledge_graph
- Round-trip: write_episode → export → set_actor_knowledge → restore → import → query_spread
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.game_core.adapters import NullPersistencePort, SaveStore
from app.game_core.bootstrap import build_default_world, build_runtime_for_world
from app.game_core.runtime import GameRuntime
from app.game_core.state.slices.narrative_plan import NarrativePlanSlice
from app.world_knowledge_graph import EdgeType, WorldKnowledgeGraph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_wkg(llm: Any = None) -> WorldKnowledgeGraph:
    return WorldKnowledgeGraph(llm=llm)


def _make_runtime_with_wkg() -> tuple[Any, WorldKnowledgeGraph]:
    """Build a minimal DefaultRuntime and attach a WKG to its tick coordinator."""
    world = build_default_world("test_world")
    runtime = build_runtime_for_world(world)
    wkg = _make_wkg()
    runtime.tick_coordinator.knowledge_graph = wkg
    return runtime, wkg


def _narrative_slice() -> NarrativePlanSlice:
    return NarrativePlanSlice()


def _game_runtime() -> GameRuntime:
    return GameRuntime(save_store=SaveStore(NullPersistencePort()))


# ---------------------------------------------------------------------------
# WKG serialize/restore
# ---------------------------------------------------------------------------


class TestExportActorState:
    def test_empty_when_no_actor_graphs(self) -> None:
        wkg = _make_wkg()
        result = wkg.export_actor_state()
        assert result == {}

    def test_empty_when_actor_graph_has_no_nodes(self) -> None:
        """An actor graph with zero nodes should not appear in the export."""
        wkg = _make_wkg()
        # Ensure the actor graph exists but is empty
        wkg._ensure_actor_graph("npc_empty")
        result = wkg.export_actor_state()
        assert result == {}

    def test_export_with_actor_triple(self) -> None:
        """After inserting an actor triple the export must contain nodes + edge."""
        wkg = _make_wkg()
        # Manually insert a triple — bypass LLM
        actor_id = "npc_merchant"
        ag = wkg._ensure_actor_graph(actor_id)
        ag.add_node("npc_merchant", node_type="character", label="Merchant", tags=[], description="")
        ag.add_node("frontier_town", node_type="area", label="Frontier Town", tags=[], description="")
        ag.add_edge("npc_merchant", "frontier_town", relation=EdgeType.KNOWS_ABOUT, weight=0.9)

        exported = wkg.export_actor_state()

        assert "actors" in exported
        assert actor_id in exported["actors"]
        actor_data = exported["actors"][actor_id]
        node_ids = [n["id"] for n in actor_data["nodes"]]
        assert "npc_merchant" in node_ids
        assert "frontier_town" in node_ids
        edges = actor_data["edges"]
        assert len(edges) == 1
        assert edges[0]["src"] == "npc_merchant"
        assert edges[0]["dst"] == "frontier_town"
        assert edges[0]["relation"] == EdgeType.KNOWS_ABOUT

    def test_export_includes_memory_counts(self) -> None:
        """Memory counts must be included in the export alongside actor graphs."""
        wkg = _make_wkg()
        # remember() increments _actor_memory_counts
        wkg._actor_memory_counts["npc_a"] = 3
        ag = wkg._ensure_actor_graph("npc_a")
        ag.add_node("mem:npc_a:1", node_type="memory_note", label="x", tags=[], description="")

        exported = wkg.export_actor_state()
        assert exported["memory_counts"]["npc_a"] == 3

    def test_export_multiple_actors(self) -> None:
        wkg = _make_wkg()
        for actor_id in ("npc_a", "npc_b"):
            ag = wkg._ensure_actor_graph(actor_id)
            ag.add_node(actor_id, node_type="character", label=actor_id, tags=[], description="")

        exported = wkg.export_actor_state()
        assert set(exported["actors"].keys()) == {"npc_a", "npc_b"}


class TestImportActorState:
    def test_import_restores_nodes_and_edges(self) -> None:
        wkg = _make_wkg()
        data = {
            "actors": {
                "npc_guard": {
                    "nodes": [
                        {"id": "npc_guard", "node_type": "character", "label": "Guard",
                         "tags": [], "description": ""},
                        {"id": "city_gate", "node_type": "area", "label": "City Gate",
                         "tags": [], "description": ""},
                    ],
                    "edges": [
                        {"src": "npc_guard", "dst": "city_gate",
                         "relation": "located_in", "weight": 1.0},
                    ],
                }
            },
            "memory_counts": {"npc_guard": 0},
        }
        wkg.import_actor_state(data)

        assert wkg.has_actor_edge("npc_guard", "npc_guard", "city_gate")
        assert wkg.actor_edge_count("npc_guard") == 1

    def test_import_restores_memory_counts(self) -> None:
        wkg = _make_wkg()
        data = {
            "actors": {
                "npc_x": {
                    "nodes": [
                        {"id": "mem:npc_x:5", "node_type": "memory_note",
                         "label": "...", "tags": [], "description": ""},
                    ],
                    "edges": [],
                }
            },
            "memory_counts": {"npc_x": 5},
        }
        wkg.import_actor_state(data)
        assert wkg._actor_memory_counts.get("npc_x") == 5

    def test_import_idempotent(self) -> None:
        """Calling import_actor_state twice with the same data must not duplicate edges."""
        wkg = _make_wkg()
        data = {
            "actors": {
                "npc_a": {
                    "nodes": [
                        {"id": "npc_a", "node_type": "character", "label": "A", "tags": [], "description": ""},
                        {"id": "place_b", "node_type": "area", "label": "B", "tags": [], "description": ""},
                    ],
                    "edges": [
                        {"src": "npc_a", "dst": "place_b", "relation": "knows_about", "weight": 1.0},
                    ],
                }
            },
            "memory_counts": {},
        }
        wkg.import_actor_state(data)
        wkg.import_actor_state(data)  # second call

        # NetworkX overwrites duplicate edges — count stays 1
        assert wkg.actor_edge_count("npc_a") == 1

    def test_import_does_not_mutate_input_dicts(self) -> None:
        """import_actor_state must not pop keys from the caller's dicts."""
        node_dict = {"id": "npc_z", "node_type": "character", "label": "Z", "tags": [], "description": ""}
        edge_dict = {"src": "npc_z", "dst": "npc_z", "relation": "knows_about", "weight": 0.5}
        data = {
            "actors": {
                "npc_z": {
                    "nodes": [node_dict],
                    "edges": [edge_dict],
                }
            },
            "memory_counts": {},
        }
        wkg = _make_wkg()
        wkg.import_actor_state(data)

        # Keys must still be present in the original dicts
        assert "id" in node_dict
        assert "src" in edge_dict
        assert "dst" in edge_dict

    def test_import_invalid_data_no_crash(self) -> None:
        """Passing None or non-dict data must not raise any exception."""
        wkg = _make_wkg()
        wkg.import_actor_state(None)  # type: ignore[arg-type]
        wkg.import_actor_state("not a dict")  # type: ignore[arg-type]
        wkg.import_actor_state({})
        wkg.import_actor_state({"actors": "wrong_type"})
        wkg.import_actor_state({"actors": {"npc_bad": "not_dict"}})

    def test_import_skips_nodes_without_id(self) -> None:
        """Nodes missing the 'id' key must be silently skipped."""
        wkg = _make_wkg()
        data = {
            "actors": {
                "npc_k": {
                    "nodes": [{"node_type": "character", "label": "K", "tags": [], "description": ""}],
                    "edges": [],
                }
            },
            "memory_counts": {},
        }
        wkg.import_actor_state(data)
        # Nothing imported — actor graph either absent or empty
        assert wkg.actor_edge_count("npc_k") == 0


class TestExportImportRoundTrip:
    def test_round_trip_preserves_edges(self) -> None:
        """export_actor_state → import_actor_state on a fresh WKG → same edges present."""
        wkg_src = _make_wkg()
        actor_id = "npc_healer"
        ag = wkg_src._ensure_actor_graph(actor_id)
        ag.add_node("npc_healer", node_type="character", label="Healer", tags=[], description="")
        ag.add_node("temple", node_type="area", label="Temple", tags=[], description="")
        ag.add_edge("npc_healer", "temple", relation=EdgeType.LOCATED_IN, weight=1.0)
        wkg_src._actor_memory_counts[actor_id] = 2

        exported = wkg_src.export_actor_state()

        wkg_dst = _make_wkg()
        wkg_dst.import_actor_state(exported)

        assert wkg_dst.has_actor_edge(actor_id, "npc_healer", "temple")
        assert wkg_dst._actor_memory_counts.get(actor_id) == 2


# ---------------------------------------------------------------------------
# NarrativePlanSlice actor_knowledge
# ---------------------------------------------------------------------------


class TestNarrativePlanActorKnowledge:
    def test_initial_value_is_empty_dict(self) -> None:
        slc = _narrative_slice()
        assert slc.actor_knowledge == {}

    def test_set_actor_knowledge_updates_and_marks_dirty(self) -> None:
        slc = _narrative_slice()
        slc.clear_dirty()
        blob = {"actors": {"npc_a": {"nodes": [], "edges": []}}, "memory_counts": {}}
        slc.set_actor_knowledge(blob)
        assert slc.actor_knowledge == blob
        assert slc.dirty is True

    def test_set_actor_knowledge_empty_clears(self) -> None:
        slc = _narrative_slice()
        slc.set_actor_knowledge({"actors": {"npc_x": {"nodes": [], "edges": []}}, "memory_counts": {}})
        slc.set_actor_knowledge({})
        assert slc.actor_knowledge == {}
        assert slc.dirty is True

    def test_snapshot_includes_actor_knowledge(self) -> None:
        slc = _narrative_slice()
        blob = {"actors": {}, "memory_counts": {"npc_a": 3}}
        slc.set_actor_knowledge(blob)
        snap = slc.snapshot()
        assert "actor_knowledge" in snap
        assert snap["actor_knowledge"] == blob

    def test_snapshot_is_defensive_copy(self) -> None:
        slc = _narrative_slice()
        blob = {"actors": {"npc_a": {"nodes": [], "edges": []}}, "memory_counts": {}}
        slc.set_actor_knowledge(blob)
        snap = slc.snapshot()
        snap["actor_knowledge"]["mutated"] = True
        assert "mutated" not in slc.actor_knowledge

    def test_restore_loads_actor_knowledge(self) -> None:
        slc = _narrative_slice()
        blob = {"actors": {"npc_b": {"nodes": [], "edges": []}}, "memory_counts": {"npc_b": 1}}
        slc.restore({"actor_knowledge": blob})
        assert slc.actor_knowledge == blob

    def test_restore_defaults_to_empty_dict_on_missing(self) -> None:
        slc = _narrative_slice()
        slc.restore({})
        assert slc.actor_knowledge == {}

    def test_restore_ignores_non_mapping_actor_knowledge(self) -> None:
        slc = _narrative_slice()
        slc.restore({"actor_knowledge": "not_a_dict"})
        assert slc.actor_knowledge == {}

    def test_snapshot_restore_round_trip(self) -> None:
        slc1 = _narrative_slice()
        blob = {"actors": {"npc_c": {"nodes": [{"id": "npc_c", "node_type": "character",
                                                "label": "C", "tags": [], "description": ""}],
                                     "edges": []}},
                "memory_counts": {"npc_c": 7}}
        slc1.set_actor_knowledge(blob)
        snap = slc1.snapshot()

        slc2 = _narrative_slice()
        slc2.restore(snap)
        assert slc2.actor_knowledge == blob


# ---------------------------------------------------------------------------
# GameRuntime integration
# ---------------------------------------------------------------------------


class TestSyncKnowledgeGraphState:
    def test_sync_populates_slice_when_wkg_has_actors(self) -> None:
        runtime, wkg = _make_runtime_with_wkg()
        actor_id = "npc_sync_test"
        ag = wkg._ensure_actor_graph(actor_id)
        ag.add_node("npc_sync_test", node_type="character", label="SyncTest",
                    tags=[], description="")

        gr = _game_runtime()
        gr._sync_knowledge_graph_state(runtime)

        np_slice = runtime.state.narrative_plan
        assert np_slice.actor_knowledge != {}
        assert "actors" in np_slice.actor_knowledge
        assert actor_id in np_slice.actor_knowledge["actors"]

    def test_sync_noop_when_no_knowledge_graph(self) -> None:
        """No crash when tick_coordinator has no knowledge_graph attribute."""
        runtime, _ = _make_runtime_with_wkg()
        runtime.tick_coordinator.knowledge_graph = None

        gr = _game_runtime()
        gr._sync_knowledge_graph_state(runtime)  # must not raise

    def test_sync_noop_when_no_narrative_plan_slice(self) -> None:
        """No crash when state has no narrative_plan slice."""
        world = build_default_world("test_world")
        runtime = build_runtime_for_world(world)
        runtime.tick_coordinator.knowledge_graph = _make_wkg()

        # Remove narrative_plan slice to simulate absence
        class _FakeState:
            def has_slice(self, name: str) -> bool:
                return False
        runtime.state = _FakeState()  # type: ignore[assignment]

        gr = _game_runtime()
        gr._sync_knowledge_graph_state(runtime)  # must not raise

    def test_sync_noop_when_wkg_has_no_actor_graphs(self) -> None:
        """set_actor_knowledge must not be called if WKG exports nothing."""
        runtime, wkg = _make_runtime_with_wkg()
        np_slice = runtime.state.narrative_plan
        np_slice.clear_dirty()

        gr = _game_runtime()
        gr._sync_knowledge_graph_state(runtime)

        # No actor graphs → export is {} → not written
        assert np_slice.actor_knowledge == {}
        assert not np_slice.dirty


class TestRestoreKnowledgeGraph:
    def test_restore_imports_actor_state_into_wkg(self) -> None:
        """After setting actor_knowledge on the slice, _restore_knowledge_graph
        calls import_actor_state on the WKG."""
        runtime, wkg = _make_runtime_with_wkg()
        blob = {
            "actors": {
                "npc_restore_test": {
                    "nodes": [
                        {"id": "npc_restore_test", "node_type": "character",
                         "label": "RestoreTest", "tags": [], "description": ""},
                        {"id": "old_town", "node_type": "area",
                         "label": "Old Town", "tags": [], "description": ""},
                    ],
                    "edges": [
                        {"src": "npc_restore_test", "dst": "old_town",
                         "relation": "located_in", "weight": 1.0},
                    ],
                }
            },
            "memory_counts": {"npc_restore_test": 0},
        }
        runtime.state.narrative_plan.set_actor_knowledge(blob)

        GameRuntime._restore_knowledge_graph(runtime)

        assert wkg.has_actor_edge("npc_restore_test", "npc_restore_test", "old_town")

    def test_restore_also_restores_story_facts(self) -> None:
        """_restore_knowledge_graph must still inject story_facts (existing behaviour)."""
        runtime, wkg = _make_runtime_with_wkg()
        # Put a story fact into the slice
        runtime.state.narrative_plan.add_story_facts([
            {"subject": "goblin_king", "relation": "related_to", "object": "goblin_tribe"}
        ])

        GameRuntime._restore_knowledge_graph(runtime)

        # inject_story_facts creates nodes in the main graph
        assert wkg.has_node("goblin_king") or wkg.has_node("goblin_tribe")

    def test_restore_noop_when_no_wkg(self) -> None:
        runtime, _ = _make_runtime_with_wkg()
        runtime.tick_coordinator.knowledge_graph = None
        # Must not raise
        GameRuntime._restore_knowledge_graph(runtime)

    def test_restore_noop_when_no_narrative_plan_slice(self) -> None:
        world = build_default_world("test_world")
        runtime = build_runtime_for_world(world)
        runtime.tick_coordinator.knowledge_graph = _make_wkg()

        class _FakeState:
            def has_slice(self, name: str) -> bool:
                return False
        runtime.state = _FakeState()  # type: ignore[assignment]

        GameRuntime._restore_knowledge_graph(runtime)  # must not raise


# ---------------------------------------------------------------------------
# Full round-trip test
# ---------------------------------------------------------------------------


class TestFullRoundTrip:
    def test_round_trip_actor_knowledge_via_save_and_restore(self) -> None:
        """Simulate: write actor triple → sync (save) → reload slice → restore → query."""

        async def _run() -> None:
            gr = _game_runtime()

            # Create a session and attach a WKG
            session = await gr.create_session("test_world", world_data={}, session_id="sess_rt")
            wkg = _make_wkg()
            session.runtime.tick_coordinator.knowledge_graph = wkg

            # Insert an actor-private triple directly (bypass LLM)
            actor_id = "npc_round_trip"
            ag = wkg._ensure_actor_graph(actor_id)
            ag.add_node("npc_round_trip", node_type="character", label="RoundTrip",
                        tags=[], description="")
            ag.add_node("lost_village", node_type="area", label="Lost Village",
                        tags=[], description="")
            ag.add_edge("npc_round_trip", "lost_village",
                        relation=EdgeType.KNOWS_ABOUT, weight=1.0)

            # save_session triggers _sync_knowledge_graph_state → writes to slice
            await gr.save_session(session)

            # Verify slice was populated
            assert session.runtime.state.narrative_plan.actor_knowledge != {}

            # Resume session — a fresh WKG is attached by _bind_runtime_services
            # but since we're using a stub (no real agent_orchestration),
            # knowledge_graph remains None.  We attach a new WKG manually
            # and then call _restore_knowledge_graph directly to test the restore path.
            world = gr.get_world("test_world", world_data={})
            resumed_runtime_raw, meta = await gr._save_store.load_runtime_record_for_world(
                world, "sess_rt",
            )
            assert resumed_runtime_raw is not None

            new_wkg = _make_wkg()
            resumed_runtime_raw.tick_coordinator.knowledge_graph = new_wkg
            # Manually restore
            GameRuntime._restore_knowledge_graph(resumed_runtime_raw)

            # Actor edge must be present after restore
            assert new_wkg.has_actor_edge(actor_id, "npc_round_trip", "lost_village")

        asyncio.run(_run())
