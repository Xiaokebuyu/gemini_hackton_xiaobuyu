"""Tests for Phase 2/2b: private chat prompt differentiation + scene generation + GM monologue.

Covers:
- _build_npc_prompt_text(is_private=True/False) conditional block
- is_private=True lowers secrets threshold by 20
- GM_PRIVATE_CHAT_INTROSPECTIVE_PROMPT existence
- build_gm_private_chat_prompt() return value
- PrivateChatResult dataclass fields (scene_id, scene_name, gm_result)
- _create_private_scene() returns None when areas slice absent
"""

from __future__ import annotations

import asyncio
from dataclasses import fields
from unittest.mock import AsyncMock, MagicMock

from app.game_core.content.registries.characters import SecretEntry
from app.game_core.narrative.context_builder import (
    GM_PRIVATE_CHAT_INTROSPECTIVE_PROMPT,
    AgentContextBuilder,
    _build_npc_prompt_text,
)
from app.game_core.orchestration.private_chat import (
    PrivateChatResult,
    _PRIVATE_CHAT_SCENES,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _npc_profile(**kwargs):
    base = {
        "name": "TestNPC",
        "personality": "Gruff",
        "dialogue_style": "",
        "tags": [],
        "backstory": "",
        "speech_pattern": "",
        "character_class": "",
        "class_id": "",
        "faction": "",
        "faction_id": "",
        "secrets": [],
    }
    base.update(kwargs)
    return base


def _disp(**kwargs):
    d = {"approval": 0, "trust": 0, "fear": 0, "romance": 0}
    d.update(kwargs)
    return d


# ------------------------------------------------------------------
# _build_npc_prompt_text: is_private block
# ------------------------------------------------------------------


def test_npc_prompt_private_block_present_when_is_private() -> None:
    profile = _npc_profile()
    prompt = _build_npc_prompt_text(profile, _disp(), "stranger", [], is_private=True)
    assert "Private conversation context" in prompt
    assert "没有其他人能听到" in prompt


def test_npc_prompt_no_private_block_when_not_private() -> None:
    profile = _npc_profile()
    prompt = _build_npc_prompt_text(profile, _disp(), "stranger", [], is_private=False)
    assert "Private conversation context" not in prompt


# ------------------------------------------------------------------
# is_private lowers secrets threshold by 20
# ------------------------------------------------------------------


def test_secrets_visible_in_private_with_adjusted_threshold() -> None:
    """trust=40, threshold=50 → not visible normally, but visible in private (+20 → 60 >= 50)."""
    s = SecretEntry(content="私密秘密", trust_threshold=50)
    profile = _npc_profile(secrets=[s])
    prompt = _build_npc_prompt_text(
        profile, _disp(trust=40), "stranger", [], is_private=True
    )
    assert "私密秘密" in prompt


def test_secrets_not_visible_when_not_private_below_threshold() -> None:
    s = SecretEntry(content="隐藏秘密", trust_threshold=50)
    profile = _npc_profile(secrets=[s])
    prompt = _build_npc_prompt_text(
        profile, _disp(trust=40), "stranger", [], is_private=False
    )
    assert "隐藏秘密" not in prompt


def test_secrets_still_gated_in_private_if_way_below_threshold() -> None:
    """trust=10, threshold=80 → effective=30 < 80 → still not visible."""
    s = SecretEntry(content="高级机密", trust_threshold=80)
    profile = _npc_profile(secrets=[s])
    prompt = _build_npc_prompt_text(
        profile, _disp(trust=10), "stranger", [], is_private=True
    )
    assert "高级机密" not in prompt


# ------------------------------------------------------------------
# GM_PRIVATE_CHAT_INTROSPECTIVE_PROMPT constant
# ------------------------------------------------------------------


def test_gm_introspective_prompt_exists() -> None:
    assert GM_PRIVATE_CHAT_INTROSPECTIVE_PROMPT
    assert "inner" in GM_PRIVATE_CHAT_INTROSPECTIVE_PROMPT.lower()


def test_gm_introspective_prompt_tone() -> None:
    assert "pass_turn" in GM_PRIVATE_CHAT_INTROSPECTIVE_PROMPT
    assert "introspective" in GM_PRIVATE_CHAT_INTROSPECTIVE_PROMPT


# ------------------------------------------------------------------
# build_gm_private_chat_prompt() method
# ------------------------------------------------------------------


def _make_builder() -> AgentContextBuilder:
    world = MagicMock()
    world.has_registry.return_value = False
    state = MagicMock()
    state.has_slice.return_value = False
    return AgentContextBuilder(world, state)


def test_build_gm_private_chat_prompt_returns_constant() -> None:
    builder = _make_builder()
    result = builder.build_gm_private_chat_prompt()
    assert result == GM_PRIVATE_CHAT_INTROSPECTIVE_PROMPT


# ------------------------------------------------------------------
# PrivateChatResult dataclass fields
# ------------------------------------------------------------------


def test_private_chat_result_has_scene_id_field() -> None:
    field_names = {f.name for f in fields(PrivateChatResult)}
    assert "scene_id" in field_names
    assert "scene_name" in field_names
    assert "gm_result" in field_names


def test_private_chat_result_scene_id_default_none() -> None:
    r = PrivateChatResult(success=True, npc_id="npc1")
    assert r.scene_id is None
    assert r.scene_name == ""
    assert r.gm_result is None


# ------------------------------------------------------------------
# _PRIVATE_CHAT_SCENES constant structure
# ------------------------------------------------------------------


def test_private_chat_scenes_has_default() -> None:
    assert "default" in _PRIVATE_CHAT_SCENES
    assert len(_PRIVATE_CHAT_SCENES["default"]) >= 1


def test_private_chat_scenes_entries_have_name_and_description() -> None:
    for scenes in _PRIVATE_CHAT_SCENES.values():
        for scene in scenes:
            assert "name" in scene
            assert "description" in scene
            assert scene["name"]
            assert scene["description"]


# ------------------------------------------------------------------
# _create_private_scene() returns None without areas slice
# ------------------------------------------------------------------


def _make_coordinator_without_areas():
    from app.game_core.narrative.executor import AgenticExecutor
    from app.game_core.orchestration.private_chat import PrivateChatCoordinator

    executor = MagicMock(spec=AgenticExecutor)
    world = MagicMock()
    world.has_registry.return_value = False
    state = MagicMock()
    state.has_slice.return_value = False

    return PrivateChatCoordinator(executor=executor, world=world, state=state)


def test_create_private_scene_returns_none_without_areas_slice() -> None:
    coordinator = _make_coordinator_without_areas()
    result = coordinator._create_private_scene("npc1", "area1")
    assert result is None


def test_create_private_scene_returns_none_when_area_id_empty() -> None:
    coordinator = _make_coordinator_without_areas()
    result = coordinator._create_private_scene("npc1", "")
    assert result is None
