from __future__ import annotations

import asyncio
import copy
import json
from concurrent.futures import ThreadPoolExecutor
import threading
import time

from fastapi.testclient import TestClient
import pytest

import app.main as api_main
from app.agent_orchestration import OpeningSequence
from app.deps import get_admin_coordinator
from app.game_core import GameRuntime
from app.game_core.adapters import NullPersistencePort, SaveStore
from app.game_core.orchestration.models import SSEEvent
from app.game_core.state import StateChange
from app.world_seed import _shell_world_seed


def _runtime(agent_orchestration=None) -> GameRuntime:
    api_main.app.state.interaction_service = None
    runtime = GameRuntime(
        save_store=SaveStore(NullPersistencePort()),
        agent_orchestration=agent_orchestration,
    )
    world_data = copy.deepcopy(_shell_world_seed("goblin_slayer"))
    world_data["maps"]["guild_hall"]["connections"] = [
        {"target": "training_grounds", "travel_slots": 1, "blocked": False},
        {"target": "frontier", "travel_slots": 1, "blocked": False},
    ]
    world_data["maps"]["training_grounds"]["connections"] = [
        {"target": "guild_hall", "travel_slots": 1, "blocked": False},
    ]
    world_data["maps"]["frontier"]["connections"] = [
        {"target": "guild_hall", "travel_slots": 1, "blocked": False},
    ]
    runtime.get_world("goblin_slayer", world_data=world_data)
    return runtime


def _create_session(client: TestClient) -> str:
    response = client.post("/api/game/goblin_slayer/sessions")
    assert response.status_code == 201
    payload = response.json()
    return str(payload["session_id"])


def _character_payload(**overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "调试者",
        "race": "human",
        "character_class": "fighter",
        "background": "adventurer",
        "ability_scores": {
            "str": 15,
            "dex": 12,
            "con": 14,
            "int": 10,
            "wis": 10,
            "cha": 8,
        },
        "skill_proficiencies": ["athletics", "survival"],
        "backstory": "用于 API 骨架联调",
    }
    payload.update(overrides)
    return payload


def _create_character(client: TestClient, session_id: str) -> dict[str, object]:
    response = client.post(
        f"/api/game/goblin_slayer/sessions/{session_id}/character",
        json=_character_payload(),
    )
    assert response.status_code == 200
    session = asyncio.run(_load_session(session_id))
    assert session is not None
    session.runtime.state.areas.update_npc_location("merchant", "counter")
    asyncio.run(_save_session(session))
    return response.json()


def _clear_opening_bootstrap(runtime: GameRuntime, session_id: str) -> None:
    del runtime
    session = asyncio.run(_load_session(session_id))
    assert session is not None
    session.runtime.state.quests.dynamic_quests = {}
    session.runtime.state.quests._dirty = True
    session.runtime.state.narrative_plan.npc_directives = []
    session.runtime.state.narrative_plan.quest_history = []
    session.runtime.state.narrative_plan.strategy_notes = ""
    session.runtime.state.narrative_plan._dirty = True
    if session.runtime.state.has_slice("areas"):
        for area_state in session.runtime.state.areas.areas.values():
            area_state.board_bulletins = {}
        session.runtime.state.areas._dirty = True
    asyncio.run(_save_session(session))


def _latest_board_title(session) -> str | None:
    player_area = session.runtime.state.player.current_area
    if not player_area:
        return None
    if not session.runtime.state.has_slice("areas"):
        return None
    area_state = session.runtime.state.areas.areas.get(player_area)
    if area_state is None:
        return None
    all_bulletins: list[dict] = []
    for entries in area_state.board_bulletins.values():
        for bulletin in entries:
            if isinstance(bulletin, dict):
                all_bulletins.append(bulletin)
    if not all_bulletins:
        return None
    latest = all_bulletins[-1]
    return str(latest.get("title", "") or "").strip() or None


async def _load_session(session_id: str):
    return await get_admin_coordinator().get_session("goblin_slayer", session_id)


async def _save_session(session) -> None:
    await get_admin_coordinator().save_session(session)


def _event_payloads(stream_text: str, event_type: str) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    lines = stream_text.splitlines()
    for index, line in enumerate(lines):
        if line != f"event: {event_type}" or index + 1 >= len(lines):
            continue
        data_line = lines[index + 1]
        if not data_line.startswith("data: "):
            continue
        payloads.append(json.loads(data_line[6:]))
    return payloads


def test_health_and_world_catalog_reflect_real_session_count(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        created = client.post("/api/game/goblin_slayer/sessions")
        health = client.get("/health")
        worlds = client.get("/api/game/worlds")

    assert created.status_code == 201
    assert created.json()["phase"] == "character_creation"
    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "service": "game_core_api",
        "mode": "api_shell",
    }
    assert worlds.status_code == 200
    assert worlds.json()[0]["player_count"] == 1


def test_session_create_resume_list_and_delete_flow(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        listed = client.get("/api/game/goblin_slayer/sessions")
        resumed = client.post(f"/api/game/goblin_slayer/sessions/{session_id}/resume")
        missing_resume = client.post(
            "/api/game/goblin_slayer/sessions/sess_missing/resume"
        )
        deleted = client.delete(f"/api/game/goblin_slayer/sessions/{session_id}")
        missing_session = client.delete(f"/api/game/goblin_slayer/sessions/{session_id}")
        invalid_world = client.get("/api/game/.hidden/sessions")
        missing_world = client.get("/api/game/unknown_world/sessions")

    assert listed.status_code == 200
    assert listed.json()[0]["session_id"] == session_id
    assert resumed.status_code == 200
    resumed_payload = resumed.json()
    assert resumed_payload["world_id"] == "goblin_slayer"
    assert resumed_payload["session_id"] == session_id
    assert resumed_payload["phase"] == "character_creation"
    assert resumed_payload["resume_narration"]
    assert resumed_payload["scene"]["area_id"] == ""
    assert resumed_payload["location_visual"]["background_key"] == ""
    assert resumed_payload["player"]["character_name"] == ""
    assert "day" in resumed_payload["player"]
    assert "slot" in resumed_payload["player"]
    assert "period" in resumed_payload["player"]
    assert missing_resume.status_code == 404
    assert missing_resume.json()["detail"]["code"] == "session_not_found"
    assert deleted.status_code == 204
    assert missing_session.status_code == 404
    assert missing_session.json()["detail"]["code"] == "session_not_found"
    assert invalid_world.status_code == 400
    assert invalid_world.json()["detail"]["code"] == "invalid_world_id"
    assert missing_world.status_code == 404
    assert missing_world.json()["detail"]["code"] == "world_not_found"


def test_character_creation_options_and_character_flow(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        options = client.get("/api/game/goblin_slayer/character-creation/options")
        before_panel = client.get(
            f"/api/game/goblin_slayer/sessions/{session_id}/character"
        )
        created = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/character",
            json=_character_payload(),
        )
        invalid = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/character",
            json=_character_payload(character_class="wizard"),
        )

    assert options.status_code == 200
    option_payload = options.json()
    assert any(item["id"] == "human" for item in option_payload["races"])
    assert any(item["id"] == "fighter" for item in option_payload["classes"])
    assert any(item["id"] == "adventurer" for item in option_payload["backgrounds"])
    assert before_panel.status_code == 200
    assert before_panel.json()["phase"] == "character_creation"
    assert created.status_code == 200
    created_payload = created.json()
    assert created_payload["phase"] == "opening_ready"
    assert created_payload["player"]["character_name"] == "调试者"
    assert created_payload["player"]["character_class"] == "fighter"
    assert created_payload["player"]["current_area"] == "guild_hall"
    assert "day" in created_payload["player"]
    assert "slot" in created_payload["player"]
    assert "period" in created_payload["player"]
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["code"] == "invalid_character_creation"


