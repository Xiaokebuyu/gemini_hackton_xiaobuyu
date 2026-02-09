import types

from app.services.admin.agentic_enforcement import evaluate_agentic_tool_usage


def _tool_call(name: str, success: bool = True):
    return types.SimpleNamespace(name=name, success=success)


def test_enforcement_requires_add_teammate_for_join_keywords():
    result = evaluate_agentic_tool_usage(
        player_input="让艾拉加入队伍",
        inferred_intent="roleplay",
        tool_calls=[],
    )
    assert result.passed is False
    assert "add_teammate" in result.required_all
    assert "add_teammate" in result.repair_tool_names


def test_enforcement_passes_after_add_teammate_call():
    result = evaluate_agentic_tool_usage(
        player_input="让艾拉加入队伍",
        inferred_intent="roleplay",
        tool_calls=[_tool_call("add_teammate", True)],
    )
    assert result.passed is True
    assert result.missing_requirements == []


def test_enforcement_requires_ability_check_for_check_keywords():
    result = evaluate_agentic_tool_usage(
        player_input="我要做一次潜行检定，DC 15",
        inferred_intent="roleplay",
        tool_calls=[],
    )
    assert result.passed is False
    assert "ability_check" in result.required_all
    assert "ability_check" in result.repair_tool_names


def test_enforcement_requires_npc_dialogue_for_chat_keywords():
    result = evaluate_agentic_tool_usage(
        player_input="我想和见习圣女聊聊",
        inferred_intent="roleplay",
        tool_calls=[],
    )
    assert result.passed is False
    assert "npc_dialogue" in result.required_all
    assert "npc_dialogue" in result.repair_tool_names


def test_enforcement_keeps_roleplay_relaxed_without_explicit_keywords():
    result = evaluate_agentic_tool_usage(
        player_input="我观察四周的动静",
        inferred_intent="roleplay",
        tool_calls=[],
    )
    assert result.passed is True
