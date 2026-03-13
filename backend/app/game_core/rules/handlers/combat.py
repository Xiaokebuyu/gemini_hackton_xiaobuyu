"""CombatHandler implementation."""

from __future__ import annotations

import random
from typing import Any, Mapping

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.battle_grid import BattleGrid, compute_environment_modifiers
from app.game_core.rules.handler_utils import (
    build_dice_roll,
    coerce_non_empty_string,
    handler_success,
    handler_success_no_delta,
    roll_damage_dice,
)
from app.game_core.rules.battle_ai import MonsterDecision, decide_monster_turn, validate_decision
from app.game_core.rules.combat_units import (
    assign_positions,
    assign_positions_from_spawns,
    build_companion_unit,
    build_default_grid,
    build_monster_unit,
    build_player_unit,
    build_turn_order,
    compute_attack_ability_mod,
    resolve_surprise,
)
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer


def _participant_effect_ac_mod(participant: Mapping[str, Any]) -> int:
    """从战斗参与者的 active_effects 中提取 AC 修正总量。"""
    total = 0
    for effect in participant.get("active_effects", []):
        mods = effect.get("modifiers")
        if isinstance(mods, Mapping):
            raw = mods.get("ac", 0)
            try:
                total += int(raw)
            except (TypeError, ValueError):
                pass
    return total


