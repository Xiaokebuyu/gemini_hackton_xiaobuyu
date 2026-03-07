"""Tests for serialized multi-round group dialogue and free-chat.

Phase D of the group-dialogue plan.  Covers:
  1. Serialized evaluation — teammate A's speech appears in B's observation
  2. Reply limit — MAX_REPLIES_PER_PARTICIPANT caps per-teammate replies
  3. Natural convergence — all silent → loop ends; no teammates → immediate end
  4. Free chat — execute_free_chat skips NPC/GM, round_messages starts with player only
  5. SSE event ordering — ordered_responses drives event order
  6. ContextWindow writing — _write_teammate_context_windows populates each teammate
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.game_core.bootstrap import build_default_world, build_runtime_for_world
from app.game_core.content import WorldInstance
from app.game_core.narrative.companion_runtime import CompanionRuntimeManager
from app.game_core.narrative.context_window import ContextWindow, WindowMessage
from app.game_core.narrative.executor import AgenticExecutor
from app.game_core.narrative.gm_tools import register_gm_tools
from app.game_core.narrative.character_tools import register_npc_tools, register_teammate_tools
from app.game_core.narrative.models import AgentResult, ToolResult
from app.game_core.narrative.registry import RoleToolRegistry
from app.game_core.orchestration.npc_interaction import (
    MAX_REPLIES_PER_PARTICIPANT,
    NpcInteractionCoordinator,
    NpcInteractionResult,
    RoundMessage,
    _build_group_observation,
)
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateContainer


# ------------------------------------------------------------------
# LLM stub
# ------------------------------------------------------------------


class RecordingLlmProvider:
    """Returns pre-configured responses for deterministic tests."""

    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses or [])
        self._call_index = 0

    async def generate(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tool_declarations: list[dict[str, Any]],
    ) -> Any:
        self.calls.append({
            "system_prompt": system_prompt,
            "history": history,
            "tool_declarations": tool_declarations,
        })
        from app.game_core.adapters.llm import LlmResponse

        if self._call_index < len(self._responses):
            resp = self._responses[self._call_index]
            self._call_index += 1
            return LlmResponse(
                text=resp.get("text", ""),
                tool_calls=resp.get("tool_calls", []),
                finish_reason=resp.get("finish_reason", "stop"),
            )
        return LlmResponse(text="(no more responses)")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_TAGS_DATA: dict[str, Any] = {
    "profession": {"id": "profession", "tags": ["warrior"]},
    "ancestry": {"id": "ancestry", "tags": ["human"]},
}


def _npc_speak(text: str = "Hello.") -> dict[str, Any]:
    return {
        "tool_calls": [{"name": "speak", "args": {"text": text}}],
        "finish_reason": "tool_calls",
    }


def _gm_pass() -> dict[str, Any]:
    return {
        "tool_calls": [{"name": "pass_turn", "args": {}}],
        "finish_reason": "tool_calls",
    }


def _tm_speak(text: str = "I agree.") -> dict[str, Any]:
    return {
        "tool_calls": [{"name": "speak", "args": {"text": text}}],
        "finish_reason": "tool_calls",
    }


def _tm_emote(text: str = "*nods*") -> dict[str, Any]:
    return {
        "tool_calls": [{"name": "emote", "args": {"action": text}}],
        "finish_reason": "tool_calls",
    }


def _tm_pass() -> dict[str, Any]:
    return {
        "tool_calls": [{"name": "pass_turn", "args": {}}],
        "finish_reason": "tool_calls",
    }


def _stop(text: str = "") -> dict[str, Any]:
    return {"text": text, "finish_reason": "stop"}


def _noop_exec(command: Command) -> ExecuteResult:
    return ExecuteResult(success=True)


def _make_world(
    *extra_chars: tuple[str, dict[str, Any]],
) -> WorldInstance:
    """World with a merchant NPC and two teammates (tm_a, tm_b)."""
    chars: dict[str, Any] = {
        "merchant": {
            "id": "merchant",
            "name": "Merchant",
            "personality": "A friendly merchant.",
            "response_tendency": 0.0,
        },
        "tm_a": {
            "id": "tm_a",
            "name": "Teammate A",
            "personality": "Brave warrior.",
            "response_tendency": 1.0,
        },
        "tm_b": {
            "id": "tm_b",
            "name": "Teammate B",
            "personality": "Quiet rogue.",
            "response_tendency": 1.0,
        },
    }
    for cid, cdata in extra_chars:
        chars[cid] = cdata
    return build_default_world(
        "test_world",
        world_data={"tags": _TAGS_DATA, "characters": chars},
    )


def _make_state(world: WorldInstance, *, members: dict[str, Any] | None = None) -> StateContainer:
    """State with player + party members."""
    runtime = build_runtime_for_world(world)
    state = runtime.state
    state.player.restore({
        "character_name": "Hero",
        "character_class": "warrior",
        "current_area": "town",
        "current_location": "market",
    })
    state.relations.restore({
        "npc_dispositions": {
            "merchant": {"approval": 20, "trust": 10, "fear": 0, "romance": 0},
            "tm_a": {"approval": 40, "trust": 50, "fear": 0, "romance": 0},
            "tm_b": {"approval": 30, "trust": 40, "fear": 0, "romance": 0},
        },
        "relationship_stages": {
            "merchant": "acquaintance",
            "tm_a": "friend",
            "tm_b": "acquaintance",
        },
    })
    if members is None:
        members = {"tm_a": {"role": "warrior"}, "tm_b": {"role": "rogue"}}
    state.party.restore({"members": members})
    return state


def _build_coordinator(
    llm_responses: list[dict[str, Any]] | None = None,
    world: WorldInstance | None = None,
    state: StateContainer | None = None,
    *,
    companion_manager: CompanionRuntimeManager | None = None,
) -> tuple[NpcInteractionCoordinator, RecordingLlmProvider]:
    if world is None:
        world = _make_world()
    if state is None:
        state = _make_state(world)
    llm = RecordingLlmProvider(llm_responses)
    registry = RoleToolRegistry()
    register_gm_tools(registry)
    register_npc_tools(registry)
    register_teammate_tools(registry)
    executor = AgenticExecutor(tool_registry=registry, llm=llm)
    coordinator = NpcInteractionCoordinator(
        executor, world, state, companion_manager=companion_manager,
    )
    return coordinator, llm


# ------------------------------------------------------------------
# 1. Serialized evaluation — cumulative context
# ------------------------------------------------------------------


class TestSerializedEvaluation:
    """Teammate B should see teammate A's speech in its observation."""

    def test_teammate_b_sees_a_speech_in_observation(self) -> None:
        """After A speaks, B's user_message contains A's speech."""
        import app.game_core.orchestration.npc_interaction as _npc_mod

        world = _make_world()
        state = _make_state(world)
        coordinator, llm = _build_coordinator(
            llm_responses=[
                _npc_speak("Welcome traveler!"),  # NPC
                _gm_pass(),                        # GM
                _stop(),                           # GM stop
                _tm_speak("Great to be here!"),    # tm_a round 1
                _tm_speak("Indeed."),              # tm_b round 1
                # Round 2: both pass
                _tm_pass(),                        # tm_a round 2
                _stop(),                           # tm_a stop
                _tm_pass(),                        # tm_b round 2
                _stop(),                           # tm_b stop
            ],
            world=world,
            state=state,
        )

        with patch.object(_npc_mod.random, "random", return_value=0.0):
            result = asyncio.run(coordinator.execute_interaction(
                npc_id="merchant",
                player_message="Hello!",
                execute_command=_noop_exec,
            ))

        assert result.success is True

        # Find the LLM call for tm_b (should be the 4th call: NPC, GM, tm_a, tm_b)
        # The user_message in tm_b's call should contain tm_a's speech
        tm_b_calls = [
            c for c in llm.calls
            if any(
                msg.get("parts", [{}])[0].get("text", "")
                for msg in c["history"]
                if isinstance(msg, dict) and msg.get("role") == "user"
                and "Great to be here!" in str(msg.get("parts", []))
            )
        ]
        # tm_b should have received cumulative observation including tm_a's speech
        assert len(tm_b_calls) >= 1, (
            "tm_b should see tm_a's 'Great to be here!' in its observation"
        )

    def test_round_messages_contain_all_participants(self) -> None:
        """round_messages should include player, NPC, and all teammate messages."""
        import app.game_core.orchestration.npc_interaction as _npc_mod

        world = _make_world()
        state = _make_state(world)
        coordinator, _ = _build_coordinator(
            llm_responses=[
                _npc_speak("Greetings!"),
                _gm_pass(),
                _stop(),
                _tm_speak("Hello there!"),   # tm_a
                _tm_speak("Hi!"),            # tm_b
                # Round 2: both pass
                _tm_pass(),
                _stop(),
                _tm_pass(),
                _stop(),
            ],
            world=world,
            state=state,
        )

        with patch.object(_npc_mod.random, "random", return_value=0.0):
            result = asyncio.run(coordinator.execute_interaction(
                npc_id="merchant",
                player_message="Greetings everyone!",
                execute_command=_noop_exec,
            ))

        assert result.success is True
        speakers = [m.speaker_id for m in result.round_messages]
        assert "player" in speakers
        assert "merchant" in speakers
        assert "tm_a" in speakers
        assert "tm_b" in speakers


