"""
Event Generator - 随机事件生成器

根据游戏状态生成随机事件：
- 旅途遭遇（怪物、NPC、发现）
- 天气变化
- 环境事件
"""
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from app.services.time_manager import GameTime, TimePeriod


class RandomEventType(str, Enum):
    """随机事件类型"""
    ENCOUNTER = "encounter"      # 遭遇（战斗/社交）
    DISCOVERY = "discovery"      # 发现（物品/地点）
    WEATHER = "weather"          # 天气变化
    GOSSIP = "gossip"            # 传闻/情报
    AMBIANCE = "ambiance"        # 氛围/环境描写
    NONE = "none"                # 无事件


class EncounterType(str, Enum):
    """遭遇类型"""
    HOSTILE = "hostile"          # 敌对（战斗）
    NEUTRAL = "neutral"          # 中立（可选战斗/对话）
    FRIENDLY = "friendly"        # 友好（对话）


@dataclass
class RandomEvent:
    """随机事件"""
    event_type: RandomEventType
    title: str
    description: str
    encounter_type: Optional[EncounterType] = None
    entities: List[Dict[str, Any]] = field(default_factory=list)
    loot: List[Dict[str, Any]] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)
    requires_response: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "title": self.title,
            "description": self.description,
            "encounter_type": self.encounter_type.value if self.encounter_type else None,
            "entities": self.entities,
            "loot": self.loot,
            "data": self.data,
            "requires_response": self.requires_response,
        }


