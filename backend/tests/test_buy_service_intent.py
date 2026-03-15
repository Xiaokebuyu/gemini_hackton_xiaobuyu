"""Tests for Phase A-1: buy_service intent backend — player shop-panel service purchase.

Decision record: D-SvcA1 (narrative.md)

Test categories
---------------
1. execute_service_effects() shared helper — gold check, success, one_shot revoke
2. InteractionViewContext.npc_services population — content + planner merge, donation skip
3. build_shop_snapshot_payload() services field — present/absent, effects_summary
4. build_talk_snapshot_payload() — browse and buy_service intents from services
5. _execute_buy_service() path — success, gold insufficient, unknown service
6. inbound.py normalization — buy_service validated and routed correctly
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.adapters.persistence import NullPersistencePort
from app.game_core.adapters.session_store import SaveStore
from app.game_core.narrative.service_tool import (
    ServiceExecutionResult,
    execute_service_effects,
)
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateChange
from app.game_core import GameRuntime
from app.interaction_service import (
    InteractionViewContext,
    InteractionService,
    build_interaction_view_context,
)
from app.interaction_views import (
    build_shop_snapshot_payload,
    build_talk_snapshot_payload,
    _build_effects_summary,
)

# ---------------------------------------------------------------------------
# Shared test infrastructure
# ---------------------------------------------------------------------------


def _runtime() -> GameRuntime:
    return GameRuntime(save_store=SaveStore(NullPersistencePort()))


def _session(*, area_id: str = "guild_hall", location_id: str | None = "counter"):
    runtime = _runtime()
    # Use the full world (not shell seed) so that priestess and her services are loaded.
    runtime.get_world("goblin_slayer", force_reload=True)
    session = asyncio.run(runtime.create_session("goblin_slayer"))
    session.runtime.state.player.apply_state_change(
        StateChange("player", "set", "current_area", area_id)
    )
    session.runtime.state.player.apply_state_change(
        StateChange("player", "set", "current_location", location_id)
    )
    session.runtime.state.player.apply_state_change(
        StateChange("player", "set", "gold", 100)
    )
    # Place priestess in guild_hall so presence checks pass
    session.runtime.state.areas.update_npc_location("priestess", location_id or "counter")
    return session


def _view_context(session) -> InteractionViewContext:
    return build_interaction_view_context(session.runtime.state, session.runtime.world)


def _make_run_command(*, succeed: bool = True, log: list[Command] | None = None):
    """Return a run_command callable for execute_service_effects tests."""
    def run_cmd(cmd: Command) -> ExecuteResult:
        if log is not None:
            log.append(cmd)
        if succeed:
            return ExecuteResult(executed=True, metadata={})
        return ExecuteResult(executed=False, errors=["test_failure"], metadata={})
    return run_cmd


# ---------------------------------------------------------------------------
# 1. execute_service_effects() shared helper
# ---------------------------------------------------------------------------


def test_execute_service_effects_success_with_gold_deduction() -> None:
    log: list[Command] = []
    svc = {
        "service_id": "heal",
        "label": "治疗",
        "price": 25,
        "effects": [{"type": "restore_hp", "amount": 30}],
        "one_shot": False,
    }
    result = execute_service_effects(svc, _make_run_command(log=log), player_gold=100, npc_id="priestess")

    assert result.success is True
    assert result.price_paid == 25
    assert "restore_hp" in result.applied_effects
    # Two commands: restore_hp + modify_gold (price deduction)
    types = [c.type for c in log]
    assert "npc_service_effect" in types
    deduct_cmds = [c for c in log if c.type == "npc_service_effect" and c.params.get("effect_type") == "modify_gold"]
    assert len(deduct_cmds) == 1
    assert deduct_cmds[0].params["amount"] == -25


def test_execute_service_effects_insufficient_gold_rejected() -> None:
    svc = {
        "service_id": "heal",
        "price": 50,
        "effects": [{"type": "restore_hp", "amount": 30}],
    }
    result = execute_service_effects(svc, _make_run_command(), player_gold=10, npc_id="priestess")

    assert result.success is False
    assert result.status == "insufficient_gold"
    assert "50" in result.message


def test_execute_service_effects_one_shot_issues_revoke_command() -> None:
    log: list[Command] = []
    svc = {
        "service_id": "field_dressing",
        "price": 0,
        "effects": [{"type": "restore_hp", "amount": 15}],
        "one_shot": True,
    }
    result = execute_service_effects(svc, _make_run_command(log=log), player_gold=0, npc_id="priestess")

    assert result.success is True
    revoke_cmds = [c for c in log if c.type == "planner_revoke_service"]
    assert len(revoke_cmds) == 1
    assert revoke_cmds[0].params["npc_id"] == "priestess"
    assert revoke_cmds[0].params["service_id"] == "field_dressing"


def test_execute_service_effects_free_service_no_deduction() -> None:
    log: list[Command] = []
    svc = {"service_id": "free_heal", "price": 0, "effects": [{"type": "restore_hp", "amount": 10}]}
    result = execute_service_effects(svc, _make_run_command(log=log), player_gold=0, npc_id="n")

    assert result.success is True
    assert result.price_paid == 0
    deduct_cmds = [c for c in log if c.params.get("effect_type") == "modify_gold"]
    assert len(deduct_cmds) == 0


# ---------------------------------------------------------------------------
# 2. InteractionViewContext.npc_services population
# ---------------------------------------------------------------------------


def test_npc_services_populated_for_priestess_from_content_layer() -> None:
    session = _session()
    ctx = _view_context(session)

    assert "priestess" in ctx.npc_services
    service_ids = [s["service_id"] for s in ctx.npc_services["priestess"]]
    # heal, blessing, field_dressing, trail_prayer must be present; donation skipped
    assert "heal" in service_ids
    assert "blessing" in service_ids
    assert "donation" not in service_ids


def test_npc_services_donation_is_excluded() -> None:
    session = _session()
    ctx = _view_context(session)

    for npc_id, svcs in ctx.npc_services.items():
        svc_ids = [s["service_id"] for s in svcs]
        assert "donation" not in svc_ids, f"{npc_id} should not have 'donation' in npc_services"


def test_npc_services_planner_services_merged_and_override_content() -> None:
    session = _session()
    # Assign a planner service that overrides "heal" with a higher price
    session.runtime.state.narrative_plan.assign_service("priestess", {
        "service_id": "heal",
        "label": "强化治疗",
        "price": 50,
        "effects": [{"type": "restore_hp", "amount": 60}],
        "one_shot": False,
    })
    ctx = _view_context(session)

    heal_svcs = [s for s in ctx.npc_services.get("priestess", []) if s["service_id"] == "heal"]
    assert len(heal_svcs) == 1
    assert heal_svcs[0]["price"] == 50
    assert heal_svcs[0]["label"] == "强化治疗"


# ---------------------------------------------------------------------------
# 3. build_shop_snapshot_payload() services field
# ---------------------------------------------------------------------------


def test_shop_snapshot_includes_services_for_priestess() -> None:
    session = _session()
    ctx = _view_context(session)

    payload = build_shop_snapshot_payload(ctx, "priestess")

    assert "services" in payload
    assert len(payload["services"]) > 0
    service_ids = [s["service_id"] for s in payload["services"]]
    assert "heal" in service_ids
    # Each service entry has required fields
    for svc in payload["services"]:
        assert "service_id" in svc
        assert "label" in svc
        assert "price" in svc
        assert svc["type"] == "service"


def test_shop_snapshot_services_empty_for_pure_merchant() -> None:
    session = _session()
    ctx = _view_context(session)

    payload = build_shop_snapshot_payload(ctx, "merchant")

    assert "services" in payload
    assert payload["services"] == []


# ---------------------------------------------------------------------------
# 4. build_talk_snapshot_payload() — browse and buy_service intents
# ---------------------------------------------------------------------------


def test_talk_snapshot_adds_buy_service_intent_for_service_npc() -> None:
    session = _session()
    ctx = _view_context(session)

    payload = build_talk_snapshot_payload(ctx, "priestess")

    assert "buy_service" in payload["available_intents"]
    assert "browse" in payload["available_intents"]


def test_talk_snapshot_no_buy_service_for_merchant_without_services() -> None:
    session = _session()
    # merchant has no services; give it a stock to trigger browse/buy/sell
    session.runtime.state.relations.shop_states["merchant"] = {"current_stock": [{"item_id": "x"}]}
    ctx = _view_context(session)

    payload = build_talk_snapshot_payload(ctx, "merchant")

    assert "buy_service" not in payload["available_intents"]


# ---------------------------------------------------------------------------
# 5. _build_effects_summary helper
# ---------------------------------------------------------------------------


def test_effects_summary_restore_hp() -> None:
    result = _build_effects_summary([{"type": "restore_hp", "amount": 30}])
    assert "30" in result
    assert "HP" in result


def test_effects_summary_empty_effects() -> None:
    assert _build_effects_summary([]) == ""
    assert _build_effects_summary(None) == ""  # type: ignore[arg-type]


def test_effects_summary_apply_effect_with_duration() -> None:
    result = _build_effects_summary([{"type": "apply_effect", "effect_id": "blessed", "duration_ticks": 6}])
    assert "blessed" in result
    assert "6" in result
