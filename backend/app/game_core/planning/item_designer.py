"""ItemDesigner sub-system — dynamic item / reward design.

Handles: design_reward, curate_shop.

design_reward: pre-plan item rewards onto available/active dynamic quests.
curate_shop: adjust a merchant's shop inventory (add/remove/restock items).

Decision record: D-P36 (narrative.md)
"""
from __future__ import annotations

from collections import Counter
import logging
from typing import TYPE_CHECKING, Any, ClassVar, Mapping

from app.game_core.planning.subsystem import PlannerEvent, SubSystemResult
from app.game_core.planning.utils import (
    coerce_non_empty_string,
    normalize_mapping,
    string_or_empty,
)
from app.game_core.rules.models import Command

if TYPE_CHECKING:
    from app.game_core.adapters.planner_system import PlannerAgentPort
    from app.game_core.orchestration.settlement import SettlementContext

logger = logging.getLogger(__name__)


_REWARD_ITEM_WHITELIST: tuple[str, ...] = (
    "cheap_shortsword",
    "throwing_dagger",
    "sturdy_spear",
    "standard_longsword",
    "scouts_hand_axe",
    "heavy_war_hammer",
    "leather_armor",
    "dirty_chain_mail",
    "round_shield",
    "healing_potion",
    "basic_antidote",
    "sulfur_smoke_ball",
    "holy_water_flask",
)
_STARTER_TIER_WEAPONS = frozenset({"training_sword", "cheap_shortsword", "throwing_dagger"})
_MELEE_DIVINE_CLASSES = frozenset({"fighter", "priest", "cleric", "paladin"})
_COMBAT_TOKENS = frozenset({"goblin", "hunt", "raid", "slay", "combat"})
_POISON_TOKENS = frozenset({"poison", "venom", "toxic"})
_HOLY_TOKENS = frozenset({"undead", "cursed", "holy", "shrine"})
_DEFENSE_TOKENS = frozenset({"defend", "protect", "escort", "survive"})
_BLACKSMITH_DISCOURAGED_ITEMS = frozenset({"holy_water_flask"})
_BLACKSMITH_IDENTITY_TYPES = frozenset({"weapon", "armor", "shield"})
_EQUIPMENT_ITEM_TYPES = frozenset({"weapon", "armor", "shield"})
_RARITY_RANKS = {
    "common": 1,
    "uncommon": 2,
    "rare": 3,
    "very_rare": 4,
    "legendary": 5,
}