# ------------------------------------------------------------------
# 2. Reply limit — MAX_REPLIES_PER_PARTICIPANT
# ------------------------------------------------------------------


class TestReplyLimit:
    def test_max_replies_constant_value(self) -> None:
        """MAX_REPLIES_PER_PARTICIPANT is 2."""
        assert MAX_REPLIES_PER_PARTICIPANT == 2

    def test_teammate_stops_after_max_replies(self) -> None:
        """A teammate who reaches MAX_REPLIES is not evaluated again."""
        import app.game_core.orchestration.npc_interaction as _npc_mod

        world = _make_world()
        # Only one teammate so we can precisely track calls
        state = _make_state(world, members={"tm_a": {"role": "warrior"}})
        coordinator, llm = _build_coordinator(
            llm_responses=[
                _npc_speak("Hello."),     # NPC
                _gm_pass(),              # GM
                _stop(),                  # GM stop
                _tm_speak("Reply 1"),     # tm_a round 1 (reply_count=1)
                _tm_speak("Reply 2"),     # tm_a round 2 (reply_count=2 → capped)
                # Round 3 should NOT call tm_a (capped) → loop ends
            ],
            world=world,
            state=state,
        )

        with patch.object(_npc_mod.random, "random", return_value=0.0):
            result = asyncio.run(coordinator.execute_interaction(
                npc_id="merchant",
                player_message="Hey",
                execute_command=_noop_exec,
            ))

        assert result.success is True
        # tm_a should have exactly 2 speech messages in round_messages
        tm_a_speeches = [
            m for m in result.round_messages
            if m.speaker_id == "tm_a" and m.event_type == "speech"
        ]
        assert len(tm_a_speeches) == 2
        assert tm_a_speeches[0].content == "Reply 1"
        assert tm_a_speeches[1].content == "Reply 2"

    def test_different_teammates_can_have_different_reply_counts(self) -> None:
        """tm_a speaks twice, tm_b speaks once — both are valid."""
        import app.game_core.orchestration.npc_interaction as _npc_mod

        world = _make_world()
        state = _make_state(world)
        coordinator, _ = _build_coordinator(
            llm_responses=[
                _npc_speak("Hello."),
                _gm_pass(),
                _stop(),
                _tm_speak("A says 1"),    # tm_a round 1
                _tm_speak("B says 1"),    # tm_b round 1
                _tm_speak("A says 2"),    # tm_a round 2 (capped after this)
                _tm_pass(),               # tm_b round 2 pass
                _stop(),
                # Round 3: tm_a capped, tm_b already passed → ends
            ],
            world=world,
            state=state,
        )

        with patch.object(_npc_mod.random, "random", return_value=0.0):
            result = asyncio.run(coordinator.execute_interaction(
                npc_id="merchant",
                player_message="Hey",
                execute_command=_noop_exec,
            ))

        assert result.success is True
        tm_a_speeches = [
            m for m in result.round_messages
            if m.speaker_id == "tm_a" and m.event_type == "speech"
        ]
        tm_b_speeches = [
            m for m in result.round_messages
            if m.speaker_id == "tm_b" and m.event_type == "speech"
        ]
        assert len(tm_a_speeches) == 2
        assert len(tm_b_speeches) == 1


