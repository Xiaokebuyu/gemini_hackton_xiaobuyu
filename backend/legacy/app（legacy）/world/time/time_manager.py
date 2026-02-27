"""
Game Time Manager - 游戏时间管理系统

管理游戏内时间的流逝，支持：
- 时间推进（分钟级别）
- 时间段判断（黎明/白天/黄昏/夜晚）
- 时间事件触发（商店开关门、NPC位置变化等）
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any


class TimePeriod(str, Enum):
    """时间段"""
    DAWN = "dawn"       # 黎明 (5:00-7:59)
    DAY = "day"         # 白天 (8:00-17:59)
    DUSK = "dusk"       # 黄昏 (18:00-19:59)
    NIGHT = "night"     # 夜晚 (20:00-4:59)


@dataclass
class GameTime:
    """游戏时间"""
    day: int = 1
    hour: int = 8
    minute: int = 0

    def advance(self, minutes: int) -> "GameTime":
        """
        推进时间

        Args:
            minutes: 推进的分钟数

        Returns:
            新的 GameTime 实例
        """
        total_minutes = self.hour * 60 + self.minute + minutes
        new_day = self.day + total_minutes // (24 * 60)
        remaining_minutes = total_minutes % (24 * 60)
        new_hour = remaining_minutes // 60
        new_minute = remaining_minutes % 60

        return GameTime(day=new_day, hour=new_hour, minute=new_minute)

    def get_period(self) -> TimePeriod:
        """获取当前时间段"""
        if 5 <= self.hour < 8:
            return TimePeriod.DAWN
        elif 8 <= self.hour < 18:
            return TimePeriod.DAY
        elif 18 <= self.hour < 20:
            return TimePeriod.DUSK
        else:
            return TimePeriod.NIGHT

    def format(self) -> str:
        """格式化时间显示"""
        return f"第{self.day}天 {self.hour:02d}:{self.minute:02d}"

    def format_short(self) -> str:
        """简短格式"""
        return f"{self.hour:02d}:{self.minute:02d}"

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "day": self.day,
            "hour": self.hour,
            "minute": self.minute,
            "period": self.get_period().value,
            "formatted": self.format(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GameTime":
        """从字典创建"""
        return cls(
            day=data.get("day", 1),
            hour=data.get("hour", 8),
            minute=data.get("minute", 0),
        )


@dataclass
class TimeEvent:
    """时间事件"""
    event_type: str  # period_change, shop_close, npc_move, etc.
    description: str
    data: Dict[str, Any] = field(default_factory=dict)


class TimeManager:
    """
    游戏时间管理器

    管理单个会话的游戏时间，支持：
    - 时间推进
    - 时间段变化检测
    - 时间相关事件触发
    """

    # 时间段对应的描述
    PERIOD_DESCRIPTIONS = {
        TimePeriod.DAWN: "黎明时分，晨雾笼罩着大地，第一缕阳光开始出现",
        TimePeriod.DAY: "阳光明媚的白天，正是活动的好时候",
        TimePeriod.DUSK: "黄昏降临，天边染上橙红色，商铺开始打烊",
        TimePeriod.NIGHT: "夜幕降临，月光洒落，街道变得安静",
    }

    # 不同时间段的危险系数
    DANGER_MULTIPLIERS = {
        TimePeriod.DAWN: 0.8,
        TimePeriod.DAY: 0.5,
        TimePeriod.DUSK: 1.0,
        TimePeriod.NIGHT: 1.5,
    }

    def __init__(self, initial_time: Optional[GameTime] = None):
        """
        初始化时间管理器

        Args:
            initial_time: 初始时间，默认为第1天8:00
        """
        self.time = initial_time or GameTime(day=1, hour=8, minute=0)

    def tick(self, minutes: int) -> List[TimeEvent]:
        """
        推进时间并返回触发的事件

        Args:
            minutes: 推进的分钟数

        Returns:
            触发的时间事件列表
        """
        events: List[TimeEvent] = []
        old_period = self.time.get_period()
        old_day = self.time.day

        # 推进时间
        self.time = self.time.advance(minutes)
        new_period = self.time.get_period()

        # 检查时间段变化
        if old_period != new_period:
            events.append(TimeEvent(
                event_type="period_change",
                description=self.PERIOD_DESCRIPTIONS[new_period],
                data={
                    "old_period": old_period.value,
                    "new_period": new_period.value,
                },
            ))

        # 检查日期变化
        if old_day != self.time.day:
            events.append(TimeEvent(
                event_type="new_day",
                description=f"新的一天开始了，这是第{self.time.day}天",
                data={
                    "old_day": old_day,
                    "new_day": self.time.day,
                },
            ))

        return events

    def get_danger_multiplier(self) -> float:
        """获取当前时间段的危险系数"""
        return self.DANGER_MULTIPLIERS[self.time.get_period()]

    def is_shop_open(self) -> bool:
        """商店是否营业（8:00-20:00）"""
        return 8 <= self.time.hour < 20

    def is_tavern_active(self) -> bool:
        """酒馆是否热闹（18:00-02:00）"""
        return self.time.hour >= 18 or self.time.hour < 2

    def is_guild_open(self) -> bool:
        """冒险者公会是否开放（6:00-22:00）"""
        return 6 <= self.time.hour < 22

    def get_npc_activity_modifier(self, npc_type: str) -> str:
        """
        根据NPC类型和时间获取活动状态

        Args:
            npc_type: NPC类型（merchant, guard, adventurer, etc.）

        Returns:
            活动状态描述
        """
        period = self.time.get_period()

        activity_map = {
            "merchant": {
                TimePeriod.DAWN: "正在开店准备",
                TimePeriod.DAY: "在店内忙碌",
                TimePeriod.DUSK: "准备打烊",
                TimePeriod.NIGHT: "已经回家休息",
            },
            "guard": {
                TimePeriod.DAWN: "交接班中",
                TimePeriod.DAY: "在岗位巡逻",
                TimePeriod.DUSK: "准备交接班",
                TimePeriod.NIGHT: "夜间巡逻中",
            },
            "adventurer": {
                TimePeriod.DAWN: "准备出发冒险",
                TimePeriod.DAY: "可能在外冒险或在公会",
                TimePeriod.DUSK: "正在返回城镇",
                TimePeriod.NIGHT: "在酒馆休息或已入睡",
            },
            "priest": {
                TimePeriod.DAWN: "进行晨祷",
                TimePeriod.DAY: "在神殿服务",
                TimePeriod.DUSK: "进行晚祷",
                TimePeriod.NIGHT: "在神殿休息",
            },
        }

        type_map = activity_map.get(npc_type, activity_map["adventurer"])
        return type_map.get(period, "状态未知")

    def estimate_travel_time(self, distance: str) -> int:
        """
        估算旅行时间（分钟）

        Args:
            distance: 距离描述（如 "半天路程", "数小时", "附近"）

        Returns:
            估算的分钟数
        """
        distance_lower = distance.lower() if distance else ""

        if "附近" in distance_lower or "nearby" in distance_lower:
            return 15
        elif "数分钟" in distance_lower or "few minutes" in distance_lower:
            return 10
        elif "半小时" in distance_lower or "half hour" in distance_lower:
            return 30
        elif "一小时" in distance_lower or "1小时" in distance_lower or "one hour" in distance_lower:
            return 60
        elif "数小时" in distance_lower or "several hours" in distance_lower:
            return 180
        elif "半天" in distance_lower or "half day" in distance_lower:
            return 360
        elif "一天" in distance_lower or "1天" in distance_lower or "one day" in distance_lower:
            return 720
        else:
            # 默认30分钟
            return 30

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于持久化）"""
        return self.time.to_dict()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TimeManager":
        """从字典创建"""
        return cls(initial_time=GameTime.from_dict(data))
