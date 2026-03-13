"""Tests for npc_presence_sources feature (P11 presence_source field)."""

from __future__ import annotations

from types import SimpleNamespace

from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import CompanionHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import PartySlice, PlayerSlice, RelationSlice, TimeSlice
from app.game_core.state.slices.area import AreaSlice, AreaState
from app.game_core.orchestration.presence import get_area_npc_sources
from app.scene_views import build_location_overview


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_slice() -> AreaSlice:
    s = AreaSlice()
    return s


# ---------------------------------------------------------------------------
# 1. move_npc default source = "resident"
# ---------------------------------------------------------------------------

def test_move_npc_default_source_is_resident():
    s = _make_slice()
    s.move_npc("npc_a", "area1", None)
    area = s.areas["area1"]
    assert area.npc_presence_sources.get("npc_a") == "resident"


# ---------------------------------------------------------------------------
# 2. move_npc with explicit source stores correctly
# ---------------------------------------------------------------------------

def test_move_npc_explicit_source():
    s = _make_slice()
    s.move_npc("npc_b", "area1", "tavern", source="companion")
    area = s.areas["area1"]
    assert area.npc_presence_sources.get("npc_b") == "companion"


def test_move_npc_schedule_source():
    s = _make_slice()
    s.move_npc("npc_c", "area2", None, source="schedule")
    assert s.areas["area2"].npc_presence_sources.get("npc_c") == "schedule"


def test_move_npc_planner_source():
    s = _make_slice()
    s.move_npc("npc_d", "area3", None, source="planner")
    assert s.areas["area3"].npc_presence_sources.get("npc_d") == "planner"


# ---------------------------------------------------------------------------
# 3. move_npc clears source from old area
# ---------------------------------------------------------------------------

def test_move_npc_clears_old_area_source():
    s = _make_slice()
    s.move_npc("npc_x", "area1", None, source="schedule")
    # Now move to a different area
    s.move_npc("npc_x", "area2", None, source="companion")

    # Old area must not contain npc_x's source
    old_area = s.areas.get("area1")
    assert old_area is not None
    assert "npc_x" not in old_area.npc_presence_sources

    # New area contains updated source
    assert s.areas["area2"].npc_presence_sources.get("npc_x") == "companion"


def test_move_npc_removes_from_old_npc_locations_too():
    s = _make_slice()
    s.move_npc("npc_y", "area1", "shop", source="resident")
    s.move_npc("npc_y", "area2", None, source="planner")

    old_area = s.areas["area1"]
    assert "npc_y" not in old_area.npc_locations
    assert "npc_y" not in old_area.npc_rooms
    assert "npc_y" not in old_area.npc_presence_sources


# ---------------------------------------------------------------------------
# 4. snapshot() serialises npc_presence_sources
# ---------------------------------------------------------------------------

def test_snapshot_includes_npc_presence_sources():
    s = _make_slice()
    s.move_npc("npc_a", "area1", None, source="companion")
    s.move_npc("npc_b", "area1", "inn", source="schedule")

    snap = s.snapshot()
    area_snap = snap["areas"]["area1"]
    assert "npc_presence_sources" in area_snap
    assert area_snap["npc_presence_sources"]["npc_a"] == "companion"
    assert area_snap["npc_presence_sources"]["npc_b"] == "schedule"


# ---------------------------------------------------------------------------
# 5. restore() round-trips npc_presence_sources
# ---------------------------------------------------------------------------

def test_restore_round_trips_npc_presence_sources():
    s = _make_slice()
    s.move_npc("npc_a", "area1", None, source="companion")
    snap = s.snapshot()

    s2 = AreaSlice()
    s2.restore(snap)
    area = s2.areas["area1"]
    assert area.npc_presence_sources.get("npc_a") == "companion"


# ---------------------------------------------------------------------------
# 6. Backward compat — old save without npc_presence_sources → empty dict
# ---------------------------------------------------------------------------

def test_restore_backward_compat_missing_presence_sources():
    """Old saves have no npc_presence_sources key — must not crash."""
    old_payload = {
        "areas": {
            "frontier_town": {
                "exploration": "discovered",
                "danger_level": 1.0,
                "npc_locations": {"npc_old": None},
                # npc_presence_sources key intentionally absent
            }
        }
    }
    s = AreaSlice()
    s.restore(old_payload)
    area = s.areas["frontier_town"]
    assert area.npc_presence_sources == {}
    # npc_locations still populated from old data
    assert "npc_old" in area.npc_locations


