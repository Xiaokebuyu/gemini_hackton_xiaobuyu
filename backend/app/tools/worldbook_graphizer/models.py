"""
世界书图谱化数据模型
"""
from typing import List, Dict, Optional, Any, TYPE_CHECKING
from pydantic import BaseModel, Field
from enum import Enum


class NPCTier(str, Enum):
    """NPC 层级"""
    MAIN = "main"           # 主要角色 - 永久持久化
    SECONDARY = "secondary" # 次要角色 - 永久持久化
    PASSERBY = "passerby"   # 路人 NPC - 地图级持久化


class MapConnection(BaseModel):
    """地图连接"""
    target_map_id: str = Field(..., description="目标地图 ID")
    connection_type: str = Field(default="walk", description="连接类型: walk/travel/explore")
    travel_time: Optional[str] = Field(None, description="旅行时间描述")
    requirements: Optional[str] = Field(None, description="通行条件")


class SubLocationInfo(BaseModel):
    """子地点信息（地图内的可导航位置）"""
    id: str = Field(..., description="子地点 ID，如 'guild_hall'")
    name: str = Field(..., description="子地点名称，如 '冒险者公会分部'")
    description: str = Field(default="", description="子地点描述")
    interaction_type: str = Field(default="visit", description="交互类型: visit/shop/quest/rest")
    resident_npcs: List[str] = Field(default_factory=list, description="常驻 NPC ID 列表")
    available_actions: List[str] = Field(default_factory=list, description="可执行的动作")
    passerby_spawn_rate: float = Field(default=0.3, description="路人生成概率")
    travel_time_minutes: int = Field(default=0, description="从地图到此处的时间（分钟）")


class MapInfo(BaseModel):
    """地图/箱庭信息"""
    id: str = Field(..., description="英文 ID，如 'frontier_town'")
    name: str = Field(..., description="中文名称，如 '边境小镇'")
    description: str = Field(..., description="地图描述")
    atmosphere: Optional[str] = Field(None, description="氛围描述")
    danger_level: str = Field(default="low", description="危险等级: low/medium/high/extreme")
    region: Optional[str] = Field(None, description="所属区域")
    connections: List[MapConnection] = Field(default_factory=list, description="连接的地图")
    available_actions: List[str] = Field(default_factory=list, description="可执行的动作")
    key_features: List[str] = Field(default_factory=list, description="关键特征/地标")
    sub_locations: List[SubLocationInfo] = Field(default_factory=list, description="子地点列表")


class PasserbyTemplate(BaseModel):
    """路人 NPC 模板"""
    template_id: str = Field(..., description="模板 ID")
    name_pattern: str = Field(..., description="名称模式，如 '新手冒险者'")
    personality_template: str = Field(..., description="性格模板")
    speech_pattern: Optional[str] = Field(None, description="说话方式")
    appearance_hints: Optional[str] = Field(None, description="外观提示")


class CharacterInfo(BaseModel):
    """角色信息"""
    id: str = Field(..., description="英文 ID，如 'priestess'")
    name: str = Field(..., description="中文名称，如 '女神官'")
    tier: NPCTier = Field(..., description="NPC 层级")
    default_map: Optional[str] = Field(None, description="默认所在地图 ID")
    default_sub_location: Optional[str] = Field(None, description="默认所在子地点 ID，如 'guild_hall'")
    aliases: List[str] = Field(default_factory=list, description="别名列表")

    # 角色资料 - 对应 CharacterProfile
    occupation: Optional[str] = Field(None, description="职业")
    age: Optional[int] = Field(None, description="年龄")
    personality: Optional[str] = Field(None, description="性格描述")
    speech_pattern: Optional[str] = Field(None, description="说话方式")
    example_dialogue: Optional[str] = Field(None, description="示例对话")
    appearance: Optional[str] = Field(None, description="外貌描述")
    backstory: Optional[str] = Field(None, description="背景故事")

    # 关系
    relationships: Dict[str, str] = Field(default_factory=dict, description="与其他角色的关系")

    # 元数据
    importance: float = Field(default=0.5, description="重要性 0-1")
    tags: List[str] = Field(default_factory=list, description="标签")