class ItemDesignerSubSystem:
    """PlannerSubSystem for item and reward design directives."""

    _HANDLES: ClassVar[frozenset[str]] = frozenset({"design_reward", "curate_shop"})
    _EVENTS: ClassVar[frozenset[str]] = frozenset(
        {"quest_created", "quest_accepted", "shop_refreshed"}
    )

    def __init__(
        self,
        *,
        sse_collector: list | None = None,
        agent: PlannerAgentPort | None = None,
    ) -> None:
        self._sse_collector = sse_collector
        self._agent = agent

    # ------------------------------------------------------------------
    # PlannerSubSystem protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "item_designer"

    @property
    def handles(self) -> frozenset[str]:
        return self._HANDLES

    def accepts_event(self, event: PlannerEvent) -> bool:
        return event.kind in self._EVENTS

    async def evaluate(
        self, event: PlannerEvent, context: Any
    ) -> SubSystemResult:
        if self._agent is not None:
            return await self._evaluate_with_agent(event, context)
        return SubSystemResult()

    def apply_directive(
        self,
        kind: str,
        payload: dict[str, Any],
        context: Any,
        *,
        current_tick: int,
    ) -> bool | str:
        if kind == "design_reward":
            return self._apply_design_reward(payload, context, current_tick=current_tick)
        if kind == "curate_shop":
            return self._apply_curate_shop(payload, context, current_tick=current_tick)
        logger.debug("ItemDesignerSubSystem: unsupported directive %r", kind)
        return "unsupported_kind"

    # ------------------------------------------------------------------
    # design_reward
    # ------------------------------------------------------------------

    def _apply_design_reward(
        self,
        payload: dict[str, Any],
        context: "SettlementContext",
        *,
        current_tick: int,
    ) -> bool | str:
        params = dict(payload)
        params["current_tick"] = current_tick
        result = context.execute_command(
            Command(
                type="planner_design_reward",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            reason = "; ".join(result.errors) if result.errors else "command_failed"
            logger.debug("design_reward rejected: %s", reason)
            return reason

        # C-6: W6-3 — emit SSE event on successful design_reward
        if self._sse_collector is not None:
            from app.game_core.orchestration.models import SSEEvent

            reward_items = payload.get("reward_items", [])
            item_ids = [
                r.get("item_id")
                for r in reward_items
                if isinstance(r, Mapping) and r.get("item_id")
            ]
            self._sse_collector.append(
                SSEEvent(
                    event_type="reward_designed",
                    payload={
                        "quest_id": payload.get("linked_quest_id") or payload.get("quest_id"),
                        "items": item_ids,
                    },
                )
            )
        return True

    @classmethod
    def _normalize_reward_items(cls, raw_items: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_items, list):
            return []
        merged: list[dict[str, Any]] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, Mapping):
                continue
            item_id = coerce_non_empty_string(raw_item.get("item_id"))
            if item_id is None:
                continue
            try:
                count = int(raw_item.get("count", 1))
            except (TypeError, ValueError):
                count = 1
            if count < 1:
                continue
            merged = cls._merge_reward_item(merged, item_id, count)
        return merged

    @classmethod
    def _merge_reward_item(
        cls,
        items: list[dict[str, Any]],
        item_id: str,
        quantity: int,
    ) -> list[dict[str, Any]]:
        merged = cls._normalize_reward_items(items)
        for entry in merged:
            if entry.get("item_id") != item_id:
                continue
            try:
                current = int(entry.get("count", 1))
            except (TypeError, ValueError):
                current = 1
            entry["count"] = max(1, current) + quantity
            return merged
        merged.append({"item_id": item_id, "count": quantity})
        return merged

    # ------------------------------------------------------------------
    # curate_shop
    # ------------------------------------------------------------------

    def _apply_curate_shop(
        self,
        payload: dict[str, Any],
        context: "SettlementContext",
        *,
        current_tick: int,
    ) -> bool | str:
        params = dict(payload)
        params["current_tick"] = current_tick
        result = context.execute_command(
            Command(
                type="planner_curate_shop",
                params=params,
                source="narrative_planner",
            )
        )
        if not result.executed:
            reason = "; ".join(result.errors) if result.errors else "command_failed"
            logger.debug("curate_shop rejected: %s", reason)
            return reason

        if self._sse_collector is not None:
            from app.game_core.orchestration.models import SSEEvent

            metadata = dict(result.metadata) if isinstance(result.metadata, dict) else {}
            self._sse_collector.append(
                SSEEvent(event_type="shop_curated", payload={"npc_id": metadata.get("npc_id")})
            )
        return True

    # ------------------------------------------------------------------
    # Agent bridge
    # ------------------------------------------------------------------

    async def _evaluate_with_agent(
        self,
        event: PlannerEvent,
        context: "SettlementContext",
    ) -> SubSystemResult:
        base_context = event.payload.get("planner_context", {})
        if not isinstance(base_context, Mapping):
            base_context = {}
        agent_context = dict(base_context)
        current_event = {
            "kind": event.kind,
            "tick": event.tick,
            "source": event.source,
            "emitter": event.emitter,
            "round_index": event.round_index,
            "payload": {
                key: value
                for key, value in event.payload.items()
                if key != "planner_context"
            },
        }
        agent_context["event"] = current_event
        agent_context["current_event"] = current_event
        player_snapshot = self._build_player_snapshot(context)
        agent_context["player_snapshot"] = player_snapshot
        reward_candidates: list[dict[str, Any]] = []
        shop_candidates: list[dict[str, Any]] = []
        reward_noop_reason: str | None = None
        shop_noop_reason: str | None = None
        quest_id: str | None = None
        npc_id: str | None = None
        quest_snapshot: dict[str, Any] | None = None
        merchant_profile: dict[str, Any] = {}
        shop_snapshot: dict[str, Any] | None = None
        if event.kind in {"quest_created", "quest_accepted"}:
            quest_id = coerce_non_empty_string(current_event["payload"].get("quest_id"))
            if quest_id is not None:
                quest_snapshot = context.state.quests.get_dynamic_quest(quest_id)
                if quest_snapshot is not None:
                    agent_context["quest_snapshot"] = quest_snapshot
                    reward_candidates = self._build_reward_candidates(
                        context,
                        quest_snapshot,
                        player_snapshot=player_snapshot,
                    )
                    reward_noop_reason = self._reward_candidate_noop_reason(
                        context,
                        quest_snapshot,
                        player_snapshot=player_snapshot,
                    )
        agent_context["reward_candidates"] = reward_candidates
        if event.kind == "shop_refreshed":
            npc_id = coerce_non_empty_string(current_event["payload"].get("npc_id"))
            if npc_id is not None and context.state.has_slice("relations"):
                raw_shop_snapshot = context.state.relations.shop_states.get(npc_id)
                if isinstance(raw_shop_snapshot, Mapping):
                    shop_snapshot = dict(raw_shop_snapshot)
                    agent_context["shop_snapshot"] = shop_snapshot
                    merchant_profile = self._build_merchant_profile(
                        context,
                        npc_id,
                        player_snapshot=player_snapshot,
                    )
                    agent_context["merchant_profile"] = merchant_profile
                    shop_candidates = self._build_shop_candidates(
                        context,
                        npc_id,
                        shop_snapshot,
                        player_snapshot=player_snapshot,
                        merchant_profile=merchant_profile,
                    )
                    shop_noop_reason = self._shop_candidate_noop_reason(
                        context,
                        npc_id,
                        player_snapshot=player_snapshot,
                        merchant_profile=merchant_profile,
                    )
        agent_context["shop_candidates"] = shop_candidates
        agent_context["available_items"] = self._build_available_items(
            context,
            event_kind=event.kind,
            reward_candidates=reward_candidates,
            shop_candidates=shop_candidates,
            merchant_profile=merchant_profile,
        )

        raw = await self._agent.evaluate(agent_context)
        directives = raw.get("directives", []) if isinstance(raw, Mapping) else []
        directives, audit_metadata = self._filter_agent_directives(
            context,
            event.kind,
            directives,
            quest_id=quest_id,
            reward_candidates=reward_candidates,
            quest_snapshot=quest_snapshot,
            player_snapshot=player_snapshot,
            npc_id=npc_id,
            shop_candidates=shop_candidates,
            merchant_profile=merchant_profile,
            available_items=agent_context["available_items"],
        )
        story_facts = raw.get("story_facts", []) if isinstance(raw, Mapping) else []
        strategy_notes = (
            string_or_empty(raw.get("strategy_notes"))
            if isinstance(raw, Mapping)
            else ""
        )
        metadata = normalize_mapping(raw.get("metadata")) if isinstance(raw, Mapping) else {}
        metadata["candidate_summary"] = {
            "reward_candidate_count": len(reward_candidates),
            "shop_candidate_count": len(shop_candidates),
        }
        metadata.update(audit_metadata)
        if not directives:
            noop_reason = reward_noop_reason if event.kind in {"quest_created", "quest_accepted"} else shop_noop_reason
            if noop_reason:
                metadata["noop_reason"] = noop_reason
        return SubSystemResult(
            directives=list(directives) if isinstance(directives, list) else [],
            story_facts=(
                [dict(item) for item in story_facts if isinstance(item, Mapping)]
                if isinstance(story_facts, list)
                else []
            ),
            strategy_notes=strategy_notes,
            metadata=metadata,
        )

    def _build_player_snapshot(
        self,
        context: "SettlementContext",
    ) -> dict[str, Any]:
        if not context.state.has_slice("player"):
            return {
                "level": 1,
                "character_class": "",
                "hp": 0,
                "max_hp": 0,
                "ac": 10,
                "stats": {},
                "equipped_item_ids": {},
                "inventory_counts": {},
            }
        snapshot = context.state.player.snapshot()
        inventory_counts: dict[str, int] = {}
        for entry in snapshot.get("inventory", []):
            if not isinstance(entry, Mapping):
                continue
            item_id = coerce_non_empty_string(entry.get("item_id"))
            if item_id is None:
                continue
            try:
                count = int(entry.get("count", 1))
            except (TypeError, ValueError):
                count = 1
            inventory_counts[item_id] = inventory_counts.get(item_id, 0) + max(0, count)

        equipped_item_ids: dict[str, str] = {}
        raw_equipment = snapshot.get("equipment", {})
        if isinstance(raw_equipment, Mapping):
            for slot, raw_item in raw_equipment.items():
                if not isinstance(raw_item, Mapping):
                    continue
                item_id = coerce_non_empty_string(raw_item.get("item_id"))
                if item_id is not None:
                    equipped_item_ids[str(slot)] = item_id

        return {
            "level": int(snapshot.get("level", 1) or 1),
            "character_class": string_or_empty(snapshot.get("character_class")).strip().lower(),
            "hp": int(snapshot.get("hp", 0) or 0),
            "max_hp": int(snapshot.get("max_hp", 0) or 0),
            "ac": int(snapshot.get("ac", 10) or 10),
            "stats": dict(snapshot.get("stats", {}))
            if isinstance(snapshot.get("stats"), Mapping)
            else {},
            "equipped_item_ids": equipped_item_ids,
            "inventory_counts": inventory_counts,
        }

    def _build_reward_candidates(
        self,
        context: "SettlementContext",
        quest_snapshot: Mapping[str, Any],
        *,
        player_snapshot: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        if not context.world.has_registry("items"):
            return []
        existing_reward_items = {
            entry.get("item_id")
            for entry in self._normalize_reward_items(
                normalize_mapping(quest_snapshot.get("rewards")).get("items")
            )
        }
        if existing_reward_items:
            return []

        quest_text = self._build_quest_text_blob(quest_snapshot).lower()
        level = self._coerce_int(player_snapshot.get("level"), 1)
        hp = self._coerce_int(player_snapshot.get("hp"), 0)
        max_hp = max(1, self._coerce_int(player_snapshot.get("max_hp"), 0))
        hp_ratio = hp / max_hp if max_hp > 0 else 1.0
        ac = self._coerce_int(player_snapshot.get("ac"), 10)
        character_class = string_or_empty(player_snapshot.get("character_class")).strip().lower()
        stats = (
            dict(player_snapshot.get("stats", {}))
            if isinstance(player_snapshot.get("stats"), Mapping)
            else {}
        )
        strength = self._coerce_int(stats.get("str"), 10)
        equipped_item_ids = (
            dict(player_snapshot.get("equipped_item_ids", {}))
            if isinstance(player_snapshot.get("equipped_item_ids"), Mapping)
            else {}
        )
        inventory_counts = (
            dict(player_snapshot.get("inventory_counts", {}))
            if isinstance(player_snapshot.get("inventory_counts"), Mapping)
            else {}
        )
        combat_cue = self._contains_token(quest_text, _COMBAT_TOKENS)
        poison_cue = self._contains_token(quest_text, _POISON_TOKENS)
        holy_cue = self._contains_token(quest_text, _HOLY_TOKENS)
        defense_cue = self._contains_token(quest_text, _DEFENSE_TOKENS)
        main_hand_item = string_or_empty(equipped_item_ids.get("main_hand")).strip()
        chest_item = string_or_empty(equipped_item_ids.get("chest")).strip()
        off_hand_item = string_or_empty(equipped_item_ids.get("off_hand")).strip()
        main_hand_is_starter = not main_hand_item or main_hand_item in _STARTER_TIER_WEAPONS
        ac_target = 13 if level <= 2 else 15
        ac_gap = ac < ac_target
        obvious_gap = main_hand_is_starter or ac_gap or hp_ratio < 0.6

        candidates: list[dict[str, Any]] = []
        for item_id in _REWARD_ITEM_WHITELIST:
            item = context.world.items.get(item_id)
            if item is None:
                continue
            item_type = self._item_role_type(item)
            reward_guardrail_reason = self._reward_progression_guardrail_reason(
                item_type=item_type,
                rarity=string_or_empty(getattr(item, "rarity", "")).strip().lower(),
                base_price=self._coerce_item_base_price(item),
                level=level,
            )
            if reward_guardrail_reason is not None:
                continue
            if not self._reward_item_allowed(
                item_id,
                level=level,
                character_class=character_class,
                strength=strength,
                off_hand_item=off_hand_item,
                equipped_item_ids=equipped_item_ids,
                existing_reward_items=existing_reward_items,
            ):
                continue

            quantity = self._reward_quantity(
                item_id,
                level=level,
                hp_ratio=hp_ratio,
                quest_text=quest_text,
                inventory_counts=inventory_counts,
            )
            score = 0
            reasons: list[str] = []

            if item_id == "healing_potion":
                score += 12
                reasons.append("safe_consumable")
                if hp_ratio < 0.6:
                    score += 60
                    reasons.append("low_hp")
                if inventory_counts.get("healing_potion", 0) == 0:
                    score += 28
                    reasons.append("no_healing_stock")
                if defense_cue:
                    score += 12
                    reasons.append("defensive_quest")
            elif item_id == "basic_antidote":
                score += 10
                reasons.append("safe_consumable")
                if inventory_counts.get("basic_antidote", 0) == 0:
                    score += 10
                    reasons.append("no_antidote_stock")
                if poison_cue:
                    score += 60
                    reasons.append("poison_threat")
            elif item_id == "sulfur_smoke_ball":
                score += 8
                reasons.append("tactical_consumable")
                if combat_cue:
                    score += 32
                    reasons.append("combat_pressure")
            elif item_id == "holy_water_flask":
                score += 6
                reasons.append("specialized_consumable")
                if holy_cue:
                    score += 44
                    reasons.append("holy_threat")
            elif item_id == "round_shield":
                if ac_gap:
                    score += 34
                    reasons.append("ac_gap")
                if defense_cue:
                    score += 18
                    reasons.append("defensive_quest")
                if not off_hand_item:
                    score += 6
                    reasons.append("open_offhand")
            elif item_id == "leather_armor":
                if not chest_item:
                    score += 24
                    reasons.append("empty_chest_slot")
                if ac_gap:
                    score += 22
                    reasons.append("ac_gap")
            elif item_id == "dirty_chain_mail":
                if ac_gap:
                    score += 34
                    reasons.append("ac_gap")
                if defense_cue:
                    score += 10
                    reasons.append("defensive_quest")
            elif item_type == "weapon":
                if combat_cue:
                    score += 22
                    reasons.append("combat_quest")
                if main_hand_is_starter and level >= 3 and item_id in {
                    "sturdy_spear",
                    "standard_longsword",
                    "scouts_hand_axe",
                    "heavy_war_hammer",
                }:
                    score += 30
                    reasons.append("starter_weapon_upgrade")
                if not main_hand_item and item_id in {"cheap_shortsword", "sturdy_spear"}:
                    score += 18
                    reasons.append("empty_main_hand")

            if not obvious_gap:
                if item_type == "consumable":
                    score += 10
                    reasons.append("low_risk_default")
                else:
                    score -= 8
            elif item_type == "consumable":
                score += 4

            if score <= 0:
                continue
            candidates.append(
                {
                    "item_id": item_id,
                    "item_type": item_type,
                    "quantity": quantity,
                    "score": score,
                    "reasons": reasons,
                }
            )

        candidates.sort(key=lambda item: (-self._coerce_int(item.get("score"), 0), str(item.get("item_id", ""))))
        return candidates[:3]

    def _build_merchant_profile(
        self,
        context: "SettlementContext",
        npc_id: str,
        *,
        player_snapshot: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not (
            context.world.has_registry("characters")
            and context.world.has_registry("items")
        ):
            return {}
        merchant = context.world.characters.get(npc_id)
        if merchant is None or merchant.shop_inventory is None:
            return {}

        allowed_item_ids: list[str] = []
        rotating_candidate_ids: list[str] = []
        level = self._coerce_int(player_snapshot.get("level"), 1)
        for pool_name in ("base_pool", "rotating_pool"):
            pool = getattr(merchant.shop_inventory, pool_name, [])
            for entry in pool:
                item_id = string_or_empty(entry.item_id).strip()
                if not item_id:
                    continue
                item = context.world.items.get(item_id)
                if item is None:
                    continue
                if item_id not in allowed_item_ids:
                    allowed_item_ids.append(item_id)
                if (
                    pool_name == "rotating_pool"
                    and item_id not in rotating_candidate_ids
                    and int(entry.min_player_level or 0) <= level
                ):
                    rotating_candidate_ids.append(item_id)

        type_counter: Counter[str] = Counter()
        for item_id in allowed_item_ids:
            item = context.world.items.get(item_id)
            if item is None:
                continue
            type_counter[self._item_role_type(item)] += 1
        dominant_item_types = [
            item_type for item_type, _count in type_counter.most_common(3)
        ]
        identity_weight = sum(
            count for item_type, count in type_counter.items() if item_type in _BLACKSMITH_IDENTITY_TYPES
        )
        other_weight = sum(
            count for item_type, count in type_counter.items() if item_type not in _BLACKSMITH_IDENTITY_TYPES
        )
        merchant_archetype = "general_merchant"
        if "craftsman" in merchant.tags and identity_weight >= other_weight:
            merchant_archetype = "blacksmith_like"

        return {
            "npc_id": npc_id,
            "tags": list(merchant.tags),
            "allowed_item_ids": allowed_item_ids,
            "rotating_candidate_ids": rotating_candidate_ids,
            "dominant_item_types": dominant_item_types,
            "merchant_archetype": merchant_archetype,
        }

    def _build_shop_candidates(
        self,
        context: "SettlementContext",
        npc_id: str,
        shop_snapshot: Mapping[str, Any],
        *,
        player_snapshot: Mapping[str, Any],
        merchant_profile: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        if not (
            context.world.has_registry("characters")
            and context.world.has_registry("items")
        ):
            return []
        merchant = context.world.characters.get(npc_id)
        if merchant is None or merchant.shop_inventory is None:
            return []
        rotating_ids = merchant_profile.get("rotating_candidate_ids", [])
        if not isinstance(rotating_ids, list) or not rotating_ids:
            return []

        current_stock = [
            dict(row)
            for row in shop_snapshot.get("current_stock", [])
            if isinstance(row, Mapping)
        ]
        current_type_counts: Counter[str] = Counter()
        for row in current_stock:
            item_id = coerce_non_empty_string(row.get("item_id"))
            if item_id is None:
                continue
            item = context.world.items.get(item_id)
            if item is None:
                continue
            current_type_counts[self._item_role_type(item)] += 1

        level = self._coerce_int(player_snapshot.get("level"), 1)
        hp = self._coerce_int(player_snapshot.get("hp"), 0)
        max_hp = max(1, self._coerce_int(player_snapshot.get("max_hp"), 0))
        hp_ratio = hp / max_hp if max_hp > 0 else 1.0
        ac = self._coerce_int(player_snapshot.get("ac"), 10)
        inventory_counts = (
            dict(player_snapshot.get("inventory_counts", {}))
            if isinstance(player_snapshot.get("inventory_counts"), Mapping)
            else {}
        )
        equipped_item_ids = (
            dict(player_snapshot.get("equipped_item_ids", {}))
            if isinstance(player_snapshot.get("equipped_item_ids"), Mapping)
            else {}
        )
        main_hand_item = string_or_empty(equipped_item_ids.get("main_hand")).strip()
        main_hand_is_starter = not main_hand_item or main_hand_item in _STARTER_TIER_WEAPONS
        ac_gap = ac < (13 if level <= 2 else 15)
        archetype = string_or_empty(merchant_profile.get("merchant_archetype")).strip()

        candidates: list[dict[str, Any]] = []
        for entry in merchant.shop_inventory.rotating_pool:
            item_id = string_or_empty(entry.item_id).strip()
            if item_id not in rotating_ids:
                continue
            item = context.world.items.get(item_id)
            if item is None:
                continue
            if archetype == "blacksmith_like" and item_id in _BLACKSMITH_DISCOURAGED_ITEMS:
                continue
            item_type = self._item_role_type(item)
            if archetype == "blacksmith_like" and item_type not in _BLACKSMITH_IDENTITY_TYPES | {"consumable"}:
                continue
            shop_guardrail_reason = self._shop_progression_guardrail_reason(
                rarity=string_or_empty(getattr(item, "rarity", "")).strip().lower(),
                base_price=self._coerce_item_base_price(item),
                level=level,
            )
            if shop_guardrail_reason is not None:
                continue

            current_row = next(
                (row for row in current_stock if row.get("item_id") == item_id),
                None,
            )
            action = "add"
            if current_row is not None:
                remaining = current_row.get("remaining")
                if remaining is None:
                    continue
                try:
                    remaining_count = int(remaining)
                except (TypeError, ValueError):
                    continue
                if remaining_count > 1:
                    continue
                action = "restock"

            score = 0
            reasons: list[str] = []
            if archetype == "blacksmith_like":
                if item_type in _BLACKSMITH_IDENTITY_TYPES:
                    score += 40
                    reasons.append("merchant_identity")
                elif item_type == "consumable":
                    score += 14
                    reasons.append("secondary_supplies")
            if main_hand_is_starter and item_type == "weapon":
                score += 28
                reasons.append("starter_weapon_gap")
            if ac_gap and item_id in {"dirty_chain_mail", "round_shield", "battered_plate_armor"}:
                score += 26
                reasons.append("ac_gap")
            if inventory_counts.get("healing_potion", 0) == 0 and hp_ratio < 0.85 and item_id == "healing_potion":
                score += 20
                reasons.append("recovery_gap")
            if action == "restock":
                score += 24
                reasons.append("low_stock")
            elif current_type_counts.get(item_type, 0) == 0:
                score += 12
                reasons.append("inventory_gap")

            if score <= 0:
                continue
            candidates.append(
                {
                    "action": action,
                    "item_id": item_id,
                    "recommended_count": self._recommended_shop_count(entry.count),
                    "score": score,
                    "reasons": reasons,
                }
            )

        candidates.sort(
            key=lambda item: (
                -self._coerce_int(item.get("score"), 0),
                str(item.get("item_id", "")),
            )
        )
        return candidates[:3]

    def _filter_agent_directives(
        self,
        context: "SettlementContext",
        event_kind: str,
        directives: Any,
        *,
        quest_id: str | None,
        reward_candidates: list[dict[str, Any]],
        quest_snapshot: Mapping[str, Any] | None,
        player_snapshot: Mapping[str, Any],
        npc_id: str | None,
        shop_candidates: list[dict[str, Any]],
        merchant_profile: Mapping[str, Any],
        available_items: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not isinstance(directives, list):
            return [], {"filtered_directive_count": 0, "rejected_directives": []}
        filtered: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for raw_directive in directives:
            if not isinstance(raw_directive, Mapping):
                continue
            directive = dict(raw_directive)
            kind = string_or_empty(directive.get("kind")).strip()
            payload = normalize_mapping(directive.get("payload"))
            if event_kind in {"quest_created", "quest_accepted"}:
                reward_match = self._match_reward_candidate(
                    context,
                    kind,
                    payload,
                    quest_id=quest_id,
                    reward_candidates=reward_candidates,
                    quest_snapshot=quest_snapshot,
                    player_snapshot=player_snapshot,
                    available_items=available_items,
                )
                if not reward_match["ok"]:
                    rejected.append(reward_match["rejected"])
                    continue
            elif event_kind == "shop_refreshed":
                shop_match = self._match_shop_candidate(
                    context,
                    kind,
                    payload,
                    npc_id=npc_id,
                    shop_candidates=shop_candidates,
                    player_snapshot=player_snapshot,
                    merchant_profile=merchant_profile,
                    available_items=available_items,
                )
                if not shop_match["ok"]:
                    rejected.append(shop_match["rejected"])
                    continue
            filtered.append({"kind": kind, "payload": payload})
        return (
            filtered[:1],
            {
                "filtered_directive_count": len(rejected),
                "rejected_directives": rejected,
            },
        )

    def _match_reward_candidate(
        self,
        context: "SettlementContext",
        kind: str,
        payload: Mapping[str, Any],
        *,
        quest_id: str | None,
        reward_candidates: list[dict[str, Any]],
        quest_snapshot: Mapping[str, Any] | None,
        player_snapshot: Mapping[str, Any],
        available_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload_quest_id = coerce_non_empty_string(payload.get("linked_quest_id"))
        payload_item_id = coerce_non_empty_string(payload.get("item_id"))
        rejected = {
            "kind": kind or "design_reward",
            "reason_code": "candidate_not_allowed",
            "linked_quest_id": payload_quest_id or quest_id or "",
        }
        if payload_item_id is not None:
            rejected["item_id"] = payload_item_id
        if kind != "design_reward" or quest_id is None:
            return {"ok": False, "rejected": rejected}
        if payload_quest_id != quest_id or payload_item_id is None:
            return {"ok": False, "rejected": rejected}
        quantity = self._coerce_int(payload.get("quantity"), 1)
        if any(
            payload_item_id == candidate.get("item_id")
            and quantity == self._coerce_int(candidate.get("quantity"), 1)
            for candidate in reward_candidates
        ):
            return {"ok": True}
        rejected["reason_code"] = self._reward_rejection_reason(
            context,
            payload_item_id,
            quantity=quantity,
            quest_snapshot=quest_snapshot,
            player_snapshot=player_snapshot,
            reward_candidates=reward_candidates,
            available_items=available_items,
        )
        return {"ok": False, "rejected": rejected}

    def _match_shop_candidate(
        self,
        context: "SettlementContext",
        kind: str,
        payload: Mapping[str, Any],
        *,
        npc_id: str | None,
        shop_candidates: list[dict[str, Any]],
        player_snapshot: Mapping[str, Any],
        merchant_profile: Mapping[str, Any],
        available_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload_npc_id = coerce_non_empty_string(payload.get("npc_id"))
        rejected = {
            "kind": kind or "curate_shop",
            "reason_code": "candidate_not_allowed",
            "npc_id": payload_npc_id or npc_id or "",
        }
        if kind != "curate_shop" or npc_id is None:
            return {"ok": False, "rejected": rejected}
        if payload_npc_id != npc_id:
            return {"ok": False, "rejected": rejected}
        candidate_by_action: dict[tuple[str, str, int], dict[str, Any]] = {}
        for candidate in shop_candidates:
            action = string_or_empty(candidate.get("action")).strip()
            item_id = string_or_empty(candidate.get("item_id")).strip()
            count = self._coerce_int(candidate.get("recommended_count"), 1)
            candidate_by_action[(action, item_id, count)] = candidate

        add_items = payload.get("add_items", [])
        restock_items = payload.get("restock_items", [])
        remove_items = payload.get("remove_items", [])
        if remove_items:
            return {"ok": False, "rejected": rejected}
        add_entries = [
            entry for entry in add_items
            if isinstance(entry, Mapping)
            and coerce_non_empty_string(entry.get("item_id")) is not None
        ] if isinstance(add_items, list) else []
        restock_entries = [
            entry for entry in restock_items
            if isinstance(entry, Mapping)
            and coerce_non_empty_string(entry.get("item_id")) is not None
        ] if isinstance(restock_items, list) else []

        if add_entries and restock_entries:
            return {"ok": False, "rejected": rejected}
        if len(add_entries) == 1 and not restock_entries:
            item_id = coerce_non_empty_string(add_entries[0].get("item_id"))
            count = self._coerce_int(add_entries[0].get("count"), 1)
            if (("add", item_id or "", count) in candidate_by_action):
                return {"ok": True}
            if item_id is not None:
                rejected["item_id"] = item_id
            rejected["reason_code"] = self._shop_rejection_reason(
                context,
                item_id,
                player_snapshot=player_snapshot,
                merchant_profile=merchant_profile,
                available_items=available_items,
            )
            return {"ok": False, "rejected": rejected}
        if len(restock_entries) == 1 and not add_entries:
            item_id = coerce_non_empty_string(restock_entries[0].get("item_id"))
            count = self._coerce_int(restock_entries[0].get("count"), 1)
            if (("restock", item_id or "", count) in candidate_by_action):
                return {"ok": True}
            if item_id is not None:
                rejected["item_id"] = item_id
            rejected["reason_code"] = self._shop_rejection_reason(
                context,
                item_id,
                player_snapshot=player_snapshot,
                merchant_profile=merchant_profile,
                available_items=available_items,
            )
            return {"ok": False, "rejected": rejected}
        return {"ok": False, "rejected": rejected}

    @staticmethod
    def _build_available_items(
        context: "SettlementContext",
        *,
        event_kind: str,
        reward_candidates: list[dict[str, Any]],
        shop_candidates: list[dict[str, Any]],
        merchant_profile: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        if not context.world.has_registry("items"):
            return []
        item_ids: list[str] = []
        if event_kind in {"quest_created", "quest_accepted"}:
            item_ids = [
                str(candidate.get("item_id", "")).strip()
                for candidate in reward_candidates
                if str(candidate.get("item_id", "")).strip()
            ]
        elif event_kind == "shop_refreshed":
            item_ids = [
                str(candidate.get("item_id", "")).strip()
                for candidate in shop_candidates
                if str(candidate.get("item_id", "")).strip()
            ]
            allowed_item_ids = merchant_profile.get("allowed_item_ids", [])
            if isinstance(allowed_item_ids, list):
                item_ids.extend(
                    str(item_id).strip()
                    for item_id in allowed_item_ids
                    if str(item_id).strip()
                )
        else:
            return []

        catalog: list[dict[str, Any]] = []
        seen_item_ids: set[str] = set()
        for item_id in item_ids:
            if item_id in seen_item_ids:
                continue
            seen_item_ids.add(item_id)
            item = context.world.items.get(item_id)
            if item is None:
                continue
            catalog.append(
                {
                    "item_id": item.id,
                    "type": item.type,
                    "rarity": item.rarity,
                    "base_price": item.base_price,
                    "tags": list(item.tags),
                    "name": item.name,
                }
            )
        return catalog

    def _reward_candidate_noop_reason(
        self,
        context: "SettlementContext",
        quest_snapshot: Mapping[str, Any],
        *,
        player_snapshot: Mapping[str, Any],
    ) -> str | None:
        existing_reward_items = self._normalize_reward_items(
            normalize_mapping(quest_snapshot.get("rewards")).get("items")
        )
        if existing_reward_items:
            return "quest_already_has_item_reward"
        candidates = self._build_reward_candidates(
            context,
            quest_snapshot,
            player_snapshot=player_snapshot,
        )
        if not candidates:
            return "no_candidates"
        return None

    def _shop_candidate_noop_reason(
        self,
        context: "SettlementContext",
        npc_id: str,
        *,
        player_snapshot: Mapping[str, Any],
        merchant_profile: Mapping[str, Any],
    ) -> str | None:
        rotating_ids = merchant_profile.get("rotating_candidate_ids", [])
        if not isinstance(rotating_ids, list) or not rotating_ids:
            return "merchant_has_no_rotating_candidates"
        shop_state = context.state.relations.shop_states.get(npc_id, {})
        if not isinstance(shop_state, Mapping):
            return "merchant_has_no_rotating_candidates"
        candidates = self._build_shop_candidates(
            context,
            npc_id,
            dict(shop_state),
            player_snapshot=player_snapshot,
            merchant_profile=merchant_profile,
        )
        if not candidates:
            return "no_candidates"
        return None

    def _reward_rejection_reason(
        self,
        context: "SettlementContext",
        item_id: str,
        *,
        quantity: int,
        quest_snapshot: Mapping[str, Any] | None,
        player_snapshot: Mapping[str, Any],
        reward_candidates: list[dict[str, Any]],
        available_items: list[dict[str, Any]],
    ) -> str:
        if not context.world.has_registry("items"):
            return "candidate_not_allowed"
        item = context.world.items.get(item_id)
        if item is None:
            return "unknown_item"
        if quest_snapshot is None:
            return "candidate_not_allowed"
        existing_reward_items = {
            entry.get("item_id")
            for entry in self._normalize_reward_items(
                normalize_mapping(quest_snapshot.get("rewards")).get("items")
            )
        }
        if item_id in existing_reward_items:
            return "quest_already_rewarded"
        item_type = self._item_role_type(item)
        level = self._coerce_int(player_snapshot.get("level"), 1)
        character_class = string_or_empty(player_snapshot.get("character_class")).strip().lower()
        stats = (
            dict(player_snapshot.get("stats", {}))
            if isinstance(player_snapshot.get("stats"), Mapping)
            else {}
        )
        strength = self._coerce_int(stats.get("str"), 10)
        equipped_item_ids = (
            dict(player_snapshot.get("equipped_item_ids", {}))
            if isinstance(player_snapshot.get("equipped_item_ids"), Mapping)
            else {}
        )
        off_hand_item = string_or_empty(equipped_item_ids.get("off_hand")).strip()
        guardrail_reason = self._reward_progression_guardrail_reason(
            item_type=item_type,
            rarity=string_or_empty(getattr(item, "rarity", "")).strip().lower(),
            base_price=self._coerce_item_base_price(item),
            level=level,
        )
        if guardrail_reason is not None:
            return guardrail_reason
        if not self._reward_item_allowed(
            item_id,
            level=level,
            character_class=character_class,
            strength=strength,
            off_hand_item=off_hand_item,
            equipped_item_ids=equipped_item_ids,
            existing_reward_items=existing_reward_items,
        ):
            return "candidate_not_allowed"
        if item_id not in {candidate.get("item_id") for candidate in reward_candidates}:
            if item_id not in {entry.get("item_id") for entry in available_items}:
                return "item_not_in_available_items"
            return "candidate_not_allowed"
        if not any(
            item_id == candidate.get("item_id")
            and quantity == self._coerce_int(candidate.get("quantity"), 1)
            for candidate in reward_candidates
        ):
            return "candidate_not_allowed"
        return "candidate_not_allowed"

    def _shop_rejection_reason(
        self,
        context: "SettlementContext",
        item_id: str | None,
        *,
        player_snapshot: Mapping[str, Any],
        merchant_profile: Mapping[str, Any],
        available_items: list[dict[str, Any]],
    ) -> str:
        if item_id is None:
            return "candidate_not_allowed"
        if not context.world.has_registry("items"):
            return "candidate_not_allowed"
        item = context.world.items.get(item_id)
        if item is None:
            return "unknown_item"
        if item_id not in set(merchant_profile.get("allowed_item_ids", [])):
            return "merchant_profile_rejected"
        archetype = string_or_empty(merchant_profile.get("merchant_archetype")).strip()
        item_type = self._item_role_type(item)
        if archetype == "blacksmith_like":
            if item_id in _BLACKSMITH_DISCOURAGED_ITEMS:
                return "merchant_profile_rejected"
            if item_type not in _BLACKSMITH_IDENTITY_TYPES | {"consumable"}:
                return "merchant_profile_rejected"
        guardrail_reason = self._shop_progression_guardrail_reason(
            rarity=string_or_empty(getattr(item, "rarity", "")).strip().lower(),
            base_price=self._coerce_item_base_price(item),
            level=self._coerce_int(player_snapshot.get("level"), 1),
        )
        if guardrail_reason is not None:
            return guardrail_reason
        if item_id not in {entry.get("item_id") for entry in available_items}:
            return "item_not_in_available_items"
        return "candidate_not_allowed"

    @classmethod
    def _reward_progression_guardrail_reason(
        cls,
        *,
        item_type: str,
        rarity: str,
        base_price: int,
        level: int,
    ) -> str | None:
        if item_type not in _EQUIPMENT_ITEM_TYPES:
            return None
        max_rarity = "common" if level <= 2 else "uncommon"
        max_price = 50 if level <= 2 else (80 if level <= 4 else 200)
        if cls._rarity_rank(rarity) > cls._rarity_rank(max_rarity):
            return "item_rarity_disallowed"
        if base_price > max_price:
            return "item_price_too_high"
        return None

    @classmethod
    def _shop_progression_guardrail_reason(
        cls,
        *,
        rarity: str,
        base_price: int,
        level: int,
    ) -> str | None:
        if cls._rarity_rank(rarity) > cls._rarity_rank("uncommon"):
            return "item_rarity_disallowed"
        max_price = 60 if level <= 2 else (120 if level <= 4 else 200)
        if base_price > max_price:
            return "item_price_too_high"
        return None

    @staticmethod
    def _coerce_item_base_price(item: Any) -> int:
        try:
            return int(getattr(item, "base_price", 0) or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _rarity_rank(rarity: str) -> int:
        return _RARITY_RANKS.get(str(rarity).strip().lower(), 0)

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _reward_item_allowed(
        cls,
        item_id: str,
        *,
        level: int,
        character_class: str,
        strength: int,
        off_hand_item: str,
        equipped_item_ids: Mapping[str, str],
        existing_reward_items: set[Any],
    ) -> bool:
        if item_id in existing_reward_items:
            return False
        if item_id in {"standard_longsword", "scouts_hand_axe", "dirty_chain_mail"} and level <= 2:
            return False
        if item_id == "heavy_war_hammer":
            if level <= 4 or character_class != "fighter" or strength < 14:
                return False
        if item_id == "dirty_chain_mail" and character_class not in _MELEE_DIVINE_CLASSES:
            return False
        if item_id == "round_shield" and bool(off_hand_item):
            return False
        if item_id in set(equipped_item_ids.values()) and item_id not in {
            "healing_potion",
            "basic_antidote",
            "sulfur_smoke_ball",
            "holy_water_flask",
        }:
            return False
        return True

    @staticmethod
    def _contains_token(text: str, tokens: frozenset[str]) -> bool:
        return any(token in text for token in tokens)

    @classmethod
    def _reward_quantity(
        cls,
        item_id: str,
        *,
        level: int,
        hp_ratio: float,
        quest_text: str,
        inventory_counts: Mapping[str, int],
    ) -> int:
        if item_id == "healing_potion":
            if inventory_counts.get("healing_potion", 0) == 0 and level >= 3 and hp_ratio < 0.7:
                return 2
            return 1
        if item_id == "basic_antidote":
            return 2 if cls._contains_token(quest_text, _POISON_TOKENS) else 1
        if item_id == "sulfur_smoke_ball":
            return 2 if cls._contains_token(quest_text, _COMBAT_TOKENS) else 1
        return 1

    @staticmethod
    def _build_quest_text_blob(quest_snapshot: Mapping[str, Any]) -> str:
        parts: list[str] = []
        for key in ("title", "summary", "current_step"):
            value = quest_snapshot.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        for key in ("next_steps", "hints"):
            value = quest_snapshot.get(key)
            if isinstance(value, list):
                parts.extend(str(item).strip() for item in value if str(item).strip())
        objectives = quest_snapshot.get("objectives")
        if isinstance(objectives, list):
            for raw in objectives:
                if not isinstance(raw, Mapping):
                    continue
                for key in ("title", "description", "type", "target"):
                    value = raw.get(key)
                    if isinstance(value, str) and value.strip():
                        parts.append(value.strip())
        return " ".join(parts)

    @classmethod
    def _item_role_type(cls, item: Any) -> str:
        item_type = string_or_empty(getattr(item, "type", "")).strip().lower()
        armor_data = getattr(item, "armor_data", None)
        if item_type == "armor" and armor_data is not None:
            armor_type = string_or_empty(getattr(armor_data, "armor_type", "")).strip().lower()
            if armor_type == "shield":
                return "shield"
        return item_type

    @classmethod
    def _recommended_shop_count(cls, raw_count: Any) -> int:
        if isinstance(raw_count, int):
            return max(1, raw_count)
        if isinstance(raw_count, str):
            stripped = raw_count.strip().lower()
            if stripped.isdigit():
                return max(1, int(stripped))
            if stripped == "unlimited":
                return 1
        return 1
