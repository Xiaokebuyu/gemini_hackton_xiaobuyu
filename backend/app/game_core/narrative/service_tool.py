"""ExecuteServiceTool — NPC meta-tool for executing assigned services.

An NPC that has been assigned services (via ``assign_service`` planner
directive or content-layer ``shop.services``) can invoke this tool to
actually deliver the service effects to the player.

Effect atoms are executed in order:
- ``restore_hp`` / ``modify_gold`` → ``npc_service_effect`` command
  (handled by ServiceEffectHandler)
- ``grant_item`` → ``pick_up`` command
- ``remove_item`` → ``drop`` command
- ``apply_effect`` → ``apply_effect`` command
- ``remove_effect`` → ``remove_effect`` command
- ``add_xp`` → ``add_xp`` command
- ``add_knowledge`` → ``add_knowledge`` command

After successful execution the price is deducted and, if the service is
``one_shot``, a ``planner_revoke_service`` command is issued so that the
service cannot be used again.

Decision record: D-Svc03 (narrative.md)
"""

from __future__ import annotations

import logging
from typing import Any

from app.game_core.narrative.character_tools import _CharacterTool
from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.models import ToolResult
from app.game_core.rules.models import Command

logger = logging.getLogger(__name__)

# Effect atom types that map directly to npc_service_effect sub-operations.
_DIRECT_EFFECT_TYPES: frozenset[str] = frozenset({"restore_hp", "modify_gold"})

# Effect atom types that map to their own canonical command type.
_COMMAND_MAP: dict[str, str] = {
    "grant_item": "pick_up",
    "remove_item": "drop",
    "apply_effect": "apply_effect",
    "remove_effect": "remove_effect",
    "add_xp": "add_xp",
    "add_knowledge": "add_knowledge",
}