def test_inventory_map_and_quest_panels_after_character_creation(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        inventory = client.get(f"/api/game/goblin_slayer/sessions/{session_id}/inventory")
        map_panel = client.get(f"/api/game/goblin_slayer/sessions/{session_id}/map")
        quests = client.get(f"/api/game/goblin_slayer/sessions/{session_id}/quests")

    assert inventory.status_code == 200
    inventory_payload = inventory.json()
    assert {item["item_id"] for item in inventory_payload["inventory"]} == {
        "training_sword",
        "wooden_shield",
    }
    assert inventory_payload["equipment"]["main_hand"]["item_id"] == "training_sword"
    assert map_panel.status_code == 200
    map_payload = map_panel.json()
    assert map_payload["current_area"] == "guild_hall"
    assert "guild_hall" in map_payload["discovered_area_ids"]
    assert any(area["id"] == "training_grounds" for area in map_payload["areas"])
    assert quests.status_code == 200
    quest_payload = quests.json()
    assert "report_in" in quest_payload["milestone_states"]
    assert "dq_report_in" in quest_payload["dynamic_quests"]
    intro_quest = quest_payload["dynamic_quests"]["dq_report_in"]
    assert intro_quest["ui_state"] == "available"
    assert intro_quest["badge"] == {"key": "available", "label": "可接取"}

    session = asyncio.run(_load_session(session_id))
    assert session is not None
    assert session.runtime.state.narrative_plan.last_run_tick == 0
    assert _latest_board_title(session) == "New Lead Posted"
    assert session.runtime.state.time.accumulated == 0.0


def test_quest_panel_returns_dynamic_quests_in_stable_order(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        _clear_opening_bootstrap(runtime, session_id)

        session = asyncio.run(_load_session(session_id))
        assert session is not None
        session.runtime.state.quests.dynamic_quests = {
            "dq_unknown": {"title": "Unknown"},
            "dq_retired": {"status": "retired", "title": "Retired", "created_at_tick": 7},
            "dq_available": {"status": "available", "title": "Available", "created_at_tick": 4},
            "dq_failed": {"status": "failed", "title": "Failed", "created_at_tick": 8},
            "dq_completed_done": {
                "status": "completed",
                "title": "Reported",
                "requires_report": True,
                "reported": True,
                "created_at_tick": 5,
            },
            "dq_report_in": {
                "status": "completed",
                "title": "Report In",
                "requires_report": True,
                "created_at_tick": 6,
            },
            "dq_active": {"status": "active", "title": "Active", "created_at_tick": 2},
            "dq_expired": {"status": "expired", "title": "Expired", "created_at_tick": 3},
        }
        session.runtime.state.quests._dirty = True
        asyncio.run(_save_session(session))
        quests = client.get(f"/api/game/goblin_slayer/sessions/{session_id}/quests")

    assert quests.status_code == 200
    assert list(quests.json()["dynamic_quests"]) == [
        "dq_active",
        "dq_report_in",
        "dq_available",
        "dq_completed_done",
        "dq_failed",
        "dq_retired",
        "dq_expired",
        "dq_unknown",
    ]


def test_opening_stream_bootstraps_legacy_session_and_mentions_seeded_quest(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)

        session = asyncio.run(_load_session(session_id))
        assert session is not None
        session.runtime.state.quests.dynamic_quests = {}
        session.runtime.state.quests._dirty = True
        session.runtime.state.narrative_plan.npc_directives = []
        session.runtime.state.narrative_plan._dirty = True
        asyncio.run(_save_session(session))

        opening = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/opening/stream",
        )

    assert opening.status_code == 200
    events = _parse_sse(opening)
    comment = _sse_event(events, "gm_comment")
    assert comment is not None
    assert "Lead: Report In" in comment["content"]
    assert "report_in" not in comment["content"]

    session = asyncio.run(_load_session(session_id))
    assert session is not None
    assert "dq_report_in" in session.runtime.state.quests.dynamic_quests
    assert _latest_board_title(session) == "New Lead Posted"
    assert session.phase == "active"


def test_opening_stream_surfaces_bootstrap_hook_error(monkeypatch) -> None:
    runtime = _runtime()

    async def _failing_bootstrap(session, *, persist=False):
        del session, persist
        return [
            SSEEvent(
                "hook_error",
                {
                    "hook": "narrative_planner_bootstrap",
                    "error_type": "RuntimeError",
                    "message": "bootstrap failed",
                },
            )
        ]

    monkeypatch.setattr(runtime, "bootstrap_opening_planner", _failing_bootstrap)
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        opening = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/opening/stream",
        )

    assert opening.status_code == 200
    events = _parse_sse(opening)
    assert events[0]["event"] == "hook_error"
    assert events[0]["data"]["hook"] == "narrative_planner_bootstrap"
    assert _sse_event(events, "scene_change") is not None
    assert _sse_event(events, "stream_end") is not None


def test_opening_stream_prefers_agent_generated_opening_sequence(monkeypatch) -> None:
    class FakeAgentOrchestration:
        async def run_post_action_round(self, shared, result, apply_delta, event_sink=None):
            del shared, result, apply_delta, event_sink
            return []

        async def generate_opening_sequence(self, session):
            del session
            return OpeningSequence(
                narration_event=SSEEvent("gm_narration", {"content": "Agent opening narration."}),
                comment_event=SSEEvent("gm_comment", {"content": "Agent opening comment.", "tone": "grim"}),
                dialogue_options_event=SSEEvent(
                    "dialogue_options",
                    {
                        "options": [
                            {
                                "id": "agent-look",
                                "text": "观察四周",
                                "label": "观察四周",
                                "dispatch": {
                                    "kind": "input",
                                    "payload": {"text": "观察四周"},
                                },
                            }
                        ],
                    },
                ),
            )

    runtime = _runtime(agent_orchestration=FakeAgentOrchestration())
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        opening = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/opening/stream",
        )

    assert opening.status_code == 200
    events = _parse_sse(opening)
    event_names = [event["event"] for event in events]
    assert event_names[:3] == [
        "scene_change",
        "gm_narration",
        "gm_comment",
    ]
    assert "character_enter" in event_names[3:]
    assert "status_update" in event_names[3:]
    assert "dialogue_options" in event_names[3:]
    assert event_names.index("status_update") < event_names.index("dialogue_options")
    assert _sse_event(events, "gm_narration")["content"] == "Agent opening narration."
    assert _sse_event(events, "gm_comment")["content"] == "Agent opening comment."
    assert _sse_event(events, "dialogue_options")["options"][0]["id"] == "agent-look"


def test_opening_stream_rejects_active_session_replay(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        first_opening = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/opening/stream",
        )
        replay = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/opening/stream",
        )

    assert first_opening.status_code == 200
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "opening_not_available"


def test_opening_stream_stale_queued_request_fails_inside_lock(monkeypatch) -> None:
    class SlowAgentOrchestration:
        def __init__(self) -> None:
            self.started = threading.Event()

        async def run_post_action_round(self, shared, result, apply_delta, event_sink=None):
            del shared, result, apply_delta, event_sink
            return []

        async def generate_opening_sequence(self, session):
            del session
            self.started.set()
            await asyncio.sleep(0.05)
            return OpeningSequence(
                narration_event=SSEEvent("gm_narration", {"content": "Slow opening narration."}),
                comment_event=None,
                dialogue_options_event=SSEEvent(
                    "dialogue_options",
                    {"options": [{"id": "look", "text": "观察四周", "label": "观察四周"}]},
                ),
            )

    agent = SlowAgentOrchestration()
    runtime = _runtime(agent_orchestration=agent)
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)

        def _post_opening():
            return client.post(f"/api/game/goblin_slayer/sessions/{session_id}/opening/stream")

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_first = executor.submit(_post_opening)
            assert agent.started.wait(timeout=1.0)
            time.sleep(0.01)
            replay = client.post(f"/api/game/goblin_slayer/sessions/{session_id}/opening/stream")
            first = future_first.result(timeout=1.0)

    assert first.status_code == 200
    assert _sse_event(_parse_sse(first), "scene_change") is not None
    assert replay.status_code == 200
    replay_events = _parse_sse(replay)
    assert replay_events[0]["event"] == "stream_error"
    assert replay_events[0]["data"]["code"] == "opening_not_available"
    assert replay_events[-1]["event"] == "stream_end"


