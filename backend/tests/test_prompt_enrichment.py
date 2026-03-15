"""Tests for Round 1 prompt enrichment (P2/P5 Phase 1 + Phase 1b).

Covers:
- _trust_hint / _romance_hint boundary values
- _filter_secrets: str legacy + SecretEntry trust threshold
- _build_npc_prompt_text: backstory/speech/identity/behavior/time/secrets blocks
- _serialize_context_layers: L0 lore includes description + cap 3
- SecretEntry backward-compatible parsing (str → threshold=50)
"""

from __future__ import annotations

from app.game_core.content.registries.characters import (
    CharacterRegistry,
    SecretEntry,
)
from app.game_core.narrative.context_builder import (
    _STAGE_GUIDES,
    _build_npc_prompt_text,
    _filter_secrets,
    _romance_hint,
    _trust_hint,
)
from app.game_core.narrative.executor import AgenticExecutor


# ------------------------------------------------------------------
# _trust_hint boundaries
# ------------------------------------------------------------------


def test_trust_hint_very_negative() -> None:
    assert "高度戒备" in _trust_hint(-50)


def test_trust_hint_slightly_negative() -> None:
    assert "提防" in _trust_hint(-10)


def test_trust_hint_neutral() -> None:
    assert "中性" in _trust_hint(0)


def test_trust_hint_moderate() -> None:
    assert "信任" in _trust_hint(40)


def test_trust_hint_high() -> None:
    assert "非常信任" in _trust_hint(70)


def test_trust_hint_boundary_minus30() -> None:
    # -30 is the last value in the "high guard" range
    assert "提防" in _trust_hint(-30)


def test_trust_hint_boundary_60() -> None:
    # 60 should fall into the "非常信任" bucket
    assert "非常信任" in _trust_hint(60)


# ------------------------------------------------------------------
# _romance_hint boundaries
# ------------------------------------------------------------------


def test_romance_hint_low_returns_empty() -> None:
    assert _romance_hint(15) == ""


def test_romance_hint_at_20_threshold() -> None:
    result = _romance_hint(20)
    assert result != ""


def test_romance_hint_mid_range() -> None:
    assert "好感" in _romance_hint(50)


def test_romance_hint_high() -> None:
    assert "强烈" in _romance_hint(65)


def test_romance_hint_boundary_40() -> None:
    assert "好感" in _romance_hint(40)


# ------------------------------------------------------------------
# _STAGE_GUIDES entries
# ------------------------------------------------------------------


def test_stage_guides_hostile_text() -> None:
    assert "厌恶" in _STAGE_GUIDES["hostile"]


def test_stage_guides_enemy_text() -> None:
    assert "敌" in _STAGE_GUIDES["enemy"]


def test_stage_guides_stranger_text() -> None:
    assert "距离感" in _STAGE_GUIDES["stranger"]


# ------------------------------------------------------------------
# _filter_secrets: legacy list[str]
# ------------------------------------------------------------------


def test_filter_secrets_str_always_eligible() -> None:
    """Plain str secrets have no threshold — always returned."""
    assert _filter_secrets(["秘密A", "秘密B"], 0) == ["秘密A", "秘密B"]


def test_filter_secrets_str_empty_skipped() -> None:
    assert _filter_secrets(["", "  "], 0) == []


def test_filter_secrets_empty_list() -> None:
    assert _filter_secrets([], 99) == []


# ------------------------------------------------------------------
# _filter_secrets: SecretEntry trust threshold
# ------------------------------------------------------------------


def test_filter_secrets_entry_below_threshold() -> None:
    s = SecretEntry(content="秘密C", trust_threshold=60)
    assert _filter_secrets([s], 50) == []


def test_filter_secrets_entry_at_threshold() -> None:
    s = SecretEntry(content="秘密D", trust_threshold=60)
    assert _filter_secrets([s], 60) == ["秘密D"]