# ------------------------------------------------------------------
# 3. Natural convergence
# ------------------------------------------------------------------


class TestNaturalConvergence:
    def test_all_pass_turn_ends_loop(self) -> None:
        """When all teammates pass, the while-loop exits."""
        import app.game_core.orchestration.npc_interaction as _npc_mod

        world = _make_world()
        state = _make_state(world)
        coordinator, llm = _build_coordinator(
            llm_responses=[
                _npc_speak("Hello."),
                _gm_pass(),
                _stop(),
                _tm_pass(),    # tm_a passes
                _stop(),
                _tm_pass(),    # tm_b passes
                _stop(),
                # → anyone_spoke remains False → break
            ],
            world=world,
            state=state,
        )

        with patch.object(_npc_mod.random, "random", return_value=0.0):
            result = asyncio.run(coordinator.execute_interaction(
                npc_id="merchant",
                player_message="Hello",
                execute_command=_noop_exec,
            ))

        assert result.success is True
        # No teammate speech in round_messages — only player + NPC
        tm_speeches = [
            m for m in result.round_messages
            if m.speaker_role == "teammate" and m.event_type == "speech"
        ]
        assert tm_speeches == []

    def test_no_teammates_ends_immediately(self) -> None:
        """With empty party, Step 4 produces no teammate results."""
        world = _make_world()
        state = _make_state(world, members={})
        coordinator, _ = _build_coordinator(
            llm_responses=[
                _npc_speak("Hello."),
                _gm_pass(),
                _stop(),
            ],
            world=world,
            state=state,
        )

        result = asyncio.run(coordinator.execute_interaction(
            npc_id="merchant",
            player_message="Hello",
            execute_command=_noop_exec,
        ))

        assert result.success is True
        assert result.ordered_responses == []
        assert result.teammate_results == {}
        # round_messages: player + NPC only
        assert len(result.round_messages) == 2
        assert result.round_messages[0].speaker_role == "player"
        assert result.round_messages[1].speaker_role == "npc"

    def test_emote_does_not_count_as_speech_for_convergence(self) -> None:
        """Emotes appear in round_messages but do not increment reply_counts
        and do not set anyone_spoke=True — so an emote-only round converges."""
        import app.game_core.orchestration.npc_interaction as _npc_mod

        world = _make_world()
        state = _make_state(world, members={"tm_a": {"role": "warrior"}})
        coordinator, _ = _build_coordinator(
            llm_responses=[
                _npc_speak("Hi."),
                _gm_pass(),
                _stop(),
                _tm_emote("*waves*"),   # emote does not set anyone_spoke
                # → anyone_spoke remains False → break
            ],
            world=world,
            state=state,
        )

        with patch.object(_npc_mod.random, "random", return_value=0.0):
            result = asyncio.run(coordinator.execute_interaction(
                npc_id="merchant",
                player_message="Hi",
                execute_command=_noop_exec,
            ))

        assert result.success is True
        emotes = [
            m for m in result.round_messages
            if m.speaker_id == "tm_a" and m.event_type == "emote"
        ]
        assert len(emotes) == 1
        assert emotes[0].content == "*waves*"
        # No speech from teammates
        tm_speeches = [
            m for m in result.round_messages
            if m.speaker_role == "teammate" and m.event_type == "speech"
        ]
        assert tm_speeches == []