def test_resume_after_character_creation_returns_full_restore_payload(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        resumed = client.post(f"/api/game/goblin_slayer/sessions/{session_id}/resume")

    assert resumed.status_code == 200
    payload = resumed.json()
    assert payload["phase"] == "opening_ready"
    assert payload["player"]["character_name"] == "调试者"
    assert payload["scene"]["area_id"] == "guild_hall"
    assert payload["location_visual"]["background_key"] == "guild_hall/counter"
    assert "merchant" in payload["location_visual"]["present_character_ids"]
    assert payload["resume_narration"]
    assert "report_in" not in payload["resume_narration"]


def test_world_bootstrap_validates_before_caching() -> None:
    runtime = GameRuntime(save_store=SaveStore(NullPersistencePort()))
    world_data = copy.deepcopy(_shell_world_seed("goblin_slayer"))
    world_data["characters"]["merchant"]["current_area"] = "missing_area"

    with pytest.raises(ValueError, match="world validation failed"):
        runtime.get_world("goblin_slayer", world_data=world_data)


def _parse_sse(response) -> list[dict]:
    """Parse a text/event-stream response into a list of {event, data} dicts."""
    events = []
    current: dict[str, object] = {}
    for line in response.text.split("\n"):
        if line.startswith("event: "):
            current["event"] = line[7:].strip()
        elif line.startswith("data: "):
            raw = line[6:].strip()
            try:
                current["data"] = json.loads(raw)
            except json.JSONDecodeError:
                current["data"] = raw
        elif line == "" and current:
            events.append(current)
            current = {}
    return events


def _sse_event(events: list[dict], event_type: str) -> dict | None:
    """Return the first event of a given type from a parsed SSE list."""
    return next((e["data"] for e in events if e.get("event") == event_type), None)


def _action_result_payload(response) -> dict:
    payload = _sse_event(_parse_sse(response), "action_result")
    assert payload is not None
    return payload


def test_navigate_executes_real_runtime_and_maps_failures(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        moved = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/navigate",
            json={"action": "move_area", "area_id": "training_grounds"},
        )
        entered = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/navigate",
            json={"action": "enter_sub_location", "location_id": "yard"},
        )
        left = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/navigate",
            json={"action": "leave_sub_location"},
        )
        invalid_action = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/navigate",
            json={"action": "jump"},
        )
        rejected = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/navigate",
            json={"action": "move_area", "area_id": "missing"},
        )

    # Successful navigate → SSE stream with action_result + scene_change + location_overview
    assert moved.status_code == 200
    moved_events = _parse_sse(moved)
    moved_result = _sse_event(moved_events, "action_result")
    assert moved_result is not None
    assert moved_result["executed"] is True
    assert _sse_event(moved_events, "scene_change") is not None
    moved_overview = _sse_event(moved_events, "location_overview")
    assert moved_overview is not None
    assert moved_overview["area_id"] == "training_grounds"

    assert entered.status_code == 200
    entered_overview = _sse_event(_parse_sse(entered), "location_overview")
    assert entered_overview is not None
    assert entered_overview["location_id"] == "yard"

    assert left.status_code == 200
    left_overview = _sse_event(_parse_sse(left), "location_overview")
    assert left_overview is not None
    assert left_overview["location_id"] is None

    # invalid_action: parameter validation before SSE → HTTP 400
    assert invalid_action.status_code == 400
    assert invalid_action.json()["detail"]["code"] == "invalid_navigation_request"

    # rejected: execution failure inside SSE → action_result.executed=False
    assert rejected.status_code == 200
    rejected_result = _sse_event(_parse_sse(rejected), "action_result")
    assert rejected_result is not None
    assert rejected_result["executed"] is False


def test_companion_routes_run_command_round_trip(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        session = asyncio.run(_load_session(session_id))
        assert session is not None
        merchant = session.runtime.world.characters.get("merchant")
        assert merchant is not None
        if "recruitable" not in merchant.tags:
            merchant.tags.append("recruitable")
        session.runtime.state.relations.relationship_stages["merchant"] = "acquaintance"
        session.runtime.state.relations.npc_dispositions["merchant"] = {
            "approval": 20,
            "trust": 10,
            "fear": 0,
            "romance": 0,
        }
        session.runtime.state.relations._dirty = True
        initial_accumulated = session.runtime.state.time.accumulated
        asyncio.run(_save_session(session))

        recruited = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/companion/recruit",
            json={"npc_id": "merchant"},
        )
        dismissed = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/companion/dismiss",
            json={"npc_id": "merchant"},
        )

    assert recruited.status_code == 200
    recruited_events = _parse_sse(recruited)
    recruited_names = [evt["event"] for evt in recruited_events]
    assert recruited_names.index("action_result") < recruited_names.index("companion_recruited")
    recruit_action = _sse_event(recruited_events, "action_result")
    assert recruit_action is not None
    assert recruit_action["executed"] is True
    assert recruit_action["action_type"] == "recruit_companion"
    assert recruit_action["time_cost"] == 0.0
    recruit_payload = _sse_event(recruited_events, "companion_recruited")
    assert recruit_payload is not None
    assert recruit_payload["npc_id"] == "merchant"
    assert "merchant" in recruit_payload["party_members"]
    recruited_overview = _sse_event(recruited_events, "location_overview")
    assert recruited_overview is not None
    assert any(
        item.get("character_id") == "merchant" and item.get("is_companion") is True
        for item in recruited_overview["present_npcs"]
    )

    assert dismissed.status_code == 200
    dismissed_events = _parse_sse(dismissed)
    dismissed_names = [evt["event"] for evt in dismissed_events]
    assert dismissed_names.index("action_result") < dismissed_names.index("companion_dismissed")
    dismiss_action = _sse_event(dismissed_events, "action_result")
    assert dismiss_action is not None
    assert dismiss_action["executed"] is True
    assert dismiss_action["action_type"] == "dismiss_companion"
    assert dismiss_action["time_cost"] == 0.0
    dismiss_payload = _sse_event(dismissed_events, "companion_dismissed")
    assert dismiss_payload is not None
    assert dismiss_payload["npc_id"] == "merchant"
    assert "merchant" not in dismiss_payload["party_members"]

    session = asyncio.run(_load_session(session_id))
    assert session is not None
    assert "merchant" not in session.runtime.state.party.members
    assert session.runtime.state.time.accumulated == initial_accumulated


def test_action_stream_executes_real_structured_actions(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        map_panel = client.get(f"/api/game/goblin_slayer/sessions/{session_id}/map")
        assert map_panel.status_code == 200
        map_payload = map_panel.json()
        target_area = next(
            area["id"]
            for area in map_payload["areas"]
            if area["id"] != map_payload["current_area"]
        )
        streamed = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/action/stream",
            json={"action_type": "move_area", "params": {"area_id": target_area}},
        )
        rejected = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/action/stream",
            json={"action_type": "move_area", "params": {"area_id": "missing"}},
        )
        invalid_request = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/action/stream",
            json={"action_type": "", "params": {}},
        )
        unknown_action = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/action/stream",
            json={"action_type": "dance", "params": {}},
        )

    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert "event: action_result" in streamed.text
    assert "event: stream_end" in streamed.text
    assert '"executed":true' in streamed.text
    assert '"action_type":"move_area"' in streamed.text
    assert rejected.status_code == 200
    assert '"executed":false' in rejected.text
    assert "unknown area: missing" in rejected.text
    assert invalid_request.status_code == 400
    assert invalid_request.json()["detail"]["code"] == "invalid_action_request"
    assert unknown_action.status_code == 400
    assert unknown_action.json()["detail"]["code"] == "unknown_action"


def test_action_stream_emits_dice_roll_for_skill_checks(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)
    monkeypatch.setattr("app.game_core.rules.handler_utils.roll_d20", lambda: 10)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        streamed = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/action/stream",
            json={"action_type": "skill_check", "params": {"skill": "athletics", "dc": 13}},
        )

    assert streamed.status_code == 200
    events = _parse_sse(streamed)
    dice_roll = _sse_event(events, "dice_roll")
    assert dice_roll is not None
    assert dice_roll["skill"] == "athletics"
    assert dice_roll["result"] == 10
    assert dice_roll["modifier"] == 5
    assert dice_roll["total"] == 15
    assert dice_roll["dc"] == 13
    assert dice_roll["passed"] is True
    assert dice_roll["passed"] is True

    action_result = _sse_event(events, "action_result")
    assert action_result is not None
    assert action_result["executed"] is True
    assert action_result["outcome"] == {
        "category": "check",
        "passed": True,
        "margin": 2,
    }
    assert action_result["rolls"][0]["purpose"] == "skill_check"
    assert action_result["rolls"][0]["dice"] == "1d20"

    event_names = [event.get("event") for event in events]
    assert event_names.index("dice_roll") < event_names.index("action_result")


