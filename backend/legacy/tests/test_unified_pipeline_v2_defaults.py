from app.tools.worldbook_graphizer.unified_pipeline import UnifiedWorldExtractor


def test_ensure_v2_story_defaults_synthesizes_missing_story_fields():
    data = {
        "mainlines": [
            {
                "id": "vol_1",
                "name": "第一卷",
                "chapters": ["ch_1_1", "ch_1_2"],
            }
        ],
        "chapters": [
            {
                "id": "ch_1_1",
                "mainline_id": "vol_1",
                "type": "story",
                "completion_conditions": {"events_required": ["ev_a", "ev_b"]},
            },
            {
                "id": "ch_1_2",
                "mainline_id": "vol_1",
                "type": "story",
                "completion_conditions": {},
            },
        ],
    }

    UnifiedWorldExtractor._ensure_v2_story_defaults(data)

    ch1 = data["chapters"][0]
    assert isinstance(ch1["events"], list) and len(ch1["events"]) == 2
    assert ch1["events"][0]["id"] == "ev_a"
    assert ch1["events"][1]["trigger_conditions"]["conditions"][0]["type"] == "event_triggered"
    assert isinstance(ch1["transitions"], list)
    assert isinstance(ch1["pacing"], dict)

    ch2 = data["chapters"][1]
    assert isinstance(ch2["events"], list) and len(ch2["events"]) == 1
    assert ch2["completion_conditions"]["events_required"] == [ch2["events"][0]["id"]]

    mainline = data["mainlines"][0]
    assert isinstance(mainline["chapter_graph"], dict)
    assert mainline["chapter_graph"].get("ch_1_1") == ["ch_1_2"]


def test_ensure_v2_story_defaults_skips_non_story_chapter():
    data = {
        "mainlines": [{"id": "vol_1", "chapters": ["ch_meta"]}],
        "chapters": [
            {
                "id": "ch_meta",
                "mainline_id": "vol_1",
                "type": "metadata",
                "completion_conditions": {},
            }
        ],
    }

    UnifiedWorldExtractor._ensure_v2_story_defaults(data)

    ch = data["chapters"][0]
    assert "events" not in ch
    assert data["mainlines"][0]["chapter_graph"] == {}