class WorldMapRegion(BaseModel):
    """世界地图区域"""
    id: str = Field(..., description="区域 ID")
    name: str = Field(..., description="区域名称")
    description: Optional[str] = Field(None, description="区域描述")
    maps: List[str] = Field(default_factory=list, description="包含的地图 ID 列表")
    danger_level: str = Field(default="low", description="危险等级")


class WorldMap(BaseModel):
    """世界地图"""
    name: str = Field(..., description="世界名称")
    description: str = Field(..., description="世界描述")
    regions: List[WorldMapRegion] = Field(default_factory=list, description="区域列表")
    global_connections: List[Dict[str, Any]] = Field(default_factory=list, description="跨区域连接")


class MapsData(BaseModel):
    """地图数据汇总"""
    maps: List[MapInfo] = Field(default_factory=list)
    passerby_templates: Dict[str, List[PasserbyTemplate]] = Field(
        default_factory=dict,
        description="按地图 ID 分组的路人模板"
    )


class CharactersData(BaseModel):
    """角色数据汇总"""
    characters: List[CharacterInfo] = Field(default_factory=list)
    map_assignments: Dict[str, Dict[str, List[str]]] = Field(
        default_factory=dict,
        description="地图 -> {main: [], secondary: [], passerby_templates: []}"
    )


class GraphizeResult(BaseModel):
    """图谱化结果"""
    maps: MapsData
    characters: CharactersData
    world_map: WorldMap
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ============ 酒馆卡片相关模型 ============

class WorldbookEntry(BaseModel):
    """
    SillyTavern 世界书条目模型

    从酒馆卡片 JSON 文件中解析的单个条目
    """
    uid: int = Field(..., description="条目唯一 ID")
    key: List[str] = Field(default_factory=list, description="触发关键词列表")
    keysecondary: List[str] = Field(default_factory=list, description="次要关键词")
    comment: str = Field(default="", description="条目注释/分类")
    content: str = Field(default="", description="条目内容")

    # 条目控制参数
    constant: bool = Field(default=False, description="是否常驻上下文")
    selective: bool = Field(default=False, description="是否选择性触发")
    disable: bool = Field(default=False, description="是否禁用")
    order: int = Field(default=0, description="排序权重")
    position: int = Field(default=0, description="插入位置")
    depth: int = Field(default=4, description="递归深度")

    # 元数据
    group: str = Field(default="", description="分组")

    # 派生属性
    entry_type: Optional[str] = Field(default=None, description="从 comment 解析的类型")
    entry_name: Optional[str] = Field(default=None, description="从 comment 解析的名称")

    @classmethod
    def from_raw(cls, raw: Dict[str, Any], index: int) -> "WorldbookEntry":
        """从原始字典创建条目"""
        # 解析 comment 获取类型和名称
        comment = raw.get("comment", "")
        entry_type = None
        entry_name = None

        if " - " in comment:
            parts = comment.split(" - ", 1)
            entry_type = parts[0].strip()
            entry_name = parts[1].strip() if len(parts) > 1 else None
        elif "-" in comment:
            parts = comment.split("-", 1)
            entry_type = parts[0].strip()
            entry_name = parts[1].strip() if len(parts) > 1 else None
        else:
            entry_type = comment.strip() or "other"

        return cls(
            uid=raw.get("uid", index),
            key=raw.get("key", []),
            keysecondary=raw.get("keysecondary", []),
            comment=comment,
            content=raw.get("content", ""),
            constant=raw.get("constant", False),
            selective=raw.get("selective", False),
            disable=raw.get("disable", False),
            order=raw.get("order", 0),
            position=raw.get("position", 0),
            depth=raw.get("depth", 4),
            group=raw.get("group", ""),
            entry_type=entry_type,
            entry_name=entry_name,
        )


class EntryTypeGroup(BaseModel):
    """按类型分组的条目集合"""
    entry_type: str = Field(..., description="条目类型")
    entries: List[WorldbookEntry] = Field(default_factory=list, description="该类型的所有条目")
    count: int = Field(default=0, description="条目数量")


class TavernCardData(BaseModel):
    """酒馆卡片解析结果"""
    entries: List[WorldbookEntry] = Field(default_factory=list, description="所有条目")
    groups: Dict[str, EntryTypeGroup] = Field(default_factory=dict, description="按类型分组")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="原始元数据")
