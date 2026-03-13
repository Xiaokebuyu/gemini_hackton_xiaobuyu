"""Deterministic capability harness coverage for the final P28 live gap."""

from __future__ import annotations

import asyncio

from tests._capability_harness import (
    build_npc_capability_harness,
    build_private_capability_harness,
    gm_suggest_options_response,
    noop_executor,
    npc_speak_response,
    stop_response,
)


def test_trade_capability_prompt_and_functional_option_survive_round_trip() -> None:
    coordinator, llm, _ = build_npc_capability_harness(
        llm_responses=[
            npc_speak_response("补给都在这里，看看你要什么。"),
            gm_suggest_options_response(
                [
                    {
                        "text": "看看补给",
                        "message": "让我看看你的货物。",
                        "functional": {
                            "type": "trade_browse",
                            "params": {"npc_id": "merchant_tom"},
                        },
                    }
                ]
            ),
            stop_response(),
        ],
        capabilities=[
            {
                "npc_id": "merchant_tom",
                "capability_id": "sell_supplies",
                "instruction": "展示可用补给并协助购买。",
                "functional": "trade_browse",
            }
        ],
    )

    result = asyncio.run(
        coordinator.execute_interaction(
            npc_id="merchant_tom",
            player_message="给我看看补给。",
            execute_command=noop_executor,
        )
    )

    assert result.completed is True
    assert "展示可用补给并协助购买。" in llm.calls[0]["system_prompt"]
    assert result.dialogue_options[0]["functional"]["type"] == "trade_browse"
    assert result.dialogue_options[0]["functional"]["params"] == {
        "npc_id": "merchant_tom"
    }


def test_board_capability_prompt_and_gm_board_option_survive_round_trip() -> None:
    coordinator, llm, _ = build_npc_capability_harness(
        llm_responses=[
            npc_speak_response("公会委托都在公告板上。"),
            gm_suggest_options_response(
                [
                    {
                        "text": "查看委托",
                        "message": "我想看看公会委托。",
                        "functional": {
                            "type": "board_browse",
                            "params": {"board_id": "guild_board"},
                        },
                    }
                ]
            ),
            stop_response(),
        ],
        capabilities=[
            {
                "npc_id": "guild_girl",
                "capability_id": "guide_board",
                "instruction": "引导玩家查看公会公告板并解释委托流程。",
                "functional": "board_browse",
                "functional_params": {"board_id": "guild_board"},
            }
        ],
    )

    result = asyncio.run(
        coordinator.execute_interaction(
            npc_id="guild_girl",
            player_message="我想看看委托。",
            execute_command=noop_executor,
        )
    )

    assert result.completed is True
    assert "引导玩家查看公会公告板并解释委托流程。" in llm.calls[0]["system_prompt"]
    assert result.dialogue_options[0]["functional"]["type"] == "board_browse"
    assert result.dialogue_options[0]["functional"]["params"] == {
        "board_id": "guild_board"
    }


def test_private_chat_prompt_includes_assigned_capability_instruction() -> None:
    coordinator, llm, _ = build_private_capability_harness(
        llm_responses=[
            npc_speak_response("我只私下告诉你，遗迹入口最近有人巡逻。"),
            stop_response(),
        ],
        capabilities=[
            {
                "npc_id": "lore_keeper",
                "capability_id": "share_ruins_hint",
                "instruction": "在私聊中透露遗迹外围的最新情报。",
            }
        ],
    )

    result = asyncio.run(
        coordinator.execute(
            npc_id="lore_keeper",
            player_message="私下告诉我遗迹的情况。",
            execute_command=noop_executor,
        )
    )

    assert result.completed is True
    assert "在私聊中透露遗迹外围的最新情报。" in llm.calls[0]["system_prompt"]


def test_unassigned_prompt_omits_dynamic_capability_block() -> None:
    coordinator, llm, _ = build_npc_capability_harness(
        llm_responses=[
            npc_speak_response("今天就这些货。"),
            stop_response(),
        ]
    )

    result = asyncio.run(
        coordinator.execute_interaction(
            npc_id="merchant_tom",
            player_message="给我看看补给。",
            execute_command=noop_executor,
        )
    )

    assert result.completed is True
    assert "## 你的特殊能力" not in llm.calls[0]["system_prompt"]
    assert "展示可用补给并协助购买。" not in llm.calls[0]["system_prompt"]