# ---------------------------------------------------------------------------
# 7. get_area_npc_sources returns correct mapping
# ---------------------------------------------------------------------------

class _FakeState:
    """Minimal state stub for get_area_npc_sources tests."""
    def __init__(self, slice_: AreaSlice) -> None:
        self.areas = slice_


def test_get_area_npc_sources_returns_correct_mapping():
    s = _make_slice()
    s.move_npc("npc1", "area1", None, source="schedule")
    s.move_npc("npc2", "area1", "inn", source="companion")

    state = _FakeState(s)
    sources = get_area_npc_sources(state, "area1")
    assert sources == {"npc1": "schedule", "npc2": "companion"}


def test_get_area_npc_sources_empty_for_unknown_area():
    s = _make_slice()
    state = _FakeState(s)
    sources = get_area_npc_sources(state, "nonexistent_area")
    assert sources == {}


def test_get_area_npc_sources_returns_defensive_copy():
    """Mutating the returned dict must not affect the slice."""
    s = _make_slice()
    s.move_npc("npc1", "area1", None, source="resident")
    state = _FakeState(s)
    sources = get_area_npc_sources(state, "area1")
    sources["npc1"] = "HACKED"
    # Slice is unchanged
    assert s.areas["area1"].npc_presence_sources["npc1"] == "resident"


# ---------------------------------------------------------------------------
# 8. CompanionManager.recruit uses source="companion"
# ---------------------------------------------------------------------------

class _FakeWorld:
    def has_registry(self, name: str) -> bool:
        return False


def _make_companion_state(area_slice: AreaSlice) -> StateContainer:
    state = StateContainer()
    state.register(area_slice)

    party = PartySlice()
    party.restore({"members": {}, "companion_approval": {}, "shared_experiences": []})
    state.register(party)

    relations = RelationSlice()
    relations.restore({
        "npc_dispositions": {"npc_recruit": {"approval": 10}},
        "relationship_stages": {"npc_recruit": "acquaintance"},
        "faction_standings": {},
        "npc_impressions": {},
        "shop_states": {},
    })
    state.register(relations)

    player = PlayerSlice()
    player.restore({"current_area": "frontier_town", "current_location": None})
    state.register(player)

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 8})
    state.register(time_slice)

    return state


class _FakeCharacterTemplate:
    def __init__(self) -> None:
        self.id = "npc_recruit"
        self.name = "Recruitable NPC"
        self.class_id = "warrior"
        self.tags = ["recruitable"]
        self.shop = None
        self.shop_inventory = None


class _FakeCharacterRegistry:
    def get(self, npc_id: str):
        if npc_id == "npc_recruit":
            return _FakeCharacterTemplate()
        return None


class _FakeWorldWithChar:
    def has_registry(self, name: str) -> bool:
        return name == "characters"

    @property
    def characters(self):
        return _FakeCharacterRegistry()


def test_companion_handler_recruit_uses_companion_source():
    area_slice = _make_slice()
    state = _make_companion_state(area_slice)
    world = _FakeWorldWithChar()
    engine = RulesEngine()
    engine.register(CompanionHandler())
    result = engine.execute(
        Command(type="recruit_companion", params={"npc_id": "npc_recruit"}),
        state,
        world,
    )
    assert result.delta is not None
    state.apply(result.delta)

    assert result.executed, f"recruit failed: {result.errors}"
    # The NPC should have been placed with source="companion"
    area = area_slice.areas.get("frontier_town")
    assert area is not None
    assert area.npc_presence_sources.get("npc_recruit") == "companion"


def test_companion_handler_recruit_inherits_player_room():
    area_slice = _make_slice()
    state = _make_companion_state(area_slice)
    state.player.current_location = "adventurer_guild"
    state.player.current_room = "guild_counter"
    world = _FakeWorldWithChar()
    engine = RulesEngine()
    engine.register(CompanionHandler())

    result = engine.execute(
        Command(type="recruit_companion", params={"npc_id": "npc_recruit"}),
        state,
        world,
    )
    assert result.delta is not None
    state.apply(result.delta)

    area = area_slice.areas.get("frontier_town")
    assert area is not None
    assert area.npc_locations.get("npc_recruit") == "adventurer_guild"
    assert area.npc_rooms.get("npc_recruit") == "guild_counter"