def test_action_stream_failed_skill_check_uses_executed_plus_outcome(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)
    monkeypatch.setattr("app.game_core.rules.handler_utils.roll_d20", lambda: 5)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        streamed = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/action/stream",
            json={"action_type": "skill_check", "params": {"skill": "athletics", "dc": 13}},
        )

    assert streamed.status_code == 200
    events = _parse_sse(streamed)
    dice_roll = _sse_event(events, "dice_roll")
    assert dice_roll is not None
    assert dice_roll["passed"] is False
    assert dice_roll["passed"] is False

    action_result = _sse_event(events, "action_result")
    assert action_result is not None
    assert action_result["executed"] is True
    assert action_result["executed"] is True
    assert action_result["outcome"] == {
        "category": "check",
        "passed": False,
        "margin": -3,
    }


def test_action_stream_summarizes_before_agent_reactions(monkeypatch) -> None:
    class FakeAgentOrchestration:
        async def run_post_action_round(self, shared, result, apply_delta, event_sink=None):
            del shared, result, apply_delta
            events = [
                SSEEvent("gm_narration", {"content": "The moment hangs in the air."}),
                SSEEvent("npc_response", {"npc_id": "merchant", "content": "Hmm.", "type": "speech"}),
            ]
            if event_sink is not None:
                for event in events:
                    await event_sink(event)
            return events

    runtime = _runtime(agent_orchestration=FakeAgentOrchestration())
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)
    monkeypatch.setattr("app.game_core.rules.handler_utils.roll_d20", lambda: 10)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        streamed = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/action/stream",
            json={"action_type": "skill_check", "params": {"skill": "athletics", "dc": 13}},
        )

    assert streamed.status_code == 200
    event_names = [event.get("event") for event in _parse_sse(streamed)]
    assert event_names.index("dice_roll") < event_names.index("action_result")
    assert event_names.index("action_result") < event_names.index("gm_narration")
    assert event_names.index("action_result") < event_names.index("npc_response")


def test_action_stream_defers_dialogue_options_until_after_reactions(monkeypatch) -> None:
    class FakeAgentOrchestration:
        async def run_post_action_round(self, shared, result, apply_delta, event_sink=None):
            del shared, result, apply_delta, event_sink
            return [
                SSEEvent(
                    "dialogue_options",
                    {
                        "npc_id": "merchant",
                        "options": [{"text": "继续交谈", "intent": "talk"}],
                    },
                ),
                SSEEvent("gm_narration", {"content": "The moment hardens into a choice."}),
                SSEEvent("npc_response", {"npc_id": "merchant", "content": "Well?", "type": "speech"}),
            ]

    runtime = _runtime(agent_orchestration=FakeAgentOrchestration())
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)
    monkeypatch.setattr("app.game_core.rules.handler_utils.roll_d20", lambda: 10)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        streamed = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/action/stream",
            json={"action_type": "skill_check", "params": {"skill": "athletics", "dc": 13}},
        )

    assert streamed.status_code == 200
    event_names = [event.get("event") for event in _parse_sse(streamed)]
    assert event_names.index("action_result") < event_names.index("gm_narration")
    assert event_names.index("gm_narration") < event_names.index("dialogue_options")
    assert event_names.index("npc_response") < event_names.index("dialogue_options")


def test_action_stream_keeps_summary_ahead_of_forced_dialogue_npc_reactions(monkeypatch) -> None:
    class FakeAgentOrchestration:
        async def run_post_action_round(self, shared, result, apply_delta, event_sink=None):
            del shared, apply_delta
            assert result.metadata["action_context"]["dialogue_npc_id"] == "merchant_tom"
            events = [
                SSEEvent(
                    "npc_response",
                    {
                        "npc_id": "merchant_tom",
                        "content": "让我看看你的手气。",
                        "type": "speech",
                    },
                ),
                SSEEvent(
                    "dialogue_options",
                    {
                        "npc_id": "merchant_tom",
                        "options": [{"text": "继续交谈", "intent": "talk"}],
                    },
                ),
            ]
            if event_sink is not None:
                await event_sink(events[0])
                await asyncio.sleep(0.01)
                await event_sink(events[1])
            return events

    runtime = _runtime(agent_orchestration=FakeAgentOrchestration())
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)
    monkeypatch.setattr("app.game_core.rules.handler_utils.roll_d20", lambda: 10)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        streamed = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/action/stream",
            json={
                "action_type": "skill_check",
                "params": {"skill": "athletics", "dc": 13},
                "context": {"dialogue_npc_id": "merchant_tom"},
            },
        )

    assert streamed.status_code == 200
    event_names = [event.get("event") for event in _parse_sse(streamed)]
    assert event_names.index("dice_roll") < event_names.index("action_result")
    assert event_names.index("action_result") < event_names.index("npc_response")
    assert event_names.index("npc_response") < event_names.index("dialogue_options")


def test_input_stream_parses_text_commands(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        map_panel = client.get(f"/api/game/goblin_slayer/sessions/{session_id}/map")
        assert map_panel.status_code == 200
        map_payload = map_panel.json()
        target_area = next(
            area["id"]
            for area in map_payload["areas"]
            if area["id"] != map_payload["current_area"]
        )
        parsed = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/input/stream",
            json={"text": f"move {target_area}"},
        )
        runtime_rejected = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/input/stream",
            json={"text": "go missing"},
        )
        parser_rejected = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/input/stream",
            json={"text": "inventory"},
        )
        empty_rejected = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/input/stream",
            json={"text": "   "},
        )

    assert parsed.status_code == 200
    assert parsed.headers["content-type"].startswith("text/event-stream")
    assert "event: input_parsed" in parsed.text
    assert '\"action_type\":\"move_area\"' in parsed.text
    assert f'\"area_id\":\"{target_area}\"' in parsed.text
    assert "event: action_result" in parsed.text
    assert '\"executed\":true' in parsed.text
    assert "event: stream_end" in parsed.text

    assert runtime_rejected.status_code == 200
    assert runtime_rejected.headers["content-type"].startswith("text/event-stream")
    assert "event: input_parsed" in runtime_rejected.text
    assert "event: action_result" in runtime_rejected.text
    assert '\"executed\":false' in runtime_rejected.text
    assert "unknown area: missing" in runtime_rejected.text

    assert parser_rejected.status_code == 200
    assert parser_rejected.headers["content-type"].startswith("text/event-stream")
    assert "event: input_rejected" in parser_rejected.text
    assert '\"code\":\"unsupported_input\"' in parser_rejected.text
    assert "event: action_result" not in parser_rejected.text
    assert '\"reason\":\"input_rejected\"' in parser_rejected.text

    assert empty_rejected.status_code == 200
    assert "event: input_rejected" in empty_rejected.text
    assert '\"code\":\"empty_input\"' in empty_rejected.text


def test_interact_stream_routes_public_utterance_without_focus(monkeypatch) -> None:
    class FakeAgentOrchestration:
        async def run_public_utterance(
            self,
            session,
            player_message,
            intent="talk",
            text_chunk_sink=None,
            check_result=None,
        ):
            del session, text_chunk_sink
            assert player_message == "大家先听我说。"
            assert intent == "talk"
            assert check_result is None
            return [
                SSEEvent("gm_comment", {"content": "空气瞬间安静下来。"}),
                SSEEvent(
                    "teammate_response",
                    {"character_id": "cow_girl", "content": "我在听。", "type": "speech"},
                ),
            ]

    runtime = _runtime(agent_orchestration=FakeAgentOrchestration())
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        streamed = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={"intent": "talk", "message": "大家先听我说。", "scope": "public"},
        )

    assert streamed.status_code == 200
    assert "event: interaction_rejected" not in streamed.text
    events = _parse_sse(streamed)
    assert _sse_event(events, "gm_comment")["content"] == "空气瞬间安静下来。"
    assert _sse_event(events, "teammate_response")["content"] == "我在听。"
    assert _sse_event(events, "stream_end")["completed"] is True


