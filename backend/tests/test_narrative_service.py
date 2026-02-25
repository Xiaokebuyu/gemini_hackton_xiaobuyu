import pytest

from app.world.narrative.narrative_service import NarrativeService


class _FakeDocSnapshot:
    def __init__(self, doc_id: str, data: dict, exists: bool = True):
        self.id = doc_id
        self._data = data
        self.exists = exists

    def to_dict(self):
        return dict(self._data)


class _FakeDocRef:
    """支持 .get() / .update() / .collection() 的 fake 文档引用。"""

    def __init__(self, collection_docs: dict, doc_id: str):
        self._docs = collection_docs
        self._id = doc_id

    def get(self):
        data = self._docs.get(self._id)
        if data is None:
            return _FakeDocSnapshot(self._id, {}, exists=False)
        return _FakeDocSnapshot(self._id, dict(data), exists=True)

    def update(self, updates: dict):
        data = self._docs.setdefault(self._id, {})
        for key, value in updates.items():
            parts = key.split(".")
            target = data
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value

    def collection(self, name: str):
        data = self._docs.get(self._id) or {}
        return _FakeCollectionRef(data.get(name, {}))


class _FakeCollectionRef:
    def __init__(self, docs: dict):
        self._docs = docs if isinstance(docs, dict) else {}

    def stream(self):
        return [_FakeDocSnapshot(doc_id, data) for doc_id, data in self._docs.items()]

    def document(self, doc_id: str):
        return _FakeDocRef(self._docs, doc_id)


class _FakeWorldRef:
    def __init__(self, world_data: dict):
        self._world_data = world_data if isinstance(world_data, dict) else {}

    def collection(self, name: str):
        return _FakeCollectionRef(self._world_data.get(name, {}))


class _FakeWorldsCollection:
    def __init__(self, worlds_data: dict):
        self._worlds_data = worlds_data if isinstance(worlds_data, dict) else {}

    def document(self, world_id: str):
        return _FakeWorldRef(self._worlds_data.get(world_id, {}))


class _FakeFirestoreDB:
    def __init__(self, worlds_data: dict):
        self._worlds_data = worlds_data if isinstance(worlds_data, dict) else {}

    def collection(self, name: str):
        if name != "worlds":
            return _FakeCollectionRef({})
        return _FakeWorldsCollection(self._worlds_data)


def _build_firestore_world_data() -> dict:
    return {
        "unit_world": {
            "mainlines": {
                "vol_1": {
                    "id": "vol_1",
                    "name": "第一卷",
                    "description": "测试主线",
                    "chapters": ["ch_1", "ch_1", "ch_2", "ch_missing"],
                }
            },
            "chapters": {
                "ch_1": {
                    "id": "ch_1",
                    "mainline_id": "vol_1",
                    "name": "第一章",
                    "description": "进入村庄并调查异动。",
                    "available_areas": ["frontier_town", "frontier_town"],
                    "events": [
                        {
                            "id": "event_rescue",
                            "name": "救援事件",
                            "is_required": True,
                            "trigger_conditions": {
                                "operator": "and",
                                "conditions": [],
                            },
                        }
                    ],
                    "objectives": [
                        "前往村庄",
                        {"id": "ch_1_obj_2", "description": "调查异动"},
                    ],
                    "trigger_conditions": {"type": "auto"},
                    "completion_conditions": {"events_required": ["event_rescue"]},
                    "order": 1,
                },
                "ch_2": {
                    "id": "ch_2",
                    "mainline_id": "vol_1",
                    "name": "第二章",
                    "description": "庆功并准备下一次远征。",
                    "available_areas": ["guild"],
                    "events": [
                        {
                            "id": "event_celebration",
                            "name": "庆功事件",
                            "is_required": False,
                            "trigger_conditions": {
                                "operator": "and",
                                "conditions": [],
                            },
                        }
                    ],
                    "objectives": [],
                    "trigger_conditions": {
                        "type": "chapter_complete",
                        "chapter_id": "ch_1",
                    },
                    "completion_conditions": {},
                    "order": 2,
                },
            },
        }
    }


@pytest.mark.asyncio
async def test_load_narrative_data_supports_string_objectives_and_dedup():
    db = _FakeFirestoreDB(_build_firestore_world_data())
    service = NarrativeService(db=db)
    await service.load_narrative_data("unit_world")

    mainline = service.get_mainline_info("unit_world", "vol_1")
    assert mainline is not None
    assert mainline["chapters"] == ["ch_1", "ch_2"]

    chapter = service.get_chapter_info("unit_world", "ch_1")
    assert chapter is not None
    assert chapter["objectives"][0]["id"] == "ch_1_obj_1"
    assert chapter["objectives"][0]["description"] == "前往村庄"


