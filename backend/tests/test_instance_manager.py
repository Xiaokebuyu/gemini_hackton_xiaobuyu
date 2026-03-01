"""Tests for InstanceManager — per-actor ContextWindow LRU pool.

All tests are synchronous (InstanceManager has no async methods).
"""

from __future__ import annotations

from app.game_core.narrative.context_window import ContextWindow, WindowMessage
from app.game_core.narrative.instance_manager import InstanceManager


class TestInstanceManager:
    # ------------------------------------------------------------------
    # Init / empty state
    # ------------------------------------------------------------------

    def test_init_empty(self) -> None:
        mgr = InstanceManager()
        assert mgr.instance_count() == 0

    def test_instance_count_starts_zero(self) -> None:
        mgr = InstanceManager(max_instances=10)
        assert mgr.instance_count() == 0

    # ------------------------------------------------------------------
    # get_or_create
    # ------------------------------------------------------------------

    def test_get_or_create_creates_new_window(self) -> None:
        mgr = InstanceManager()
        window = mgr.get_or_create("npc_01")
        assert isinstance(window, ContextWindow)
        assert window.actor_id == "npc_01"

    def test_get_or_create_returns_same_instance(self) -> None:
        mgr = InstanceManager()
        w1 = mgr.get_or_create("npc_01")
        w2 = mgr.get_or_create("npc_01")
        assert w1 is w2

    def test_get_or_create_increments_count(self) -> None:
        mgr = InstanceManager()
        mgr.get_or_create("npc_01")
        assert mgr.instance_count() == 1
        mgr.get_or_create("npc_02")
        assert mgr.instance_count() == 2

    def test_get_or_create_moves_to_mru(self) -> None:
        """Accessing npc_01 after adding npc_02 and npc_03 keeps npc_01 in pool."""
        mgr = InstanceManager(max_instances=3)
        mgr.get_or_create("npc_01")
        mgr.get_or_create("npc_02")
        mgr.get_or_create("npc_03")
        # Re-access npc_01 (moves to MRU)
        mgr.get_or_create("npc_01")
        # Adding npc_04 evicts LRU which should now be npc_02
        mgr.get_or_create("npc_04")
        assert mgr.contains("npc_01")   # was re-accessed → kept
        assert not mgr.contains("npc_02")  # was LRU → evicted
        assert mgr.contains("npc_03")
        assert mgr.contains("npc_04")

    # ------------------------------------------------------------------
    # get
    # ------------------------------------------------------------------

    def test_get_returns_none_for_unknown(self) -> None:
        mgr = InstanceManager()
        assert mgr.get("npc_99") is None

    def test_get_returns_existing(self) -> None:
        mgr = InstanceManager()
        created = mgr.get_or_create("npc_01")
        fetched = mgr.get("npc_01")
        assert fetched is created

    def test_get_does_not_create(self) -> None:
        mgr = InstanceManager()
        mgr.get("npc_01")
        assert mgr.instance_count() == 0

    def test_get_promotes_to_mru(self) -> None:
        """get() should also promote to MRU, preventing eviction."""
        mgr = InstanceManager(max_instances=2)
        mgr.get_or_create("npc_01")
        mgr.get_or_create("npc_02")
        # Promote npc_01 via get()
        mgr.get("npc_01")
        # Adding npc_03 evicts LRU = npc_02
        mgr.get_or_create("npc_03")
        assert mgr.contains("npc_01")
        assert not mgr.contains("npc_02")
        assert mgr.contains("npc_03")

    # ------------------------------------------------------------------
    # contains
    # ------------------------------------------------------------------

    def test_contains_false_for_unknown(self) -> None:
        mgr = InstanceManager()
        assert not mgr.contains("ghost")

    def test_contains_true_after_create(self) -> None:
        mgr = InstanceManager()
        mgr.get_or_create("npc_01")
        assert mgr.contains("npc_01")

    # ------------------------------------------------------------------
    # LRU eviction
    # ------------------------------------------------------------------

    def test_lru_evicts_when_full(self) -> None:
        mgr = InstanceManager(max_instances=2)
        mgr.get_or_create("npc_01")
        mgr.get_or_create("npc_02")
        assert mgr.instance_count() == 2
        # Adding a third evicts LRU (npc_01)
        mgr.get_or_create("npc_03")
        assert mgr.instance_count() == 2
        assert not mgr.contains("npc_01")
        assert mgr.contains("npc_02")
        assert mgr.contains("npc_03")

    def test_lru_evicts_least_recently_used(self) -> None:
        mgr = InstanceManager(max_instances=3)
        mgr.get_or_create("a")
        mgr.get_or_create("b")
        mgr.get_or_create("c")
        # Access "a" and "b" (promote them)
        mgr.get_or_create("a")
        mgr.get_or_create("b")
        # "c" is now LRU — adding "d" should evict it
        mgr.get_or_create("d")
        assert mgr.contains("a")
        assert mgr.contains("b")
        assert not mgr.contains("c")
        assert mgr.contains("d")

    def test_lru_mru_is_kept_on_eviction(self) -> None:
        """Most-recently-used window must never be evicted."""
        mgr = InstanceManager(max_instances=1)
        w1 = mgr.get_or_create("npc_01")
        # Add npc_02 — evicts npc_01
        mgr.get_or_create("npc_02")
        assert not mgr.contains("npc_01")
        assert mgr.contains("npc_02")
        # npc_02 is MRU; adding npc_03 evicts npc_02
        mgr.get_or_create("npc_03")
        assert not mgr.contains("npc_02")
        assert mgr.contains("npc_03")
        # The w1 reference is now stale (evicted) — a re-create gives fresh window
        w1_new = mgr.get_or_create("npc_01")
        assert w1_new is not w1

    def test_eviction_does_not_exceed_max(self) -> None:
        mgr = InstanceManager(max_instances=5)
        for i in range(20):
            mgr.get_or_create(f"npc_{i:02d}")
        assert mgr.instance_count() == 5

    # ------------------------------------------------------------------
    # Independent windows
    # ------------------------------------------------------------------

    def test_different_actors_independent_windows(self) -> None:
        mgr = InstanceManager()
        wa = mgr.get_or_create("npc_a")
        wb = mgr.get_or_create("npc_b")
        assert wa is not wb
        assert wa.actor_id == "npc_a"
        assert wb.actor_id == "npc_b"

    def test_window_accumulates_messages(self) -> None:
        """Adding messages to a window does not affect instance count."""
        mgr = InstanceManager()
        window = mgr.get_or_create("npc_01")
        window.add_message(WindowMessage(
            role="user", content="Hello",
            token_count=1, metadata={},
        ))
        assert mgr.instance_count() == 1
        assert len(window.messages) == 1

    # ------------------------------------------------------------------
    # Reaccess after eviction
    # ------------------------------------------------------------------

    def test_reaccess_after_eviction_creates_fresh_window(self) -> None:
        """After eviction, get_or_create must return a brand-new empty window."""
        mgr = InstanceManager(max_instances=1)
        w_old = mgr.get_or_create("npc_01")
        # Add a message to npc_01's window
        w_old.add_message(WindowMessage(
            role="user", content="Remember this",
            token_count=3, metadata={},
        ))
        assert len(w_old.messages) == 1
        # Evict npc_01 by adding npc_02
        mgr.get_or_create("npc_02")
        assert not mgr.contains("npc_01")
        # Re-create npc_01 — must be empty fresh window
        w_new = mgr.get_or_create("npc_01")
        assert w_new is not w_old
        assert len(w_new.messages) == 0

    # ------------------------------------------------------------------
    # Constructor parameters propagated to ContextWindow
    # ------------------------------------------------------------------

    def test_custom_max_tokens_propagated(self) -> None:
        mgr = InstanceManager(max_tokens_per_instance=4_096)
        window = mgr.get_or_create("npc_01")
        assert window.max_tokens == 4_096

    def test_custom_overflow_threshold_propagated(self) -> None:
        mgr = InstanceManager(overflow_threshold=0.75)
        window = mgr.get_or_create("npc_01")
        assert abs(window.overflow_threshold - 0.75) < 1e-9