def test_interact_stream_party_scope_advances_even_when_silent(monkeypatch) -> None:
    class FakeAgentOrchestration:
        async def run_free_chat(self, session, player_message):
            del session
            assert player_message == "先等等。"
            return []

    runtime = _runtime(agent_orchestration=FakeAgentOrchestration())
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        before = asyncio.run(_load_session(session_id))
        assert before is not None
        initial_accumulated = before.runtime.state.time.accumulated

        streamed = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={"intent": "chat", "message": "先等等。", "scope": "party"},
        )

    assert streamed.status_code == 200
    events = _parse_sse(streamed)
    assert _sse_event(events, "stream_end")["completed"] is True
    after = asyncio.run(_load_session(session_id))
    assert after is not None
    assert after.runtime.state.time.accumulated == pytest.approx(initial_accumulated + (1 / 6))


def test_interact_stream_executes_minimal_shop_flow(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)

        browse = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={"npc_id": "merchant", "intent": "browse"},
        )
        buy = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "npc_id": "merchant",
                "intent": "buy",
                "item_id": "bandage",
                "count": 1,
            },
        )
        inventory_after_buy = client.get(
            f"/api/game/goblin_slayer/sessions/{session_id}/inventory"
        )
        sell = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "npc_id": "merchant",
                "intent": "sell",
                "item_id": "training_sword",
                "count": 1,
            },
        )
        inventory_after_sell = client.get(
            f"/api/game/goblin_slayer/sessions/{session_id}/inventory"
        )
        trade_rejected = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "npc_id": "merchant",
                "intent": "buy",
                "item_id": "bandage",
                "count": 6,
            },
        )
        invalid_intent = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={"npc_id": "merchant", "intent": "inspect"},
        )
        missing_item = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={"npc_id": "merchant", "intent": "buy"},
        )
        invalid_count = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "npc_id": "merchant",
                "intent": "sell",
                "item_id": "wooden_shield",
                "count": 0,
            },
        )
        missing_npc = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={"npc_id": "missing", "intent": "browse"},
        )
        move_away = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/navigate",
            json={"action": "move_area", "area_id": "training_grounds"},
        )
        npc_not_present = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={"npc_id": "merchant", "intent": "browse"},
        )

    assert browse.status_code == 200
    assert browse.headers["content-type"].startswith("text/event-stream")
    assert "event: interaction_resolved" in browse.text
    assert "event: shop_snapshot" in browse.text
    assert "event: action_result" not in browse.text
    assert '\"target_kind\":\"npc\"' in browse.text
    assert '\"target_id\":\"merchant\"' in browse.text
    assert '\"item_id\":\"bandage\"' in browse.text
    assert "event: stream_end" in browse.text

    assert buy.status_code == 200
    assert buy.headers["content-type"].startswith("text/event-stream")
    assert "event: interaction_resolved" in buy.text
    assert "event: action_result" in buy.text
    assert "event: shop_snapshot" in buy.text
    assert '\"executed\":true' in buy.text
    assert '\"action_type\":\"trade_buy\"' in buy.text
    assert "event: stream_end" in buy.text
    assert inventory_after_buy.status_code == 200
    assert any(
        item["item_id"] == "bandage" and item["count"] == 1
        for item in inventory_after_buy.json()["inventory"]
    )

    assert sell.status_code == 200
    assert sell.headers["content-type"].startswith("text/event-stream")
    assert "event: interaction_resolved" in sell.text
    assert "event: action_result" in sell.text
    assert "event: shop_snapshot" in sell.text
    assert '\"executed\":true' in sell.text
    assert '\"action_type\":\"trade_sell\"' in sell.text
    assert "event: stream_end" in sell.text
    assert inventory_after_sell.status_code == 200
    assert all(
        item["item_id"] != "training_sword"
        for item in inventory_after_sell.json()["inventory"]
    )

    assert trade_rejected.status_code == 200
    assert trade_rejected.headers["content-type"].startswith("text/event-stream")
    assert "event: interaction_resolved" in trade_rejected.text
    assert "event: interaction_rejected" in trade_rejected.text
    assert "event: action_result" not in trade_rejected.text
    assert '\"code\":\"interaction_failed\"' in trade_rejected.text
    assert "event: stream_end" in trade_rejected.text

    assert invalid_intent.status_code == 200
    assert "event: interaction_rejected" in invalid_intent.text
    assert '\"code\":\"invalid_intent\"' in invalid_intent.text
    assert '\"target_kind\":\"npc\"' in invalid_intent.text
    assert "event: action_result" not in invalid_intent.text
    assert "event: interaction_resolved" not in invalid_intent.text

    assert missing_item.status_code == 200
    assert "event: interaction_rejected" in missing_item.text
    assert '\"code\":\"missing_item\"' in missing_item.text
    assert "event: action_result" not in missing_item.text

    assert invalid_count.status_code == 200
    assert "event: interaction_rejected" in invalid_count.text
    assert '\"code\":\"invalid_count\"' in invalid_count.text
    assert "event: action_result" not in invalid_count.text

    assert missing_npc.status_code == 200
    assert "event: interaction_rejected" in missing_npc.text
    assert '\"code\":\"npc_not_found\"' in missing_npc.text
    assert '\"target_kind\":\"npc\"' in missing_npc.text
    assert "event: action_result" not in missing_npc.text

    assert move_away.status_code == 200
    assert _action_result_payload(move_away)["executed"] is True
    assert npc_not_present.status_code == 200
    assert "event: interaction_rejected" in npc_not_present.text
    assert '\"code\":\"npc_not_present\"' in npc_not_present.text
    assert '\"target_kind\":\"npc\"' in npc_not_present.text
    assert "event: action_result" not in npc_not_present.text


def test_interact_stream_executes_minimal_talk_flow(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        _clear_opening_bootstrap(runtime, session_id)

        left = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/navigate",
            json={"action": "leave_sub_location"},
        )
        rejected = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "talk",
            },
        )
        entered = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/navigate",
            json={"action": "enter_sub_location", "location_id": "counter"},
        )
        talk = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "talk",
            },
        )

        session = asyncio.run(_load_session(session_id))
        assert session is not None
        session.runtime.state.relations.modify_disposition("merchant", "trust", 7)
        session.runtime.state.relations.modify_disposition("merchant", "approval", 3)
        for impression in ("first", "second", "third", "fourth"):
            session.runtime.state.relations.add_impression("merchant", impression)
        asyncio.run(_save_session(session))
        enriched_talk = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "talk",
            },
        )

    assert left.status_code == 200
    assert _action_result_payload(left)["executed"] is True

    assert rejected.status_code == 200
    assert "event: interaction_rejected" in rejected.text
    assert '\"code\":\"npc_not_present\"' in rejected.text
    assert "event: talk_snapshot" not in rejected.text
    assert "event: action_result" not in rejected.text

    assert entered.status_code == 200
    entered_result = _action_result_payload(entered)
    assert entered_result["executed"] is True
    entered_overview = _sse_event(_parse_sse(entered), "location_overview")
    assert entered_overview is not None
    assert entered_overview["location_id"] == "counter"

    assert talk.status_code == 200
    assert "event: interaction_resolved" in talk.text
    assert "event: talk_snapshot" in talk.text
    assert "event: action_result" not in talk.text
    assert "event: shop_snapshot" not in talk.text
    assert "event: board_snapshot" not in talk.text
    talk_payloads = _event_payloads(talk.text, "talk_snapshot")
    assert len(talk_payloads) == 1
    assert talk_payloads[0]["target_id"] == "merchant"

    assert enriched_talk.status_code == 200
    enriched_payloads = _event_payloads(enriched_talk.text, "talk_snapshot")
    assert len(enriched_payloads) == 1
    assert enriched_payloads[0]["target_id"] == "merchant"