@pytest.mark.asyncio
async def test_flow_board_plan_and_trigger_event():
    worlds_data = _build_firestore_world_data()
    worlds_data["unit_world"]["sessions"] = {
        "sess_1": {
            "metadata": {
                "narrative": {
                    "current_mainline": "vol_1",
                    "current_chapter": "ch_1",
                    "objectives_completed": ["ch_1_obj_1"],
                    "events_triggered": [],
                    "chapters_completed": [],
                }
            }
        }
    }
    db = _FakeFirestoreDB(worlds_data)
    service = NarrativeService(db=db)

    board = await service.get_flow_board("unit_world", "sess_1", lookahead=2)
    assert board["current_mainline"]["id"] == "vol_1"
    assert board["progress"]["chapter_index"] == 1
    assert board["progress"]["chapter_total"] == 2
    assert board["progress"]["next_chapter"]["id"] == "ch_2"

    current_step = next(step for step in board["steps"] if step["status"] == "current")
    assert current_step["id"] == "ch_1"
    assert current_step["objectives"][0]["completed"] is True

    plan = await service.get_current_chapter_plan("unit_world", "sess_1")
    assert plan["chapter"]["id"] == "ch_1"
    assert plan["goals"] == ["调查异动"]
    assert plan["required_events"] == ["event_rescue"]

    trigger_result = await service.trigger_event("unit_world", "sess_1", "event_rescue")
    assert trigger_result["event_recorded"] is True
    assert trigger_result["chapter_completed"] is True
    assert trigger_result["new_chapter"] == "ch_2"


@pytest.mark.asyncio
async def test_progress_raises_when_world_narrative_missing():
    db = _FakeFirestoreDB(_build_firestore_world_data())
    service = NarrativeService(db=db)

    progress = await service.get_progress("unit_world", "sess_a")
    assert progress.current_mainline == "vol_1"
    assert progress.current_chapter == "ch_1"

    with pytest.raises(ValueError, match="缺少章节叙事数据"):
        await service.get_progress("missing_world", "sess_b")


# ---- DAG 章节导航 (Phase 5.3) ----


def _build_dag_world_data() -> dict:
    """构建 DAG 结构的世界数据：ch_1 -> ch_2a / ch_2b"""
    return {
        "dag_world": {
            "mainlines": {
                "vol_1": {
                    "id": "vol_1",
                    "name": "第一卷",
                    "description": "DAG测试主线",
                    "chapters": ["ch_1", "ch_2a", "ch_2b", "ch_3"],
                    "chapter_graph": {
                        "ch_1": ["ch_2a", "ch_2b"],
                        "ch_2a": ["ch_3"],
                        "ch_2b": ["ch_3"],
                    },
                }
            },
            "chapters": {
                "ch_1": {
                    "id": "ch_1",
                    "mainline_id": "vol_1",
                    "name": "第一章",
                    "description": "起始章节",
                    "available_maps": ["town"],
                    "events": [
                        {
                            "id": "ev_start",
                            "name": "开端事件",
                            "is_required": True,
                            "trigger_conditions": {
                                "operator": "and",
                                "conditions": [],
                            },
                        }
                    ],
                    "completion_conditions": {"events_required": ["ev_start"]},
                    "transitions": [
                        {
                            "target_chapter_id": "ch_2a",
                            "conditions": {
                                "operator": "and",
                                "conditions": [
                                    {"type": "event_triggered", "params": {"event_id": "ev_choose_a"}},
                                ],
                            },
                            "priority": 10,
                            "transition_type": "normal",
                        },
                        {
                            "target_chapter_id": "ch_2b",
                            "conditions": {
                                "operator": "and",
                                "conditions": [
                                    {"type": "event_triggered", "params": {"event_id": "ev_choose_b"}},
                                ],
                            },
                            "priority": 5,
                            "transition_type": "normal",
                        },
                    ],
                    "order": 0,
                },
                "ch_2a": {
                    "id": "ch_2a",
                    "mainline_id": "vol_1",
                    "name": "分支A",
                    "description": "分支A路线",
                    "available_maps": ["forest"],
                    "events": [
                        {
                            "id": "ev_a_done",
                            "name": "A线完成",
                            "is_required": True,
                            "trigger_conditions": {
                                "operator": "and",
                                "conditions": [],
                            },
                        }
                    ],
                    "completion_conditions": {"events_required": ["ev_a_done"]},
                    "order": 1,
                },
                "ch_2b": {
                    "id": "ch_2b",
                    "mainline_id": "vol_1",
                    "name": "分支B",
                    "description": "分支B路线",
                    "available_maps": ["mountain"],
                    "events": [
                        {
                            "id": "ev_b_done",
                            "name": "B线完成",
                            "is_required": True,
                            "trigger_conditions": {
                                "operator": "and",
                                "conditions": [],
                            },
                        }
                    ],
                    "completion_conditions": {"events_required": ["ev_b_done"]},
                    "order": 2,
                },
                "ch_3": {
                    "id": "ch_3",
                    "mainline_id": "vol_1",
                    "name": "第三章",
                    "description": "汇合章节",
                    "available_maps": ["castle"],
                    "events": [
                        {
                            "id": "ev_finale",
                            "name": "终幕",
                            "is_required": False,
                            "trigger_conditions": {
                                "operator": "and",
                                "conditions": [],
                            },
                        }
                    ],
                    "completion_conditions": {},
                    "order": 3,
                },
            },
        }
    }