def test_filter_secrets_entry_above_threshold() -> None:
    s = SecretEntry(content="秘密E", trust_threshold=30)
    assert _filter_secrets([s], 70) == ["秘密E"]


def test_filter_secrets_mixed_str_and_entry() -> None:
    entries = ["str_secret", SecretEntry(content="entry_secret", trust_threshold=80)]
    # trust=70: str always passes, entry (threshold=80) does not
    result = _filter_secrets(entries, 70)
    assert result == ["str_secret"]


# ------------------------------------------------------------------
# _build_npc_prompt_text: conditional blocks
# ------------------------------------------------------------------


def _npc_profile(**kwargs):
    """Minimal profile dict for prompt builder tests."""
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


def _default_disposition(**kwargs):
    d = {"approval": 0, "trust": 0, "fear": 0, "romance": 0}
    d.update(kwargs)
    return d


def test_npc_prompt_backstory_block() -> None:
    profile = _npc_profile(backstory="曾经是一名雇佣兵，因背叛而逃亡")
    prompt = _build_npc_prompt_text(
        profile, _default_disposition(), "stranger", []
    )
    assert "## Your background" in prompt
    assert "曾经是一名雇佣兵" in prompt


def test_npc_prompt_no_backstory_block() -> None:
    profile = _npc_profile(backstory="")
    prompt = _build_npc_prompt_text(
        profile, _default_disposition(), "stranger", []
    )
    assert "## Your background" not in prompt


def test_npc_prompt_speech_pattern_block() -> None:
    profile = _npc_profile(speech_pattern="喜欢说「好嘞」")
    prompt = _build_npc_prompt_text(
        profile, _default_disposition(), "stranger", []
    )
    assert "## Your speech pattern" in prompt
    assert "好嘞" in prompt


def test_npc_prompt_identity_block_class_and_faction() -> None:
    profile = _npc_profile(character_class="战士", faction="暗影公会")
    prompt = _build_npc_prompt_text(
        profile, _default_disposition(), "stranger", []
    )
    assert "## Your identity" in prompt
    assert "战士" in prompt
    assert "暗影公会" in prompt


def test_npc_prompt_identity_block_absent_when_empty() -> None:
    profile = _npc_profile()
    prompt = _build_npc_prompt_text(
        profile, _default_disposition(), "stranger", []
    )
    assert "## Your identity" not in prompt


def test_npc_prompt_behavior_block_from_stage() -> None:
    """3-C: behavior_block removed; stage is now in compact relationship line."""
    profile = _npc_profile()
    prompt = _build_npc_prompt_text(
        profile, _default_disposition(), "hostile", []
    )
    # behavior_block with stage guides removed; stage still present in relationship line
    assert "## How to behave" not in prompt
    assert "hostile" in prompt


def test_npc_prompt_time_block_with_slot() -> None:
    """3-C: time now appears as compact Chinese format in relationship line."""
    profile = _npc_profile()
    prompt = _build_npc_prompt_text(
        profile, _default_disposition(), "stranger", [],
        time_info={"day": 3, "slot": "night"},
    )
    # Chinese format: 第3天 night
    assert "第3天" in prompt
    assert "night" in prompt


def test_npc_prompt_time_block_no_slot() -> None:
    """3-C: time without slot shows day only."""
    profile = _npc_profile()
    prompt = _build_npc_prompt_text(
        profile, _default_disposition(), "stranger", [],
        time_info={"day": 5, "slot": ""},
    )
    assert "第5天" in prompt


def test_npc_prompt_no_time_block_when_absent() -> None:
    profile = _npc_profile()
    prompt = _build_npc_prompt_text(
        profile, _default_disposition(), "stranger", [],
        time_info=None,
    )
    assert "当前时间" not in prompt
    assert "Current time" not in prompt