class CombatHandler(StaticCommandHandler):
    COMMAND_TYPES = (
        "start_combat",
        "combat_move",
        "combat_end_turn",
        "combat_disengage",
        "combat_dash",
        "combat_attack",
        "combat_defend",
        "combat_npc_turn",
        "combat_finalize_status",
    )

    _SURPRISE_STATES = frozenset({"none", "player_surprise", "enemy_surprise"})

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if cmd.type == "start_combat":
            return self._validate_start_combat(cmd, state, world)
        if cmd.type == "combat_npc_turn":
            return self._validate_combat_npc_turn(cmd, state, world)
        if cmd.type == "combat_finalize_status":
            return self._validate_combat_finalize_status(cmd, state)
        if cmd.type in {"combat_move", "combat_end_turn", "combat_disengage", "combat_dash",
                        "combat_attack", "combat_defend"}:
            return self._validate_v2_command(cmd, state, world)
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

        if cmd.type == "start_combat":
            return self._compute_start_combat(cmd, state, world)
        if cmd.type == "combat_move":
            return self._compute_combat_move(cmd, state, world)
        if cmd.type == "combat_end_turn":
            return self._compute_combat_end_turn(cmd, state, world)
        if cmd.type == "combat_disengage":
            return self._compute_combat_disengage(cmd, state)
        if cmd.type == "combat_dash":
            return self._compute_combat_dash(cmd, state)
        if cmd.type == "combat_attack":
            return self._compute_combat_attack(cmd, state, world)
        if cmd.type == "combat_defend":
            return self._compute_combat_defend(cmd, state)
        if cmd.type == "combat_npc_turn":
            return self._compute_combat_npc_turn(cmd, state, world)
        if cmd.type == "combat_finalize_status":
            return self._compute_combat_finalize_status(cmd, state)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _validate_combat_finalize_status(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        if cmd.source not in {"engine", "system"}:
            return ValidationResult(
                ok=False,
                reason="combat_finalize_status is restricted to engine/system",
            )
        if not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice is required")
        sub_area_id = coerce_non_empty_string(cmd.params.get("sub_area_id"))
        if sub_area_id is None:
            return ValidationResult(ok=False, reason="sub_area_id must be a non-empty string")
        status = coerce_non_empty_string(cmd.params.get("status"))
        if status is None:
            return ValidationResult(ok=False, reason="status must be a non-empty string")
        payload = state.areas.get_hostile_state(sub_area_id)
        if payload is None:
            return ValidationResult(ok=False, reason=f"unknown hostile sub area: {sub_area_id}")
        return ValidationResult(ok=True)

    def _validate_start_combat(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if cmd.source not in {"engine", "system"}:
            return ValidationResult(ok=False, reason="start_combat is restricted to engine/system")
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        if not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice is required")
        if not world.has_registry("monsters"):
            return ValidationResult(ok=False, reason="monsters registry is required")

        area_id = self._resolve_area_id(cmd.params, state)
        if area_id is None:
            return ValidationResult(ok=False, reason="area_id must be available")
        if not self._area_exists(area_id, state, world):
            return ValidationResult(ok=False, reason=f"unknown area: {area_id}")

        surprise_state = coerce_non_empty_string(cmd.params.get("surprise_state")) or "none"
        if surprise_state not in self._SURPRISE_STATES:
            return ValidationResult(ok=False, reason=f"unsupported surprise_state: {surprise_state}")

        explicit_sub_area = coerce_non_empty_string(cmd.params.get("sub_area_id"))
        if "sub_area_id" in cmd.params and explicit_sub_area is None:
            return ValidationResult(ok=False, reason="sub_area_id must be a non-empty string")

        monster_ids = self._normalize_monster_ids(cmd.params.get("monsters"))
        if cmd.params.get("monsters") is not None and not monster_ids:
            return ValidationResult(ok=False, reason="monsters must be a non-empty list")

        if not monster_ids:
            monster_ids = self._resolve_monsters_from_existing_hostile(
                area_id,
                explicit_sub_area,
                state,
            )
            if not monster_ids:
                return ValidationResult(
                    ok=False,
                    reason="monsters are required when no hostile monster_ids are available",
                )

        for monster_id in monster_ids:
            if world.monsters.get(monster_id) is None:
                return ValidationResult(ok=False, reason=f"unknown monster: {monster_id}")

        # D-5: unit count cap (W3-7)
        _MAX_ENEMY_UNITS = 5
        if len(monster_ids) > _MAX_ENEMY_UNITS:
            return ValidationResult(
                ok=False,
                reason=f"too many enemies: {len(monster_ids)}, max {_MAX_ENEMY_UNITS}",
            )

        return ValidationResult(ok=True)

    def _compute_start_combat(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        area_id = self._resolve_area_id(cmd.params, state) or ""
        explicit_sub_area = coerce_non_empty_string(cmd.params.get("sub_area_id"))
        surprise_state = coerce_non_empty_string(cmd.params.get("surprise_state")) or "none"

        existing_sub_area = explicit_sub_area
        if existing_sub_area is None:
            uncleared_hostiles = self._uncleared_hostiles_in_area(area_id, state)
            if len(uncleared_hostiles) == 1:
                existing_sub_area = uncleared_hostiles[0][0]

        if existing_sub_area is None:
            if state.has_slice("time"):
                existing_sub_area = f"_combat_{area_id}_{state.time.absolute_tick()}"
            else:
                existing_sub_area = f"_combat_{area_id}_static"

        existing_payload = self._hostile_payload_for(existing_sub_area, area_id, state)
        if existing_payload and bool(existing_payload.get("combat_active")):
            return handler_success_no_delta(
                "combat",
                "start_combat",
                metadata={
                    "status": "already_active",
                    "area_id": area_id,
                    "sub_area_id": existing_sub_area,
                    "monster_count": len(existing_payload.get("monster_ids", []))
                    if isinstance(existing_payload.get("monster_ids"), list)
                    else 0,
                    "surprise_state": coerce_non_empty_string(existing_payload.get("surprise_state")) or surprise_state,
                    "combat_round": int(existing_payload.get("combat_round", 0)),
                },
            )

        monster_ids = self._normalize_monster_ids(cmd.params.get("monsters"))
        if not monster_ids and existing_payload:
            monster_ids = self._normalize_monster_ids(existing_payload.get("monster_ids"))
        existing_payload = existing_payload or {}

        # --- v2 unit construction ---

        # 1. Build units list starting with player
        units: list[dict[str, Any]] = [build_player_unit(state, world)]

        # Companions
        if state.has_slice("party") and world.has_registry("characters"):
            for char_id, member_data in state.party.members.items():
                tmpl = world.characters.get(char_id)
                if tmpl is not None:
                    companion = build_companion_unit(char_id, member_data, tmpl)
                    if companion is not None:
                        units.append(companion)

        # Monsters
        seen: dict[str, int] = {}
        for mid in monster_ids:
            seen[mid] = seen.get(mid, 0) + 1
            tmpl = world.monsters.get(mid)
            if tmpl is not None:
                units.append(build_monster_unit(mid, tmpl, seen[mid]))
            else:
                # Fallback for unknown monster templates
                units.append({
                    "unit_id": f"{mid}_{seen[mid]}",
                    "side": "enemy",
                    "source": "monster",
                    "monster_id": mid,
                    "character_id": None,
                    "name": mid,
                    "hp": 10,
                    "max_hp": 10,
                    "ac": 10,
                    "stats": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
                    "speed": 3,
                    "position": None,
                    "alive": True,
                    "fled": False,
                    "active_effects": [],
                    "attacks": [{"name": "Slam", "hit_bonus": 0, "damage_dice": "1d4",
                                 "damage_type": "bludgeoning", "range": 1, "tags": ["MELEE"]}],
                    "ai_personality": "aggressive",
                    "flee_threshold": 0.0,
                    "flee_chance": 0.5,
                    "action_used": False,
                    "move_used": False,
                    "disengaged": False,
                    "dashed": False,
                    "defending": False,
                    "reaction_used": False,
                    "surprised": False,
                    "proficiency_bonus": 2,
                })

        # 2. Select battle map or build default grid
        map_variant = None
        map_category = cmd.params.get("map_category")
        if map_category is None and existing_payload:
            map_category = existing_payload.get("map_category")
        map_tags = cmd.params.get("map_tags")
        if map_tags is None and existing_payload:
            map_tags = existing_payload.get("map_tags")
        if world is not None and world.has_registry("battle_maps"):
            if map_category:
                map_variant = world.battle_maps.select_variant(str(map_category))
            elif map_tags and isinstance(map_tags, list):
                map_variant = world.battle_maps.select_by_tags(map_tags)

        # 3. Assign positions
        if map_variant is not None:
            grid = {
                "width": map_variant.width,
                "height": map_variant.height,
                "terrain": list(map_variant.terrain),
            }
            assign_positions_from_spawns(
                units, map_variant.player_spawn, map_variant.enemy_spawn
            )
        else:
            grid = build_default_grid()
            assign_positions(units, grid["width"], grid["height"])

        # 4. Surprise resolution — extract stealth_total from payload or cmd params
        stealth_total = 0
        if existing_payload:
            lsr = existing_payload.get("last_stealth_result")
            if isinstance(lsr, dict):
                stealth_total = int(lsr.get("roll", 0)) + int(lsr.get("modifier", 0))
        stealth_total = int(cmd.params.get("stealth_total", stealth_total))
        resolve_surprise(units, surprise_state, stealth_total)

        # 5. Initiative and turn order
        turn_order, initiative_rolls = build_turn_order(units)

        # 6. Combat round: 0 if any unit is surprised (surprise round), else 1
        combat_round = 0 if any(u["surprised"] for u in units) else 1

        # 7. Assemble combat seed with v2 fields
        combat_seed = {
            **existing_payload,
            "area_id": area_id,
            "blocking": bool(existing_payload.get("blocking", True)),
            "combat_round": combat_round,
            "surprise_state": surprise_state,
            "combat_started_at_tick": state.time.absolute_tick() if state.has_slice("time") else None,
            "monster_ids": list(monster_ids),
            # v2 new fields
            "version": 2,
            "grid": grid,
            "units": units,
            "turn_order": turn_order,
            "initiative_rolls": initiative_rolls,
            "current_turn_index": 0,
            "current_unit_id": turn_order[0] if turn_order else "",
            "environment": {
                "time_of_day": state.time.period if state.has_slice("time") else "day",
                "weather": "clear",
            },
        }

        # 8. Build normalized combat payload via area slice
        combat_payload, _, _ = state.areas.build_combat_hostile(
            combat_seed,
            [],
            blocking=bool(existing_payload.get("blocking", True)),
            player_flags={},
            current_tick=self._current_tick(state),
        )

        return handler_success(
            "combat",
            "start_combat",
            changes=[
                StateChange(
                    "areas",
                    "modify",
                    f"hostile_tracking.{existing_sub_area}",
                    combat_payload,
                )
            ],
            metadata={
                "status": "started",
                "area_id": area_id,
                "sub_area_id": existing_sub_area,
                "monster_count": len(monster_ids),
                "surprise_state": surprise_state,
                "combat_round": combat_round,
            },
            omit_empty_delta=False,
        )

    def _resolve_active_combat(
        self,
        params: Mapping[str, Any],
        state: StateContainer,
        world: WorldInstance | None,
    ) -> tuple[str, dict[str, Any], str] | None:
        area_id = self._resolve_area_id(params, state)
        explicit_area = coerce_non_empty_string(params.get("area_id"))
        if explicit_area is not None and state.player.current_area and explicit_area != state.player.current_area:
            return None
        if area_id is None:
            return None
        if world is not None and not self._area_exists(area_id, state, world):
            return None

        sub_area_id = coerce_non_empty_string(params.get("sub_area_id"))
        if sub_area_id is not None:
            payload = self._hostile_payload_for(sub_area_id, area_id, state)
            if payload is None or not bool(payload.get("combat_active")):
                return None
            return (sub_area_id, payload, area_id)

        active = [
            (candidate_id, payload)
            for candidate_id, payload in self._active_hostiles_in_area(area_id, state)
            if bool(payload.get("combat_active"))
        ]
        if len(active) != 1:
            return None
        return (active[0][0], active[0][1], area_id)

    def _resolve_area_id(
        self,
        params: Mapping[str, Any],
        state: StateContainer,
    ) -> str | None:
        explicit = coerce_non_empty_string(params.get("area_id"))
        current = coerce_non_empty_string(state.player.current_area)
        if explicit is not None:
            if current is not None and explicit != current:
                return None
            return explicit
        return current

    def _resolve_monsters_from_existing_hostile(
        self,
        area_id: str,
        explicit_sub_area: str | None,
        state: StateContainer,
    ) -> list[str]:
        if explicit_sub_area is not None:
            payload = self._hostile_payload_for(explicit_sub_area, area_id, state)
            if payload is None:
                return []
            return self._normalize_monster_ids(payload.get("monster_ids"))
        uncleared = self._uncleared_hostiles_in_area(area_id, state)
        if len(uncleared) != 1:
            return []
        return self._normalize_monster_ids(uncleared[0][1].get("monster_ids"))

    def _area_exists(
        self,
        area_id: str,
        state: StateContainer,
        world: WorldInstance,
    ) -> bool:
        if world.has_registry("maps"):
            return world.maps.get(area_id) is not None
        return area_id in state.areas.areas

    def _uncleared_hostiles_in_area(
        self,
        area_id: str,
        state: StateContainer,
    ) -> list[tuple[str, dict[str, Any]]]:
        return [
            (sub_area_id, dict(payload))
            for sub_area_id, payload in self._hostiles_in_area(area_id, state)
            if not bool(payload.get("cleared", False))
        ]

    def _active_hostiles_in_area(
        self,
        area_id: str,
        state: StateContainer,
    ) -> list[tuple[str, dict[str, Any]]]:
        return [
            (sub_area_id, payload)
            for sub_area_id, payload in self._hostiles_in_area(area_id, state)
            if not bool(payload.get("cleared", False))
        ]

    def _hostiles_in_area(
        self,
        area_id: str,
        state: StateContainer,
    ) -> list[tuple[str, dict[str, Any]]]:
        area = state.areas.areas.get(area_id)
        if area is None:
            return []
        hostiles: list[tuple[str, dict[str, Any]]] = []
        for sub_area_id, payload in area.hostile_tracking.items():
            if isinstance(payload, Mapping):
                hostiles.append((str(sub_area_id), state.areas.copy_hostile_state(payload)))
        return hostiles

    def _hostile_payload_for(
        self,
        sub_area_id: str,
        area_id: str,
        state: StateContainer,
    ) -> dict[str, Any] | None:
        payload = state.areas.get_hostile_state(sub_area_id)
        if not isinstance(payload, Mapping):
            return None
        resolved_area = coerce_non_empty_string(payload.get("area_id"))
        if resolved_area is not None and resolved_area != area_id:
            return None
        normalized = state.areas.copy_hostile_state(payload)
        normalized["area_id"] = area_id
        return normalized

    @staticmethod
    def _current_tick(state: StateContainer) -> int | None:
        if not state.has_slice("time"):
            return None
        return state.time.absolute_tick()

    @staticmethod
    def _apply_damage_resistance(
        damage: int,
        damage_type: str,
        state: StateContainer,
    ) -> int:
        """检查玩家 active_effects 的 tags，应用 immunity/resistance/vulnerability。"""
        for effect in state.player.get_active_effects():
            tags = effect.get("tags", [])
            if f"{damage_type}_immunity" in tags:
                return 0
            if f"{damage_type}_resistance" in tags:
                return max(1, damage // 2)
            if f"{damage_type}_vulnerability" in tags:
                return damage * 2
        return damage

    def _compute_combat_xp(
        self,
        participants: list[dict[str, Any]],
        world: WorldInstance,
    ) -> int:
        """Sum xp_reward for killed monsters (dead but not fled)."""
        if not world.has_registry("monsters"):
            return 0
        total = 0
        for p in participants:
            if bool(p.get("alive", True)):
                continue  # still alive
            if bool(p.get("fled", False)):
                continue  # fled, no XP
            monster_id = str(p.get("monster_id", ""))
            template = world.monsters.get(monster_id)
            if template is not None:
                total += int(getattr(template, "xp_reward", 0) or 0)
        return total

    def _compute_combat_loot(
        self,
        participants: list[dict[str, Any]],
        world: WorldInstance,
    ) -> tuple[list[tuple[str, int]], int]:
        """Roll loot_table + gold_drop for killed monsters."""
        items: list[tuple[str, int]] = []
        gold_total = 0
        if not world.has_registry("monsters"):
            return items, gold_total
        for p in participants:
            if bool(p.get("alive", True)) or bool(p.get("fled", False)):
                continue
            monster_id = str(p.get("monster_id", ""))
            template = world.monsters.get(monster_id)
            if template is None:
                continue
            for entry in getattr(template, "loot_table", None) or []:
                chance = float(getattr(entry, "chance", 0))
                if random.random() < chance:
                    item_id = str(getattr(entry, "item_id", ""))
                    count_expr = str(getattr(entry, "count", "1"))
                    count = max(1, roll_damage_dice(count_expr))
                    if item_id:
                        items.append((item_id, count))
            gold_expr = getattr(template, "gold_drop", None)
            if gold_expr and str(gold_expr).strip() not in ("", "0"):
                gold_total += max(0, roll_damage_dice(str(gold_expr)))
        return items, gold_total

    @staticmethod
    def _normalize_monster_ids(raw_value: Any) -> list[str]:
        if not isinstance(raw_value, list):
            return []
        monster_ids: list[str] = []
        for item in raw_value:
            monster_id = coerce_non_empty_string(item)
            if monster_id is not None:
                monster_ids.append(monster_id)
        return monster_ids

    # ------------------------------------------------------------------
    # V2 SRPG commands（移动、回合推进、脱离、冲刺）
    # ------------------------------------------------------------------

    @staticmethod
    def _find_unit(units: list[dict[str, Any]], unit_id: str) -> dict[str, Any] | None:
        """在 units 列表中按 unit_id 查找。"""
        for u in units:
            if u.get("unit_id") == unit_id:
                return u
        return None

    @staticmethod
    def _is_unit_active(unit: dict[str, Any]) -> bool:
        """存活且未逃跑。"""
        return bool(unit.get("alive")) and not bool(unit.get("fled"))

    def _resolve_v2_combat(
        self,
        params: Mapping[str, Any],
        state: StateContainer,
        world: WorldInstance | None = None,
    ) -> tuple[str, dict[str, Any], str] | None:
        """解析 v2 战斗状态。返回 (sub_area_id, payload, area_id) 或 None。"""
        resolved = self._resolve_active_combat(params, state, world)
        if resolved is None:
            return None
        sub_area_id, payload, area_id = resolved
        if payload.get("version") != 2:
            return None
        return (sub_area_id, payload, area_id)

    def _validate_v2_command(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        """所有 v2 命令的通用验证：v2 payload 存在 + 战斗激活。"""
        if not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice is required")
        resolved = self._resolve_v2_combat(cmd.params, state, world)
        if resolved is None:
            return ValidationResult(ok=False, reason="active v2 combat not found")
        _, payload, _ = resolved
        # 对需要当前回合验证的命令，检查 unit_id
        if cmd.type in {"combat_move", "combat_disengage", "combat_dash", "combat_attack", "combat_defend"}:
            current_uid = payload.get("current_unit_id", "")
            unit = self._find_unit(payload.get("units", []), current_uid)
            if unit is None:
                return ValidationResult(ok=False, reason="current unit not found")
            if cmd.type == "combat_move" and unit.get("move_used"):
                return ValidationResult(ok=False, reason="unit has already moved this turn")
            if cmd.type in {"combat_disengage", "combat_dash", "combat_attack", "combat_defend"} and unit.get("action_used"):
                return ValidationResult(ok=False, reason="unit has already used action this turn")
        # combat_attack 额外验证：target 参数
        if cmd.type == "combat_attack":
            target_uid = coerce_non_empty_string(cmd.params.get("target"))
            if target_uid is None:
                return ValidationResult(ok=False, reason="target is required")
        return ValidationResult(ok=True)

    def _compute_combat_move(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance | None,
    ) -> ExecuteResult:
        resolved = self._resolve_v2_combat(cmd.params, state, world)
        sub_area_id, payload, _ = resolved  # type: ignore[misc]
        updated = state.areas.copy_hostile_state(payload)
        units = updated["units"]
        current_uid = updated["current_unit_id"]
        unit = self._find_unit(units, current_uid)

        # 目标格
        target_col = int(cmd.params["col"])
        target_row = int(cmd.params["row"])
        target = (target_col, target_row)

        # 构建 BattleGrid
        grid = BattleGrid.from_map_data(updated["grid"])

        # 有效速度：dashed 时翻倍
        effective_speed = unit["speed"] * 2 if unit.get("dashed") else unit["speed"]  # type: ignore[index]

        # 可达性检查
        start = tuple(unit["position"])  # type: ignore[index]
        reachable = grid.reachable_cells(start, effective_speed, units, side=unit["side"])  # type: ignore[index]
        if target not in reachable:
            return ExecuteResult.error("target cell is not reachable")

        # --- 机会攻击检查 ---
        rolls = []
        opportunity_attacks = []
        opposing_side = "enemy" if unit["side"] == "ally" else "ally"  # type: ignore[index]
        for other in units:
            if other["side"] != opposing_side:
                continue
            if not self._is_unit_active(other):
                continue
            if other.get("reaction_used"):
                continue
            if unit.get("disengaged"):  # type: ignore[union-attr]
                continue
            # 检查：other 是否与 start 相邻（距离=1）且移动后不再相邻
            other_pos = tuple(other["position"])
            if grid.distance(other_pos, start) == 1 and grid.distance(other_pos, target) > 1:  # type: ignore[arg-type]
                # 触发机会攻击 — 取第一个近战攻击
                melee_atk = None
                for atk in other.get("attacks", []):
                    if atk.get("range", 1) <= 1:
                        melee_atk = atk
                        break
                if melee_atk is None:
                    continue
                # 掷攻击骰（机会攻击 — 移动触发）
                atk_roll = random.randint(1, 20)
                hit_bonus = int(melee_atk.get("hit_bonus", 0))
                atk_total = atk_roll + hit_bonus
                # 属性修正 + 熟练加值（非怪物单位）
                oa_ability_mod = 0
                oa_prof_bonus = 0
                if other.get("source") != "monster":
                    oa_ability_mod = compute_attack_ability_mod(
                        other.get("stats", {}), melee_atk.get("tags", [])
                    )
                    oa_prof_bonus = int(other.get("proficiency_bonus", 0))
                    atk_total += oa_ability_mod + oa_prof_bonus
                target_ac = unit["ac"]  # type: ignore[index]
                hit = atk_total >= target_ac
                damage = 0
                if hit:
                    damage = roll_damage_dice(melee_atk.get("damage_dice", "1d4"))
                    if other.get("source") != "monster":
                        damage = max(1, damage + oa_ability_mod)
                    unit["hp"] = max(0, unit["hp"] - damage)  # type: ignore[index]
                    if unit["hp"] <= 0:  # type: ignore[index]
                        unit["alive"] = False  # type: ignore[index]
                other["reaction_used"] = True
                oa_modifiers = [{"name": "hit_bonus", "value": hit_bonus}]
                if other.get("source") != "monster":
                    oa_modifiers.append({"name": "ability_mod", "value": oa_ability_mod})
                    oa_modifiers.append({"name": "proficiency_bonus", "value": oa_prof_bonus})
                rolls.append(build_dice_roll(
                    purpose=f"opportunity_attack_{other['unit_id']}",
                    dice="1d20", result=atk_roll,
                    modifiers=oa_modifiers,
                    total=atk_total,
                ))
                opportunity_attacks.append({
                    "attacker_id": other["unit_id"],
                    "target_id": unit["unit_id"],  # type: ignore[index]
                    "attack_name": melee_atk["name"],
                    "hit": hit, "damage": damage,
                    "target_hp": unit["hp"],  # type: ignore[index]
                    "target_alive": unit["alive"],  # type: ignore[index]
                })

        # 如果移动者被击杀，仍然记录结果但不执行移动
        if unit["alive"]:  # type: ignore[index]
            unit["position"] = [target_col, target_row]  # type: ignore[index]
        unit["move_used"] = True  # type: ignore[index]

        return handler_success(
            "combat", "combat_move",
            changes=[StateChange("areas", "modify",
                                 f"hostile_tracking.{sub_area_id}", updated)],
            metadata={
                "unit_id": current_uid,
                "from": list(start),
                "to": [target_col, target_row],
                "opportunity_attacks": opportunity_attacks,
            },
            rolls=rolls,
            omit_empty_delta=False,
        )

    def _compute_combat_end_turn(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance | None,
    ) -> ExecuteResult:
        resolved = self._resolve_v2_combat(cmd.params, state, world)
        sub_area_id, payload, _ = resolved  # type: ignore[misc]
        updated = state.areas.copy_hostile_state(payload)
        units = updated["units"]
        turn_order = updated["turn_order"]

        # 推进 index
        idx = updated["current_turn_index"] + 1
        round_advanced = False
        if idx >= len(turn_order):
            idx = 0
            updated["combat_round"] = updated.get("combat_round", 1) + 1
            round_advanced = True

        # 进入 round 1 时清除所有 surprised
        if round_advanced and updated["combat_round"] == 1:
            for u in units:
                u["surprised"] = False

        # 跳过死亡/逃跑/被突袭（round 0）单位
        attempts = 0
        while attempts < len(turn_order):
            uid = turn_order[idx]
            u = self._find_unit(units, uid)
            if u is None or not self._is_unit_active(u):
                idx = (idx + 1) % len(turn_order)
                if idx == 0:
                    updated["combat_round"] = updated.get("combat_round", 1) + 1
                    round_advanced = True
                    if updated["combat_round"] == 1:
                        for uu in units:
                            uu["surprised"] = False
                attempts += 1
                continue
            # 突袭轮：round 0 中被突袭的单位跳过
            if updated["combat_round"] == 0 and u.get("surprised"):
                idx = (idx + 1) % len(turn_order)
                if idx == 0:
                    updated["combat_round"] = updated.get("combat_round", 0) + 1
                    round_advanced = True
                    if updated["combat_round"] == 1:
                        for uu in units:
                            uu["surprised"] = False
                attempts += 1
                continue
            break
        else:
            # 理论上不应到这（至少 1 个单位活着），但防御性处理
            pass

        # 重置下一个单位的回合状态
        next_uid = turn_order[idx]
        next_unit = self._find_unit(units, next_uid)
        if next_unit is not None:
            next_unit["action_used"] = False
            next_unit["move_used"] = False
            next_unit["disengaged"] = False
            next_unit["dashed"] = False
            next_unit["defending"] = False
            # reaction_used 在回合结束后不重置（每轮重置一次）
            # → 进入新回合时重置所有 reaction_used
        if round_advanced:
            for u in units:
                u["reaction_used"] = False

        updated["current_turn_index"] = idx
        updated["current_unit_id"] = next_uid

        return handler_success(
            "combat", "combat_end_turn",
            changes=[StateChange("areas", "modify",
                                 f"hostile_tracking.{sub_area_id}", updated)],
            metadata={
                "next_unit_id": next_uid,
                "combat_round": updated["combat_round"],
                "round_advanced": round_advanced,
            },
            omit_empty_delta=False,
        )

    def _compute_combat_disengage(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        resolved = self._resolve_v2_combat(cmd.params, state)
        sub_area_id, payload, _ = resolved  # type: ignore[misc]
        updated = state.areas.copy_hostile_state(payload)
        unit = self._find_unit(updated["units"], updated["current_unit_id"])
        unit["disengaged"] = True  # type: ignore[index]
        unit["action_used"] = True  # type: ignore[index]
        return handler_success(
            "combat", "combat_disengage",
            changes=[StateChange("areas", "modify",
                                 f"hostile_tracking.{sub_area_id}", updated)],
            metadata={"unit_id": unit["unit_id"], "status": "disengaged"},  # type: ignore[index]
            omit_empty_delta=False,
        )

    def _compute_combat_dash(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        resolved = self._resolve_v2_combat(cmd.params, state)
        sub_area_id, payload, _ = resolved  # type: ignore[misc]
        updated = state.areas.copy_hostile_state(payload)
        unit = self._find_unit(updated["units"], updated["current_unit_id"])
        unit["dashed"] = True  # type: ignore[index]
        unit["action_used"] = True  # type: ignore[index]
        return handler_success(
            "combat", "combat_dash",
            changes=[StateChange("areas", "modify",
                                 f"hostile_tracking.{sub_area_id}", updated)],
            metadata={"unit_id": unit["unit_id"], "status": "dashed"},  # type: ignore[index]
            omit_empty_delta=False,
        )

    def _compute_combat_attack(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance | None,
    ) -> ExecuteResult:
        """v2 攻击命令：距离检查 + LoS + 地形效果 + 伤害 + 战斗结束检测。"""
        resolved = self._resolve_v2_combat(cmd.params, state, world)
        sub_area_id, payload, _ = resolved  # type: ignore[misc]
        updated = state.areas.copy_hostile_state(payload)
        units = updated["units"]
        current_uid = updated["current_unit_id"]
        attacker = self._find_unit(units, current_uid)

        # 1. 目标解析
        target_uid = str(cmd.params["target"]).strip()
        target = self._find_unit(units, target_uid)
        if target is None or not self._is_unit_active(target):
            return ExecuteResult.error(f"invalid target: {target_uid}")
        if target["side"] == attacker["side"]:  # type: ignore[index]
            return ExecuteResult.error("cannot attack friendly unit")

        # 2. 选择攻击
        attack_index = int(cmd.params.get("attack_index", 0))
        attacks = attacker.get("attacks", [])  # type: ignore[union-attr]
        if not attacks or attack_index >= len(attacks):
            return ExecuteResult.error("invalid attack selection")
        selected_attack = attacks[attack_index]

        # 3. 构建 BattleGrid + 距离检查
        grid = BattleGrid.from_map_data(updated["grid"])
        attacker_pos: tuple[int, int] = tuple(attacker["position"])  # type: ignore[assignment,index]
        target_pos: tuple[int, int] = tuple(target["position"])
        distance = grid.distance(attacker_pos, target_pos)

        weapon_range = int(selected_attack.get("range", 1))
        attacker_terrain = grid.at(attacker_pos[0], attacker_pos[1])
        effective_range = weapon_range + attacker_terrain.range_bonus
        if distance > effective_range:
            return ExecuteResult.error("target is out of range")

        # 4. LoS 检查（远程攻击：range > 1）
        is_ranged = weapon_range > 1
        if is_ranged and not grid.line_of_sight(attacker_pos, target_pos):
            return ExecuteResult.error("no line of sight to target")

        # 5. 目标 AC = base + terrain_ac_bonus + defending_bonus + active_effects
        base_ac = int(target["ac"])
        target_terrain = grid.at(target_pos[0], target_pos[1])
        terrain_ac_bonus = target_terrain.ac_bonus
        defending_bonus = 2 if target.get("defending") else 0
        effect_ac_mod = _participant_effect_ac_mod(target)
        total_ac = base_ac + terrain_ac_bonus + defending_bonus + effect_ac_mod

        # 6. 攻击骰 d20 + hit_bonus + 属性修正（仅非怪物单位）
        atk_roll = random.randint(1, 20)
        hit_bonus = int(selected_attack.get("hit_bonus", 0))
        atk_total = atk_roll + hit_bonus
        # 属性修正 + 熟练加值（monster 的 hit_bonus 已 baked-in，不再叠加）
        ability_mod = 0
        prof_bonus = 0
        if attacker.get("source") != "monster":
            ability_mod = compute_attack_ability_mod(
                attacker.get("stats", {}), selected_attack.get("tags", [])
            )
            prof_bonus = int(attacker.get("proficiency_bonus", 0))
            atk_total += ability_mod + prof_bonus
        # 环境修正
        env = updated.get("environment", {})
        env_mods = compute_environment_modifiers(
            str(env.get("weather", "clear")),
            str(env.get("time_of_day", "day")),
        )
        atk_total += env_mods.hit_modifier
        if weapon_range > 1:
            atk_total += env_mods.ranged_hit_modifier
        # D-8: 夜间/浓雾能见度限制 — 超出能见度的远程攻击自动未命中
        visibility_blocked = (
            is_ranged
            and env_mods.max_visibility is not None
            and distance > env_mods.max_visibility
        )
        # 保留旧逻辑名称（fog_blocked）兼容浓雾，合并夜间视野
        fog_blocked = (
            env_mods.max_visibility is not None and distance > env_mods.max_visibility
        ) and not is_ranged  # 近战攻击不受能见度影响
        visibility_check_failed = visibility_blocked  # 远程超出视野
        critical = atk_roll == 20
        auto_miss = atk_roll == 1
        hit = (atk_total >= total_ac or critical) and not auto_miss and not visibility_check_failed

        # 7. 伤害
        damage = 0
        damage_type = str(selected_attack.get("damage_type", "physical"))
        if hit:
            damage = roll_damage_dice(selected_attack.get("damage_dice", "1d4"))
            if critical:
                damage += roll_damage_dice(selected_attack.get("damage_dice", "1d4"))  # 暴击双骰
            # 属性修正加入伤害（仅非怪物单位，最小 1）
            if attacker.get("source") != "monster":
                damage = max(1, damage + ability_mod)
            # D-7: 天气伤害类型修正（雨天火焰×0.5，雷电×1.5）
            type_multiplier = env_mods.get_damage_modifier(damage_type)
            if type_multiplier != 1.0:
                damage = max(0, int(damage * type_multiplier))
            # 地形免疫：目标站在浅水格时免疫火焰伤害
            target_terrain_immunities = getattr(target_terrain, "damage_immunities", frozenset())
            if damage_type in target_terrain_immunities:
                damage = 0
            # 伤害减免：怪物模板抗性
            monster_id = target.get("monster_id")
            if monster_id and world is not None and world.has_registry("monsters"):
                template = world.monsters.get(monster_id)
                if template is not None:
                    if damage_type in template.immunities:
                        damage = 0
                    elif damage_type in template.resistances:
                        damage = max(1, damage // 2)
                    elif damage_type in template.vulnerabilities:
                        damage = damage * 2
            # 伤害减免：玩家 active_effects 抗性
            if target.get("unit_id") == "player" and state.has_slice("player"):
                damage = self._apply_damage_resistance(damage, damage_type, state)

            target["hp"] = max(0, target["hp"] - damage)
            if target["hp"] <= 0:
                target["alive"] = False

        # 8. 标记 action_used
        attacker["action_used"] = True  # type: ignore[index]

        # 9. 骰子记录
        attack_modifiers = [{"name": "hit_bonus", "value": hit_bonus}]
        if attacker.get("source") != "monster":
            attack_modifiers.append({"name": "ability_mod", "value": ability_mod})
            attack_modifiers.append({"name": "proficiency_bonus", "value": prof_bonus})
        attack_roll_record = build_dice_roll(
            purpose="combat_attack",
            dice="1d20", result=atk_roll,
            modifiers=attack_modifiers,
            total=atk_total,
        )

        # 10. 战斗结束检测
        extra_changes: list[StateChange] = []
        combat_cleared = False
        xp_awarded = 0
        loot_items: list[tuple[str, int]] = []
        loot_gold = 0

        enemy_units = [u for u in units if u["side"] == "enemy"]
        if all(not self._is_unit_active(u) for u in enemy_units):
            combat_cleared = True
            updated["combat_active"] = False
            updated["cleared"] = True
            if state.has_slice("time"):
                updated["cleared_at_tick"] = self._current_tick(state)
            # XP
            if world is not None:
                xp_awarded = self._compute_combat_xp(enemy_units, world)
                if xp_awarded > 0 and state.has_slice("player"):
                    extra_changes.append(StateChange("player", "set", "xp", state.player.xp + xp_awarded))
                # Loot
                loot_items, loot_gold = self._compute_combat_loot(enemy_units, world)
                if loot_gold > 0 and state.has_slice("player"):
                    current_gold = int(getattr(state.player, "gold", 0))
                    extra_changes.append(StateChange("player", "set", "gold", current_gold + loot_gold))
                if loot_items and state.has_slice("player"):
                    inventory = [stack.snapshot() for stack in state.player.inventory]
                    for item_id, count in loot_items:
                        merged = False
                        for stack in inventory:
                            if stack.get("item_id") == item_id:
                                stack["count"] = int(stack.get("count", 0)) + count
                                merged = True
                                break
                        if not merged:
                            inventory.append({"item_id": item_id, "count": count, "tags": []})
                    extra_changes.append(StateChange("player", "set", "inventory", inventory))
            # Kill counts
            if state.has_slice("flags"):
                for u in enemy_units:
                    if not u.get("alive") and not u.get("fled"):
                        mid = u.get("monster_id")
                        if mid:
                            key = f"kill_count_{mid}"
                            cur = int(state.flags.get(key, 0) or 0)
                            extra_changes.append(StateChange("flags", "set", f"flags.{key}", cur + 1))

        # 11. 玩家 HP 同步（当玩家单位被攻击时）
        if target.get("unit_id") == "player" and damage > 0 and state.has_slice("player"):
            extra_changes.append(StateChange("player", "set", "hp", target["hp"]))

        all_changes = [
            StateChange("areas", "modify", f"hostile_tracking.{sub_area_id}", updated),
            *extra_changes,
        ]

        # D-4: 玩家死亡元数据（让 handler 自身也能检测 defeat）
        player_defeated = False
        if target.get("unit_id") == "player" and target["hp"] <= 0:
            player_defeated = True
            updated["combat_active"] = False

        return handler_success(
            "combat", "combat_attack",
            changes=all_changes,
            metadata={
                "attacker_id": current_uid,
                "target_id": target_uid,
                # D-10: 丰富化 unit_defeated SSE 所需字段
                "target_name": str(target.get("name", target_uid)),
                "target_side": str(target.get("side", "unknown")),
                "target_source": str(target.get("source", "unknown")),
                "attack_name": selected_attack.get("name", "Attack"),
                "hit": hit,
                "critical": critical,
                "damage": damage,
                "damage_type": damage_type,
                "target_hp": target["hp"],
                "target_alive": target["alive"],
                "target_ac": total_ac,
                "terrain_ac_bonus": terrain_ac_bonus,
                "combat_cleared": combat_cleared,
                "xp_awarded": xp_awarded,
                "loot_items": [{"item_id": iid, "count": c} for iid, c in loot_items],
                "gold_dropped": loot_gold,
                "player_defeated": player_defeated,
            },
            rolls=[attack_roll_record],
            omit_empty_delta=False,
        )

    def _compute_combat_defend(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        """v2 防御命令：设置 defending=True + action_used=True。"""
        resolved = self._resolve_v2_combat(cmd.params, state)
        sub_area_id, payload, _ = resolved  # type: ignore[misc]
        updated = state.areas.copy_hostile_state(payload)
        unit = self._find_unit(updated["units"], updated["current_unit_id"])
        unit["defending"] = True  # type: ignore[index]
        unit["action_used"] = True  # type: ignore[index]
        return handler_success(
            "combat", "combat_defend",
            changes=[StateChange("areas", "modify",
                                 f"hostile_tracking.{sub_area_id}", updated)],
            metadata={"unit_id": unit["unit_id"], "status": "defending"},  # type: ignore[index]
            omit_empty_delta=False,
        )

    # ------------------------------------------------------------------
    # combat_npc_turn（怪物 AI 自主回合，engine-only）
    # ------------------------------------------------------------------

    def _validate_combat_npc_turn(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance | None,
    ) -> ValidationResult:
        """engine/system 专用：验证 NPC 回合命令。"""
        if cmd.source not in {"engine", "system"}:
            return ValidationResult(ok=False, reason="combat_npc_turn is restricted to engine/system")
        if not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice is required")
        resolved = self._resolve_v2_combat(cmd.params, state, world)
        if resolved is None:
            return ValidationResult(ok=False, reason="active v2 combat not found")
        _, payload, _ = resolved
        unit = self._find_unit(payload.get("units", []), payload.get("current_unit_id", ""))
        if unit is None:
            return ValidationResult(ok=False, reason="current unit not found")
        if not self._is_unit_active(unit):
            return ValidationResult(ok=False, reason="current unit is not active")
        return ValidationResult(ok=True)

    def _compute_combat_npc_turn(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance | None,
    ) -> ExecuteResult:
        """怪物 AI 自主回合：移动 + 行动（攻击/逃跑/防御/hold）。

        调用方（编排层）负责后续 combat_end_turn。
        """
        resolved = self._resolve_v2_combat(cmd.params, state, world)
        sub_area_id, payload, _ = resolved  # type: ignore[misc]
        updated = state.areas.copy_hostile_state(payload)
        units = updated["units"]
        unit = self._find_unit(units, updated["current_unit_id"])

        if unit is None:
            return ExecuteResult.error("current unit not found after state copy")
        if unit.get("source") == "player":
            return ExecuteResult.error("combat_npc_turn cannot be used for player units")

        grid = BattleGrid.from_map_data(updated["grid"])

        # --- 决策覆盖（LLM pre-computed） ---
        decision_source = "rules_ai"
        raw_decision = cmd.params.get("decision")
        if raw_decision and isinstance(raw_decision, dict):
            try:
                parsed = MonsterDecision(
                    move_to=tuple(raw_decision["move_to"]) if raw_decision.get("move_to") else None,
                    action=str(raw_decision.get("action", "hold")),
                    target_id=raw_decision.get("target_id"),
                    attack_index=int(raw_decision.get("attack_index", 0)),
                )
                if validate_decision(parsed, unit, grid, units):
                    decision = parsed
                    decision_source = "override"
                else:
                    decision = decide_monster_turn(unit, grid, units)
            except (KeyError, TypeError, ValueError):
                decision = decide_monster_turn(unit, grid, units)
        else:
            decision = decide_monster_turn(unit, grid, units)

        rolls: list[dict[str, Any]] = []
        opportunity_attacks: list[dict[str, Any]] = []
        attack_result: dict[str, Any] = {}

        # --- Phase A: 移动 ---
        if decision.move_to is not None and self._is_unit_active(unit):
            start: tuple[int, int] = tuple(unit["position"])  # type: ignore[assignment]
            move_target = decision.move_to
            # 机会攻击检查（复用 combat_move 逻辑）
            opposing_side = "enemy" if unit["side"] == "ally" else "ally"
            for other in units:
                if other["side"] != opposing_side or not self._is_unit_active(other):
                    continue
                if other.get("reaction_used"):
                    continue
                other_pos: tuple[int, int] = tuple(other["position"])  # type: ignore[assignment]
                if grid.distance(other_pos, start) == 1 and grid.distance(other_pos, move_target) > 1:
                    melee_atk = next(
                        (a for a in other.get("attacks", []) if a.get("range", 1) <= 1),
                        None,
                    )
                    if melee_atk is None:
                        continue
                    atk_roll = random.randint(1, 20)
                    hit_bonus = int(melee_atk.get("hit_bonus", 0))
                    atk_total = atk_roll + hit_bonus
                    # 属性修正 + 熟练加值（非怪物单位做机会攻击）
                    npc_oa_ability_mod = 0
                    npc_oa_prof_bonus = 0
                    if other.get("source") != "monster":
                        npc_oa_ability_mod = compute_attack_ability_mod(
                            other.get("stats", {}), melee_atk.get("tags", [])
                        )
                        npc_oa_prof_bonus = int(other.get("proficiency_bonus", 0))
                        atk_total += npc_oa_ability_mod + npc_oa_prof_bonus
                    hit = atk_total >= int(unit["ac"])
                    damage = 0
                    if hit:
                        damage = roll_damage_dice(melee_atk.get("damage_dice", "1d4"))
                        if other.get("source") != "monster":
                            damage = max(1, damage + npc_oa_ability_mod)
                        if unit.get("unit_id") == "player" and state.has_slice("player"):
                            damage_type = str(melee_atk.get("damage_type", "physical"))
                            damage = self._apply_damage_resistance(damage, damage_type, state)
                        unit["hp"] = max(0, unit["hp"] - damage)
                        if unit["hp"] <= 0:
                            unit["alive"] = False
                    other["reaction_used"] = True
                    npc_oa_modifiers = [{"name": "hit_bonus", "value": hit_bonus}]
                    if other.get("source") != "monster":
                        npc_oa_modifiers.append({"name": "ability_mod", "value": npc_oa_ability_mod})
                        npc_oa_modifiers.append({"name": "proficiency_bonus", "value": npc_oa_prof_bonus})
                    rolls.append(build_dice_roll(
                        purpose=f"opportunity_attack_{other['unit_id']}",
                        dice="1d20", result=atk_roll,
                        modifiers=npc_oa_modifiers,
                        total=atk_total,
                    ))
                    opportunity_attacks.append({
                        "attacker_id": other["unit_id"],
                        "target_id": unit["unit_id"],
                        "attack_name": melee_atk["name"],
                        "hit": hit,
                        "damage": damage,
                        "target_hp": unit["hp"],
                        "target_alive": unit.get("alive", False),
                    })
            # 仅在移动单位仍存活时更新位置
            if self._is_unit_active(unit):
                unit["position"] = list(move_target)
        unit["move_used"] = True

        # --- Phase B: 行动 ---
        if decision.action == "attack" and self._is_unit_active(unit) and decision.target_id:
            target = self._find_unit(units, decision.target_id)
            if target and self._is_unit_active(target):
                atk = unit["attacks"][decision.attack_index]
                attacker_pos: tuple[int, int] = tuple(unit["position"])  # type: ignore[assignment]
                target_pos: tuple[int, int] = tuple(target["position"])  # type: ignore[assignment]
                distance = grid.distance(attacker_pos, target_pos)
                weapon_range = int(atk.get("range", 1))
                attacker_terrain = grid.at(attacker_pos[0], attacker_pos[1])
                in_range = distance <= weapon_range + attacker_terrain.range_bonus
                has_los = weapon_range <= 1 or grid.line_of_sight(attacker_pos, target_pos)
                if in_range and has_los:
                    base_ac = int(target["ac"])
                    t_terrain = grid.at(target_pos[0], target_pos[1])
                    total_ac = (
                        base_ac
                        + t_terrain.ac_bonus
                        + (2 if target.get("defending") else 0)
                        + _participant_effect_ac_mod(target)
                    )
                    atk_roll = random.randint(1, 20)
                    hit_bonus = int(atk.get("hit_bonus", 0))
                    atk_total = atk_roll + hit_bonus
                    # 属性修正 + 熟练加值（非怪物单位）
                    npc_ability_mod = 0
                    npc_prof_bonus = 0
                    if unit.get("source") != "monster":
                        npc_ability_mod = compute_attack_ability_mod(
                            unit.get("stats", {}), atk.get("tags", [])
                        )
                        npc_prof_bonus = int(unit.get("proficiency_bonus", 0))
                        atk_total += npc_ability_mod + npc_prof_bonus
                    # 环境修正
                    env = updated.get("environment", {})
                    env_mods = compute_environment_modifiers(
                        str(env.get("weather", "clear")),
                        str(env.get("time_of_day", "day")),
                    )
                    atk_total += env_mods.hit_modifier
                    if weapon_range > 1:
                        atk_total += env_mods.ranged_hit_modifier
                    # D-8: 夜间/浓雾能见度限制 — 远程攻击超出视野自动未命中
                    npc_vis_blocked = (
                        weapon_range > 1
                        and env_mods.max_visibility is not None
                        and distance > env_mods.max_visibility
                    )
                    critical = atk_roll == 20
                    auto_miss = atk_roll == 1
                    hit = (atk_total >= total_ac or critical) and not auto_miss and not npc_vis_blocked
                    damage = 0
                    damage_type = str(atk.get("damage_type", "physical"))
                    if hit:
                        damage = roll_damage_dice(atk.get("damage_dice", "1d4"))
                        if critical:
                            damage += roll_damage_dice(atk.get("damage_dice", "1d4"))
                        # 属性修正加入伤害（非怪物单位，最小 1）
                        if unit.get("source") != "monster":
                            damage = max(1, damage + npc_ability_mod)
                        # D-7: 天气伤害类型修正
                        type_multiplier = env_mods.get_damage_modifier(damage_type)
                        if type_multiplier != 1.0:
                            damage = max(0, int(damage * type_multiplier))
                        # 地形免疫：目标站在浅水格时免疫火焰伤害
                        t_terrain_immunities = getattr(t_terrain, "damage_immunities", frozenset())
                        if damage_type in t_terrain_immunities:
                            damage = 0
                        if target.get("unit_id") == "player" and state.has_slice("player"):
                            damage = self._apply_damage_resistance(damage, damage_type, state)
                    target["hp"] = max(0, target["hp"] - damage)
                    if target["hp"] <= 0:
                        target["alive"] = False
                    npc_atk_modifiers = [{"name": "hit_bonus", "value": hit_bonus}]
                    if unit.get("source") != "monster":
                        npc_atk_modifiers.append({"name": "ability_mod", "value": npc_ability_mod})
                        npc_atk_modifiers.append({"name": "proficiency_bonus", "value": npc_prof_bonus})
                    rolls.append(build_dice_roll(
                        purpose=f"npc_attack:{atk.get('name', 'Attack')}",
                        dice="1d20", result=atk_roll,
                        modifiers=npc_atk_modifiers,
                        total=atk_total,
                    ))
                    attack_result = {
                        "target_id": decision.target_id,
                        # D-10: 丰富化 unit_defeated SSE 所需字段
                        "target_name": str(target.get("name", decision.target_id)),
                        "target_side": str(target.get("side", "unknown")),
                        "target_source": str(target.get("source", "unknown")),
                        "attack_name": atk.get("name", "Attack"),
                        "hit": hit,
                        "critical": critical,
                        "damage": damage,
                        "damage_type": damage_type,
                        "target_hp": target["hp"],
                        "target_alive": target["alive"],
                    }
        elif decision.action == "flee" and self._is_unit_active(unit):
            unit["alive"] = False
            unit["fled"] = True
        elif decision.action == "defend" and self._is_unit_active(unit):
            unit["defending"] = True
        unit["action_used"] = True

        # --- 战斗结束检测 ---
        extra_changes: list[StateChange] = []
        combat_cleared = False
        xp_awarded = 0
        enemy_units = [u for u in units if u["side"] == "enemy"]
        if all(not self._is_unit_active(u) for u in enemy_units):
            combat_cleared = True
            updated["combat_active"] = False
            updated["cleared"] = True
            if state.has_slice("time"):
                updated["cleared_at_tick"] = self._current_tick(state)
            if world is not None:
                xp_awarded = self._compute_combat_xp(enemy_units, world)
                if xp_awarded > 0 and state.has_slice("player"):
                    extra_changes.append(
                        StateChange("player", "set", "xp", state.player.xp + xp_awarded)
                    )
            if state.has_slice("flags"):
                for u in enemy_units:
                    if not u.get("alive") and not u.get("fled"):
                        mid = u.get("monster_id")
                        if mid:
                            key = f"kill_count_{mid}"
                            cur = int(state.flags.get(key, 0) or 0)
                            extra_changes.append(
                                StateChange("flags", "set", f"flags.{key}", cur + 1)
                            )

        # 玩家 HP 同步
        if attack_result.get("target_id") == "player":
            player_unit = self._find_unit(units, "player")
            if player_unit and state.has_slice("player"):
                extra_changes.append(StateChange("player", "set", "hp", player_unit["hp"]))

        # D-4: 玩家死亡元数据
        player_defeated = (
            attack_result.get("target_id") == "player"
            and not attack_result.get("target_alive", True)
        )
        if player_defeated:
            updated["combat_active"] = False

        metadata = {
            "unit_id": updated["current_unit_id"],
            "decision_action": decision.action,
            "decision_source": decision_source,
            "move_to": list(decision.move_to) if decision.move_to else None,
            "opportunity_attacks": opportunity_attacks,
            "attack": attack_result or None,
            "combat_cleared": combat_cleared,
            "xp_awarded": xp_awarded,
            "player_defeated": player_defeated,
            "attacker_id": unit.get("unit_id"),
        }
        decision_provider = coerce_non_empty_string(cmd.params.get("decision_provider"))
        if decision_provider is not None:
            metadata["decision_provider"] = decision_provider
        decision_tier = coerce_non_empty_string(cmd.params.get("decision_tier"))
        if decision_tier is not None:
            metadata["decision_tier"] = decision_tier

        return handler_success(
            "combat", "combat_npc_turn",
            changes=[
                StateChange("areas", "modify", f"hostile_tracking.{sub_area_id}", updated),
                *extra_changes,
            ],
            metadata=metadata,
            rolls=rolls,
            omit_empty_delta=False,
        )

    def _compute_combat_finalize_status(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        sub_area_id = coerce_non_empty_string(cmd.params.get("sub_area_id")) or ""
        status = coerce_non_empty_string(cmd.params.get("status")) or ""
        payload = state.areas.get_hostile_state(sub_area_id)
        if payload is None:
            return ExecuteResult.error(f"unknown hostile sub area: {sub_area_id}")
        updated = state.areas.copy_hostile_state(payload)
        updated["status"] = status
        updated["combat_active"] = False
        return handler_success(
            "combat",
            "combat_finalize_status",
            changes=[
                StateChange(
                    "areas",
                    "modify",
                    f"hostile_tracking.{sub_area_id}",
                    updated,
                )
            ],
            metadata={
                "sub_area_id": sub_area_id,
                "status": status,
            },
            omit_empty_delta=False,
        )
