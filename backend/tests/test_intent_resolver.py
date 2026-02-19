"""Tests for IntentResolver (Direction A.2)."""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.world.intent_resolver import IntentResolver, IntentType


def _make_mock_node(node_id: str, name: str, node_type: str = "area", state: dict = None):
    node = MagicMock()
    node.id = node_id
    node.name = name
    node.type = node_type
    node.state = state or {}
    return node


def _make_graph_and_session(
    current_area="town_square",
    connects=None,
    children=None,
    entities_at=None,
    nodes=None,
    sub_location=None,
):
    """Build mock WorldGraph + SessionRuntime."""
    wg = MagicMock()
    nodes = nodes or {}

    def get_node(nid):
        return nodes.get(nid)

    wg.get_node = get_node
    wg.has_node = lambda nid: nid in nodes

    # CONNECTS neighbors
    def get_neighbors(node_id, relation=None):
        if relation == "connects":
            return connects or []
        return []

    wg.get_neighbors = get_neighbors

    # CONTAINS children
    def get_children(node_id, type_filter=None):
        if type_filter == "location":
            return [cid for cid, _ in (children or [])]
        return []

    wg.get_children = get_children

    # Entities at
    def get_entities_at(loc_id):
        return entities_at.get(loc_id, []) if entities_at else []

    wg.get_entities_at = get_entities_at

    session = MagicMock()
    session.player_location = current_area
    session.sub_location = sub_location
    session.world_graph = wg

    return wg, session


class TestMoveResolution:
    def test_move_by_area_name(self):
        tavern = _make_mock_node("tavern_area", "酒馆")
        wg, session = _make_graph_and_session(
            connects=[("tavern_area", {"travel_time": "30 minutes"})],
            nodes={"tavern_area": tavern},
        )
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("去酒馆")
        assert result is not None
        assert result.type == IntentType.MOVE
        assert result.target == "tavern_area"

    def test_move_by_area_id(self):
        forest = _make_mock_node("dark_forest", "黑暗森林")
        wg, session = _make_graph_and_session(
            connects=[("dark_forest", {"travel_time": "1 hour"})],
            nodes={"dark_forest": forest},
        )
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("去dark_forest")
        assert result is not None
        assert result.type == IntentType.MOVE
        assert result.target == "dark_forest"

    def test_move_english(self):
        market = _make_mock_node("market", "Market")
        wg, session = _make_graph_and_session(
            connects=[("market", {})],
            nodes={"market": market},
        )
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("go to Market")
        assert result is not None
        assert result.type == IntentType.MOVE

    def test_move_sublocation(self):
        smithy = _make_mock_node("smithy", "铁匠铺", "location")
        wg, session = _make_graph_and_session(
            children=[("smithy", {})],
            nodes={"smithy": smithy},
        )
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("进入铁匠铺")
        assert result is not None
        assert result.type == IntentType.MOVE
        assert result.target == "smithy"
        assert result.params.get("is_sublocation") is True

    def test_no_match_returns_none(self):
        wg, session = _make_graph_and_session()
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("我想搞点事情")
        assert result is None

    def test_no_move_keyword_returns_none(self):
        tavern = _make_mock_node("tavern", "酒馆")
        wg, session = _make_graph_and_session(
            connects=[("tavern", {})],
            nodes={"tavern": tavern},
        )
        resolver = IntentResolver(wg, session)
        # 没有导航关键词，即使有酒馆这个名字也不匹配
        result = resolver.resolve("酒馆真不错")
        assert result is None