def test_build_location_overview_exposes_recruitable_flag():
    area_slice = _make_slice()
    area_slice.move_npc("npc_recruit", "frontier_town", None, source="resident")

    class _Player:
        current_area = "frontier_town"
        current_location = None

    class _Relations:
        npc_dispositions: dict = {}
        relationship_stages: dict = {}

    class _RecruitableTemplate:
        id = "npc_recruit"
        name = "Recruitable NPC"
        tags = ["recruitable", "friendly"]
        shop = None
        shop_inventory = None

    class _Characters:
        def get(self, npc_id: str):
            if npc_id == "npc_recruit":
                return _RecruitableTemplate()
            return None

        def list_all(self):
            return [_RecruitableTemplate()]

    class _World:
        def has_registry(self, name: str) -> bool:
            return name == "characters"

        @property
        def characters(self):
            return _Characters()

    runtime = SimpleNamespace(
        state=SimpleNamespace(
            player=_Player(),
            areas=area_slice,
            relations=_Relations(),
            has_slice=lambda name: name != "party",
        ),
        world=_World(),
    )
    session = SimpleNamespace(runtime=runtime)

    overview = build_location_overview(session)
    assert overview["present_npcs"][0]["character_id"] == "npc_recruit"
    assert overview["present_npcs"][0]["recruitable"] is True


def test_build_location_overview_exposes_current_room_room_flags_and_room_filtered_npcs():
    area_slice = _make_slice()
    area_slice.move_npc("npc_hall", "frontier_town", "guild", source="resident")
    area_slice.move_npc("npc_office", "frontier_town", "guild", source="resident")
    area_slice.set_npc_room("frontier_town", "npc_hall", "hall")
    area_slice.set_npc_room("frontier_town", "npc_office", "office")
    area_slice.mark_room_discovered("frontier_town", "guild", "office")

    class _Player:
        current_area = "frontier_town"
        current_location = "guild"
        current_room = "hall"

    class _Relations:
        npc_dispositions: dict = {}
        relationship_stages: dict = {}

    class _NpcTemplate:
        def __init__(self, npc_id: str, name: str) -> None:
            self.id = npc_id
            self.name = name
            self.tags: list[str] = []
            self.shop = None
            self.shop_inventory = None

    class _Characters:
        def __init__(self) -> None:
            self._templates = {
                "npc_hall": _NpcTemplate("npc_hall", "Hall NPC"),
                "npc_office": _NpcTemplate("npc_office", "Office NPC"),
            }

        def get(self, npc_id: str):
            return self._templates.get(npc_id)

        def list_all(self):
            return list(self._templates.values())

    room_hall = SimpleNamespace(name="Hall", discoverable=False)
    room_office = SimpleNamespace(name="Office", discoverable=True)
    room_vault = SimpleNamespace(name="Vault", discoverable=True)
    guild = SimpleNamespace(
        name="Guild",
        type="visit",
        interactables=[],
        rooms={
            "hall": room_hall,
            "office": room_office,
            "vault": room_vault,
        },
    )
    area_template = SimpleNamespace(
        name="Frontier Town",
        sub_locations={"guild": guild},
        connections=[],
    )

    class _Maps:
        def get(self, area_id: str):
            if area_id == "frontier_town":
                return area_template
            return None

    class _World:
        def has_registry(self, name: str) -> bool:
            return name in {"maps", "characters"}

        @property
        def maps(self):
            return _Maps()

        @property
        def characters(self):
            return _Characters()

    runtime = SimpleNamespace(
        state=SimpleNamespace(
            player=_Player(),
            areas=area_slice,
            relations=_Relations(),
            has_slice=lambda name: False,
        ),
        world=_World(),
    )
    session = SimpleNamespace(runtime=runtime)

    overview = build_location_overview(session)
    assert overview["current_room"] == "hall"
    assert [npc["character_id"] for npc in overview["present_npcs"]] == ["npc_hall"]

    rooms = {room["id"]: room for room in overview["rooms"]}
    assert rooms["hall"] == {
        "id": "hall",
        "name": "Hall",
        "discoverable": False,
        "discovered": True,
    }
    assert rooms["office"] == {
        "id": "office",
        "name": "Office",
        "discoverable": True,
        "discovered": True,
    }
    assert rooms["vault"] == {
        "id": "vault",
        "name": "Vault",
        "discoverable": True,
        "discovered": False,
    }