def test_npc_prompt_secrets_block_visible() -> None:
    s = SecretEntry(content="知道密道入口", trust_threshold=30)
    profile = _npc_profile(secrets=[s])
    prompt = _build_npc_prompt_text(
        profile, _default_disposition(trust=50), "stranger", []
    )
    assert "知道密道入口" in prompt
    assert "haven't told" in prompt


def test_npc_prompt_secrets_block_hidden_below_threshold() -> None:
    s = SecretEntry(content="高机密", trust_threshold=80)
    profile = _npc_profile(secrets=[s])
    prompt = _build_npc_prompt_text(
        profile, _default_disposition(trust=50), "stranger", []
    )
    assert "高机密" not in prompt


# ------------------------------------------------------------------
# SecretEntry backward-compatible parsing in CharacterRegistry
# ------------------------------------------------------------------


def test_secret_entry_parsed_from_str() -> None:
    registry = CharacterRegistry()
    registry.load({
        "npc1": {
            "id": "npc1",
            "name": "A",
            "secrets": ["str_secret_value"],
        }
    })
    char = registry.get("npc1")
    assert char is not None
    assert len(char.secrets) == 1
    s = char.secrets[0]
    assert isinstance(s, SecretEntry)
    assert s.content == "str_secret_value"
    assert s.trust_threshold == 50


def test_secret_entry_parsed_from_mapping() -> None:
    registry = CharacterRegistry()
    registry.load({
        "npc2": {
            "id": "npc2",
            "name": "B",
            "secrets": [{"content": "mapping_secret", "trust_threshold": 70, "tags": ["dark"]}],
        }
    })
    char = registry.get("npc2")
    assert char is not None
    s = char.secrets[0]
    assert isinstance(s, SecretEntry)
    assert s.content == "mapping_secret"
    assert s.trust_threshold == 70
    assert s.tags == ["dark"]


def test_secret_entry_empty_secrets() -> None:
    registry = CharacterRegistry()
    registry.load({"npc3": {"id": "npc3", "name": "C"}})
    char = registry.get("npc3")
    assert char is not None
    assert char.secrets == []


# ------------------------------------------------------------------
# _serialize_context_layers: L0 lore description + cap 3
# ------------------------------------------------------------------


def _lore_item(name: str, description: str = ""):
    """Simple object mimicking a LoreEntry dataclass."""
    from types import SimpleNamespace
    return SimpleNamespace(name=name, description=description)


def test_lore_description_included() -> None:
    layers = {
        "l0_world_constants": {
            "world_id": "test",
            "lore": [_lore_item("The Void", "An ancient darkness")],
            "factions": [],
        },
        "l2_area_environment": None,
        "l3_location_details": None,
        "l5_scene_bus": None,
    }
    text = AgenticExecutor._serialize_context_layers("npc", layers)
    assert "The Void" in text
    assert "An ancient darkness" in text


def test_lore_description_absent_when_empty() -> None:
    layers = {
        "l0_world_constants": {
            "world_id": "test",
            "lore": [_lore_item("The Void")],
            "factions": [],
        },
        "l2_area_environment": None,
        "l3_location_details": None,
        "l5_scene_bus": None,
    }
    text = AgenticExecutor._serialize_context_layers("npc", layers)
    assert "The Void" in text
    assert ": " not in text.split("The Void")[1].split("\n")[0]


def test_lore_cap_3() -> None:
    """Only 3 lore entries should appear even with 5 supplied."""
    items = [_lore_item(f"Lore{i}", f"desc{i}") for i in range(5)]
    layers = {
        "l0_world_constants": {
            "world_id": "test",
            "lore": items,
            "factions": [],
        },
        "l2_area_environment": None,
        "l3_location_details": None,
        "l5_scene_bus": None,
    }
    text = AgenticExecutor._serialize_context_layers("npc", layers)
    assert "Lore0" in text
    assert "Lore1" in text
    assert "Lore2" in text
    assert "Lore3" not in text
    assert "Lore4" not in text