# ------------------------------------------------------------------
# 4. Free chat — no NPC
# ------------------------------------------------------------------


class TestFreeChatNoNpc:
    def test_free_chat_result_has_empty_npc_id(self) -> None:
        """execute_free_chat returns npc_id='' with no npc_result/gm_result."""
        import app.game_core.orchestration.npc_interaction as _npc_mod

        world = _make_world()
        state = _make_state(world)
        coordinator, _ = _build_coordinator(
            llm_responses=[
                _tm_speak("Hey player!"),
                _tm_pass(),
                _stop(),
                # tm_b round 1 + pass round 2
                _tm_pass(),
                _stop(),
            ],
            world=world,
            state=state,
        )

        with patch.object(_npc_mod.random, "random", return_value=0.0):
            result = asyncio.run(coordinator.execute_free_chat(
                player_message="Hey team!",
                execute_command=_noop_exec,
            ))

        assert result.success is True
        assert result.npc_id == ""
        assert result.npc_result is None
        assert result.gm_result is None

    def test_free_chat_round_messages_start_with_player_only(self) -> None:
        """In free chat, round_messages[0] is the player; no NPC message follows."""
        import app.game_core.orchestration.npc_interaction as _npc_mod

        world = _make_world()
        state = _make_state(world, members={"tm_a": {"role": "warrior"}})
        coordinator, _ = _build_coordinator(
            llm_responses=[
                _tm_speak("Acknowledged."),
                # Round 2: pass
                _tm_pass(),
                _stop(),
            ],
            world=world,
            state=state,
        )

        with patch.object(_npc_mod.random, "random", return_value=0.0):
            result = asyncio.run(coordinator.execute_free_chat(
                player_message="What do you think?",
                execute_command=_noop_exec,
            ))

        assert result.round_messages[0] == RoundMessage(
            speaker_id="player", speaker_role="player",
            content="What do you think?", event_type="speech",
        )
        npc_msgs = [m for m in result.round_messages if m.speaker_role == "npc"]
        assert npc_msgs == []

    def test_free_chat_no_party_returns_empty(self) -> None:
        """Free chat with no party members returns success with no responses."""
        world = _make_world()
        state = _make_state(world, members={})
        coordinator, _ = _build_coordinator(
            llm_responses=[],
            world=world,
            state=state,
        )

        result = asyncio.run(coordinator.execute_free_chat(
            player_message="Anyone there?",
            execute_command=_noop_exec,
        ))

        assert result.success is True
        assert result.ordered_responses == []
        assert len(result.round_messages) == 1  # Only player


