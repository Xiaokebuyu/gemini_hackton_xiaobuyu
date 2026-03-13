"""EncounterHandler implementation."""

from __future__ import annotations

import random
from typing import Any, Mapping

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import (
    get_non_empty_string,
    handler_success,
    handler_success_no_delta,
    roll_damage_dice,
)
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer


class EncounterHandler(StaticCommandHandler):
    COMMAND_TYPES = ("encounter_check", "generate_loot", "clear_hostile")

    _PERIOD_MULTIPLIERS = {
        "dawn": 0.8,
        "day": 0.5,
        "dusk": 1.0,
        "night": 1.5,
    }

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if cmd.type == "encounter_check":
            return self._validate_encounter_check(cmd, state, world)
        if cmd.type == "generate_loot":
            return self._validate_generate_loot(cmd, state, world)
        if cmd.type == "clear_hostile":
            return self._validate_clear_hostile(cmd, state)
        return ValidationResult(ok=False, reason=f"unsupported command: {cmd.type}")

    def compute(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        validation = self.validate(cmd, state, world)
        if not validation.ok:
            return ExecuteResult.error(validation.reason or "validation failed")

        if cmd.type == "encounter_check":
            return self._compute_encounter_check(cmd, state)
        if cmd.type == "generate_loot":
            return self._compute_generate_loot(cmd, state, world)
        if cmd.type == "clear_hostile":
            return self._compute_clear_hostile(cmd, state)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _validate_encounter_check(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice is required")

        area_id = get_non_empty_string(cmd.params, "area_id")
        if area_id is None:
            return ValidationResult(ok=False, reason="area_id must be a non-empty string")
        if not self._area_exists(state, world, area_id):
            return ValidationResult(ok=False, reason=f"unknown area: {area_id}")

        period = get_non_empty_string(cmd.params, "period")
        if period is None:
            return ValidationResult(ok=False, reason="period must be a non-empty string")
        if period not in self._PERIOD_MULTIPLIERS:
            return ValidationResult(ok=False, reason=f"unsupported period: {period}")

        if "force_triggered" in cmd.params and not isinstance(
            cmd.params.get("force_triggered"),
            bool,
        ):
            return ValidationResult(ok=False, reason="force_triggered must be a boolean")

        if "sub_area_id" in cmd.params:
            sub_area_id = self._get_optional_non_empty_string(cmd.params.get("sub_area_id"))
            if sub_area_id is None and cmd.params.get("sub_area_id") is not None:
                return ValidationResult(
                    ok=False,
                    reason="sub_area_id must be a non-empty string or null",
                )

        if "source" in cmd.params:
            source = self._get_optional_non_empty_string(cmd.params.get("source"))
            if source is None:
                return ValidationResult(
                    ok=False,
                    reason="source must be a non-empty string",
                )

        if "template_id" in cmd.params:
            template_id = self._get_optional_non_empty_string(cmd.params.get("template_id"))
            if template_id is None:
                return ValidationResult(
                    ok=False,
                    reason="template_id must be a non-empty string",
                )

        if "monster_ids" in cmd.params and self._normalize_monster_ids(
            cmd.params.get("monster_ids")
        ) is None:
            return ValidationResult(
                ok=False,
                    reason="monster_ids must be a list of non-empty values",
                )

        if "map_category" in cmd.params:
            map_category = self._get_optional_non_empty_string(cmd.params.get("map_category"))
            if map_category is None and cmd.params.get("map_category") is not None:
                return ValidationResult(
                    ok=False,
                    reason="map_category must be a non-empty string or null",
                )

        return ValidationResult(ok=True)

    def _validate_generate_loot(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        raw_monster_ids = cmd.params.get("monster_ids")
        monster_ids = self._normalize_monster_ids(raw_monster_ids)
        if monster_ids is None:
            return ValidationResult(ok=False, reason="monster_ids must be a list of non-empty values")

        if "area_id" in cmd.params:
            area_id = self._get_optional_non_empty_string(cmd.params.get("area_id"))
            if area_id is None:
                return ValidationResult(
                    ok=False,
                    reason="area_id must be a non-empty string",
                )
            if world.has_registry("maps") and world.maps.get(area_id) is None:
                return ValidationResult(ok=False, reason=f"unknown area: {area_id}")

        del monster_ids
        return ValidationResult(ok=True)

    def _validate_clear_hostile(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        if not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice is required")
        sub_area_id = get_non_empty_string(cmd.params, "sub_area_id")
        if sub_area_id is None:
            return ValidationResult(ok=False, reason="sub_area_id must be a non-empty string")
        return ValidationResult(ok=True)

    def _compute_encounter_check(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        area_id = str(cmd.params["area_id"]).strip()
        period = str(cmd.params["period"]).strip()
        danger = float(state.areas.get_danger(area_id))
        period_multiplier = self._period_multiplier(period)
        trigger_score = danger * period_multiplier

        if "force_triggered" in cmd.params:
            triggered = bool(cmd.params["force_triggered"])
        else:
            triggered = trigger_score >= 1.0

        base_metadata = {
            "area_id": area_id,
            "period": period,
            "danger": danger,
            "period_multiplier": period_multiplier,
            "trigger_score": trigger_score,
            "triggered": triggered,
        }
        if not triggered:
            return handler_success_no_delta(
                "encounter",
                "encounter_check",
                metadata={
                    **base_metadata,
                    "status": "checked",
                    "reason": "danger_below_threshold",
                },
            )

        source = self._get_optional_non_empty_string(cmd.params.get("source")) or "encounter"
        template_id = self._get_optional_non_empty_string(cmd.params.get("template_id"))
        monster_ids = self._normalize_monster_ids(cmd.params.get("monster_ids")) or []
        monster_count = len(monster_ids)
        threat_level = self._get_optional_non_empty_string(cmd.params.get("threat_level")) or (
            self._threat_level_for(danger, monster_count)
        )
        stealth_dc = self._coerce_int(cmd.params.get("stealth_dc"))
        if stealth_dc is None:
            stealth_dc = self._default_stealth_dc(threat_level)
        description = (
            self._get_optional_non_empty_string(cmd.params.get("description"))
            or f"A hostile group stirs in {area_id}."
        )
        map_category = self._get_optional_non_empty_string(cmd.params.get("map_category"))
        name = (
            self._get_optional_non_empty_string(cmd.params.get("name"))
            or description
        )
        sub_area_id = self._resolve_sub_area_id(cmd, state, area_id)
        current_tick = state.time.absolute_tick() if state.has_slice("time") else None
        blocking = danger >= 0.6
        temporary_sub_areas = self._upsert_temporary_sub_area(
            state,
            area_id=area_id,
            sub_area_id=sub_area_id,
            payload={
                "id": sub_area_id,
                "name": name,
                "label": name,
                "type": "encounter",
                "source": source,
                "temporary": True,
                "hostile": True,
                "blocking": blocking,
                "threat_level": threat_level,
                "description": description,
                "expiry": 6,
            },
        )
        hostile_state = {
            "area_id": area_id,
            "sub_area_id": sub_area_id,
            "source": source,
            "status": "spotted",
            "cleared": False,
            "combat_active": False,
            "created_at_tick": current_tick,
            "period": period,
            "danger_snapshot": danger,
            "blocking": blocking,
            "monster_ids": list(monster_ids),
            "monster_count": monster_count,
            "threat_level": threat_level,
            "stealth_dc": stealth_dc,
            "name": name,
            "description": description,
            "last_stealth_result": None,
            "last_stealth_choice": None,
            "entry_mode": None,
        }
        if template_id is not None:
            hostile_state["template_id"] = template_id
        if map_category is not None:
            hostile_state["map_category"] = map_category
        return handler_success(
            "encounter",
            "encounter_check",
            changes=[
                StateChange(
                    "areas",
                    "modify",
                    f"{area_id}.temporary_sub_areas",
                    temporary_sub_areas,
                ),
                StateChange(
                    "areas",
                    "modify",
                    f"hostile_tracking.{sub_area_id}",
                    hostile_state,
                ),
            ],
            metadata={
                **base_metadata,
                "status": "triggered",
                "sub_area_id": sub_area_id,
                "blocking": blocking,
                "source": source,
                "monster_ids": list(monster_ids),
                "monster_count": monster_count,
                "threat_level": threat_level,
                "stealth_dc": stealth_dc,
                "name": name,
                "description": description,
                **({"map_category": map_category} if map_category is not None else {}),
                **({"template_id": template_id} if template_id is not None else {}),
            },
        )

    def _compute_clear_hostile(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        sub_area_id = str(cmd.params["sub_area_id"]).strip()
        existing = state.areas.get_hostile_state(sub_area_id)
        if existing is None:
            return handler_success_no_delta(
                "encounter",
                "clear_hostile",
                metadata={
                    "status": "noop",
                    "cleared": False,
                    "sub_area_id": sub_area_id,
                },
            )

        area_id = self._get_optional_non_empty_string(existing.get("area_id"))
        if area_id is None:
            area_id = state.areas.find_hostile_area(sub_area_id)
        if area_id is None:
            return ExecuteResult.error("hostile state missing area_id")

        updated = state.areas.copy_hostile_state(existing)
        updated["area_id"] = area_id
        updated = state.areas.build_cleared_hostile(
            updated,
            current_tick=(
                state.time.absolute_tick() if state.has_slice("time") else None
            ),
        )
        temporary_sub_areas = self._clear_temporary_sub_area(
            state,
            area_id=area_id,
            sub_area_id=sub_area_id,
        )

        return handler_success(
            "encounter",
            "clear_hostile",
            changes=[
                StateChange(
                    "areas",
                    "modify",
                    f"{area_id}.temporary_sub_areas",
                    temporary_sub_areas,
                ),
                StateChange(
                    "areas",
                    "modify",
                    f"hostile_tracking.{sub_area_id}",
                    updated,
                ),
            ],
            metadata={
                "status": "cleared",
                "cleared": True,
                "sub_area_id": sub_area_id,
            },
        )

    def _compute_generate_loot(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        monster_ids = self._normalize_monster_ids(cmd.params.get("monster_ids")) or []
        gold = 0
        items: list[dict[str, Any]] = []
        processed_monster_count = 0
        unknown_monster_count = 0

        if not world.has_registry("monsters"):
            unknown_monster_count = len(monster_ids)
        else:
            for monster_id in monster_ids:
                monster = world.monsters.get(monster_id)
                if monster is None:
                    unknown_monster_count += 1
                    continue
                processed_monster_count += 1
                gold += self._resolve_gold(monster)
                items.extend(self._resolve_loot_items(monster, world))

        loot = {
            "gold": gold,
            "items": items,
            "source": (
                f"{len(monster_ids)} defeated enemies"
                if monster_ids
                else "encounter loot"
            ),
        }
        metadata = {
            "status": "generated" if gold > 0 or items else "empty",
            "loot": loot,
            "processed_monster_count": processed_monster_count,
            "unknown_monster_count": unknown_monster_count,
        }

        if gold <= 0:
            return handler_success_no_delta("encounter", "generate_loot", metadata=metadata)

        return handler_success(
            "encounter",
            "generate_loot",
            changes=[
                StateChange("player", "add", "gold", gold),
            ],
            metadata=metadata,
        )

    def _resolve_gold(self, monster: Any) -> int:
        raw = monster.gold_drop
        if not raw or raw == "0":
            return 0
        try:
            return max(0, int(raw))
        except (ValueError, TypeError):
            return max(0, roll_damage_dice(raw))

    def _resolve_loot_items(
        self,
        monster: Any,
        world: WorldInstance,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for loot_entry in monster.loot_table:
            if not loot_entry.item_id:
                continue

            if loot_entry.chance < 1.0:
                if random.random() >= loot_entry.chance:
                    continue

            raw_count = loot_entry.count
            try:
                count = int(raw_count)
            except (ValueError, TypeError):
                count = roll_damage_dice(raw_count) if raw_count else 1
            if count <= 0:
                continue

            item_name = loot_entry.item_id
            rarity = "common"
            if world.has_registry("items"):
                item_template = world.items.get(loot_entry.item_id)
                if item_template is not None:
                    item_name = item_template.name or loot_entry.item_id
                    rarity = item_template.rarity or "common"

            items.append(
                {
                    "item_id": loot_entry.item_id,
                    "name": item_name,
                    "count": count,
                    "rarity": rarity,
                }
            )
        return items

    def _area_exists(
        self,
        state: StateContainer,
        world: WorldInstance,
        area_id: str,
    ) -> bool:
        if world.has_registry("maps"):
            return world.maps.get(area_id) is not None
        return area_id in state.areas.areas

    def _upsert_temporary_sub_area(
        self,
        state: StateContainer,
        *,
        area_id: str,
        sub_area_id: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        updated: list[dict[str, Any]] = []
        replaced = False
        for item in state.areas.list_temporary_sub_areas(area_id):
            candidate_id = self._get_optional_non_empty_string(item.get("id"))
            if candidate_id != sub_area_id:
                updated.append(dict(item))
                continue
            updated.append(dict(payload))
            replaced = True
        if not replaced:
            updated.append(dict(payload))
        return updated

    def _clear_temporary_sub_area(
        self,
        state: StateContainer,
        *,
        area_id: str,
        sub_area_id: str,
    ) -> list[dict[str, Any]]:
        updated: list[dict[str, Any]] = []
        for item in state.areas.list_temporary_sub_areas(area_id):
            candidate_id = self._get_optional_non_empty_string(item.get("id"))
            if candidate_id != sub_area_id:
                updated.append(dict(item))
                continue
            cleared = dict(item)
            cleared["hostile"] = False
            cleared["blocking"] = False
            cleared["cleared"] = True
            updated.append(cleared)
        return updated

    @classmethod
    def _period_multiplier(cls, period: str) -> float:
        return cls._PERIOD_MULTIPLIERS[period]

    def _resolve_sub_area_id(
        self,
        cmd: Command,
        state: StateContainer,
        area_id: str,
    ) -> str:
        provided = self._get_optional_non_empty_string(cmd.params.get("sub_area_id"))
        if provided is not None:
            return provided
        if state.has_slice("time"):
            return f"_encounter_{area_id}_{state.time.absolute_tick()}"
        return f"_encounter_{area_id}_static"

    @staticmethod
    def _get_optional_non_empty_string(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return int(value)
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        if isinstance(value, bool):
            return float(value)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _normalize_monster_ids(self, value: Any) -> list[str] | None:
        if not isinstance(value, list):
            return None
        monster_ids: list[str] = []
        for raw_item in value:
            normalized = str(raw_item).strip()
            if not normalized:
                return None
            monster_ids.append(normalized)
        return monster_ids

    @staticmethod
    def _threat_level_for(danger: float, monster_count: int) -> str:
        score = max(float(danger), monster_count * 0.35)
        if score >= 1.45:
            return "deadly"
        if score >= 1.0:
            return "hard"
        if score >= 0.55:
            return "moderate"
        return "easy"

    @staticmethod
    def _default_stealth_dc(threat_level: str) -> int:
        return {
            "easy": 10,
            "moderate": 12,
            "hard": 14,
            "deadly": 16,
        }.get(threat_level, 12)