class TestTalkResolution:
    def test_talk_by_npc_name(self):
        priestess = _make_mock_node("priestess", "女祭司", "npc")
        wg, session = _make_graph_and_session(
            entities_at={"town_square": ["priestess"]},
            nodes={"priestess": priestess},
        )
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("跟女祭司说话")
        assert result is not None
        assert result.type == IntentType.TALK
        assert result.target == "priestess"

    def test_talk_english(self):
        goblin = _make_mock_node("goblin_slayer", "Goblin Slayer", "npc")
        wg, session = _make_graph_and_session(
            entities_at={"town_square": ["goblin_slayer"]},
            nodes={"goblin_slayer": goblin},
        )
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("talk to Goblin Slayer")
        assert result is not None
        assert result.type == IntentType.TALK

    def test_talk_by_id(self):
        npc = _make_mock_node("guild_girl", "公会柜员", "npc")
        wg, session = _make_graph_and_session(
            entities_at={"town_square": ["guild_girl"]},
            nodes={"guild_girl": npc},
        )
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("找guild_girl聊聊")
        assert result is not None
        assert result.type == IntentType.TALK

    def test_no_talk_keyword_returns_none(self):
        npc = _make_mock_node("priestess", "女祭司", "npc")
        wg, session = _make_graph_and_session(
            entities_at={"town_square": ["priestess"]},
            nodes={"priestess": npc},
        )
        resolver = IntentResolver(wg, session)
        # 没有对话关键词
        result = resolver.resolve("女祭司在这里")
        assert result is None

    def test_npc_not_in_area_returns_none(self):
        npc = _make_mock_node("dragon", "恶龙", "npc")
        wg, session = _make_graph_and_session(
            entities_at={},  # NPC not at current area
            nodes={"dragon": npc},
        )
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("跟恶龙说话")
        assert result is None


class TestLeaveResolution:
    def test_leave_in_sublocation(self):
        wg, session = _make_graph_and_session(sub_location="smithy")
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("离开这里")
        assert result is not None
        assert result.type == IntentType.LEAVE
        assert result.target == "smithy"

    def test_leave_english(self):
        wg, session = _make_graph_and_session(sub_location="tavern_room")
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("exit this place")
        assert result is not None
        assert result.type == IntentType.LEAVE

    def test_leave_not_in_sublocation_returns_none(self):
        """不在子地点时，离开关键词不匹配。"""
        wg, session = _make_graph_and_session(sub_location=None)
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("离开这里")
        assert result is None

    def test_leave_go_back(self):
        wg, session = _make_graph_and_session(sub_location="inn_room")
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("go back")
        assert result is not None
        assert result.type == IntentType.LEAVE


class TestRestResolution:
    def test_rest_chinese(self):
        wg, session = _make_graph_and_session()
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("我想休息一下")
        assert result is not None
        assert result.type == IntentType.REST

    def test_rest_english(self):
        wg, session = _make_graph_and_session()
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("take a break")
        assert result is not None
        assert result.type == IntentType.REST

    def test_rest_sleep(self):
        wg, session = _make_graph_and_session()
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("睡觉")
        assert result is not None
        assert result.type == IntentType.REST

    def test_rest_camp(self):
        wg, session = _make_graph_and_session()
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("扎营休息")
        assert result is not None
        assert result.type == IntentType.REST

    def test_no_rest_keyword_returns_none(self):
        wg, session = _make_graph_and_session()
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("我要战斗")
        assert result is None


class TestEdgeCases:
    def test_empty_input(self):
        wg, session = _make_graph_and_session()
        resolver = IntentResolver(wg, session)
        assert resolver.resolve("") is None

    def test_no_world_graph(self):
        resolver = IntentResolver(None, MagicMock())
        assert resolver.resolve("去酒馆") is None

    def test_no_player_location(self):
        wg = MagicMock()
        session = MagicMock()
        session.player_location = None
        resolver = IntentResolver(wg, session)
        assert resolver.resolve("去酒馆") is None

    def test_longest_name_wins(self):
        """多候选时选最长 name 匹配。"""
        guild = _make_mock_node("guild", "公会", "area")
        guild_hall = _make_mock_node("guild_hall", "公会大厅", "area")
        wg, session = _make_graph_and_session(
            connects=[("guild", {}), ("guild_hall", {})],
            nodes={"guild": guild, "guild_hall": guild_hall},
        )
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("去公会大厅")
        assert result is not None
        assert result.target == "guild_hall"


