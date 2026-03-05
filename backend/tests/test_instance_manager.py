"""Tests for InstanceManager and NPCInstance."""

from __future__ import annotations

from app.game_core.narrative.context_window import ContextWindow, WindowMessage
from app.game_core.narrative.instance_manager import InstanceManager, NPCInstance


class TestInstanceManager:
    def test_init_empty(self) -> None:
        mgr = InstanceManager()
        assert mgr.instance_count() == 0

    def test_get_or_create_returns_npc_instance(self) -> None:
        mgr = InstanceManager()
        instance = mgr.get_or_create("npc_01")

        assert isinstance(instance, NPCInstance)
        assert instance.actor_id == "npc_01"
        assert isinstance(instance.context_window, ContextWindow)
        assert instance.context_window.actor_id == "npc_01"

    def test_get_or_create_returns_same_instance(self) -> None:
        mgr = InstanceManager()

        i1 = mgr.get_or_create("npc_01")
        i2 = mgr.get_or_create("npc_01")

        assert i1 is i2

    def test_get_returns_existing_instance(self) -> None:
        mgr = InstanceManager()
        created = mgr.get_or_create("npc_01")

        fetched = mgr.get("npc_01")

        assert fetched is created

    def test_get_does_not_create(self) -> None:
        mgr = InstanceManager()

        assert mgr.get("npc_01") is None
        assert mgr.instance_count() == 0

    def test_get_or_create_moves_to_mru(self) -> None:
        mgr = InstanceManager(max_instances=3)
        mgr.get_or_create("npc_01")
        mgr.get_or_create("npc_02")
        mgr.get_or_create("npc_03")

        mgr.get_or_create("npc_01")
        mgr.get_or_create("npc_04")

        assert mgr.contains("npc_01")
        assert not mgr.contains("npc_02")
        assert mgr.contains("npc_03")
        assert mgr.contains("npc_04")

    def test_get_promotes_to_mru(self) -> None:
        mgr = InstanceManager(max_instances=2)
        mgr.get_or_create("npc_01")
        mgr.get_or_create("npc_02")

        mgr.get("npc_01")
        mgr.get_or_create("npc_03")

        assert mgr.contains("npc_01")
        assert not mgr.contains("npc_02")
        assert mgr.contains("npc_03")

    def test_lru_evicts_when_full(self) -> None:
        mgr = InstanceManager(max_instances=2)
        mgr.get_or_create("npc_01")
        mgr.get_or_create("npc_02")

        mgr.get_or_create("npc_03")

        assert mgr.instance_count() == 2
        assert not mgr.contains("npc_01")
        assert mgr.contains("npc_02")
        assert mgr.contains("npc_03")

    def test_reaccess_after_eviction_creates_fresh_instance(self) -> None:
        mgr = InstanceManager(max_instances=1)
        old = mgr.get_or_create("npc_01")
        old.context_window.add_message(
            WindowMessage(role="user", content="remember", token_count=3, metadata={})
        )

        mgr.get_or_create("npc_02")
        new = mgr.get_or_create("npc_01")

        assert new is not old
        assert len(new.context_window.messages) == 0

    def test_custom_window_settings_propagate(self) -> None:
        mgr = InstanceManager(max_tokens_per_instance=4096, overflow_threshold=0.75)
        instance = mgr.get_or_create("npc_01")

        assert instance.context_window.max_tokens == 4096
        assert abs(instance.context_window.overflow_threshold - 0.75) < 1e-9

    def test_context_window_messages_are_isolated_per_actor(self) -> None:
        mgr = InstanceManager()
        a = mgr.get_or_create("npc_a")
        b = mgr.get_or_create("npc_b")

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

        instance = mgr.get_or_create("npc_01", npc_directives=[directive], current_tick=3)

        assert instance.directive_queue == [directive]

    def test_existing_instance_picks_up_new_directive_on_next_get_or_create(self) -> None:
        mgr = InstanceManager()
        instance = mgr.get_or_create("npc_01", current_tick=3)
        directive = {
            "npc_id": "npc_01",
            "directive": {"kind": "hint", "topic": "well"},
            "priority": "medium",
            "expires_at_tick": 12,
            "consumed": False,
        }

        same = mgr.get_or_create("npc_01", npc_directives=[directive], current_tick=4)

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
        mgr.get_or_create("npc_01", current_tick=2)

        injected = mgr.inject_directive("npc_01", directive, current_tick=2)

        assert injected is True
        assert mgr.get("npc_01").directive_queue == [directive]

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
        instance = mgr.get_or_create("npc_01")
        instance.context_window.add_message(
            WindowMessage(role="user", content="one", token_count=1, metadata={})
        )
        instance.context_window.add_message(
            WindowMessage(role="model", content="two", token_count=1, metadata={})
        )

        mgr.get_or_create("npc_02")
        pending = mgr.drain_pending_writebacks()

        assert len(pending) == 1
        assert pending[0].actor_id == "npc_01"
        assert [msg.content for msg in pending[0].messages] == ["one", "two"]

    def test_pending_directive_instance_is_kept_before_idle_instance(self) -> None:
        mgr = InstanceManager(max_instances=2)
        idle = mgr.get_or_create("npc_idle", current_tick=1)
        active = mgr.get_or_create(
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

        mgr.get_or_create("npc_new", current_tick=2)

        assert idle.actor_id == "npc_idle"
        assert active.actor_id == "npc_active"
        assert not mgr.contains("npc_idle")
        assert mgr.contains("npc_active")
        assert mgr.contains("npc_new")
