"""Tests for Phase 1 of NPC Service System — ServiceDescriptor + NarrativePlanSlice.npc_services.

Coverage:
- ServiceDescriptor snapshot/restore round-trip
- validate_effects: valid atoms, missing type, unknown type
- NarrativePlanSlice: assign / revoke / get / prune_expired_services
- replace same service_id
- defensive copy from get_services
- snapshot/restore includes npc_services
- apply_state_change: npc_services.assign / npc_services.revoke paths
- validate() catches malformed npc_services
"""

from app.game_core.planning.service_descriptors import (
    ServiceDescriptor,
    VALID_EFFECT_ATOM_TYPES,
    validate_effects,
)
from app.game_core.state.slices.narrative_plan import NarrativePlanSlice
from app.game_core.state.delta import StateChange


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_svc(**overrides) -> dict:
    base = {
        "service_id": "heal",
        "npc_id": "priestess",
        "label": "治疗",
        "price": 10,
        "effects": [{"type": "restore_hp", "amount": 30}],
        "preconditions": {},
        "notes": "",
        "assigned_tick": 5,
        "expiry_tick": 0,
        "source": "content",
        "one_shot": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# TestValidEffectAtomTypes
# ---------------------------------------------------------------------------

class TestValidEffectAtomTypes:
    def test_all_expected_types_present(self):
        expected = {
            "restore_hp", "modify_gold", "grant_item", "remove_item",
            "apply_effect", "remove_effect", "add_xp", "add_knowledge",
        }
        assert expected == VALID_EFFECT_ATOM_TYPES

    def test_frozenset_is_immutable(self):
        assert isinstance(VALID_EFFECT_ATOM_TYPES, frozenset)


# ---------------------------------------------------------------------------
# TestValidateEffects
# ---------------------------------------------------------------------------

class TestValidateEffects:
    def test_empty_list_is_valid(self):
        errors = validate_effects([])
        assert errors == []

    def test_single_valid_atom(self):
        errors = validate_effects([{"type": "restore_hp", "amount": 30}])
        assert errors == []

    def test_multiple_valid_atoms(self):
        atoms = [
            {"type": "restore_hp", "amount": 15},
            {"type": "modify_gold", "delta": -10},
            {"type": "add_xp", "amount": 50},
        ]
        errors = validate_effects(atoms)
        assert errors == []

    def test_not_a_list_returns_error(self):
        errors = validate_effects("not_a_list")  # type: ignore[arg-type]
        assert any("must be a list" in e for e in errors)

    def test_atom_not_dict_returns_error(self):
        errors = validate_effects(["not_a_dict"])
        assert any("must be a dict" in e for e in errors)

    def test_atom_missing_type_returns_error(self):
        errors = validate_effects([{"amount": 30}])
        assert any("missing 'type'" in e for e in errors)

    def test_atom_unknown_type_returns_error(self):
        errors = validate_effects([{"type": "teleport"}])
        assert any("unknown type 'teleport'" in e for e in errors)

    def test_mixed_valid_and_invalid(self):
        atoms = [
            {"type": "restore_hp", "amount": 10},
            {"type": "unknown_atom"},
        ]
        errors = validate_effects(atoms)
        assert len(errors) == 1
        assert "unknown_atom" in errors[0]

    def test_all_valid_effect_types_pass(self):
        for atom_type in VALID_EFFECT_ATOM_TYPES:
            errors = validate_effects([{"type": atom_type}])
            assert errors == [], f"Expected no errors for type {atom_type!r}"


# ---------------------------------------------------------------------------
# TestServiceDescriptorSnapshotRestore
# ---------------------------------------------------------------------------

class TestServiceDescriptorSnapshotRestore:
    def test_snapshot_restore_round_trip(self):
        svc = ServiceDescriptor(
            service_id="heal",
            npc_id="priestess",
            label="治疗",
            price=10,
            effects=[{"type": "restore_hp", "amount": 30}],
            preconditions={"min_gold": 10},
            notes="use when hp low",
            assigned_tick=5,
            expiry_tick=100,
            source="content",
            one_shot=False,
        )
        data = svc.snapshot()
        restored = ServiceDescriptor.restore(data)

        assert restored.service_id == "heal"
        assert restored.npc_id == "priestess"
        assert restored.label == "治疗"
        assert restored.price == 10
        assert restored.effects == [{"type": "restore_hp", "amount": 30}]
        assert restored.preconditions == {"min_gold": 10}
        assert restored.notes == "use when hp low"
        assert restored.assigned_tick == 5
        assert restored.expiry_tick == 100
        assert restored.source == "content"
        assert restored.one_shot is False

    def test_snapshot_returns_defensive_copy_effects(self):
        svc = ServiceDescriptor(
            service_id="heal",
            npc_id="priestess",
            label="治疗",
            effects=[{"type": "restore_hp", "amount": 30}],
        )
        data = svc.snapshot()
        data["effects"][0]["amount"] = 999
        # Original should be unchanged
        assert svc.effects[0]["amount"] == 30

    def test_snapshot_returns_defensive_copy_preconditions(self):
        svc = ServiceDescriptor(
            service_id="s1",
            npc_id="n1",
            label="X",
            preconditions={"min_gold": 10},
        )
        data = svc.snapshot()
        data["preconditions"]["min_gold"] = 999
        assert svc.preconditions["min_gold"] == 10

    def test_restore_missing_fields_use_defaults(self):
        restored = ServiceDescriptor.restore({"service_id": "s1", "npc_id": "n1", "label": "L"})
        assert restored.price == 0
        assert restored.effects == []
        assert restored.preconditions == {}
        assert restored.notes == ""
        assert restored.assigned_tick == 0
        assert restored.expiry_tick == 0
        assert restored.source == "planner"
        assert restored.one_shot is False

    def test_restore_one_shot_true(self):
        restored = ServiceDescriptor.restore(
            {"service_id": "s1", "npc_id": "n1", "label": "L", "one_shot": True}
        )
        assert restored.one_shot is True

    def test_restore_invalid_effects_skipped(self):
        data = {
            "service_id": "s1",
            "npc_id": "n1",
            "label": "L",
            "effects": ["not_a_dict", {"type": "restore_hp"}, 42],
        }
        restored = ServiceDescriptor.restore(data)
        # Only the valid dict entry survives
        assert len(restored.effects) == 1
        assert restored.effects[0]["type"] == "restore_hp"

    def test_restore_invalid_preconditions_becomes_empty_dict(self):
        data = {"service_id": "s1", "npc_id": "n1", "label": "L", "preconditions": "invalid"}
        restored = ServiceDescriptor.restore(data)
        assert restored.preconditions == {}


# ---------------------------------------------------------------------------
# TestNarrativePlanServices
# ---------------------------------------------------------------------------

class TestNarrativePlanServices:
    def test_assign_and_get(self):
        np = NarrativePlanSlice()
        svc = _make_svc()
        np.assign_service("priestess", svc)
        svcs = np.get_services("priestess")
        assert len(svcs) == 1
        assert svcs[0]["service_id"] == "heal"
        assert np._dirty

    def test_get_returns_defensive_copy(self):
        np = NarrativePlanSlice()
        np.assign_service("priestess", _make_svc(notes="original"))
        svcs = np.get_services("priestess")
        svcs[0]["notes"] = "mutated"
        # Original internal state should be unchanged
        assert np.npc_services["priestess"][0]["notes"] == "original"

    def test_get_unknown_npc_returns_empty_list(self):
        np = NarrativePlanSlice()
        assert np.get_services("unknown_npc") == []

    def test_assign_replaces_same_service_id(self):
        np = NarrativePlanSlice()
        np.assign_service("priestess", _make_svc(notes="v1"))
        np.assign_service("priestess", _make_svc(notes="v2"))
        svcs = np.get_services("priestess")
        assert len(svcs) == 1
        assert svcs[0]["notes"] == "v2"

    def test_assign_multiple_different_ids(self):
        np = NarrativePlanSlice()
        np.assign_service("priestess", _make_svc(service_id="heal"))
        np.assign_service("priestess", _make_svc(service_id="blessing"))
        svcs = np.get_services("priestess")
        assert len(svcs) == 2
        ids = {s["service_id"] for s in svcs}
        assert ids == {"heal", "blessing"}

    def test_revoke_existing_returns_true(self):
        np = NarrativePlanSlice()
        np.assign_service("priestess", _make_svc())
        result = np.revoke_service("priestess", "heal")
        assert result is True
        assert np.get_services("priestess") == []
        # key should be cleaned up
        assert "priestess" not in np.npc_services

    def test_revoke_nonexistent_returns_false(self):
        np = NarrativePlanSlice()
        result = np.revoke_service("priestess", "no_such_service")
        assert result is False

    def test_revoke_one_of_many(self):
        np = NarrativePlanSlice()
        np.assign_service("priestess", _make_svc(service_id="heal"))
        np.assign_service("priestess", _make_svc(service_id="blessing"))
        np.revoke_service("priestess", "heal")
        svcs = np.get_services("priestess")
        assert len(svcs) == 1
        assert svcs[0]["service_id"] == "blessing"

    def test_prune_expired_removes_expired_entries(self):
        np = NarrativePlanSlice()
        # expiry_tick=5, current_tick=10 → expired
        np.assign_service("priestess", _make_svc(service_id="heal", expiry_tick=5))
        # expiry_tick=0 → never expires
        np.assign_service("priestess", _make_svc(service_id="blessing", expiry_tick=0))
        np._dirty = False  # reset to detect prune sets dirty
        pruned = np.prune_expired_services(current_tick=10)
        assert pruned == 1
        svcs = np.get_services("priestess")
        assert len(svcs) == 1
        assert svcs[0]["service_id"] == "blessing"
        assert np._dirty

    def test_prune_removes_npc_key_when_all_expired(self):
        np = NarrativePlanSlice()
        np.assign_service("priestess", _make_svc(expiry_tick=5))
        np.prune_expired_services(current_tick=10)
        assert "priestess" not in np.npc_services

    def test_prune_with_no_expired_returns_zero(self):
        np = NarrativePlanSlice()
        np.assign_service("priestess", _make_svc(expiry_tick=20))
        np._dirty = False
        pruned = np.prune_expired_services(current_tick=10)
        assert pruned == 0
        assert not np._dirty

    def test_prune_high_expiry_tick_still_active(self):
        np = NarrativePlanSlice()
        np.assign_service("priestess", _make_svc(expiry_tick=100))
        pruned = np.prune_expired_services(current_tick=99)
        assert pruned == 0
        assert len(np.get_services("priestess")) == 1


# ---------------------------------------------------------------------------
# TestNarrativePlanServicesStateChange
# ---------------------------------------------------------------------------

class TestNarrativePlanServicesStateChange:
    def test_assign_via_state_change(self):
        np = NarrativePlanSlice()
        svc = _make_svc()
        change = StateChange(
            slice="narrative_plan",
            path="npc_services.assign",
            value=svc,
            operation="set",
        )
        np.apply_state_change(change)
        svcs = np.get_services("priestess")
        assert len(svcs) == 1
        assert svcs[0]["service_id"] == "heal"

    def test_revoke_via_state_change(self):
        np = NarrativePlanSlice()
        np.assign_service("priestess", _make_svc())
        change = StateChange(
            slice="narrative_plan",
            path="npc_services.revoke",
            value={"npc_id": "priestess", "service_id": "heal"},
            operation="remove",
        )
        np.apply_state_change(change)
        assert np.get_services("priestess") == []

    def test_assign_state_change_replaces_same_id(self):
        np = NarrativePlanSlice()
        np.assign_service("priestess", _make_svc(notes="v1"))
        change = StateChange(
            slice="narrative_plan",
            path="npc_services.assign",
            value=_make_svc(notes="v2"),
            operation="set",
        )
        np.apply_state_change(change)
        svcs = np.get_services("priestess")
        assert len(svcs) == 1
        assert svcs[0]["notes"] == "v2"

    def test_revoke_nonexistent_does_not_raise(self):
        np = NarrativePlanSlice()
        change = StateChange(
            slice="narrative_plan",
            path="npc_services.revoke",
            value={"npc_id": "priestess", "service_id": "nonexistent"},
            operation="remove",
        )
        # Should not raise; returns silently
        np.apply_state_change(change)


# ---------------------------------------------------------------------------
# TestNarrativePlanServicesSnapshotRestore
# ---------------------------------------------------------------------------

class TestNarrativePlanServicesSnapshotRestore:
    def test_snapshot_includes_npc_services(self):
        np = NarrativePlanSlice()
        np.assign_service("priestess", _make_svc())
        snap = np.snapshot()
        assert "npc_services" in snap
        assert "priestess" in snap["npc_services"]
        assert len(snap["npc_services"]["priestess"]) == 1

    def test_snapshot_npc_services_defensive_copy(self):
        np = NarrativePlanSlice()
        np.assign_service("priestess", _make_svc())
        snap = np.snapshot()
        snap["npc_services"]["priestess"][0]["notes"] = "mutated"
        # Internal state should be unchanged
        assert np.npc_services["priestess"][0].get("notes", "") == ""

    def test_restore_round_trip_with_services(self):
        np = NarrativePlanSlice()
        np.assign_service("priestess", _make_svc(notes="healer", price=15))
        np.assign_service("tavern_keeper", _make_svc(service_id="food", label="食物", npc_id="tavern_keeper"))
        snap = np.snapshot()

        np2 = NarrativePlanSlice()
        np2.restore(snap)

        assert len(np2.get_services("priestess")) == 1
        assert np2.get_services("priestess")[0]["price"] == 15
        assert len(np2.get_services("tavern_keeper")) == 1
        assert np2.get_services("tavern_keeper")[0]["service_id"] == "food"

    def test_restore_with_missing_npc_services_defaults_to_empty(self):
        np = NarrativePlanSlice()
        snap = np.snapshot()
        snap.pop("npc_services", None)

        np2 = NarrativePlanSlice()
        np2.restore(snap)
        assert np2.npc_services == {}

    def test_restore_ignores_malformed_npc_services_entries(self):
        np = NarrativePlanSlice()
        snap = np.snapshot()
        snap["npc_services"] = {
            "priestess": ["not_a_dict", {"service_id": "heal", "npc_id": "priestess", "label": "治疗"}],
        }
        np2 = NarrativePlanSlice()
        np2.restore(snap)
        # Only the valid dict entry survives
        assert len(np2.get_services("priestess")) == 1
        assert np2.get_services("priestess")[0]["service_id"] == "heal"


# ---------------------------------------------------------------------------
# TestNarrativePlanValidateServices
# ---------------------------------------------------------------------------

class TestNarrativePlanValidateServices:
    def test_validate_clean_services_no_issues(self):
        np = NarrativePlanSlice()
        np.assign_service("priestess", _make_svc())
        issues = np.validate()
        assert issues == []

    def test_validate_detects_non_dict_npc_services(self):
        np = NarrativePlanSlice()
        # Directly corrupt the internal structure
        np.npc_services = "not_a_dict"  # type: ignore[assignment]
        issues = np.validate()
        assert any("npc_services must be a dict" in i for i in issues)

    def test_validate_detects_non_list_for_npc(self):
        np = NarrativePlanSlice()
        np.npc_services = {"priestess": "not_a_list"}  # type: ignore[dict-item]
        issues = np.validate()
        assert any("npc_services[priestess] must be a list" in i for i in issues)

    def test_validate_detects_non_dict_in_services_list(self):
        np = NarrativePlanSlice()
        np.npc_services = {"priestess": ["not_a_dict"]}  # type: ignore[list-item]
        issues = np.validate()
        assert any("npc_services[priestess][0] must be a dict" in i for i in issues)