class EventGenerator:
    """
    随机事件生成器

    根据位置、时间、玩家状态等因素生成随机事件。
    """

    # 基础遭遇表（按危险等级）
    ENCOUNTER_TABLES = {
        "low": [
            {"type": "friendly", "name": "旅行商人", "description": "一位推着小车的旅行商人"},
            {"type": "friendly", "name": "巡逻卫兵", "description": "正在巡逻的城镇卫兵"},
            {"type": "neutral", "name": "流浪者", "description": "一个风尘仆仆的旅人"},
            {"type": "ambiance", "name": "野兔", "description": "一只野兔从路边窜过"},
        ],
        "medium": [
            {"type": "neutral", "name": "野狼", "description": "几只野狼在远处观望"},
            {"type": "hostile", "name": "哥布林斥候", "description": "一个鬼鬼祟祟的哥布林"},
            {"type": "friendly", "name": "受伤的冒险者", "description": "一个受伤倒在路边的冒险者"},
            {"type": "neutral", "name": "神秘商人", "description": "一个戴着斗篷的神秘商人"},
        ],
        "high": [
            {"type": "hostile", "name": "哥布林小队", "description": "一群哥布林正在埋伏"},
            {"type": "hostile", "name": "强盗", "description": "几个凶神恶煞的强盗"},
            {"type": "neutral", "name": "受困的旅队", "description": "一队被怪物围困的商人"},
            {"type": "hostile", "name": "饥饿的狼群", "description": "一群饥肠辘辘的狼"},
        ],
        "extreme": [
            {"type": "hostile", "name": "哥布林战群", "description": "一大群哥布林，还有一个哥布林首领"},
            {"type": "hostile", "name": "食人魔", "description": "一只巨大的食人魔挡在路中央"},
            {"type": "hostile", "name": "落单的巨魔", "description": "一只凶猛的巨魔"},
        ],
    }

    # 发现事件表
    DISCOVERY_TABLE = [
        {"name": "废弃营地", "description": "发现一个废弃的营地，还有些残留的物资"},
        {"name": "野果丛", "description": "发现一丛可食用的野果"},
        {"name": "清澈泉水", "description": "发现一处清澈的泉水"},
        {"name": "古老石碑", "description": "发现一块刻有古老文字的石碑"},
        {"name": "隐秘小径", "description": "注意到一条隐蔽的小路"},
        {"name": "掉落的钱袋", "description": "在路边发现一个被遗落的钱袋"},
    ]

    # 天气事件表
    WEATHER_TABLE = [
        {"name": "乌云密布", "description": "天空开始阴沉下来，似乎要下雨了"},
        {"name": "微风习习", "description": "一阵凉爽的微风吹来"},
        {"name": "阳光明媚", "description": "云层散去，阳光洒落"},
        {"name": "起雾", "description": "一阵薄雾开始弥漫"},
        {"name": "小雨", "description": "开始下起淅淅沥沥的小雨"},
    ]

    # 传闻事件表
    GOSSIP_TABLE = [
        {"name": "冒险者传闻", "description": "听到路人谈论最近冒险者公会的悬赏任务"},
        {"name": "怪物出没", "description": "有人说附近出现了不寻常的怪物活动"},
        {"name": "商队消息", "description": "一支商队带来了远方的消息"},
        {"name": "神殿公告", "description": "神殿最近发布了新的公告"},
    ]

    def __init__(self, world_id: str, seed: Optional[int] = None):
        """
        初始化事件生成器

        Args:
            world_id: 世界ID
            seed: 随机种子（用于测试）
        """
        self.world_id = world_id
        if seed is not None:
            random.seed(seed)

    def check_travel_event(
        self,
        from_location: str,
        to_location: str,
        danger_level: str,
        time: GameTime,
        player_level: int = 1,
        base_chance: float = 0.3,
    ) -> Optional[RandomEvent]:
        """
        检查旅途中是否触发事件

        Args:
            from_location: 起点位置
            to_location: 终点位置
            danger_level: 危险等级
            time: 当前游戏时间
            player_level: 玩家等级
            base_chance: 基础触发概率

        Returns:
            随机事件，如果没有触发返回None
        """
        # 计算最终触发概率
        chance = base_chance

        # 时间段影响
        period = time.get_period()
        if period == TimePeriod.NIGHT:
            chance *= 1.5  # 夜间更危险
        elif period == TimePeriod.DAWN or period == TimePeriod.DUSK:
            chance *= 1.2

        # 危险等级影响
        danger_multipliers = {
            "low": 0.5,
            "medium": 1.0,
            "high": 1.5,
            "extreme": 2.0,
        }
        chance *= danger_multipliers.get(danger_level, 1.0)

        # 限制最大概率
        chance = min(chance, 0.8)

        # 随机判定
        if random.random() > chance:
            return None

        # 决定事件类型
        event_roll = random.random()

        if event_roll < 0.5:
            # 遭遇事件
            return self._generate_encounter(danger_level, time)
        elif event_roll < 0.7:
            # 发现事件
            return self._generate_discovery()
        elif event_roll < 0.85:
            # 氛围描写
            return self._generate_ambiance(danger_level, time)
        else:
            # 天气变化
            return self._generate_weather()

    def _generate_encounter(
        self,
        danger_level: str,
        time: GameTime,
    ) -> RandomEvent:
        """生成遭遇事件"""
        table = self.ENCOUNTER_TABLES.get(danger_level, self.ENCOUNTER_TABLES["low"])

        # 夜间更容易遇到敌对遭遇
        if time.get_period() == TimePeriod.NIGHT:
            hostile_entries = [e for e in table if e.get("type") == "hostile"]
            if hostile_entries and random.random() < 0.6:
                entry = random.choice(hostile_entries)
            else:
                entry = random.choice(table)
        else:
            entry = random.choice(table)

        # 处理纯氛围类型
        if entry.get("type") == "ambiance":
            return RandomEvent(
                event_type=RandomEventType.AMBIANCE,
                title=entry["name"],
                description=entry["description"],
            )

        encounter_type = {
            "hostile": EncounterType.HOSTILE,
            "neutral": EncounterType.NEUTRAL,
            "friendly": EncounterType.FRIENDLY,
        }.get(entry.get("type", "neutral"), EncounterType.NEUTRAL)

        return RandomEvent(
            event_type=RandomEventType.ENCOUNTER,
            title=entry["name"],
            description=entry["description"],
            encounter_type=encounter_type,
            entities=[{
                "name": entry["name"],
                "type": entry.get("type", "neutral"),
            }],
            requires_response=encounter_type in [EncounterType.HOSTILE, EncounterType.NEUTRAL],
        )

    def _generate_discovery(self) -> RandomEvent:
        """生成发现事件"""
        entry = random.choice(self.DISCOVERY_TABLE)

        # 随机决定是否有战利品
        loot = []
        if random.random() < 0.3:
            loot = [{"name": "少量金币", "amount": random.randint(1, 10)}]

        return RandomEvent(
            event_type=RandomEventType.DISCOVERY,
            title=entry["name"],
            description=entry["description"],
            loot=loot,
        )

    def _generate_weather(self) -> RandomEvent:
        """生成天气事件"""
        entry = random.choice(self.WEATHER_TABLE)

        return RandomEvent(
            event_type=RandomEventType.WEATHER,
            title=entry["name"],
            description=entry["description"],
            data={"weather": entry["name"]},
        )

    def _generate_ambiance(
        self,
        danger_level: str,
        time: GameTime,
    ) -> RandomEvent:
        """生成氛围描写"""
        period = time.get_period()

        ambiance_by_period = {
            TimePeriod.DAWN: [
                "晨雾在脚边缭绕，露水打湿了路边的草叶",
                "第一缕阳光穿透树冠，在地面上投下斑驳的光影",
                "远处传来公鸡的啼鸣，新的一天开始了",
            ],
            TimePeriod.DAY: [
                "阳光温暖，鸟儿在枝头歌唱",
                "微风带来远方的花香",
                "路边的野花随风摇曳",
            ],
            TimePeriod.DUSK: [
                "夕阳将天边染成金红色",
                "归巢的鸟群从头顶掠过",
                "远处的城镇开始亮起灯火",
            ],
            TimePeriod.NIGHT: [
                "月光洒落，在地面上投下银色的光芒",
                "虫鸣声此起彼伏，打破夜的寂静",
                "远处传来不明生物的嚎叫",
                "星空璀璨，银河清晰可见",
            ],
        }

        descriptions = ambiance_by_period.get(period, ambiance_by_period[TimePeriod.DAY])
        description = random.choice(descriptions)

        return RandomEvent(
            event_type=RandomEventType.AMBIANCE,
            title="途中风景",
            description=description,
        )

    def generate_location_event(
        self,
        location_id: str,
        time: GameTime,
        player_state: Optional[Dict[str, Any]] = None,
    ) -> Optional[RandomEvent]:
        """
        生成位置相关事件（进入新区域时）

        Args:
            location_id: 位置ID
            time: 当前时间
            player_state: 玩家状态

        Returns:
            随机事件
        """
        # 低概率触发位置事件
        if random.random() > 0.2:
            return None

        # 根据位置和时间生成事件
        event_roll = random.random()

        if event_roll < 0.4:
            return self._generate_gossip()
        else:
            return self._generate_ambiance("low", time)

    def _generate_gossip(self) -> RandomEvent:
        """生成传闻事件"""
        entry = random.choice(self.GOSSIP_TABLE)

        return RandomEvent(
            event_type=RandomEventType.GOSSIP,
            title=entry["name"],
            description=entry["description"],
        )

    def generate_encounter_for_combat(
        self,
        danger_level: str,
        count: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        为战斗生成敌人数据

        Args:
            danger_level: 危险等级
            count: 敌人数量

        Returns:
            敌人数据列表（可直接用于战斗系统）
        """
        # 基础敌人模板
        enemy_templates = {
            "哥布林斥候": {
                "type": "goblin_scout",
                "name": "哥布林斥候",
                "hp": 7,
                "ac": 12,
                "attack_bonus": 2,
                "damage": "1d6",
                "xp": 25,
            },
            "哥布林小队": {
                "type": "goblin",
                "name": "哥布林",
                "hp": 10,
                "ac": 13,
                "attack_bonus": 3,
                "damage": "1d6+1",
                "xp": 50,
            },
            "野狼": {
                "type": "wolf",
                "name": "野狼",
                "hp": 11,
                "ac": 13,
                "attack_bonus": 4,
                "damage": "2d4+2",
                "xp": 50,
            },
            "强盗": {
                "type": "bandit",
                "name": "强盗",
                "hp": 11,
                "ac": 12,
                "attack_bonus": 3,
                "damage": "1d6+1",
                "xp": 25,
            },
            "食人魔": {
                "type": "ogre",
                "name": "食人魔",
                "hp": 59,
                "ac": 11,
                "attack_bonus": 6,
                "damage": "2d8+4",
                "xp": 450,
            },
        }

        # 根据危险等级选择敌人类型
        level_enemies = {
            "low": ["哥布林斥候"],
            "medium": ["哥布林斥候", "野狼", "强盗"],
            "high": ["哥布林小队", "野狼", "强盗"],
            "extreme": ["哥布林小队", "食人魔"],
        }

        available = level_enemies.get(danger_level, ["哥布林斥候"])
        enemies = []

        for i in range(count):
            enemy_name = random.choice(available)
            template = enemy_templates.get(enemy_name, enemy_templates["哥布林斥候"])
            enemy = template.copy()
            enemy["id"] = f"{enemy['type']}_{i+1}"
            enemies.append(enemy)

        return enemies
