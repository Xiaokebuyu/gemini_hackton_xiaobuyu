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

The module-level ``execute_service_effects()`` function encapsulates the
atomic execution logic and is reused by the player-panel ``buy_service``
intent path in the application layer (app/interaction_service.py).

Decision record: D-Svc03, D-SvcA1 (narrative.md)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

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


# ---------------------------------------------------------------------------
# Shared result type
# ---------------------------------------------------------------------------


@dataclass
class ServiceExecutionResult:
    """Result of a service execution, returned by ``execute_service_effects()``.

    Attributes:
        success: Whether the service was fully applied.
        message: Human-readable status message.
        applied_effects: List of effect-atom type strings that were applied.
        price_paid: Amount of gold deducted from the player.
        status: Machine-readable status tag (e.g. ``"ok"``, ``"insufficient_gold"``).
    """

    success: bool
    message: str
    applied_effects: list[str] = field(default_factory=list)
    price_paid: int = 0
    status: str = "ok"
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Shared execution helper — used by NPC tool AND player buy_service intent
# ---------------------------------------------------------------------------


def execute_service_effects(
    service: dict[str, Any],
    run_command: Callable[[Command], Any],
    *,
    player_gold: int,
    npc_id: str = "",
) -> ServiceExecutionResult:
    """Execute the effect atoms of one service and deduct its price.

    This is the core logic shared between:
    - ``ExecuteServiceTool.execute()`` (NPC tool path)
    - ``InteractionService._execute_buy_service()`` (player panel path)

    Args:
        service: The service descriptor dict (contains ``effects``, ``price``,
            ``one_shot``, ``service_id``, ``npc_id`` etc.).
        run_command: Callable that accepts a ``Command`` and returns a result
            with ``.executed`` and ``.errors`` attributes.
        player_gold: Current player gold (used for the pre-flight check only;
            the actual deduction happens via run_command).
        npc_id: NPC id string, used when issuing ``planner_revoke_service``
            for one_shot services.

    Returns:
        ``ServiceExecutionResult`` with success flag, message, and effect list.
    """
    service_id = str(service.get("service_id", ""))
    label = str(service.get("label", service_id))
    price = 0
    try:
        price = int(service.get("price", 0))
    except (TypeError, ValueError):
        price = 0

    # Pre-flight: gold check.
    if price > 0 and player_gold < price:
        return ServiceExecutionResult(
            success=False,
            message=f"金币不足：需要 {price} 枚金币，当前持有 {player_gold} 枚。",
            status="insufficient_gold",
            extra={"required": price, "current": player_gold},
        )

    # Execute each effect atom in order.
    effects: list[dict[str, Any]] = service.get("effects", [])
    if not isinstance(effects, list):
        effects = []

    applied: list[str] = []
    for atom in effects:
        if not isinstance(atom, dict):
            continue
        atom_type = atom.get("type", "")
        ok, err = _run_effect_atom(atom, run_command)
        if not ok:
            return ServiceExecutionResult(
                success=False,
                message=f"效果 '{atom_type}' 执行失败：{err}",
                status="effect_failed",
            )
        applied.append(str(atom_type))

    # Deduct price.
    if price > 0:
        ok, err = _run_effect_atom({"type": "modify_gold", "amount": -price}, run_command)
        if not ok:
            logger.warning(
                "execute_service_effects: gold deduction failed for %s/%s: %s",
                npc_id,
                service_id,
                err,
            )

    # one_shot: revoke after first successful execution.
    if service.get("one_shot", False) and npc_id and service_id:
        revoke_cmd = Command(
            type="planner_revoke_service",
            params={"npc_id": npc_id, "service_id": service_id},
            source="npc_service",
        )
        revoke_result = run_command(revoke_cmd)
        if not revoke_result.executed:
            logger.warning(
                "execute_service_effects: revoke_service failed for %s/%s: %s",
                npc_id,
                service_id,
                revoke_result.errors,
            )

    return ServiceExecutionResult(
        success=True,
        message=f"服务 '{label}' 已执行。",
        applied_effects=applied,
        price_paid=price,
        status="ok",
    )


def _run_effect_atom(
    atom: dict[str, Any],
    run_command: Callable[[Command], Any],
) -> tuple[bool, str]:
    """Execute one effect atom. Returns (ok, error_message)."""
    atom_type = atom.get("type", "")

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
        result = run_command(cmd)
        if not result.executed:
            err = result.errors[0] if result.errors else "command failed"
            return False, str(err)
        return True, ""

    cmd_type = _COMMAND_MAP.get(atom_type)
    if cmd_type is None:
        logger.warning(
            "_run_effect_atom: unrecognised effect atom type '%s'; skipping", atom_type,
        )
        return False, f"未知效果类型 '{atom_type}'"

    cmd_params = {k: v for k, v in atom.items() if k != "type"}
    cmd = Command(type=cmd_type, params=cmd_params, source="npc_service")
    result = run_command(cmd)
    if not result.executed:
        err = result.errors[0] if result.errors else "command failed"
        return False, str(err)
    return True, ""


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

        # 3. Validate preconditions (quest_completed check).
        precond_check = self._check_preconditions(service, context)
        if precond_check is not None:
            return precond_check

        # 4. Read current gold for the pre-flight check in execute_service_effects.
        # Only needed when the service has a price — free services skip the player check.
        price = 0
        try:
            price = int(service.get("price", 0))
        except (TypeError, ValueError):
            price = 0
        if price > 0 and not context.state.has_slice("player"):
            return ToolResult(
                ok=False,
                message="无法验证玩家金币（player 状态切片不可用）。",
                metadata={"status": "state_unavailable"},
            )
        player_gold = int(context.state.player.gold) if context.state.has_slice("player") else 0

        # 5. Delegate to the shared execution helper.
        exec_result = execute_service_effects(
            service,
            context.run_command,
            player_gold=player_gold,
            npc_id=character_id,
        )
        if not exec_result.success:
            return ToolResult(
                ok=False,
                message=exec_result.message,
                metadata={"status": exec_result.status, **exec_result.extra},
            )

        # 6. Write to SceneBus.
        label = str(service.get("label", service_id))
        self._add_scene_entry(
            context,
            character_id,
            f"为冒险者提供了{label}服务",
            tags=["service", "execute_service"],
        )

        return ToolResult(
            ok=True,
            message=exec_result.message,
            metadata={
                "status": "ok",
                "event_type": "service_executed",
                "character_id": character_id,
                "service_id": service_id,
                "effects_applied": exec_result.applied_effects,
                "price_paid": exec_result.price_paid,
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

