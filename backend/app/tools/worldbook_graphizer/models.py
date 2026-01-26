"""
世界书图谱化数据模型
"""
from typing import List, Dict, Optional, Any
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
