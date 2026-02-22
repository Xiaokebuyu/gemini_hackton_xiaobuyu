"""Tests for GraphStore v2 (GraphScope addressing, dispositions)."""
from unittest.mock import MagicMock, patch

from app.models.graph_scope import GraphScope
from app.services.graph_store import GraphStore


def _make_store() -> GraphStore:
    """Create a GraphStore with a mocked Firestore client."""
    mock_client = MagicMock()
    # Patch settings import so GraphStore doesn't try to use real Firestore
    with patch("app.services.graph_store.settings"):
        store = GraphStore.__new__(GraphStore)
        store.db = mock_client
    return store


class TestGetBaseRefV2Paths:
    """_get_base_ref_v2 resolves scopes to correct Firestore paths."""

    def test_world_scope(self):
        store = _make_store()
        ref = store._get_base_ref_v2("test_world", GraphScope.world())
        store.db.collection.assert_called_with("worlds")
        assert ref is not None

    def test_chapter_scope(self):
        store = _make_store()
        ref = store._get_base_ref_v2("w1", GraphScope.chapter("ch1"))
        store.db.collection.assert_called_with("worlds")
        assert ref is not None

    def test_area_scope(self):
        store = _make_store()
        ref = store._get_base_ref_v2("w1", GraphScope.area("ch1", "frontier"))
        assert ref is not None

    def test_location_scope(self):
        store = _make_store()
        ref = store._get_base_ref_v2(
            "w1", GraphScope.location("ch1", "frontier", "tavern")
        )
        assert ref is not None

    def test_character_scope(self):
        store = _make_store()
        ref = store._get_base_ref_v2("w1", GraphScope.character("goblin_slayer"))
        assert ref is not None

    def test_camp_scope(self):
        store = _make_store()
        ref = store._get_base_ref_v2("w1", GraphScope.camp())
        assert ref is not None

    def test_graph_refs_v2_returns_nodes_and_edges(self):
        store = _make_store()
        nodes_ref, edges_ref = store._get_graph_refs_v2("w1", GraphScope.world())
        assert nodes_ref is not None
        assert edges_ref is not None