def test_interact_stream_executes_minimal_greet_flow(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        _clear_opening_bootstrap(runtime, session_id)

        left = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/navigate",
            json={"action": "leave_sub_location"},
        )
        rejected = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "greet",
            },
        )
        entered = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/navigate",
            json={"action": "enter_sub_location", "location_id": "counter"},
        )
        greeted_once = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "greet",
            },
        )
        greeted_twice = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "greet",
            },
        )
        talked = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "talk",
            },
        )

    assert left.status_code == 200
    assert _action_result_payload(left)["executed"] is True

    assert rejected.status_code == 200
    assert "event: interaction_rejected" in rejected.text
    assert '\"code\":\"npc_not_present\"' in rejected.text
    assert "event: action_result" not in rejected.text
    assert "event: talk_snapshot" not in rejected.text

    assert entered.status_code == 200
    entered_result = _action_result_payload(entered)
    assert entered_result["executed"] is True
    entered_overview = _sse_event(_parse_sse(entered), "location_overview")
    assert entered_overview is not None
    assert entered_overview["location_id"] == "counter"

    assert greeted_once.status_code == 200
    assert "event: interaction_resolved" in greeted_once.text
    assert "event: action_result" in greeted_once.text
    assert "event: talk_snapshot" in greeted_once.text
    assert "event: shop_snapshot" not in greeted_once.text
    assert "event: board_snapshot" not in greeted_once.text
    action_payloads = _event_payloads(greeted_once.text, "action_result")
    assert len(action_payloads) == 1
    assert action_payloads[0]["executed"] is True
    assert action_payloads[0]["action_type"] == "add_knowledge"
    greeted_once_payloads = _event_payloads(greeted_once.text, "talk_snapshot")
    assert len(greeted_once_payloads) == 1
    assert greeted_once_payloads[0]["profile"]["recent_impressions"] == [
        "Shared a brief greeting."
    ]

    assert greeted_twice.status_code == 200
    greeted_twice_payloads = _event_payloads(greeted_twice.text, "talk_snapshot")
    assert len(greeted_twice_payloads) == 1
    assert greeted_twice_payloads[0]["profile"]["recent_impressions"][-2:] == [
        "Shared a brief greeting.",
        "Shared a brief greeting.",
    ]

    assert talked.status_code == 200
    assert "event: interaction_resolved" in talked.text
    assert "event: talk_snapshot" in talked.text
    assert "event: action_result" not in talked.text
    talked_payloads = _event_payloads(talked.text, "talk_snapshot")
    assert len(talked_payloads) == 1
    assert talked_payloads[0]["profile"]["recent_impressions"][-2:] == [
        "Shared a brief greeting.",
        "Shared a brief greeting.",
    ]


def test_interact_stream_check_uses_standard_dice_roll_payload(monkeypatch) -> None:
    class FakeAgentOrchestration:
        async def run_npc_interaction(
            self,
            *,
            session,
            npc_id,
            player_message,
            intent="talk",
            text_chunk_sink=None,
            check_result=None,
        ):
            del session, player_message, intent, text_chunk_sink
            assert npc_id == "merchant"
            assert check_result is not None
            return [
                SSEEvent(
                    "npc_response",
                    {"npc_id": "merchant", "content": "行吧，你过去。", "type": "speech"},
                )
            ]

    runtime = _runtime(agent_orchestration=FakeAgentOrchestration())
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)
    monkeypatch.setattr("app.game_core.rules.handler_utils.roll_d20", lambda: 10)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        _clear_opening_bootstrap(runtime, session_id)
        client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/navigate",
            json={"action": "enter_sub_location", "location_id": "counter"},
        )
        streamed = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "talk",
                "message": "让我过去。",
                "check_skill": "persuasion",
                "check_dc": 13,
            },
        )

    assert streamed.status_code == 200
    events = _parse_sse(streamed)
    dice_roll = _sse_event(events, "dice_roll")
    assert dice_roll is not None
    assert dice_roll["skill"] == "persuasion"
    assert dice_roll["result"] == 10
    assert dice_roll["modifier"] == 1
    assert dice_roll["total"] == 11
    assert dice_roll["dc"] == 13
    assert dice_roll["passed"] is False
    assert dice_roll["roll"] == 10
    assert dice_roll["passed"] is False
    assert _sse_event(events, "npc_response")["content"] == "行吧，你过去。"


def test_interact_stream_executes_minimal_ask_quest_flow(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        _clear_opening_bootstrap(runtime, session_id)

        left = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/navigate",
            json={"action": "leave_sub_location"},
        )
        rejected = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "ask_quest",
                "quest_id": "dq_report_in",
            },
        )
        entered = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/navigate",
            json={"action": "enter_sub_location", "location_id": "counter"},
        )
        missing = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "ask_quest",
                "quest_id": "dq_report_in",
            },
        )

        session = asyncio.run(_load_session(session_id))
        assert session is not None
        session.runtime.state.quests.add_dynamic_quest(
            "dq_report_in",
            {
                "status": "available",
                "title": "Lead: Report In",
                "summary": "Follow the new lead tied to report_in.",
            },
        )
        asyncio.run(_save_session(session))
        no_board_link = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "ask_quest",
                "quest_id": "dq_report_in",
            },
        )

        session.runtime.state.areas.add_board_bulletin(
            session.runtime.state.player.current_area,
            "board",
            {
                "board_id": "board",
                "quest_id": "dq_report_in",
                "title": "New Lead Posted",
                "content": "A fresh lead is available: Report In.",
                "metadata": {
                    "quest_id": "dq_report_in",
                    "source_milestone": "report_in",
                },
                "published_at_tick": 9,
                "source": "test",
            },
        )
        asyncio.run(_save_session(session))
        with_board_link = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "npc_id": "merchant",
                "intent": "ask_quest",
                "quest_id": "dq_report_in",
            },
        )

    assert left.status_code == 200
    assert _action_result_payload(left)["executed"] is True

    assert rejected.status_code == 200
    assert "event: interaction_rejected" in rejected.text
    assert '\"code\":\"npc_not_present\"' in rejected.text
    assert "event: quest_brief" not in rejected.text

    assert entered.status_code == 200
    entered_result = _action_result_payload(entered)
    assert entered_result["executed"] is True
    entered_overview = _sse_event(_parse_sse(entered), "location_overview")
    assert entered_overview is not None
    assert entered_overview["location_id"] == "counter"

    assert missing.status_code == 200
    assert "event: interaction_rejected" in missing.text
    assert '\"code\":\"quest_not_found\"' in missing.text
    assert "event: interaction_resolved" not in missing.text
    assert "event: quest_brief" not in missing.text
    assert "event: action_result" not in missing.text

    assert no_board_link.status_code == 200
    assert "event: interaction_resolved" in no_board_link.text
    assert "event: quest_brief" in no_board_link.text
    assert "event: action_result" not in no_board_link.text
    no_board_payloads = _event_payloads(no_board_link.text, "quest_brief")
    assert len(no_board_payloads) == 1
    assert no_board_payloads[0]["target_id"] == "merchant"

    assert with_board_link.status_code == 200
    assert "event: interaction_resolved" in with_board_link.text
    assert "event: quest_brief" in with_board_link.text
    assert "event: action_result" not in with_board_link.text
    with_board_payloads = _event_payloads(with_board_link.text, "quest_brief")
    assert len(with_board_payloads) == 1
    assert with_board_payloads[0]["target_id"] == "merchant"


def test_interact_stream_executes_receptionist_accept_quest_flow(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        accepted = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "receptionist",
                "intent": "accept_quest",
                "quest_id": "dq_report_in",
            },
        )
        quests = client.get(f"/api/game/goblin_slayer/sessions/{session_id}/quests")

    assert accepted.status_code == 200
    events = _parse_sse(accepted)
    resolved = _sse_event(events, "interaction_resolved")
    assert resolved is not None
    assert resolved["target_id"] == "receptionist"
    assert resolved["intent"] == "accept_quest"
    action_result = _sse_event(events, "action_result")
    assert action_result is not None
    assert action_result["executed"] is True
    assert quests.status_code == 200
    assert quests.json()["dynamic_quests"]["dq_report_in"]["status"] == "active"


def test_interact_stream_accept_quest_rejects_merchant_and_missing_offer(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        merchant_rejected = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "accept_quest",
                "quest_id": "dq_report_in",
            },
        )

        _clear_opening_bootstrap(runtime, session_id)
        session = asyncio.run(_load_session(session_id))
        assert session is not None
        session.runtime.state.quests.add_dynamic_quest(
            "dq_report_in",
            {
                "status": "available",
                "title": "Lead: Report In",
                "summary": "Follow the new lead tied to report_in.",
            },
        )
        asyncio.run(_save_session(session))
        missing_offer = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "receptionist",
                "intent": "accept_quest",
                "quest_id": "dq_report_in",
            },
        )

    assert merchant_rejected.status_code == 200
    assert "event: interaction_rejected" in merchant_rejected.text
    assert '"code":"npc_cannot_accept_quests"' in merchant_rejected.text
    assert "event: interaction_resolved" not in merchant_rejected.text

    assert missing_offer.status_code == 200
    assert "event: interaction_rejected" in missing_offer.text
    assert '"code":"quest_not_offerable"' in missing_offer.text
    assert "event: interaction_resolved" not in missing_offer.text