@pytest.mark.asyncio
async def test_dag_navigation_chooses_branch_by_transition():
    """DAG 导航：多个后继时根据 transitions 条件选择分支"""
    worlds_data = _build_dag_world_data()
    worlds_data["dag_world"]["sessions"] = {
        "sess_1": {
            "metadata": {
                "narrative": {
                    "current_mainline": "vol_1",
                    "current_chapter": "ch_1",
                    "events_triggered": ["ev_start", "ev_choose_a"],
                    "chapters_completed": [],
                }
            }
        }
    }
    db = _FakeFirestoreDB(worlds_data)
    service = NarrativeService(db=db)

    # 触发完成事件
    result = await service.trigger_event("dag_world", "sess_1", "ev_start")
    assert result["chapter_completed"] is True
    # 因为 ch_1 有 transition 到 ch_2a（ev_choose_a 已触发，priority=10）
    assert result["new_chapter"] == "ch_2a"


@pytest.mark.asyncio
async def test_dag_navigation_fallback_to_first_successor():
    """DAG 导航：无 transition 条件满足时 fallback 到第一个后继"""
    worlds_data = _build_dag_world_data()
    worlds_data["dag_world"]["sessions"] = {
        "sess_1": {
            "metadata": {
                "narrative": {
                    "current_mainline": "vol_1",
                    "current_chapter": "ch_1",
                    "events_triggered": ["ev_start"],
                    "chapters_completed": [],
                }
            }
        }
    }
    db = _FakeFirestoreDB(worlds_data)
    service = NarrativeService(db=db)

    result = await service.trigger_event("dag_world", "sess_1", "ev_start")
    assert result["chapter_completed"] is True
    # 无 transition 满足 → fallback 到第一个后继 ch_2a
    assert result["new_chapter"] == "ch_2a"


@pytest.mark.asyncio
async def test_dag_navigation_single_successor():
    """DAG 导航：单后继直接跳转"""
    worlds_data = _build_dag_world_data()
    worlds_data["dag_world"]["sessions"] = {
        "sess_1": {
            "metadata": {
                "narrative": {
                    "current_mainline": "vol_1",
                    "current_chapter": "ch_2a",
                    "events_triggered": ["ev_a_done"],
                    "chapters_completed": ["ch_1"],
                }
            }
        }
    }
    db = _FakeFirestoreDB(worlds_data)
    service = NarrativeService(db=db)

    result = await service.trigger_event("dag_world", "sess_1", "ev_a_done")
    assert result["chapter_completed"] is True
    assert result["new_chapter"] == "ch_3"


@pytest.mark.asyncio
async def test_advance_resets_v2_counters():
    """章节推进时重置 rounds_in_chapter 和 rounds_since_last_progress"""
    worlds_data = _build_firestore_world_data()
    worlds_data["unit_world"]["sessions"] = {
        "sess_1": {
            "metadata": {
                "narrative": {
                    "current_mainline": "vol_1",
                    "current_chapter": "ch_1",
                    "events_triggered": [],
                    "chapters_completed": [],
                    "rounds_in_chapter": 15,
                    "rounds_since_last_progress": 3,
                }
            }
        }
    }
    db = _FakeFirestoreDB(worlds_data)
    service = NarrativeService(db=db)

    result = await service.trigger_event("unit_world", "sess_1", "event_rescue")
    assert result["chapter_completed"] is True
    assert result["new_chapter"] == "ch_2"

    # 验证保存的进度中 v2 计数器已重置
    progress = await service.get_progress("unit_world", "sess_1")
    assert progress.rounds_in_chapter == 0
    assert progress.rounds_since_last_progress == 0