class TestExamineIntent:
    def test_examine_sublocation_chinese(self):
        """查看关键词 + 子地点名称 → EXAMINE。"""
        smithy = _make_mock_node("smithy", "铁匠铺", "location")
        wg, session = _make_graph_and_session(
            children=[("smithy", {})],
            nodes={"smithy": smithy},
        )
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("查看铁匠铺")
        assert result is not None
        assert result.type == IntentType.EXAMINE
        assert result.target == "smithy"
        assert result.target_name == "铁匠铺"

    def test_examine_npc_chinese(self):
        """审视关键词 + NPC 名称 → EXAMINE。"""
        npc = _make_mock_node("guild_girl", "公会柜员", "npc")
        wg, session = _make_graph_and_session(
            entities_at={"town_square": ["guild_girl"]},
            nodes={"guild_girl": npc},
        )
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("仔细审视公会柜员")
        assert result is not None
        assert result.type == IntentType.EXAMINE
        assert result.target == "guild_girl"

    def test_examine_english(self):
        """examine 关键词 + 目标 ID → EXAMINE。"""
        chest = _make_mock_node("treasure_chest", "宝箱", "location")
        wg, session = _make_graph_and_session(
            children=[("treasure_chest", {})],
            nodes={"treasure_chest": chest},
        )
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("examine treasure_chest")
        assert result is not None
        assert result.type == IntentType.EXAMINE

    def test_examine_no_keyword_returns_none(self):
        """没有检查关键词时不匹配。"""
        smithy = _make_mock_node("smithy", "铁匠铺", "location")
        wg, session = _make_graph_and_session(
            children=[("smithy", {})],
            nodes={"smithy": smithy},
        )
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("铁匠铺不错")
        assert result is None or result.type != IntentType.EXAMINE

    def test_examine_no_match_target_returns_none(self):
        """有关键词但无匹配目标时返回 None。"""
        wg, session = _make_graph_and_session()
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("查看神秘的黑暗之门")
        assert result is None


class TestUseItemIntent:
    def _make_session_with_inventory(self, items):
        wg, session = _make_graph_and_session()
        state_mock = MagicMock()
        player_mock = {"inventory": items}
        state_mock.player_character = player_mock
        session.state = state_mock
        return wg, session

    def test_use_item_chinese_by_name(self):
        """使用关键词 + 物品名 → USE_ITEM。"""
        items = [{"item_id": "healing_potion", "item_name": "治疗药水"}]
        wg, session = self._make_session_with_inventory(items)
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("使用治疗药水")
        assert result is not None
        assert result.type == IntentType.USE_ITEM
        assert result.target == "healing_potion"
        assert result.target_name == "治疗药水"

    def test_use_item_by_id(self):
        """使用关键词 + 物品 ID → USE_ITEM。"""
        items = [{"item_id": "mana_potion", "item_name": "魔力药水"}]
        wg, session = self._make_session_with_inventory(items)
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("用mana_potion")
        assert result is not None
        assert result.type == IntentType.USE_ITEM
        assert result.target == "mana_potion"

    def test_drink_item_english(self):
        """drink 关键词 → USE_ITEM。"""
        items = [{"item_id": "health_potion", "item_name": "Health Potion"}]
        wg, session = self._make_session_with_inventory(items)
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("drink Health Potion")
        assert result is not None
        assert result.type == IntentType.USE_ITEM

    def test_use_item_no_keyword_returns_none(self):
        """没有使用关键词时不匹配。"""
        items = [{"item_id": "healing_potion", "item_name": "治疗药水"}]
        wg, session = self._make_session_with_inventory(items)
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("治疗药水有多少")
        assert result is None or result.type != IntentType.USE_ITEM

    def test_use_item_empty_inventory_returns_none(self):
        """背包为空时无法匹配 USE_ITEM。"""
        wg, session = self._make_session_with_inventory([])
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("使用药水")
        assert result is None

    def test_use_item_reads_session_player_inventory(self):
        """真实 SessionRuntime 风格：读取 session.player.inventory（无 session.state）。"""
        wg, _ = _make_graph_and_session()
        session = SimpleNamespace(
            player_location="town_square",
            sub_location=None,
            world_graph=wg,
            player=SimpleNamespace(
                inventory=[{"item_id": "healing_potion", "item_name": "治疗药水"}]
            ),
        )
        resolver = IntentResolver(wg, session)
        result = resolver.resolve("使用治疗药水")
        assert result is not None
        assert result.type == IntentType.USE_ITEM
        assert result.target == "healing_potion"