def test_interact_stream_executes_receptionist_report_quest_flow(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        _clear_opening_bootstrap(runtime, session_id)

        session = asyncio.run(_load_session(session_id))
        assert session is not None
        quest = dict(
            session.runtime.state.quests.dynamic_quests.get(
                "dq_report_in",
                {
                    "quest_id": "dq_report_in",
                    "status": "available",
                    "title": "Lead: Report In",
                    "summary": "Follow the new lead tied to report_in.",
                },
            )
        )
        quest["status"] = "completed"
        quest["requires_report"] = True
        quest["reported"] = False
        session.runtime.state.quests.dynamic_quests["dq_report_in"] = quest
        session.runtime.state.quests._dirty = True
        asyncio.run(_save_session(session))

        before_panel = client.get(f"/api/game/goblin_slayer/sessions/{session_id}/quests")
        reported = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "receptionist",
                "intent": "report_quest",
                "quest_id": "dq_report_in",
            },
        )
        after_panel = client.get(f"/api/game/goblin_slayer/sessions/{session_id}/quests")

    assert before_panel.status_code == 200
    before_quest = before_panel.json()["dynamic_quests"]["dq_report_in"]
    assert before_quest["status"] == "completed"
    assert before_quest["can_report"] is True

    assert reported.status_code == 200
    events = _parse_sse(reported)
    resolved = _sse_event(events, "interaction_resolved")
    assert resolved is not None
    assert resolved["intent"] == "report_quest"
    action_result = _sse_event(events, "action_result")
    assert action_result is not None
    assert action_result["executed"] is True

    assert after_panel.status_code == 200
    after_quest = after_panel.json()["dynamic_quests"]["dq_report_in"]
    assert after_quest["status"] == "completed"
    assert after_quest["reported"] is True
    assert after_quest["can_report"] is False


def test_interact_stream_executes_minimal_ask_progress_flow(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        _clear_opening_bootstrap(runtime, session_id)

        left = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/navigate",
            json={"action": "leave_sub_location"},
        )
        rejected = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "ask_progress",
                "quest_id": "dq_report_in",
            },
        )
        entered = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/navigate",
            json={"action": "enter_sub_location", "location_id": "counter"},
        )
        missing = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "ask_progress",
                "quest_id": "dq_report_in",
            },
        )

        session = asyncio.run(_load_session(session_id))
        assert session is not None
        session.runtime.state.quests.add_dynamic_quest(
            "dq_report_in",
            {
                "status": "available",
                "title": "Lead: Report In",
                "summary": "Follow the new lead tied to report_in.",
            },
        )
        asyncio.run(_save_session(session))
        no_board_link = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "ask_progress",
                "quest_id": "dq_report_in",
            },
        )

        session.runtime.state.areas.add_board_bulletin(
            session.runtime.state.player.current_area,
            "board",
            {
                "board_id": "board",
                "quest_id": "dq_report_in",
                "title": "New Lead Posted",
                "content": "A fresh lead is available: Report In.",
                "metadata": {
                    "quest_id": "dq_report_in",
                    "source_milestone": "report_in",
                },
                "published_at_tick": 9,
                "source": "test",
            },
        )
        session.runtime.state.quests.advance_milestone("report_in", "ACTIVE")
        asyncio.run(_save_session(session))
        with_board_link = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "npc_id": "merchant",
                "intent": "ask_progress",
                "quest_id": "dq_report_in",
            },
        )

    assert left.status_code == 200
    assert _action_result_payload(left)["executed"] is True

    assert rejected.status_code == 200
    assert "event: interaction_rejected" in rejected.text
    assert '\"code\":\"npc_not_present\"' in rejected.text
    assert "event: interaction_resolved" not in rejected.text
    assert "event: quest_progress" not in rejected.text

    assert entered.status_code == 200
    entered_result = _action_result_payload(entered)
    assert entered_result["executed"] is True
    entered_overview = _sse_event(_parse_sse(entered), "location_overview")
    assert entered_overview is not None
    assert entered_overview["location_id"] == "counter"

    assert missing.status_code == 200
    assert "event: interaction_rejected" in missing.text
    assert '\"code\":\"quest_not_found\"' in missing.text
    assert "event: interaction_resolved" not in missing.text
    assert "event: quest_progress" not in missing.text

    assert no_board_link.status_code == 200
    assert "event: interaction_resolved" in no_board_link.text
    assert "event: quest_progress" in no_board_link.text
    assert "event: action_result" not in no_board_link.text
    assert "event: talk_snapshot" not in no_board_link.text
    no_board_payloads = _event_payloads(no_board_link.text, "quest_progress")
    assert len(no_board_payloads) == 1
    assert no_board_payloads[0]["target_id"] == "merchant"

    assert with_board_link.status_code == 200
    assert "event: interaction_resolved" in with_board_link.text
    assert "event: quest_progress" in with_board_link.text
    assert "event: action_result" not in with_board_link.text
    with_board_payloads = _event_payloads(with_board_link.text, "quest_progress")
    assert len(with_board_payloads) == 1
    assert with_board_payloads[0]["target_id"] == "merchant"


def test_interact_stream_executes_minimal_ask_location_flow(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        _clear_opening_bootstrap(runtime, session_id)

        left = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/navigate",
            json={"action": "leave_sub_location"},
        )
        rejected = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "ask_location",
                "quest_id": "dq_report_in",
            },
        )
        entered = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/navigate",
            json={"action": "enter_sub_location", "location_id": "counter"},
        )
        missing = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "ask_location",
                "quest_id": "dq_report_in",
            },
        )

        session = asyncio.run(_load_session(session_id))
        assert session is not None
        session.runtime.state.quests.add_dynamic_quest(
            "dq_report_in",
            {
                "status": "available",
                "title": "Lead: Report In",
                "summary": "Follow the new lead tied to report_in.",
            },
        )
        asyncio.run(_save_session(session))
        no_location = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "ask_location",
                "quest_id": "dq_report_in",
            },
        )

        session.runtime.state.quests.add_dynamic_quest(
            "dq_report_in",
            {
                "status": "available",
                "title": "Lead: Report In",
                "summary": "Follow the new lead tied to report_in.",
                "area_id": "frontier",
                "location_id": "camp",
            },
        )
        session.runtime.state.areas.add_board_bulletin(
            session.runtime.state.player.current_area,
            "board",
            {
                "board_id": "board",
                "quest_id": "dq_report_in",
                "title": "New Lead Posted",
                "content": "A fresh lead is available: Report In.",
                "metadata": {
                    "quest_id": "dq_report_in",
                    "source_milestone": "report_in",
                },
                "published_at_tick": 9,
                "source": "test",
            },
        )
        asyncio.run(_save_session(session))
        known_location = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "ask_location",
                "quest_id": "dq_report_in",
            },
        )

    assert left.status_code == 200
    assert _action_result_payload(left)["executed"] is True

    assert rejected.status_code == 200
    assert "event: interaction_rejected" in rejected.text
    assert '\"code\":\"npc_not_present\"' in rejected.text
    assert "event: interaction_resolved" not in rejected.text
    assert "event: quest_location" not in rejected.text

    assert entered.status_code == 200
    entered_result = _action_result_payload(entered)
    assert entered_result["executed"] is True
    entered_overview = _sse_event(_parse_sse(entered), "location_overview")
    assert entered_overview is not None
    assert entered_overview["location_id"] == "counter"

    assert missing.status_code == 200
    assert "event: interaction_rejected" in missing.text
    assert '\"code\":\"quest_not_found\"' in missing.text
    assert "event: interaction_resolved" not in missing.text
    assert "event: quest_location" not in missing.text

    assert no_location.status_code == 200
    assert "event: interaction_resolved" in no_location.text
    assert "event: quest_location" in no_location.text
    assert "event: action_result" not in no_location.text
    no_location_payloads = _event_payloads(no_location.text, "quest_location")
    assert len(no_location_payloads) == 1
    assert no_location_payloads[0]["target_id"] == "merchant"

    assert known_location.status_code == 200
    assert "event: interaction_resolved" in known_location.text
    assert "event: quest_location" in known_location.text
    known_location_payloads = _event_payloads(known_location.text, "quest_location")
    assert len(known_location_payloads) == 1
    assert known_location_payloads[0]["target_id"] == "merchant"