# ------------------------------------------------------------------
# 5. ordered_responses and NpcInteractionResult structure
# ------------------------------------------------------------------


class TestOrderedResponses:
    def test_ordered_responses_preserves_dialogue_order(self) -> None:
        """ordered_responses should list teammates in the order they spoke."""
        import app.game_core.orchestration.npc_interaction as _npc_mod

        world = _make_world()
        state = _make_state(world)
        coordinator, _ = _build_coordinator(
            llm_responses=[
                _npc_speak("Hey."),
                _gm_pass(),
                _stop(),
                _tm_speak("A first"),     # tm_a round 1
                _tm_speak("B second"),    # tm_b round 1
                # Round 2: both pass
                _tm_pass(),
                _stop(),
                _tm_pass(),
                _stop(),
            ],
            world=world,
            state=state,
        )

        with patch.object(_npc_mod.random, "random", return_value=0.0):
            result = asyncio.run(coordinator.execute_interaction(
                npc_id="merchant",
                player_message="Hey",
                execute_command=_noop_exec,
            ))

        member_ids = [mid for mid, _res in result.ordered_responses]
        assert member_ids[0] == "tm_a"
        assert member_ids[1] == "tm_b"

    def test_teammate_results_dict_is_backfilled(self) -> None:
        """teammate_results dict is populated as compat from ordered_responses."""
        import app.game_core.orchestration.npc_interaction as _npc_mod

        world = _make_world()
        state = _make_state(world)
        coordinator, _ = _build_coordinator(
            llm_responses=[
                _npc_speak("Hey."),
                _gm_pass(),
                _stop(),
                _tm_speak("Something"),
                _tm_pass(),
                _stop(),
                # Round 2: both pass
                _tm_pass(),
                _stop(),
                _tm_pass(),
                _stop(),
            ],
            world=world,
            state=state,
        )

        with patch.object(_npc_mod.random, "random", return_value=0.0):
            result = asyncio.run(coordinator.execute_interaction(
                npc_id="merchant",
                player_message="Hey",
                execute_command=_noop_exec,
            ))

        # tm_a spoke, so it must appear in both structures
        assert "tm_a" in result.teammate_results
        assert any(mid == "tm_a" for mid, _ in result.ordered_responses)


