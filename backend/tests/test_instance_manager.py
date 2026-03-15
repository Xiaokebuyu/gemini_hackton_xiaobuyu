"""Tests for InstanceManager and NPCInstance."""

from __future__ import annotations

from app.game_core.narrative.context_window import ContextWindow, WindowMessage
from app.game_core.narrative.instance_manager import InstanceManager, NPCInstance

_SID = "test_session"


class TestInstanceManager:
    def test_init_empty(self) -> None:
        mgr = InstanceManager()
        assert mgr.instance_count() == 0

    def test_get_or_create_returns_npc_instance(self) -> None:
        mgr = InstanceManager()
        instance = mgr.get_or_create(_SID, "npc_01")

        assert isinstance(instance, NPCInstance)
        assert instance.actor_id == "npc_01"
        assert isinstance(instance.context_window, ContextWindow)
        assert instance.context_window.actor_id == "npc_01"

    def test_get_or_create_returns_same_instance(self) -> None:
        mgr = InstanceManager()

        i1 = mgr.get_or_create(_SID, "npc_01")
        i2 = mgr.get_or_create(_SID, "npc_01")

        assert i1 is i2

    def test_get_returns_existing_instance(self) -> None:
        mgr = InstanceManager()
        created = mgr.get_or_create(_SID, "npc_01")

        fetched = mgr.get(_SID, "npc_01")

        assert fetched is created

    def test_get_does_not_create(self) -> None:
        mgr = InstanceManager()

        assert mgr.get(_SID, "npc_01") is None
        assert mgr.instance_count() == 0

    def test_get_or_create_moves_to_mru(self) -> None:
        mgr = InstanceManager(max_instances=3)
        mgr.get_or_create(_SID, "npc_01")
        mgr.get_or_create(_SID, "npc_02")
        mgr.get_or_create(_SID, "npc_03")

        mgr.get_or_create(_SID, "npc_01")
        mgr.get_or_create(_SID, "npc_04")

        assert mgr.contains(_SID, "npc_01")
        assert not mgr.contains(_SID, "npc_02")
        assert mgr.contains(_SID, "npc_03")
        assert mgr.contains(_SID, "npc_04")

    def test_get_promotes_to_mru(self) -> None:
        mgr = InstanceManager(max_instances=2)
        mgr.get_or_create(_SID, "npc_01")
        mgr.get_or_create(_SID, "npc_02")

        mgr.get(_SID, "npc_01")
        mgr.get_or_create(_SID, "npc_03")

        assert mgr.contains(_SID, "npc_01")
        assert not mgr.contains(_SID, "npc_02")
        assert mgr.contains(_SID, "npc_03")

    def test_lru_evicts_when_full(self) -> None:
        mgr = InstanceManager(max_instances=2)
        mgr.get_or_create(_SID, "npc_01")
        mgr.get_or_create(_SID, "npc_02")

        mgr.get_or_create(_SID, "npc_03")

        assert mgr.instance_count() == 2
        assert not mgr.contains(_SID, "npc_01")
        assert mgr.contains(_SID, "npc_02")
        assert mgr.contains(_SID, "npc_03")

    def test_reaccess_after_eviction_creates_fresh_instance(self) -> None:
        mgr = InstanceManager(max_instances=1)
        old = mgr.get_or_create(_SID, "npc_01")
        old.context_window.add_message(
            WindowMessage(role="user", content="remember", token_count=3, metadata={})
        )

        mgr.get_or_create(_SID, "npc_02")
        new = mgr.get_or_create(_SID, "npc_01")

        assert new is not old
        assert len(new.context_window.messages) == 0

    def test_custom_window_settings_propagate(self) -> None:
        mgr = InstanceManager(max_tokens_per_instance=4096, overflow_threshold=0.75)
        instance = mgr.get_or_create(_SID, "npc_01")

        assert instance.context_window.max_tokens == 4096
        assert abs(instance.context_window.overflow_threshold - 0.75) < 1e-9

    def test_context_window_messages_are_isolated_per_actor(self) -> None:
        mgr = InstanceManager()
        a = mgr.get_or_create(_SID, "npc_a")
        b = mgr.get_or_create(_SID, "npc_b")

        a.context_window.add_message(
            WindowMessage(role="user", content="hello", token_count=1, metadata={})
        )

        assert len(a.context_window.messages) == 1
        assert len(b.context_window.messages) == 0

    def test_syncs_directives_into_new_instance(self) -> None:
        mgr = InstanceManager()
        directive = {
            "npc_id": "npc_01",
            "directive": {"kind": "hint", "topic": "west_farm"},
            "priority": "high",
            "expires_at_tick": 12,
            "consumed": False,
        }

        instance = mgr.get_or_create(_SID, "npc_01", npc_directives=[directive], current_tick=3)

        assert instance.directive_queue == [directive]

    def test_existing_instance_picks_up_new_directive_on_next_get_or_create(self) -> None:
        mgr = InstanceManager()
        instance = mgr.get_or_create(_SID, "npc_01", current_tick=3)
        directive = {
            "npc_id": "npc_01",
            "directive": {"kind": "hint", "topic": "well"},
            "priority": "medium",
            "expires_at_tick": 12,
            "consumed": False,
        }

        same = mgr.get_or_create(_SID, "npc_01", npc_directives=[directive], current_tick=4)

        assert same is instance
        assert same.directive_queue == [directive]

    def test_hot_inject_directive_into_active_instance(self) -> None:
        mgr = InstanceManager()
        directive = {
            "npc_id": "npc_01",
            "directive": {"kind": "hint", "topic": "ruins"},
            "priority": "medium",
            "expires_at_tick": 12,
            "consumed": False,
        }
        mgr.get_or_create(_SID, "npc_01", current_tick=2)

        injected = mgr.inject_directive(_SID, "npc_01", directive, current_tick=2)

        assert injected is True
        assert mgr.get(_SID, "npc_01").directive_queue == [directive]

    def test_consume_directive_marks_consumed_and_pops_queue(self) -> None:
        directive = {
            "npc_id": "npc_01",
            "directive": {"kind": "hint", "topic": "crypt"},
            "priority": "high",
            "expires_at_tick": 12,
            "consumed": False,
        }
        instance = NPCInstance(
            actor_id="npc_01",
            context_window=ContextWindow(actor_id="npc_01"),
            directive_queue=[directive],
        )

        consumed = instance.consume_directive(current_tick=5)

        assert consumed is directive
        assert consumed["consumed"] is True
        assert instance.directive_queue == []

    def test_lru_eviction_queues_writeback_messages(self) -> None:
        mgr = InstanceManager(max_instances=1)
        instance = mgr.get_or_create(_SID, "npc_01")
        instance.context_window.add_message(
            WindowMessage(role="user", content="one", token_count=1, metadata={})
        )
        instance.context_window.add_message(
            WindowMessage(role="model", content="two", token_count=1, metadata={})
        )

        mgr.get_or_create(_SID, "npc_02")
        pending = mgr.drain_pending_writebacks()

        assert len(pending) == 1
        assert pending[0].actor_id == "npc_01"
        assert [msg.content for msg in pending[0].messages] == ["one", "two"]

    def test_pending_directive_instance_is_kept_before_idle_instance(self) -> None:
        mgr = InstanceManager(max_instances=2)
        idle = mgr.get_or_create(_SID, "npc_idle", current_tick=1)
        active = mgr.get_or_create(
            _SID,
            "npc_active",
            npc_directives=[
                {
                    "npc_id": "npc_active",
                    "directive": {"kind": "hint", "topic": "camp"},
                    "priority": "high",
                    "expires_at_tick": 10,
                    "consumed": False,
                }
            ],
            current_tick=1,
        )

        mgr.get_or_create(_SID, "npc_new", current_tick=2)

        assert idle.actor_id == "npc_idle"
        assert active.actor_id == "npc_active"
        assert not mgr.contains(_SID, "npc_idle")
        assert mgr.contains(_SID, "npc_active")
        assert mgr.contains(_SID, "npc_new")

    # ------------------------------------------------------------------
    # Session isolation tests (new)
    # ------------------------------------------------------------------

    def test_same_actor_different_sessions_get_different_instances(self) -> None:
        mgr = InstanceManager()
        inst_a = mgr.get_or_create("session_a", "npc_01")
        inst_b = mgr.get_or_create("session_b", "npc_01")

        assert inst_a is not inst_b
        assert inst_a.actor_id == "npc_01"
        assert inst_b.actor_id == "npc_01"
        assert inst_a.session_id == "session_a"
        assert inst_b.session_id == "session_b"

    def test_contains_is_session_scoped(self) -> None:
        mgr = InstanceManager()
        mgr.get_or_create("session_a", "npc_01")

        assert mgr.contains("session_a", "npc_01")
        assert not mgr.contains("session_b", "npc_01")

    def test_get_is_session_scoped(self) -> None:
        mgr = InstanceManager()
        inst = mgr.get_or_create("session_a", "npc_01")

        assert mgr.get("session_a", "npc_01") is inst
        assert mgr.get("session_b", "npc_01") is None

    def test_clear_session_removes_only_that_session(self) -> None:
        mgr = InstanceManager()
        mgr.get_or_create("session_a", "npc_01")
        mgr.get_or_create("session_b", "npc_01")

        mgr.clear_session("session_a")

        assert not mgr.contains("session_a", "npc_01")
        assert mgr.contains("session_b", "npc_01")

    def test_clear_session_removes_pending_writebacks_for_that_session(self) -> None:
        mgr = InstanceManager(max_instances=1)
        inst = mgr.get_or_create("session_a", "npc_01")
        inst.context_window.add_message(
            WindowMessage(role="user", content="hi", token_count=1, metadata={})
        )
        # Evict to queue a writeback
        mgr.get_or_create("session_a", "npc_02")
        # Verify writeback queued
        assert len(mgr._pending_writebacks) == 1

        mgr.clear_session("session_a")

        assert len(mgr._pending_writebacks) == 0

    def test_iter_instances_filtered_by_session(self) -> None:
        mgr = InstanceManager()
        mgr.get_or_create("session_a", "npc_01")
        mgr.get_or_create("session_a", "npc_02")
        mgr.get_or_create("session_b", "npc_01")

        result = list(mgr.iter_instances(session_id="session_a"))
        actor_ids = {aid for aid, _ in result}

        assert actor_ids == {"npc_01", "npc_02"}
        assert len(result) == 2

    def test_drain_pending_writebacks_filtered_by_session(self) -> None:
        mgr = InstanceManager(max_instances=2)
        # Fill to capacity across two sessions
        mgr.get_or_create("session_a", "npc_01")
        inst_a2 = mgr.get_or_create("session_a", "npc_02")
        inst_a2.context_window.add_message(
            WindowMessage(role="user", content="a2 msg", token_count=1, metadata={})
        )

        # This evicts session_a/npc_01 (LRU). But let's use separate mgr for simplicity.
        mgr2 = InstanceManager(max_instances=1)
        inst_b = mgr2.get_or_create("session_b", "npc_01")
        inst_b.context_window.add_message(
            WindowMessage(role="user", content="b msg", token_count=1, metadata={})
        )
        mgr2.get_or_create("session_b", "npc_02")  # evicts session_b/npc_01

        # Only session_b writeback in mgr2
        drained = mgr2.drain_pending_writebacks(session_id="session_b")
        assert len(drained) == 1
        assert drained[0].actor_id == "npc_01"
        assert drained[0].session_id == "session_b"

    def test_instance_count_filtered_by_session(self) -> None:
        mgr = InstanceManager()
        mgr.get_or_create("session_a", "npc_01")
        mgr.get_or_create("session_a", "npc_02")
        mgr.get_or_create("session_b", "npc_01")

        assert mgr.instance_count() == 3
        assert mgr.instance_count("session_a") == 2
        assert mgr.instance_count("session_b") == 1

    def test_evict_is_session_scoped(self) -> None:
        mgr = InstanceManager()
        mgr.get_or_create("session_a", "npc_01")
        mgr.get_or_create("session_b", "npc_01")

        mgr.evict("session_a", "npc_01")

        assert not mgr.contains("session_a", "npc_01")
        assert mgr.contains("session_b", "npc_01")
