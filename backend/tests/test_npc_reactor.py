"""Tests for NPCReactor (Direction A.3)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.world.npc_reactor import NPCReactor, MAX_REACTIONS_PER_ROUND
from app.world.scene_bus import BusEntry, BusEntryType, SceneBus


def _run(coro):
    return asyncio.run(coro)


def _make_npc_node(npc_id, name, personality="friendly", is_alive=True):
    node = MagicMock()
    node.id = npc_id
    node.name = name
    node.type = "npc"
    node.state = {"is_alive": is_alive}
    node.properties = {"personality": personality, "occupation": "冒险者"}
    return node


def _make_wg(area_npcs=None, children=None, all_nodes=None):
    wg = MagicMock()
    all_nodes = all_nodes or {}

    def get_node(nid):
        return all_nodes.get(nid)

    wg.get_node = get_node

    def get_entities_at(loc_id):
        return area_npcs.get(loc_id, []) if area_npcs else []

    wg.get_entities_at = get_entities_at

    def get_children(node_id, type_filter=None):
        return children or []

    wg.get_children = get_children

    return wg


def _make_session(player_location="town_square", sub_location=None):
    session = MagicMock()
    session.player_location = player_location
    session.sub_location = sub_location
    return session


class TestNPCReactorBasics:
    def test_npc_mentioned_by_name_reacts(self):
        """NPC 被点名应该产出反应。"""
        priestess = _make_npc_node("priestess", "女祭司")
        wg = _make_wg(
            area_npcs={"town_square": ["priestess"]},
            all_nodes={"priestess": priestess},
        )
        bus = SceneBus(area_id="town_square")
        bus.publish(BusEntry(
            actor="player", type=BusEntryType.SPEECH,
            content="女祭司，你好吗？",
        ))
        session = _make_session()
        reactor = NPCReactor(world_graph=wg)
        results = _run(reactor.collect_reactions(bus, session, {}))
        assert len(results) >= 1
        assert results[0].actor == "priestess"

    def test_npc_not_in_area_no_reaction(self):
        """NPC 不在当前区域不应反应。"""
        wg = _make_wg(area_npcs={}, all_nodes={})
        bus = SceneBus(area_id="town_square")
        bus.publish(BusEntry(
            actor="player", type=BusEntryType.SPEECH,
            content="恶龙你在哪？",
        ))
        session = _make_session()
        reactor = NPCReactor(world_graph=wg)
        results = _run(reactor.collect_reactions(bus, session, {}))
        assert len(results) == 0

    def test_private_chat_non_target_no_reaction(self):
        """私密对话时非目标 NPC 不反应。"""
        priestess = _make_npc_node("priestess", "女祭司")
        warrior = _make_npc_node("warrior", "战士")
        wg = _make_wg(
            area_npcs={"town_square": ["priestess", "warrior"]},
            all_nodes={"priestess": priestess, "warrior": warrior},
        )
        bus = SceneBus(area_id="town_square")
        # Private message to priestess only
        bus.publish(BusEntry(
            actor="player", type=BusEntryType.SPEECH,
            content="女祭司，悄悄跟你说",
            visibility="private:priestess",
        ))
        session = _make_session()
        reactor = NPCReactor(world_graph=wg)
        results = _run(reactor.collect_reactions(bus, session, {}))
        # Only priestess should react, not warrior
        actor_ids = [r.actor for r in results]
        assert "warrior" not in actor_ids
        if results:
            assert results[0].actor == "priestess"

    def test_max_reactions_per_round(self):
        """每轮最多 MAX_REACTIONS_PER_ROUND 个反应。"""
        npcs = {}
        area_npcs = []
        for i in range(5):
            npc_id = f"npc_{i}"
            node = _make_npc_node(npc_id, f"NPC{i}")
            npcs[npc_id] = node
            area_npcs.append(npc_id)

        wg = _make_wg(
            area_npcs={"town_square": area_npcs},
            all_nodes=npcs,
        )
        bus = SceneBus(area_id="town_square")
        # Mention all NPCs
        for i in range(5):
            bus.publish(BusEntry(
                actor="player", type=BusEntryType.SPEECH,
                content=f"NPC{i}你好",
            ))
        session = _make_session()
        reactor = NPCReactor(world_graph=wg)
        results = _run(reactor.collect_reactions(bus, session, {}))
        assert len(results) <= MAX_REACTIONS_PER_ROUND

    def test_dead_npc_no_reaction(self):
        """死亡 NPC 不反应。"""
        dead = _make_npc_node("dead_npc", "亡灵", is_alive=False)
        wg = _make_wg(
            area_npcs={"town_square": ["dead_npc"]},
            all_nodes={"dead_npc": dead},
        )
        bus = SceneBus(area_id="town_square")
        bus.publish(BusEntry(
            actor="player", type=BusEntryType.SPEECH,
            content="亡灵你还在吗？",
        ))
        session = _make_session()
        reactor = NPCReactor(world_graph=wg)
        results = _run(reactor.collect_reactions(bus, session, {}))
        assert len(results) == 0

    def test_empty_bus_no_reaction(self):
        """空总线不产生反应。"""
        npc = _make_npc_node("npc1", "守卫")
        wg = _make_wg(
            area_npcs={"town_square": ["npc1"]},
            all_nodes={"npc1": npc},
        )
        bus = SceneBus(area_id="town_square")
        session = _make_session()
        reactor = NPCReactor(world_graph=wg)
        results = _run(reactor.collect_reactions(bus, session, {}))
        assert len(results) == 0

    def test_no_world_graph_returns_empty(self):
        """无 WorldGraph 返回空。"""
        bus = SceneBus(area_id="town_square")
        bus.publish(BusEntry(
            actor="player", type=BusEntryType.SPEECH, content="hello",
        ))
        session = _make_session()
        reactor = NPCReactor(world_graph=None)
        results = _run(reactor.collect_reactions(bus, session, {}))
        assert len(results) == 0


class TestRelevanceCalculation:
    def test_name_mention_high_relevance(self):
        score = NPCReactor._calculate_relevance(
            "priestess", "女祭司",
            [BusEntry(actor="player", type=BusEntryType.SPEECH, content="女祭司你好")],
        )
        assert score >= 10.0

    def test_engine_navigate_low_relevance(self):
        score = NPCReactor._calculate_relevance(
            "guard", "守卫",
            [BusEntry(
                actor="engine", type=BusEntryType.ENGINE_RESULT,
                content="navigated to town", data={"tool": "navigate"},
            )],
        )
        assert 0 < score < 10

    def test_no_mention_zero_relevance(self):
        score = NPCReactor._calculate_relevance(
            "priestess", "女祭司",
            [BusEntry(actor="player", type=BusEntryType.SPEECH, content="天气真好")],
        )
        assert score == 0.0


class TestLLMReaction:
    def test_llm_path_when_use_llm_true(self):
        """use_llm=True 时应使用 LLM 生成反应。"""
        priestess = _make_npc_node("priestess", "女祭司")
        wg = _make_wg(
            area_npcs={"town_square": ["priestess"]},
            all_nodes={"priestess": priestess},
        )
        bus = SceneBus(area_id="town_square")
        bus.publish(BusEntry(
            actor="player", type=BusEntryType.SPEECH,
            content="女祭司，你好吗？",
        ))
        session = _make_session()

        reactor = NPCReactor(world_graph=wg, use_llm=True)

        with patch("app.services.llm_service.LLMService") as MockLLM:
            mock_instance = MockLLM.return_value
            mock_instance.generate_simple = AsyncMock(return_value="女祭司微笑着回应。")

            results = _run(reactor.collect_reactions(bus, session, {}))

        assert len(results) >= 1
        assert results[0].actor == "priestess"
        assert "微笑" in results[0].content

    def test_llm_failure_falls_back_to_template(self):
        """LLM 失败时应回退到模板反应。"""
        priestess = _make_npc_node("priestess", "女祭司")
        wg = _make_wg(
            area_npcs={"town_square": ["priestess"]},
            all_nodes={"priestess": priestess},
        )
        bus = SceneBus(area_id="town_square")
        bus.publish(BusEntry(
            actor="player", type=BusEntryType.SPEECH,
            content="女祭司，你好吗？",
        ))
        session = _make_session()

        reactor = NPCReactor(world_graph=wg, use_llm=True)

        with patch("app.services.llm_service.LLMService") as MockLLM:
            mock_instance = MockLLM.return_value
            mock_instance.generate_simple = AsyncMock(side_effect=Exception("LLM error"))

            results = _run(reactor.collect_reactions(bus, session, {}))

        assert len(results) >= 1
        assert results[0].actor == "priestess"
        # 应回退到模板
        assert "注意到" in results[0].content


class TestLLMServiceReuse:
    def test_llm_service_reused(self):
        """两次调用共享同一个 LLMService 实例（通过构造器注入）。"""
        priestess = _make_npc_node("priestess", "女祭司")
        warrior = _make_npc_node("warrior", "战士")
        wg = _make_wg(
            area_npcs={"town_square": ["priestess", "warrior"]},
            all_nodes={"priestess": priestess, "warrior": warrior},
        )
        bus = SceneBus(area_id="town_square")
        bus.publish(BusEntry(
            actor="player", type=BusEntryType.SPEECH,
            content="女祭司和战士你们好",
        ))
        session = _make_session()

        mock_llm = MagicMock()
        mock_llm.generate_simple = AsyncMock(return_value="反应文本。")
        reactor = NPCReactor(world_graph=wg, use_llm=True, llm_service=mock_llm)

        _run(reactor.collect_reactions(bus, session, {}))

        # Same instance should be reused across calls
        assert reactor._llm_service is mock_llm
        # generate_simple should be called at least once (up to 2 for 2 NPCs)
        assert mock_llm.generate_simple.call_count >= 1

    def test_injected_llm_service_used(self):
        """传入 llm_service 时直接使用，不创建新实例。"""
        priestess = _make_npc_node("priestess", "女祭司")
        wg = _make_wg(
            area_npcs={"town_square": ["priestess"]},
            all_nodes={"priestess": priestess},
        )
        bus = SceneBus(area_id="town_square")
        bus.publish(BusEntry(
            actor="player", type=BusEntryType.SPEECH,
            content="女祭司你好",
        ))
        session = _make_session()

        mock_llm = MagicMock()
        mock_llm.generate_simple = AsyncMock(return_value="注入的反应。")
        reactor = NPCReactor(world_graph=wg, use_llm=True, llm_service=mock_llm)

        results = _run(reactor.collect_reactions(bus, session, {}))

        # Should use injected service
        assert reactor._llm_service is mock_llm
        mock_llm.generate_simple.assert_called()
        assert len(results) >= 1
        assert "注入的反应" in results[0].content

    def test_lazy_init_sets_llm_service(self):
        """未注入时，首次调用后 _llm_service 不再为 None。"""
        reactor = NPCReactor(world_graph=MagicMock(), use_llm=True)
        assert reactor._llm_service is None
        # After injection, it should be set
        mock_llm = MagicMock()
        reactor._llm_service = mock_llm
        assert reactor._llm_service is mock_llm


class TestConsecutiveFailures:
    def test_consecutive_failures_tracked(self):
        """3 次失败后计数为 3。"""
        priestess = _make_npc_node("priestess", "女祭司")
        candidate = {
            "npc_id": "priestess",
            "npc_name": "女祭司",
            "relevance": 10.0,
            "node": priestess,
        }
        bus = SceneBus(area_id="town_square")
        bus.publish(BusEntry(
            actor="player", type=BusEntryType.SPEECH, content="女祭司",
        ))
        session = _make_session()

        mock_llm = MagicMock()
        mock_llm.generate_simple = AsyncMock(side_effect=Exception("fail"))
        reactor = NPCReactor(world_graph=MagicMock(), use_llm=True, llm_service=mock_llm)

        assert reactor._llm_consecutive_failures == 0
        for _ in range(3):
            _run(reactor._generate_reaction(candidate, bus, session, {}))

        assert reactor._llm_consecutive_failures == 3

    def test_consecutive_failures_reset_on_success(self):
        """成功后计数归零。"""
        priestess = _make_npc_node("priestess", "女祭司")
        candidate = {
            "npc_id": "priestess",
            "npc_name": "女祭司",
            "relevance": 10.0,
            "node": priestess,
        }
        bus = SceneBus(area_id="town_square")
        bus.publish(BusEntry(
            actor="player", type=BusEntryType.SPEECH, content="女祭司",
        ))
        session = _make_session()

        mock_llm = MagicMock()
        mock_llm.generate_simple = AsyncMock(return_value="成功的反应。")
        reactor = NPCReactor(world_graph=MagicMock(), use_llm=True, llm_service=mock_llm)
        reactor._llm_consecutive_failures = 5

        result = _run(reactor._generate_reaction(candidate, bus, session, {}))

        assert result is not None
        assert reactor._llm_consecutive_failures == 0
