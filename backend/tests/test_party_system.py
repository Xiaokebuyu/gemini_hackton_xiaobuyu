from app.models.party import PartyMember
from app.services.admin.admin_coordinator import AdminCoordinator


def test_determine_teammate_perspective():
    coordinator = AdminCoordinator.__new__(AdminCoordinator)
    member = PartyMember(character_id="alice", name="Alice")

    event = {
        "participants": ["alice"],
        "witnesses": ["bob"],
        "visibility": "party",
    }
    assert coordinator._determine_teammate_perspective(member, event) == "participant"

    event = {
        "participants": ["player"],
        "witnesses": ["alice"],
        "visibility": "party",
    }
    assert coordinator._determine_teammate_perspective(member, event) == "witness"

    event = {
        "participants": ["player"],
        "witnesses": [],
        "visibility": "party",
    }
    assert coordinator._determine_teammate_perspective(member, event) == "bystander"
