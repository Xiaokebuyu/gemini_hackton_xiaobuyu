"""Inbound adapter protocols."""

from __future__ import annotations

from typing import Any, Mapping, Protocol


class InputPort(Protocol):
    """Protocol for inbound requests."""

    async def process_text(self, text: str) -> Any:
        """Handle free-text input."""

    async def process_action(self, action: dict[str, Any]) -> Any:
        """Handle structured action input."""


class NullInputPort:
    """No-op inbound adapter."""

    async def process_text(self, text: str) -> Any:
        return {"status": "stub", "text": text}

    async def process_action(self, action: dict[str, Any]) -> Any:
        return {"status": "stub", "action": dict(action)}


class CommandAliasInputPort:
    """Parse a minimal set of text aliases into structured actions."""

    _MOVE_ALIASES = {"go", "move", "travel"}
    _ENTER_ALIASES = {"enter"}
    _LEAVE_ALIASES = {"leave", "exit"}

    async def process_text(self, text: str) -> Any:
        normalized_text = str(text).strip()
        if not normalized_text:
            return self._rejected(
                normalized_text=normalized_text,
                code="empty_input",
                message="text input must be non-empty",
            )

        command_word, _, rest = normalized_text.partition(" ")
        command = command_word.casefold()
        argument = rest.strip()

        if command in self._MOVE_ALIASES:
            if not argument:
                return self._rejected(
                    normalized_text=normalized_text,
                    code="missing_argument",
                    message="area_id is required for go/move/travel",
                )
            return self._parsed(
                normalized_text=normalized_text,
                action_type="move_area",
                params={"area_id": argument},
            )

        if command in self._ENTER_ALIASES:
            if not argument:
                return self._rejected(
                    normalized_text=normalized_text,
                    code="missing_argument",
                    message="location_id is required for enter",
                )
            return self._parsed(
                normalized_text=normalized_text,
                action_type="enter_sub_location",
                params={"location_id": argument},
            )

        if command in self._LEAVE_ALIASES:
            if argument:
                return self._rejected(
                    normalized_text=normalized_text,
                    code="unexpected_argument",
                    message="leave/exit does not accept additional arguments",
                )
            return self._parsed(
                normalized_text=normalized_text,
                action_type="leave_sub_location",
                params={},
            )

        return self._rejected(
            normalized_text=normalized_text,
            code="unsupported_input",
            message="input is not a supported command alias",
        )

    async def process_action(self, action: dict[str, Any]) -> Any:
        return {"status": "stub", "action": dict(action)}

    @staticmethod
    def _parsed(
        *,
        normalized_text: str,
        action_type: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "status": "parsed",
            "normalized_text": normalized_text,
            "action_type": action_type,
            "params": dict(params),
        }

    @staticmethod
    def _rejected(
        *,
        normalized_text: str,
        code: str,
        message: str,
    ) -> dict[str, Any]:
        return {
            "status": "rejected",
            "normalized_text": normalized_text,
            "code": code,
            "message": message,
        }