# ------------------------------------------------------------------
# 6. _build_group_observation
# ------------------------------------------------------------------


class TestBuildGroupObservation:
    def test_basic_structure(self) -> None:
        """_build_group_observation returns valid JSON with expected fields."""
        msgs = [
            RoundMessage("player", "player", "Hello!", "speech"),
            RoundMessage("npc1", "npc", "Hi there!", "speech"),
        ]
        raw = _build_group_observation(msgs)
        data = json.loads(raw)

        assert data["interaction_type"] == "group_dialogue"
        assert len(data["messages"]) == 2
        assert data["messages"][0]["speaker"] == "player"
        assert data["messages"][0]["role"] == "player"
        assert data["messages"][0]["content"] == "Hello!"
        assert data["messages"][0]["type"] == "speech"
        assert data["messages"][1]["speaker"] == "npc1"

    def test_cumulative_messages_grow(self) -> None:
        """As round_messages grows, the observation JSON includes all entries."""
        msgs = [
            RoundMessage("player", "player", "Hi", "speech"),
            RoundMessage("npc1", "npc", "Hello", "speech"),
            RoundMessage("tm_a", "teammate", "Hi there", "speech"),
        ]
        data = json.loads(_build_group_observation(msgs))
        assert len(data["messages"]) == 3
        assert data["messages"][2]["speaker"] == "tm_a"
        assert data["messages"][2]["content"] == "Hi there"

    def test_emote_type_preserved(self) -> None:
        """Emote messages should have type='emote' in the output."""
        msgs = [
            RoundMessage("tm_a", "teammate", "*waves*", "emote"),
        ]
        data = json.loads(_build_group_observation(msgs))
        assert data["messages"][0]["type"] == "emote"

    def test_empty_messages(self) -> None:
        """Empty list produces valid JSON with empty messages array."""
        data = json.loads(_build_group_observation([]))
        assert data["interaction_type"] == "group_dialogue"
        assert data["messages"] == []


# ------------------------------------------------------------------
# 7. RoundMessage dataclass
# ------------------------------------------------------------------


class TestRoundMessage:
    def test_fields(self) -> None:
        msg = RoundMessage(
            speaker_id="player",
            speaker_role="player",
            content="Hello",
            event_type="speech",
        )
        assert msg.speaker_id == "player"
        assert msg.speaker_role == "player"
        assert msg.content == "Hello"
        assert msg.event_type == "speech"

    def test_equality(self) -> None:
        a = RoundMessage("p", "player", "Hi", "speech")
        b = RoundMessage("p", "player", "Hi", "speech")
        assert a == b

    def test_inequality(self) -> None:
        a = RoundMessage("p", "player", "Hi", "speech")
        b = RoundMessage("p", "player", "Hi", "emote")
        assert a != b


