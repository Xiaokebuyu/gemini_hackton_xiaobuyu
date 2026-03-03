"""GrowthHandler implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content import WorldInstance
from app.game_core.content.registries.class_types import Feature
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import coerce_int, get_non_empty_string, handler_success
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer


class GrowthHandler(StaticCommandHandler):
    COMMAND_TYPES = (
        "add_xp",
        "level_up",
        "apply_asi",
        "choose_subclass",
        "create_character",
    )

    _REQUIRED_STATS = ("str", "dex", "con", "int", "wis", "cha")
    _ASI_LEVELS = frozenset({4, 8, 12, 16, 19})

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")

        if cmd.type == "add_xp":
            return self._validate_add_xp(cmd, state)
        if cmd.type == "level_up":
            return self._validate_level_up(cmd, state, world)
        if cmd.type == "apply_asi":
            return self._validate_apply_asi(cmd, state)
        if cmd.type == "choose_subclass":
            return self._validate_choose_subclass(cmd, state, world)
        if cmd.type == "create_character":
            return self._validate_create_character(cmd, world)
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

        if cmd.type == "add_xp":
            return self._compute_add_xp(cmd, state, world)
        if cmd.type == "level_up":
            return self._compute_level_up(cmd, state, world)
        if cmd.type == "apply_asi":
            return self._compute_apply_asi(cmd, state)
        if cmd.type == "choose_subclass":
            return self._compute_choose_subclass(cmd, state, world)
        if cmd.type == "create_character":
            return self._compute_create_character(cmd, state, world)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _validate_add_xp(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        identity_check = self._validate_character_identity(cmd.params, state)
        if identity_check is not None:
            return identity_check
        amount = coerce_int(cmd.params.get("amount"))
        if amount is None or amount < 1:
            return ValidationResult(ok=False, reason="amount must be an integer >= 1")
        return ValidationResult(ok=True)

    def _validate_level_up(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        identity_check = self._validate_character_identity(cmd.params, state)
        if identity_check is not None:
            return identity_check
        current_level = max(1, int(state.player.level))
        target_level = self._resolve_target_level(cmd.params, current_level)
        if target_level is None:
            return ValidationResult(
                ok=False,
                reason="target_level must be an integer >= current level + 1",
            )
        available_level = self._resolve_available_level(
            world,
            current_level,
            int(state.player.xp),
        )
        if target_level > available_level:
            return ValidationResult(
                ok=False,
                reason=f"target level exceeds available level: {target_level} > {available_level}",
            )
        return ValidationResult(ok=True)

    def _validate_apply_asi(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        identity_check = self._validate_character_identity(cmd.params, state)
        if identity_check is not None:
            return identity_check
        stat = get_non_empty_string(cmd.params, "stat")
        if stat is None or stat not in self._REQUIRED_STATS:
            return ValidationResult(
                ok=False,
                reason="stat must be one of str/dex/con/int/wis/cha",
            )
        bonus = coerce_int(cmd.params.get("bonus"))
        if bonus not in {1, 2}:
            return ValidationResult(ok=False, reason="bonus must be 1 or 2")
        if int(state.player.level) not in self._ASI_LEVELS:
            return ValidationResult(
                ok=False,
                reason="ASI can only be applied at levels 4/8/12/16/19",
            )
        current_value = coerce_int(state.player.stats.get(stat))
        if current_value is None:
            return ValidationResult(ok=False, reason=f"unknown stat: {stat}")
        if current_value + bonus > 20:
            return ValidationResult(
                ok=False,
                reason=f"applying ASI would exceed 20 for {stat}",
            )
        return ValidationResult(ok=True)

    def _validate_choose_subclass(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not world.has_registry("classes"):
            return ValidationResult(ok=False, reason="classes registry is required")
        identity_check = self._validate_character_identity(cmd.params, state)
        if identity_check is not None:
            return identity_check
        subclass_id = get_non_empty_string(cmd.params, "subclass_id")
        if subclass_id is None:
            return ValidationResult(
                ok=False,
                reason="subclass_id must be a non-empty string",
            )
        if not state.player.character_class:
            return ValidationResult(ok=False, reason="player has no class selected")
        if state.player.subclass not in {None, ""}:
            return ValidationResult(ok=False, reason="subclass already chosen")
        subclass_template = world.classes.get_subclass(subclass_id)
        if subclass_template is None:
            return ValidationResult(ok=False, reason=f"unknown subclass: {subclass_id}")
        bound_class = subclass_template.class_id or None
        if bound_class is not None and bound_class != state.player.character_class:
            return ValidationResult(
                ok=False,
                reason=f"subclass '{subclass_id}' does not belong to class '{state.player.character_class}'",
            )
        class_template = world.classes.get_class(state.player.character_class)
        required_level = coerce_int(getattr(class_template, "subclass_level", None)) or 6
        if int(state.player.level) < required_level:
            return ValidationResult(
                ok=False,
                reason=f"subclass requires level {required_level}",
            )
        return ValidationResult(ok=True)

    def _validate_create_character(
        self,
        cmd: Command,
        world: WorldInstance,
    ) -> ValidationResult:
        if not world.has_registry("classes"):
            return ValidationResult(ok=False, reason="classes registry is required")
        character_id = get_non_empty_string(cmd.params, "character_id")
        if character_id is None:
            return ValidationResult(
                ok=False,
                reason="character_id must be a non-empty string",
            )
        name = get_non_empty_string(cmd.params, "name")
        if name is None:
            return ValidationResult(ok=False, reason="name must be a non-empty string")
        race_id = get_non_empty_string(cmd.params, "race_id")
        if race_id is None:
            return ValidationResult(ok=False, reason="race_id must be a non-empty string")
        class_id = get_non_empty_string(cmd.params, "class_id")
        if class_id is None:
            return ValidationResult(ok=False, reason="class_id must be a non-empty string")
        background_id = get_non_empty_string(cmd.params, "background_id")
        if background_id is None:
            return ValidationResult(
                ok=False,
                reason="background_id must be a non-empty string",
            )
        if world.classes.get_race(race_id) is None:
            return ValidationResult(ok=False, reason=f"unknown race: {race_id}")
        if world.classes.get_class(class_id) is None:
            return ValidationResult(ok=False, reason=f"unknown class: {class_id}")
        if world.classes.get_background(background_id) is None:
            return ValidationResult(
                ok=False,
                reason=f"unknown background: {background_id}",
            )
        scores = self._normalize_ability_scores(cmd.params.get("ability_scores"))
        if scores is None:
            return ValidationResult(
                ok=False,
                reason="ability_scores must contain exactly str/dex/con/int/wis/cha with integer values",
            )
        for stat, value in scores.items():
            if value < 3 or value > 20:
                return ValidationResult(
                    ok=False,
                    reason=f"ability_scores.{stat} must be between 3 and 20",
                )
        return ValidationResult(ok=True)

    def _compute_add_xp(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        amount = int(cmd.params["amount"])
        previous_xp = int(state.player.xp)
        new_xp = previous_xp + amount
        current_level = max(1, int(state.player.level))
        available_level = self._resolve_available_level(world, current_level, new_xp)
        return handler_success(
            "growth",
            "add_xp",
            changes=[
                StateChange("player", "set", "xp", new_xp),
            ],
            metadata={
                "previous_xp": previous_xp,
                "new_xp": new_xp,
                "amount": amount,
                "current_level": current_level,
                "available_level": available_level,
                "level_up_available": available_level > current_level,
            },
            omit_empty_delta=False,
        )

    def _compute_level_up(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        current_level = max(1, int(state.player.level))
        target_level = self._resolve_target_level(cmd.params, current_level) or (current_level + 1)
        class_template = self._get_player_class_template(state, world)
        con_mod = state.player.get_modifier("con")
        steps = max(1, target_level - current_level)
        hp_gain_per_level = self._resolve_hp_gain(class_template, con_mod)
        hp_gain_total = hp_gain_per_level * steps
        added_features = self._resolve_level_features(
            class_template,
            current_level + 1,
            target_level,
        )
        existing_features = list(state.player.class_features)
        new_features = self._merge_features(existing_features, added_features)
        new_proficiency_bonus = self._resolve_proficiency_bonus(target_level)
        new_max_hp = int(state.player.max_hp) + hp_gain_total
        new_hp = min(new_max_hp, int(state.player.hp) + hp_gain_total)
        resource_changes, updated_resource_keys = self._resolve_resource_changes(
            class_template, target_level, state,
        )

        return handler_success(
            "growth",
            "level_up",
            changes=[
                StateChange("player", "set", "level", target_level),
                StateChange("player", "set", "proficiency_bonus", new_proficiency_bonus),
                StateChange("player", "set", "max_hp", new_max_hp),
                StateChange("player", "set", "hp", new_hp),
                StateChange("player", "set", "class_features", new_features),
                *resource_changes,
            ],
            metadata={
                "from_level": current_level,
                "to_level": target_level,
                "hp_gain": hp_gain_total,
                "added_features": added_features,
                "new_proficiency_bonus": new_proficiency_bonus,
                "updated_resources": updated_resource_keys,
            },
            omit_empty_delta=False,
        )

    def _compute_apply_asi(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        stat = str(cmd.params["stat"]).strip()
        bonus = int(cmd.params["bonus"])
        from_value = int(state.player.stats.get(stat, 0))
        to_value = from_value + bonus
        return handler_success(
            "growth",
            "apply_asi",
            changes=[
                StateChange("player", "add", f"stats.{stat}", bonus),
            ],
            metadata={
                "stat": stat,
                "bonus": bonus,
                "from_value": from_value,
                "to_value": to_value,
            },
            omit_empty_delta=False,
        )

    def _compute_choose_subclass(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        subclass_id = str(cmd.params["subclass_id"]).strip()
        subclass_template = world.classes.get_subclass(subclass_id)
        if subclass_template is None:
            return ExecuteResult.error(f"unknown subclass: {subclass_id}")
        existing_features = list(state.player.class_features)
        added_features = self._resolve_subclass_features(
            subclass_template,
            int(state.player.level),
        )
        new_features = self._merge_features(existing_features, added_features)

        return handler_success(
            "growth",
            "choose_subclass",
            changes=[
                StateChange("player", "set", "subclass", subclass_id),
                StateChange("player", "set", "class_features", new_features),
            ],
            metadata={
                "subclass_id": subclass_id,
                "added_features": added_features,
                "status": "chosen",
            },
            omit_empty_delta=False,
        )

    def _compute_create_character(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        race_template = world.classes.get_race(str(cmd.params["race_id"]).strip())
        class_template = world.classes.get_class(str(cmd.params["class_id"]).strip())
        background_template = world.classes.get_background(str(cmd.params["background_id"]).strip())
        if race_template is None or class_template is None or background_template is None:
            return ExecuteResult.error("missing class data for character creation")
        ability_scores = self._normalize_ability_scores(cmd.params.get("ability_scores")) or {
            key: 10 for key in self._REQUIRED_STATS
        }

        final_stats = dict(ability_scores)
        for key, value in race_template.stat_bonuses.items():
            stat = str(key)
            if stat not in final_stats:
                continue
            bonus = coerce_int(value)
            if bonus is None:
                continue
            final_stats[stat] += bonus

        con_mod = (final_stats["con"] - 10) // 2
        max_hp = self._resolve_max_hp(class_template, con_mod)
        hp = max_hp
        ac = self._resolve_ac(class_template, final_stats)
        gold = self._resolve_starting_gold(class_template, background_template)
        class_features = self._resolve_class_features(
            race_template,
            class_template,
            background_template,
        )

        return handler_success(
            "growth",
            "create_character",
            changes=[
                StateChange("player", "set", "character_id", str(cmd.params["character_id"]).strip()),
                StateChange("player", "set", "character_name", str(cmd.params["name"]).strip()),
                StateChange("player", "set", "character_class", str(cmd.params["class_id"]).strip()),
                StateChange("player", "set", "level", 1),
                StateChange("player", "set", "xp", 0),
                StateChange("player", "set", "hp", hp),
                StateChange("player", "set", "max_hp", max_hp),
                StateChange("player", "set", "ac", ac),
                StateChange("player", "set", "proficiency_bonus", 2),
                StateChange("player", "set", "stats", final_stats),
                StateChange("player", "set", "class_features", class_features),
                StateChange("player", "set", "gold", gold),
                StateChange("player", "set", "subclass", None),
            ],
            metadata={"status": "created"},
            omit_empty_delta=False,
        )

    def _get_player_class_template(
        self,
        state: StateContainer,
        world: WorldInstance,
    ) -> Any:
        if not state.player.character_class or not world.has_registry("classes"):
            return None
        return world.classes.get_class(state.player.character_class)

    def _resolve_available_level(
        self,
        world: WorldInstance,
        current_level: int,
        xp: int,
    ) -> int:
        level = max(1, current_level)
        next_level = level + 1
        while True:
            threshold = self._xp_threshold_for_level(world, next_level)
            if threshold is None or xp < threshold:
                return level
            level = next_level
            next_level += 1

    def _xp_threshold_for_level(self, world: WorldInstance, level: int) -> int | None:
        if level < 1:
            return None
        if world.has_registry("classes"):
            threshold = world.classes.xp_threshold_for_level(level)
            if threshold is not None:
                return threshold
        return (level - 1) * 1000

    def _resolve_target_level(
        self,
        params: Mapping[str, Any],
        current_level: int,
    ) -> int | None:
        raw_target = params.get("target_level")
        if raw_target is None:
            return current_level + 1
        target_level = coerce_int(raw_target)
        if target_level is None or target_level <= current_level:
            return None
        return target_level

    def _resolve_max_hp(self, class_template: Any, con_mod: int) -> int:
        if class_template is None:
            return max(1, 10 + con_mod)
        base_value = coerce_int(class_template.hit_die)
        if base_value is None:
            base_value = coerce_int(class_template.base_hp)
        if base_value is None:
            base_value = 10
        return max(1, base_value + con_mod)

    def _resolve_ac(
        self,
        class_template: Any,
        stats: Mapping[str, int],
    ) -> int:
        if class_template is not None:
            base_ac = coerce_int(class_template.base_ac)
            if base_ac is not None:
                return base_ac
        dex_mod = (int(stats.get("dex", 10)) - 10) // 2
        return 10 + dex_mod

    def _resolve_starting_gold(
        self,
        class_template: Any,
        background_template: Any,
    ) -> int:
        if background_template is not None:
            for key in ("starting_gold", "gold_bonus"):
                value = coerce_int(getattr(background_template, key, None))
                if value is not None:
                    return max(0, value)
        class_gold = coerce_int(getattr(class_template, "starting_gold", None)) if class_template is not None else None
        return max(0, class_gold or 0)

    def _resolve_class_features(
        self,
        race_template: Any,
        class_template: Any,
        background_template: Any,
    ) -> list[str]:
        features: list[str] = []
        if race_template is not None:
            for trait in race_template.racial_traits:
                if isinstance(trait, Feature):
                    if trait.id:
                        features.append(trait.id)
                else:
                    normalized = self._non_empty_string(trait)
                    if normalized is not None:
                        features.append(normalized)

        features = self._merge_features(features, self._resolve_level_features(class_template, 1, 1))

        if background_template is not None:
            bg_feature = background_template.feature
            if isinstance(bg_feature, Feature):
                if bg_feature.id:
                    features = self._merge_features(features, [bg_feature.id])
            elif isinstance(bg_feature, str):
                normalized = self._non_empty_string(bg_feature)
                if normalized is not None:
                    features = self._merge_features(features, [normalized])
        return features

    def _resolve_level_features(
        self,
        class_template: Any,
        start_level: int,
        end_level: int,
    ) -> list[str]:
        if start_level > end_level or class_template is None:
            return []
        level_features = class_template.level_features
        if not isinstance(level_features, Mapping):
            return []
        features: list[str] = []
        for level in range(start_level, end_level + 1):
            raw_features = level_features.get(level, level_features.get(str(level), []))
            if not isinstance(raw_features, list):
                continue
            for feature in raw_features:
                if hasattr(feature, "id"):
                    normalized = feature.id.strip() or None
                else:
                    normalized = self._non_empty_string(feature)
                if normalized is not None and normalized not in features:
                    features.append(normalized)
        return features

    def _resolve_subclass_features(
        self,
        subclass_template: Any,
        current_level: int,
    ) -> list[str]:
        if subclass_template is None:
            return []
        features: list[str] = []
        for feature in subclass_template.features:
            normalized = self._non_empty_string(feature)
            if normalized is not None and normalized not in features:
                features.append(normalized)

        level_features_map = subclass_template.level_features
        if isinstance(level_features_map, Mapping):
            level_features = level_features_map.get(
                current_level,
                level_features_map.get(str(current_level), []),
            )
            if isinstance(level_features, list):
                for feature in level_features:
                    if hasattr(feature, "id"):
                        normalized = feature.id.strip() or None
                    else:
                        normalized = self._non_empty_string(feature)
                    if normalized is not None and normalized not in features:
                        features.append(normalized)
        return features

    def _resolve_hp_gain(
        self,
        class_template: Any,
        con_mod: int,
    ) -> int:
        if class_template is None:
            return max(1, 5 + con_mod)
        base_value = coerce_int(class_template.hp_per_level)
        if base_value is None:
            hit_die = coerce_int(class_template.hit_die)
            if hit_die is not None:
                base_value = max(1, hit_die // 2)
            else:
                base_value = 5
        return max(1, base_value + con_mod)

    @staticmethod
    def _resolve_proficiency_bonus(level: int) -> int:
        return 2 + ((max(1, level) - 1) // 4)

    @staticmethod
    def _merge_features(existing: list[str], additions: list[str]) -> list[str]:
        merged = list(existing)
        for feature in additions:
            if feature not in merged:
                merged.append(feature)
        return merged

    def _validate_character_identity(
        self,
        params: Mapping[str, Any],
        state: StateContainer,
    ) -> ValidationResult | None:
        candidate = get_non_empty_string(params, "character_id")
        if candidate is None:
            candidate = get_non_empty_string(params, "character")
        current_id = state.player.character_id
        if candidate is not None and current_id and candidate != current_id:
            return ValidationResult(
                ok=False,
                reason=f"command targets '{candidate}', current player is '{current_id}'",
            )
        return None

    def _normalize_ability_scores(self, raw: Any) -> dict[str, int] | None:
        if not isinstance(raw, Mapping):
            return None
        keys = {str(key) for key in raw.keys()}
        if keys != set(self._REQUIRED_STATS):
            return None
        normalized: dict[str, int] = {}
        for stat in self._REQUIRED_STATS:
            value = coerce_int(raw.get(stat))
            if value is None:
                return None
            normalized[stat] = value
        return normalized

    def _resolve_resource_changes(
        self,
        class_template: Any,
        target_level: int,
        state: StateContainer,
    ) -> tuple[list[StateChange], list[str]]:
        """Compute StateChanges for class_resources based on class_resources_schema.

        Returns (changes, updated_keys) so callers can surface the keys in metadata.
        """
        if class_template is None:
            return [], []
        schema = getattr(class_template, "class_resources_schema", None)
        if not isinstance(schema, Mapping):
            return [], []
        changes: list[StateChange] = []
        updated_keys: list[str] = []
        for key, config in schema.items():
            if not isinstance(config, Mapping):
                continue
            max_at_level = config.get("max_at_level", {})
            if not isinstance(max_at_level, Mapping):
                continue
            recovery = str(config.get("recovery", "long_rest"))
            new_max = self._resolve_resource_max_at_level(max_at_level, target_level)
            if new_max is None:
                continue
            existing = state.player.get_resource(str(key))
            if existing is None:
                updated = {"current": new_max, "max": new_max, "recovery": recovery}
            else:
                old_max = int(existing.get("max", 0))
                if new_max <= old_max:
                    continue
                gain = new_max - old_max
                updated = {
                    "current": int(existing.get("current", 0)) + gain,
                    "max": new_max,
                    "recovery": recovery,
                }
            changes.append(StateChange("player", "modify", f"class_resources.{key}", updated))
            updated_keys.append(str(key))
        return changes, updated_keys

    @staticmethod
    def _resolve_resource_max_at_level(
        max_at_level: Mapping[Any, Any], target_level: int,
    ) -> int | None:
        """Find the highest max value for levels <= target_level."""
        best_max: int | None = None
        for level_key, max_val in max_at_level.items():
            level = coerce_int(level_key)
            if level is None or level > target_level:
                continue
            v = coerce_int(max_val)
            if v is not None and (best_max is None or v > best_max):
                best_max = v
        return best_max

    @staticmethod
    def _non_empty_string(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