def test_interact_stream_executes_minimal_ask_requirements_flow(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        _clear_opening_bootstrap(runtime, session_id)

        missing = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "ask_requirements",
                "quest_id": "dq_report_in",
            },
        )

        session = asyncio.run(_load_session(session_id))
        assert session is not None
        session.runtime.state.quests.add_dynamic_quest(
            "dq_report_in",
            {
                "status": "available",
                "title": "Lead: Report In",
                "summary": "Follow the new lead tied to report_in.",
            },
        )
        asyncio.run(_save_session(session))
        no_requirements = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "ask_requirements",
                "quest_id": "dq_report_in",
            },
        )

        session.runtime.state.quests.add_dynamic_quest(
            "dq_report_in",
            {
                "status": "active",
                "title": "Lead: Report In",
                "summary": "Follow the new lead tied to report_in.",
                "requirements": ["bring proof", "", "return alive"],
            },
        )
        asyncio.run(_save_session(session))
        with_requirements = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "ask_requirements",
                "quest_id": "dq_report_in",
            },
        )

    assert missing.status_code == 200
    assert "event: interaction_rejected" in missing.text
    assert '\"code\":\"quest_not_found\"' in missing.text
    assert "event: interaction_resolved" not in missing.text
    assert "event: quest_requirements" not in missing.text

    assert no_requirements.status_code == 200
    assert "event: interaction_resolved" in no_requirements.text
    assert "event: quest_requirements" in no_requirements.text
    assert "event: action_result" not in no_requirements.text
    no_requirements_payloads = _event_payloads(
        no_requirements.text,
        "quest_requirements",
    )
    assert len(no_requirements_payloads) == 1
    assert no_requirements_payloads[0]["target_id"] == "merchant"

    assert with_requirements.status_code == 200
    assert "event: interaction_resolved" in with_requirements.text
    assert "event: quest_requirements" in with_requirements.text
    with_requirements_payloads = _event_payloads(
        with_requirements.text,
        "quest_requirements",
    )
    assert len(with_requirements_payloads) == 1
    assert with_requirements_payloads[0]["target_id"] == "merchant"


def test_interact_stream_executes_minimal_ask_reward_flow(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        _clear_opening_bootstrap(runtime, session_id)

        missing = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "ask_reward",
                "quest_id": "dq_report_in",
            },
        )

        session = asyncio.run(_load_session(session_id))
        assert session is not None
        session.runtime.state.quests.add_dynamic_quest(
            "dq_report_in",
            {
                "status": "available",
                "title": "Lead: Report In",
                "summary": "Follow the new lead tied to report_in.",
            },
        )
        asyncio.run(_save_session(session))
        no_reward = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "ask_reward",
                "quest_id": "dq_report_in",
            },
        )

        session.runtime.state.quests.add_dynamic_quest(
            "dq_report_in",
            {
                "status": "available",
                "title": "Lead: Report In",
                "summary": "Follow the new lead tied to report_in.",
                "rewards": {
                    "gold": 25,
                    "items": [
                        {"item_id": "bandage", "count": 2},
                        {"item_id": "", "count": 1},
                        {"count": 4},
                    ],
                },
            },
        )
        asyncio.run(_save_session(session))
        with_reward = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/interact/stream",
            json={
                "npc_id": "merchant",
                "intent": "ask_reward",
                "quest_id": "dq_report_in",
            },
        )

    assert missing.status_code == 200
    assert "event: interaction_rejected" in missing.text
    assert '\"code\":\"quest_not_found\"' in missing.text
    assert "event: interaction_resolved" not in missing.text
    assert "event: quest_reward" not in missing.text

    assert no_reward.status_code == 200
    assert "event: interaction_resolved" in no_reward.text
    assert "event: quest_reward" in no_reward.text
    assert "event: action_result" not in no_reward.text
    no_reward_payloads = _event_payloads(no_reward.text, "quest_reward")
    assert len(no_reward_payloads) == 1
    assert no_reward_payloads[0]["target_id"] == "merchant"

    assert with_reward.status_code == 200
    assert "event: interaction_resolved" in with_reward.text
    assert "event: quest_reward" in with_reward.text
    with_reward_payloads = _event_payloads(with_reward.text, "quest_reward")
    assert len(with_reward_payloads) == 1
    assert with_reward_payloads[0]["target_id"] == "merchant"


def test_action_stream_browse_board_full_chain_from_planner(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        opening = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/opening/stream",
        )
        entered_board = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/action/stream",
            json={
                "action_type": "enter_sub_location",
                "params": {"location_id": "board"},
            },
        )
        browse = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/action/stream",
            json={"action_type": "browse_board", "params": {"board_id": "board"}},
        )

    assert opening.status_code == 200
    assert _action_result_payload(entered_board)["executed"] is True
    browse_payload = _action_result_payload(browse)
    assert browse_payload["executed"] is True
    browse_entries = browse_payload["metadata"].get("entries")
    assert isinstance(browse_entries, list)
    assert browse_entries
    assert browse_entries[0].get("quest_id")

    session = asyncio.run(_load_session(session_id))
    assert session is not None
    current_area = session.runtime.state.player.current_area
    assert current_area is not None
    board_bulletins = session.runtime.state.areas.get_board_bulletins(current_area, "board")
    assert board_bulletins
    assert board_bulletins[0].get("board_id") == "board"


def test_action_stream_browse_board_accept_chain_updates_quest_status(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        opening = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/opening/stream",
        )
        entered_board = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/action/stream",
            json={
                "action_type": "enter_sub_location",
                "params": {"location_id": "board"},
            },
        )
        browse = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/action/stream",
            json={"action_type": "browse_board", "params": {"board_id": "board"}},
        )

    assert opening.status_code == 200
    assert _action_result_payload(entered_board)["executed"] is True
    assert _action_result_payload(browse)["executed"] is True

    session = asyncio.run(_load_session(session_id))
    assert session is not None
    current_area = session.runtime.state.player.current_area
    assert current_area is not None
    session_entries = session.runtime.state.areas.get_board_bulletins(current_area, "board")
    assert session_entries
    quest_id = str(session_entries[0].get("quest_id", ""))
    assert quest_id

    with TestClient(api_main.app) as client:
        # Reuse session after planner-driven bootstrap, then complete accept chain.
        accept = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/action/stream",
            json={
                "action_type": "board_accept_quest",
                "params": {"board_id": "board", "quest_id": quest_id},
            },
        )

    assert _action_result_payload(accept)["executed"] is True
    session = asyncio.run(_load_session(session_id))
    assert session is not None
    quest = session.runtime.state.quests.get_dynamic_quest(quest_id)
    assert quest is not None
    assert str(quest.get("status", "")).lower() == "active"


def test_action_stream_browse_board_empty_board_has_no_entries(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        _clear_opening_bootstrap(runtime, session_id)
        entered_board = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/action/stream",
            json={
                "action_type": "enter_sub_location",
                "params": {"location_id": "board"},
            },
        )
        empty_board = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/action/stream",
            json={"action_type": "browse_board", "params": {"board_id": "board"}},
        )

    assert _action_result_payload(entered_board)["executed"] is True
    empty_payload = _action_result_payload(empty_board)
    assert empty_payload["executed"] is True
    assert empty_payload["metadata"]["entries"] == []


def test_action_stream_browse_board_rejects_when_not_in_guild_board(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(api_main.app.state, "game_runtime", runtime, raising=False)

    with TestClient(api_main.app) as client:
        session_id = _create_session(client)
        _create_character(client, session_id)
        _clear_opening_bootstrap(runtime, session_id)
        moved = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/action/stream",
            json={"action_type": "move_area", "params": {"area_id": "training_grounds"}},
        )
        entered_yard = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/action/stream",
            json={"action_type": "enter_sub_location", "params": {"location_id": "yard"}},
        )
        rejected = client.post(
            f"/api/game/goblin_slayer/sessions/{session_id}/action/stream",
            json={"action_type": "browse_board", "params": {"board_id": "board"}},
        )

    assert _action_result_payload(moved)["executed"] is True
    assert _action_result_payload(entered_yard)["executed"] is True
    reject_payload = _action_result_payload(rejected)
    assert reject_payload["executed"] is False
    assert reject_payload["errors"]
    assert any("board interactable not found" in str(error) for error in reject_payload["errors"])
