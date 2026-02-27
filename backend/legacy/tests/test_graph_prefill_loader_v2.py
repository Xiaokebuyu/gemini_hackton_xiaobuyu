import pytest

from app.tools.world_initializer.graph_prefill_loader import GraphPrefillLoader


def test_validate_narrative_v2_artifacts_passes_with_valid_data():
    chapters_v2 = [
        {
            "id": "ch_1_1",
            "type": "story",
            "events": [{"id": "ev_1", "name": "事件1"}],
            "transitions": [],
            "pacing": {"min_rounds": 3, "ideal_rounds": 10, "max_rounds": 30},
        },
        {
            "id": "ch_meta",
            "type": "metadata",
        },
    ]
    mainlines_raw = [
        {"id": "vol_1", "chapter_graph": {"ch_1_1": []}},
    ]

    GraphPrefillLoader.validate_narrative_v2_artifacts(
        chapters_v2=chapters_v2,
        mainlines_raw=mainlines_raw,
    )


def test_validate_narrative_v2_artifacts_fails_with_missing_story_fields():
    chapters_v2 = [
        {"id": "ch_1_1", "type": "story", "events": [], "transitions": {}, "pacing": []},
    ]
    mainlines_raw = [
        {"id": "vol_1"},
    ]

    with pytest.raises(ValueError, match="strict-v2 导入失败"):
        GraphPrefillLoader.validate_narrative_v2_artifacts(
            chapters_v2=chapters_v2,
            mainlines_raw=mainlines_raw,
        )


def test_upgrade_narrative_v2_artifacts_makes_legacy_data_valid():
    chapters_v2 = [
        {
            "id": "ch_1_1",
            "mainline_id": "vol_1",
            "type": "story",
            "completion_conditions": {"events_required": ["ev_1", "ev_2"]},
        },
        {
            "id": "ch_1_2",
            "mainline_id": "vol_1",
            "type": "story",
            "completion_conditions": {},
        },
    ]
    mainlines_raw = [{"id": "vol_1", "chapters": ["ch_1_1", "ch_1_2"]}]

    upgraded_chapters, upgraded_mainlines = GraphPrefillLoader.upgrade_narrative_v2_artifacts(
        chapters_v2=chapters_v2,
        mainlines_raw=mainlines_raw,
    )

    GraphPrefillLoader.validate_narrative_v2_artifacts(
        chapters_v2=upgraded_chapters,
        mainlines_raw=upgraded_mainlines,
    )

    assert upgraded_chapters[0]["events"][0]["id"] == "ev_1"
    assert upgraded_chapters[1]["events"]
    assert isinstance(upgraded_mainlines[0]["chapter_graph"], dict)