class FastAPIInputPort:
    """Route-facing inbound adapter for text and structured interaction payloads."""

    def __init__(self, text_port: CommandAliasInputPort | None = None) -> None:
        self._text_port = text_port or CommandAliasInputPort()

    async def process_text(self, text: str) -> Any:
        return await self._text_port.process_text(text)

    async def process_action(self, action: dict[str, Any]) -> Any:
        channel = str(action.get("channel", "")).strip().casefold()
        payload = action.get("payload", {})
        payload_map = payload if isinstance(payload, Mapping) else {}
        if channel != "interaction":
            return {"status": "stub", "action": dict(action)}
        return self._normalize_interaction(payload_map)

    def _normalize_interaction(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        target_kind = self._normalized_kind(payload.get("target_kind"))
        target_id = self._normalized_id(payload.get("target_id"))
        legacy_npc_id = self._normalized_id(payload.get("npc_id"))
        if target_kind is None and target_id is None and legacy_npc_id is not None:
            target_kind = "npc"
            target_id = legacy_npc_id

        intent = str(payload.get("intent", "")).strip().casefold()
        item_id = self._normalized_id(payload.get("item_id"))
        quest_id = self._normalized_id(payload.get("quest_id"))
        count = self._coerce_int(payload.get("count"), 1)
        base = {
            "target_kind": target_kind,
            "target_id": target_id,
            "intent": intent,
            "item_id": item_id,
            "quest_id": quest_id,
            "count": count,
        }

        if target_kind is None or target_id is None:
            return self._rejected_action(
                **base,
                code="missing_target",
                message="target_kind and target_id are required",
            )
        if target_kind not in {"npc", "board"}:
            return self._rejected_action(
                **base,
                code="invalid_target_kind",
                message="target_kind must be npc or board",
            )

        if target_kind == "npc":
            return self._normalize_npc_interaction(base)
        return self._normalize_board_interaction(base)

    def _normalize_npc_interaction(self, base: Mapping[str, Any]) -> dict[str, Any]:
        target_id = str(base["target_id"])
        intent = str(base["intent"])
        item_id = base.get("item_id")
        quest_id = base.get("quest_id")
        count = int(base.get("count", 1))
        if intent not in {
            "browse",
            "buy",
            "sell",
            "talk",
            "greet",
            "inspect_item",
            "ask_quest",
            "ask_progress",
            "ask_location",
            "ask_requirements",
            "ask_reward",
        }:
            return self._rejected_action(
                **base,
                code="invalid_intent",
                message=(
                    "npc intent must be browse, buy, sell, talk, greet, "
                    "inspect_item, ask_quest, ask_progress, ask_location, "
                    "ask_requirements, or ask_reward"
                ),
            )
        if intent == "browse":
            return self._resolved_action(
                target_kind="npc",
                target_id=target_id,
                intent="browse",
                item_id=None,
                quest_id=None,
                count=1,
                execution={"kind": "shop_refresh"},
            )
        if intent in {"buy", "sell"}:
            if item_id is None:
                return self._rejected_action(
                    **base,
                    code="missing_item",
                    message="item_id is required for buy/sell",
                )
            if count < 1:
                return self._rejected_action(
                    **base,
                    code="invalid_count",
                    message="count must be >= 1",
                )
            action_type = "trade_buy" if intent == "buy" else "trade_sell"
            params = {
                ("seller_npc" if intent == "buy" else "buyer_npc"): target_id,
                "item_id": item_id,
                "count": count,
            }
            return self._resolved_action(
                target_kind="npc",
                target_id=target_id,
                intent=intent,
                item_id=str(item_id),
                quest_id=None,
                count=count,
                execution={
                    "kind": "pipeline_action",
                    "action_type": action_type,
                    "params": params,
                    "post_snapshot": "shop",
                },
            )
        if intent == "inspect_item":
            if item_id is None:
                return self._rejected_action(
                    **base,
                    code="missing_item",
                    message="item_id is required for inspect_item",
                )
            return self._resolved_action(
                target_kind="npc",
                target_id=target_id,
                intent="inspect_item",
                item_id=str(item_id),
                quest_id=None,
                count=1,
                execution={
                    "kind": "snapshot",
                    "snapshot_type": "inspect_item",
                    "item_id": str(item_id),
                },
            )
        if intent == "talk":
            return self._resolved_action(
                target_kind="npc",
                target_id=target_id,
                intent="talk",
                item_id=None,
                quest_id=None,
                count=1,
                execution={"kind": "snapshot", "snapshot_type": "talk"},
            )
        if intent == "ask_quest":
            if quest_id is None:
                return self._rejected_action(
                    **base,
                    code="missing_quest",
                    message="quest_id is required for npc ask_quest",
                )
            return self._resolved_action(
                target_kind="npc",
                target_id=target_id,
                intent="ask_quest",
                item_id=None,
                quest_id=str(quest_id),
                count=1,
                execution={
                    "kind": "snapshot",
                    "snapshot_type": "quest_brief",
                    "quest_id": str(quest_id),
                },
            )
        if intent == "ask_progress":
            if quest_id is None:
                return self._rejected_action(
                    **base,
                    code="missing_quest",
                    message="quest_id is required for npc ask_progress",
                )
            return self._resolved_action(
                target_kind="npc",
                target_id=target_id,
                intent="ask_progress",
                item_id=None,
                quest_id=str(quest_id),
                count=1,
                execution={
                    "kind": "snapshot",
                    "snapshot_type": "quest_progress",
                    "quest_id": str(quest_id),
                },
            )
        if intent == "ask_location":
            if quest_id is None:
                return self._rejected_action(
                    **base,
                    code="missing_quest",
                    message="quest_id is required for npc ask_location",
                )
            return self._resolved_action(
                target_kind="npc",
                target_id=target_id,
                intent="ask_location",
                item_id=None,
                quest_id=str(quest_id),
                count=1,
                execution={
                    "kind": "snapshot",
                    "snapshot_type": "quest_location",
                    "quest_id": str(quest_id),
                },
            )
        if intent == "ask_requirements":
            if quest_id is None:
                return self._rejected_action(
                    **base,
                    code="missing_quest",
                    message="quest_id is required for npc ask_requirements",
                )
            return self._resolved_action(
                target_kind="npc",
                target_id=target_id,
                intent="ask_requirements",
                item_id=None,
                quest_id=str(quest_id),
                count=1,
                execution={
                    "kind": "snapshot",
                    "snapshot_type": "quest_requirements",
                    "quest_id": str(quest_id),
                },
            )
        if intent == "ask_reward":
            if quest_id is None:
                return self._rejected_action(
                    **base,
                    code="missing_quest",
                    message="quest_id is required for npc ask_reward",
                )
            return self._resolved_action(
                target_kind="npc",
                target_id=target_id,
                intent="ask_reward",
                item_id=None,
                quest_id=str(quest_id),
                count=1,
                execution={
                    "kind": "snapshot",
                    "snapshot_type": "quest_reward",
                    "quest_id": str(quest_id),
                },
            )
        return self._resolved_action(
            target_kind="npc",
            target_id=target_id,
            intent="greet",
            item_id=None,
            quest_id=None,
            count=1,
            execution={
                "kind": "pipeline_action",
                "action_type": "add_knowledge",
                "params": {
                    "npc_id": target_id,
                    "impression": "Shared a brief greeting.",
                },
                "post_snapshot": "talk",
            },
        )

    def _normalize_board_interaction(self, base: Mapping[str, Any]) -> dict[str, Any]:
        target_id = str(base["target_id"])
        intent = str(base["intent"])
        quest_id = base.get("quest_id")
        if intent not in {"browse", "accept", "complete", "retire"}:
            return self._rejected_action(
                **base,
                code="invalid_intent",
                message="board intent must be browse, accept, complete, or retire",
            )
        if intent == "browse":
            return self._resolved_action(
                target_kind="board",
                target_id=target_id,
                intent="browse",
                item_id=None,
                quest_id=None,
                count=1,
                execution={"kind": "snapshot", "snapshot_type": "board"},
            )
        if quest_id is None:
            board_messages = {
                "accept": "quest_id is required for board accept",
                "complete": "quest_id is required for board complete",
                "retire": "quest_id is required for board retire",
            }
            return self._rejected_action(
                **base,
                code="missing_quest",
                message=board_messages.get(intent, "quest_id is required for board accept"),
            )
        target_states = {
            "accept": "active",
            "complete": "completed",
            "retire": "retired",
        }
        return self._resolved_action(
            target_kind="board",
            target_id=target_id,
            intent=intent,
            item_id=None,
            quest_id=str(quest_id),
            count=1,
            execution={
                "kind": "pipeline_action",
                "action_type": "advance_quest",
                "params": {
                    "quest_id": str(quest_id),
                    "to_state": target_states[intent],
                    "quest_kind": "dynamic",
                },
                "post_snapshot": "board",
            },
        )

    @staticmethod
    def _normalized_kind(value: Any) -> str | None:
        text = str(value or "").strip().casefold()
        return text or None

    @staticmethod
    def _normalized_id(value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:
        if value is None or isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _resolved_action(
        *,
        target_kind: str,
        target_id: str,
        intent: str,
        item_id: str | None,
        quest_id: str | None,
        count: int,
        execution: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "status": "resolved",
            "target_kind": target_kind,
            "target_id": target_id,
            "intent": intent,
            "item_id": item_id,
            "quest_id": quest_id,
            "count": count,
            "execution": dict(execution),
        }

    @staticmethod
    def _rejected_action(
        *,
        target_kind: str | None,
        target_id: str | None,
        intent: str,
        item_id: str | None,
        quest_id: str | None,
        count: int,
        code: str,
        message: str,
    ) -> dict[str, Any]:
        return {
            "status": "rejected",
            "target_kind": target_kind,
            "target_id": target_id,
            "intent": intent,
            "item_id": item_id,
            "quest_id": quest_id,
            "count": count,
            "code": code,
            "message": message,
        }