# ------------------------------------------------------------------
# 8. ContextWindow writing (_write_teammate_context_windows)
# ------------------------------------------------------------------


class TestContextWindowWriting:
    def test_format_round_for_window(self) -> None:
        """_format_round_for_window produces readable text."""
        from app.agent_orchestration import _format_round_for_window

        msgs = [
            RoundMessage("player", "player", "Hello!", "speech"),
            RoundMessage("npc1", "npc", "Hi!", "speech"),
            RoundMessage("tm_a", "teammate", "*nods*", "emote"),
        ]
        text = _format_round_for_window(msgs)
        assert "[player:player] Hello!" in text
        assert "[npc:npc1] Hi!" in text
        assert "[teammate:tm_a] **nods**" in text

    def test_extract_member_speech(self) -> None:
        """_extract_member_speech returns only the specified member's speech."""
        from app.agent_orchestration import _extract_member_speech

        ordered = [
            ("tm_a", AgentResult(tool_results=[
                ToolResult(success=True, message="A says hi", metadata={"event_type": "speech"}),
            ])),
            ("tm_b", AgentResult(tool_results=[
                ToolResult(success=True, message="B says bye", metadata={"event_type": "speech"}),
            ])),
        ]
        assert _extract_member_speech(ordered, "tm_a") == "A says hi"
        assert _extract_member_speech(ordered, "tm_b") == "B says bye"
        assert _extract_member_speech(ordered, "unknown") == ""

    def test_extract_member_speech_includes_emotes(self) -> None:
        """_extract_member_speech concatenates speech + emote."""
        from app.agent_orchestration import _extract_member_speech

        ordered = [
            ("tm_a", AgentResult(tool_results=[
                ToolResult(success=True, message="Hello", metadata={"event_type": "speech"}),
                ToolResult(success=True, message="*waves*", metadata={"event_type": "emote"}),
            ])),
        ]
        result = _extract_member_speech(ordered, "tm_a")
        assert "Hello" in result
        assert "*waves*" in result

    def test_write_teammate_context_windows_populates_instances(self) -> None:
        """After _write_teammate_context_windows, each teammate's CW has entries."""
        from app.agent_orchestration import _write_teammate_context_windows

        # Build a minimal mock session with companion_manager
        comp_mgr = CompanionRuntimeManager()

        runtime_mock = MagicMock()
        runtime_mock.companion_manager = comp_mgr

        session_mock = MagicMock()
        session_mock.runtime = runtime_mock

        # Build a result with round_messages and ordered_responses
        result = NpcInteractionResult(
            success=True,
            npc_id="merchant",
            round_messages=[
                RoundMessage("player", "player", "Hi", "speech"),
                RoundMessage("merchant", "npc", "Hello", "speech"),
                RoundMessage("tm_a", "teammate", "Hi back", "speech"),
            ],
            ordered_responses=[
                ("tm_a", AgentResult(tool_results=[
                    ToolResult(success=True, message="Hi back", metadata={"event_type": "speech"}),
                ])),
                ("tm_b", AgentResult(tool_results=[
                    ToolResult(success=True, message="", metadata={"event_type": "pass"}),
                ])),
            ],
        )

        _write_teammate_context_windows(session_mock, result)

        # tm_a should have both user (round text) and model (own speech) messages
        tm_a_inst = comp_mgr.get_or_create("tm_a")
        tm_a_msgs = tm_a_inst.context_window.messages
        assert len(tm_a_msgs) >= 2  # user + model
        user_msgs = [m for m in tm_a_msgs if m.role == "user"]
        model_msgs = [m for m in tm_a_msgs if m.role == "model"]
        assert len(user_msgs) >= 1
        assert len(model_msgs) >= 1
        assert "Hi back" in model_msgs[0].content

        # tm_b participated (in ordered_responses) but had no speech
        # → user message written, no model message
        tm_b_inst = comp_mgr.get_or_create("tm_b")
        tm_b_msgs = tm_b_inst.context_window.messages
        tm_b_user = [m for m in tm_b_msgs if m.role == "user"]
        tm_b_model = [m for m in tm_b_msgs if m.role == "model"]
        assert len(tm_b_user) >= 1
        assert len(tm_b_model) == 0  # no speech

    def test_write_skipped_when_no_ordered_responses(self) -> None:
        """If ordered_responses is empty, nothing is written."""
        from app.agent_orchestration import _write_teammate_context_windows

        comp_mgr = CompanionRuntimeManager()
        runtime_mock = MagicMock()
        runtime_mock.companion_manager = comp_mgr
        session_mock = MagicMock()
        session_mock.runtime = runtime_mock

        result = NpcInteractionResult(
            success=True,
            npc_id="merchant",
            round_messages=[
                RoundMessage("player", "player", "Hi", "speech"),
            ],
            ordered_responses=[],  # empty
        )

        _write_teammate_context_windows(session_mock, result)

        # No instances should have been created
        assert len(comp_mgr._pool) == 0