def test_build_location_overview_isolates_room_interactables_and_surfaces_dynamic_rooms():
    area_slice = _make_slice()
    area_slice.restore({"areas": {"frontier_town": {}}})
    area_slice.mark_discovery("frontier_town", "hall_chest")
    area_slice.init_container(
        "frontier_town",
        "hall_chest",
        {
            "area_id": "frontier_town",
            "location_id": "guild",
            "room_id": "hall",
            "lock_status": "unlocked",
            "trap_detected": True,
        },
    )
    area_slice.add_dynamic_room(
        "frontier_town",
        {
            "sub_loc_id": "guild",
            "room_id": "archive",
            "name": "Archive",
            "discoverable": False,
            "source": "narrative_planner",
        },
    )

    class _Player:
        current_area = "frontier_town"
        current_location = "guild"
        current_room = "hall"

    class _Relations:
        npc_dispositions: dict = {}
        relationship_stages: dict = {}

    hall = SimpleNamespace(
        name="Hall",
        discoverable=False,
        interactables=[
            SimpleNamespace(
                id="hall_chest",
                name="Hall Chest",
                description="A chest beside the wall.",
                visibility_dc=None,
                checks=[],
                tags=["container"],
                container_data=SimpleNamespace(locked=False, trap=SimpleNamespace(detect_dc=10)),
            ),
            SimpleNamespace(
                id="secret_switch",
                name="Secret Switch",
                description="Hidden in the molding.",
                visibility_dc=12,
                checks=[],
                tags=["hidden"],
                container_data=None,
            ),
        ],
    )
    guild = SimpleNamespace(
        name="Guild",
        type="visit",
        interactables=[
            SimpleNamespace(
                id="lobby_statue",
                name="Lobby Statue",
                description="A statue in the lobby.",
                visibility_dc=None,
                checks=[],
                tags=[],
                container_data=None,
            )
        ],
        rooms={"hall": hall},
    )
    area_template = SimpleNamespace(
        name="Frontier Town",
        sub_locations={"guild": guild},
        connections=[],
    )

    class _Maps:
        def get(self, area_id: str):
            return area_template if area_id == "frontier_town" else None

        def get_sub_location(self, area_id: str, loc_id: str):
            if area_id == "frontier_town" and loc_id == "guild":
                return guild
            return None

    class _World:
        def has_registry(self, name: str) -> bool:
            return name == "maps"

        @property
        def maps(self):
            return _Maps()

    runtime = SimpleNamespace(
        state=SimpleNamespace(
            player=_Player(),
            areas=area_slice,
            relations=_Relations(),
            has_slice=lambda name: False,
        ),
        world=_World(),
    )
    session = SimpleNamespace(runtime=runtime)

    overview = build_location_overview(session)

    assert overview["room_name"] == "Hall"
    ids = [item["id"] for item in overview["interactables"]]
    assert ids == ["hall_chest"]
    assert overview["interactables"][0]["trapped_hint"] is True
    rooms = {room["id"]: room for room in overview["rooms"]}
    assert rooms["archive"]["dynamic"] is True
    assert rooms["archive"]["source"] == "narrative_planner"


# ---------------------------------------------------------------------------
# 9. CompanionManager.sync_to_player uses source="companion"
# ---------------------------------------------------------------------------

def test_companion_manager_sync_to_player_uses_companion_source():
    from app.game_core.orchestration.companion_manager import CompanionManager

    area_slice = _make_slice()
    # Pre-place member in a different area with a different source
    area_slice.move_npc("npc_m", "old_area", None, source="resident")

    state = _make_companion_state(area_slice)
    state.party.members["npc_m"] = {"name": "Member"}

    manager = CompanionManager(state)
    moved = manager.sync_to_player()

    assert "npc_m" in moved
    area = area_slice.areas.get("frontier_town")
    assert area is not None
    assert area.npc_presence_sources.get("npc_m") == "companion"