class ExecuteServiceTool(_CharacterTool):
    """Execute an NPC service whose effects have been pre-approved by the planner.

    The tool looks up the requested ``service_id`` in the services list
    injected into ``context.metadata["role_data"]["services"]`` by the
    context builder.  Services from both the content layer and dynamic
    planner assignments are merged there.
    """

    @property
    def name(self) -> str:
        return "execute_service"

    @property
    def description(self) -> str:
        return (
            "Execute a service that has been assigned to you (e.g. heal, bless, "
            "grant an item).  Supply the exact service_id.  You must only call "
            "this after the player has agreed and, if the service has a price, "
            "they have confirmed payment."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "service_id": {
                    "type": "string",
                    "description": "The identifier of the service to execute.",
                },
            },
            "required": ["service_id"],
        }

    @property
    def allowed_roles(self) -> list[str]:
        return ["npc"]

    @property
    def applicable_traits(self) -> list[str]:
        # Intentionally empty: any NPC that has been assigned services can use
        # this tool.  The guard is the runtime service-list check.
        return []

    # ------------------------------------------------------------------
    # Main execute
    # ------------------------------------------------------------------

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        character_id = self._get_character_id(context)
        if not character_id:
            return self._no_character_id()

        # 1. Resolve service_id param.
        service_id = params.get("service_id", "")
        if not isinstance(service_id, str) or not service_id.strip():
            return ToolResult(
                ok=False,
                message="service_id is required.",
                metadata={"status": "invalid_params"},
            )
        service_id = service_id.strip()

        # 2. Find the service definition in merged services list.
        services = self._resolve_services(context, character_id)
        service = next(
            (s for s in services if s.get("service_id") == service_id), None
        )
        if service is None:
            return ToolResult(
                ok=False,
                message=f"未知服务 '{service_id}'。",
                metadata={"status": "unknown_service", "service_id": service_id},
            )

        # 3. Validate preconditions (quest_completed + price check).
        precond_check = self._check_preconditions(service, context)
        if precond_check is not None:
            return precond_check

        price = int(service.get("price", 0))
        if price > 0:
            gold_check = self._check_gold(context, price)
            if gold_check is not None:
                return gold_check

        # 4. Execute each effect atom.
        effects: list[dict[str, Any]] = service.get("effects", [])
        if not isinstance(effects, list):
            effects = []

        effects_applied: list[str] = []
        for atom in effects:
            if not isinstance(atom, dict):
                continue
            atom_type = atom.get("type", "")
            result = self._execute_effect_atom(atom, context)
            if not result.ok:
                return ToolResult(
                    ok=False,
                    message=f"效果 '{atom_type}' 执行失败：{result.message}",
                    metadata={"status": "effect_failed", "effect_type": atom_type},
                )
            effects_applied.append(atom_type)

        # 5. Deduct price.
        if price > 0:
            deduct_result = self._execute_effect_atom(
                {"type": "modify_gold", "amount": -price}, context
            )
            if not deduct_result.ok:
                # Should not happen — we checked gold above, but be defensive.
                logger.warning(
                    "execute_service: gold deduction failed for %s service %s: %s",
                    character_id,
                    service_id,
                    deduct_result.message,
                )

        # 6. one_shot: revoke the service after first successful execution.
        if service.get("one_shot", False):
            revoke_cmd = Command(
                type="planner_revoke_service",
                params={"npc_id": character_id, "service_id": service_id},
                source="npc_service",
            )
            revoke_result = context.run_command(revoke_cmd)
            if not revoke_result.executed:
                logger.warning(
                    "execute_service: revoke_service failed for %s/%s: %s",
                    character_id,
                    service_id,
                    revoke_result.errors,
                )

        # 7. Write to SceneBus.
        label = str(service.get("label", service_id))
        self._add_scene_entry(
            context,
            character_id,
            f"为冒险者提供了{label}服务",
            tags=["service", "execute_service"],
        )

        return ToolResult(
            ok=True,
            message=f"服务 '{label}' 已执行。",
            metadata={
                "status": "ok",
                "event_type": "service_executed",
                "character_id": character_id,
                "service_id": service_id,
                "effects_applied": effects_applied,
                "price_paid": price,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_services(
        self, context: AgentContext, character_id: str,
    ) -> list[dict[str, Any]]:
        """Return merged services list injected by the context builder.

        The context builder is responsible for merging content-layer and
        planner-layer services and injecting them under
        ``context.metadata["role_data"]["services"]``.  We read from there
        without re-merging here to keep the tool thin.

        Falls back to an empty list if the metadata is absent.
        """
        metadata = context.metadata if isinstance(context.metadata, dict) else {}
        role_data = metadata.get("role_data")
        if isinstance(role_data, dict):
            services = role_data.get("services", [])
            if isinstance(services, list):
                return services
        return []

    def _check_preconditions(
        self, service: dict[str, Any], context: AgentContext,
    ) -> ToolResult | None:
        """Return an error ToolResult if any service precondition is not met.

        Currently supported precondition keys:

        - ``quest_completed``: the referenced quest_id must have status
          ``"completed"`` or ``"reported"`` in the dynamic_quests slice.
          If the quest is not in dynamic_quests, also accepts milestone state
          ``COMPLETED``.

        Returns None when all preconditions pass.
        """
        preconditions = service.get("preconditions")
        if not isinstance(preconditions, dict) or not preconditions:
            return None

        quest_id = preconditions.get("quest_completed")
        if quest_id is not None and isinstance(quest_id, str) and quest_id.strip():
            quest_id = quest_id.strip()
            if not context.state.has_slice("quests"):
                return ToolResult(
                    ok=False,
                    message="任务尚未完成（无法验证任务状态）。",
                    metadata={
                        "status": "quest_not_completed",
                        "quest_id": quest_id,
                    },
                )
            # Check dynamic quest status
            dq = context.state.quests.get_dynamic_quest(quest_id)
            if isinstance(dq, dict):
                status = dq.get("status", "")
                if status not in {"completed", "reported"}:
                    return ToolResult(
                        ok=False,
                        message="任务尚未完成，无法领取报酬。",
                        metadata={
                            "status": "quest_not_completed",
                            "quest_id": quest_id,
                            "quest_status": status,
                        },
                    )
            else:
                # Not in dynamic_quests — check milestone state
                ms_state = context.state.quests.get_milestone_state(quest_id)
                if ms_state != "COMPLETED":
                    return ToolResult(
                        ok=False,
                        message="任务尚未完成，无法领取报酬。",
                        metadata={
                            "status": "quest_not_completed",
                            "quest_id": quest_id,
                        },
                    )

        return None  # all preconditions satisfied

    def _check_gold(
        self, context: AgentContext, price: int,
    ) -> ToolResult | None:
        """Return an error ToolResult if the player cannot afford the service."""
        if not context.state.has_slice("player"):
            return ToolResult(
                ok=False,
                message="无法验证玩家金币（player 状态切片不可用）。",
                metadata={"status": "state_unavailable"},
            )
        gold = int(context.state.player.gold)
        if gold < price:
            return ToolResult(
                ok=False,
                message=f"金币不足：需要 {price} 枚金币，当前持有 {gold} 枚。",
                metadata={
                    "status": "insufficient_gold",
                    "required": price,
                    "current": gold,
                },
            )
        return None  # ok

    def _execute_effect_atom(
        self, atom: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        """Execute a single effect atom.  Returns ToolResult(ok=True/False)."""
        atom_type = atom.get("type", "")

        # Direct effects handled by ServiceEffectHandler.
        if atom_type in _DIRECT_EFFECT_TYPES:
            amount = atom.get("amount", 0)
            try:
                amount = int(amount)
            except (TypeError, ValueError):
                amount = 0
            cmd = Command(
                type="npc_service_effect",
                params={"effect_type": atom_type, "amount": amount},
                source="npc_service",
            )
            result = context.run_command(cmd)
            if not result.executed:
                return ToolResult(
                    ok=False,
                    message=result.errors[0] if result.errors else "command failed",
                    metadata={"status": "command_failed"},
                )
            return ToolResult(ok=True, message=f"{atom_type} applied")

        # Canonical command mapping.
        cmd_type = _COMMAND_MAP.get(atom_type)
        if cmd_type is None:
            logger.warning(
                "execute_service: unrecognised effect atom type '%s'; skipping",
                atom_type,
            )
            return ToolResult(
                ok=False,
                message=f"未知效果类型 '{atom_type}'",
                metadata={"status": "unknown_effect_type"},
            )

        # Build params for the canonical command, forwarding all atom keys
        # except "type".
        cmd_params = {k: v for k, v in atom.items() if k != "type"}
        cmd = Command(
            type=cmd_type,
            params=cmd_params,
            source="npc_service",
        )
        result = context.run_command(cmd)
        if not result.executed:
            return ToolResult(
                ok=False,
                message=result.errors[0] if result.errors else "command failed",
                metadata={"status": "command_failed"},
            )
        return ToolResult(ok=True, message=f"{atom_type} applied")