# ------------------------------------------------------------------
# 9. SSE conversion uses ordered_responses
# ------------------------------------------------------------------


class TestSseConversion:
    def test_interaction_result_to_sse_uses_ordered_responses(self) -> None:
        """_interaction_result_to_sse should iterate ordered_responses."""
        from app.agent_orchestration import _interaction_result_to_sse

        result = NpcInteractionResult(
            success=True,
            npc_id="merchant",
            npc_result=AgentResult(tool_results=[
                ToolResult(success=True, message="Hello!", metadata={"event_type": "speech"}),
            ]),
            ordered_responses=[
                ("tm_a", AgentResult(tool_results=[
                    ToolResult(success=True, message="A here", metadata={"event_type": "speech"}),
                ])),
                ("tm_b", AgentResult(tool_results=[
                    ToolResult(success=True, message="B here", metadata={"event_type": "speech"}),
                ])),
            ],
        )

        events = _interaction_result_to_sse(result)

        # NPC event first
        npc_events = [e for e in events if e.event_type == "npc_response"]
        assert len(npc_events) >= 1

        # Teammate events in order
        tm_events = [e for e in events if e.event_type == "teammate_response"]
        assert len(tm_events) == 2
        assert tm_events[0].payload["character_id"] == "tm_a"
        assert tm_events[1].payload["character_id"] == "tm_b"

    def test_free_chat_result_to_sse_no_npc_events(self) -> None:
        """_free_chat_result_to_sse should produce no NPC/GM events."""
        from app.agent_orchestration import _free_chat_result_to_sse

        result = NpcInteractionResult(
            success=True,
            npc_id="",
            ordered_responses=[
                ("tm_a", AgentResult(tool_results=[
                    ToolResult(success=True, message="Hi!", metadata={"event_type": "speech"}),
                ])),
            ],
        )

        events = _free_chat_result_to_sse(result)

        npc_events = [e for e in events if e.event_type in ("npc_response", "gm_narration")]
        assert npc_events == []
        tm_events = [e for e in events if e.event_type == "teammate_response"]
        assert len(tm_events) == 1
        assert tm_events[0].payload["character_id"] == "tm_a"

    def test_sse_fallback_to_teammate_results_when_no_ordered(self) -> None:
        """When ordered_responses is empty, falls back to teammate_results dict."""
        from app.agent_orchestration import _interaction_result_to_sse

        result = NpcInteractionResult(
            success=True,
            npc_id="merchant",
            ordered_responses=[],  # empty
            teammate_results={
                "tm_a": AgentResult(tool_results=[
                    ToolResult(success=True, message="Fallback", metadata={"event_type": "speech"}),
                ]),
            },
        )

        events = _interaction_result_to_sse(result)
        tm_events = [e for e in events if e.event_type == "teammate_response"]
        assert len(tm_events) == 1
        assert tm_events[0].payload["character_id"] == "tm_a"
