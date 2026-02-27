"""Tests for GraphScope addressing."""
import pytest

from app.models.graph_scope import GraphScope, VALID_SCOPE_TYPES


class TestGraphScopeValidation:
    """Validation rules for GraphScope."""

    def test_invalid_scope_type_raises(self):
        with pytest.raises(ValueError, match="Invalid scope_type"):
            GraphScope(scope_type="invalid")

    def test_chapter_requires_chapter_id(self):
        with pytest.raises(ValueError, match="chapter_id"):
            GraphScope(scope_type="chapter")

    def test_area_requires_chapter_and_area(self):
        with pytest.raises(ValueError, match="chapter_id and area_id"):
            GraphScope(scope_type="area", chapter_id="ch1")
        with pytest.raises(ValueError, match="chapter_id and area_id"):
            GraphScope(scope_type="area", area_id="a1")

    def test_location_requires_all_three(self):
        with pytest.raises(ValueError, match="chapter_id, area_id, and location_id"):
            GraphScope(scope_type="location", chapter_id="ch1", area_id="a1")

    def test_character_requires_character_id(self):
        with pytest.raises(ValueError, match="character_id"):
            GraphScope(scope_type="character")

    def test_world_no_extra_ids_needed(self):
        scope = GraphScope(scope_type="world")
        assert scope.scope_type == "world"

    def test_camp_no_extra_ids_needed(self):
        scope = GraphScope(scope_type="camp")
        assert scope.scope_type == "camp"


class TestGraphScopeFactories:
    """Factory methods produce correct scopes."""

    def test_world(self):
        s = GraphScope.world()
        assert s.scope_type == "world"
        assert s.chapter_id is None

    def test_chapter(self):
        s = GraphScope.chapter("ch1")
        assert s.scope_type == "chapter"
        assert s.chapter_id == "ch1"

    def test_area(self):
        s = GraphScope.area("ch1", "frontier")
        assert s.scope_type == "area"
        assert s.chapter_id == "ch1"
        assert s.area_id == "frontier"

    def test_location(self):
        s = GraphScope.location("ch1", "frontier", "tavern")
        assert s.scope_type == "location"
        assert s.chapter_id == "ch1"
        assert s.area_id == "frontier"
        assert s.location_id == "tavern"

    def test_character(self):
        s = GraphScope.character("goblin_slayer")
        assert s.scope_type == "character"
        assert s.character_id == "goblin_slayer"

    def test_camp(self):
        s = GraphScope.camp()
        assert s.scope_type == "camp"


class TestGraphScopeImmutability:
    """GraphScope is frozen (immutable)."""

    def test_frozen(self):
        s = GraphScope.world()
        with pytest.raises(AttributeError):
            s.scope_type = "chapter"

    def test_hashable(self):
        s1 = GraphScope.world()
        s2 = GraphScope.world()
        assert hash(s1) == hash(s2)
        assert s1 == s2

    def test_different_scopes_not_equal(self):
        s1 = GraphScope.world()
        s2 = GraphScope.camp()
        assert s1 != s2


class TestValidScopeTypes:
    """VALID_SCOPE_TYPES matches all factory methods."""

    def test_all_types_covered(self):
        assert VALID_SCOPE_TYPES == {"world", "chapter", "area", "location", "character", "camp"}
