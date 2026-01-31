"""
Passerby Models - 路人NPC聚合存储数据模型

路人NPC特点：
- PASSERBY级别，按地点聚合存储
- 共享地点级记忆图谱
- 运行时生成，非持久化角色资料

Firestore结构：
    worlds/{world_id}/
      maps/{map_id}/
        passerby_pool/
          config          # PasserbySpawnConfig
          sentiment       # float: 地点舆论
          active/         # 当前活跃路人
            {instance_id} # PasserbyInstance
"""
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class PasserbyInstance(BaseModel):
    """路人NPC运行时实例"""
    instance_id: str
    template_id: str  # 来源模板ID
    map_id: str
    sub_location_id: Optional[str] = None
    name: str
    appearance: str
    personality_snippet: str
    mood: str = "neutral"
    spawn_time: datetime = Field(default_factory=datetime.now)
    interaction_count: int = 0
    last_interaction: Optional[datetime] = None


class PasserbySpawnConfig(BaseModel):
    """路人生成配置"""
    max_concurrent: int = 5  # 最大同时存在数量
    spawn_interval_minutes: int = 30  # 生成间隔
    despawn_after_minutes: int = 60  # 无交互后消失时间
    templates: List[str] = Field(default_factory=list)  # 可用模板ID


class LocationPasserbyPool(BaseModel):
    """地点级路人池"""
    map_id: str
    config: PasserbySpawnConfig = Field(default_factory=PasserbySpawnConfig)
    active_instances: Dict[str, PasserbyInstance] = Field(default_factory=dict)
    sentiment: float = 0.0  # -1到1，地点舆论/氛围


class SharedMemoryContribution(BaseModel):
    """共享记忆贡献"""
    contributor_type: str  # "passerby" / "player_action" / "event"
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)
    importance: float = 0.5  # 0-1，重要性


class PasserbyTemplate(BaseModel):
    """路人模板（从 characters.json PASSERBY 角色或 maps.json 加载）"""
    id: str  # 模板ID
    # 支持名称池（从角色数据）或名称模式（从maps.json）
    name_pool: List[str] = Field(default_factory=list)  # 可选名称池
    name_pattern: Optional[str] = None  # 名称模式，如 "新手冒险者"
    # 性格和外貌
    personality_pool: List[str] = Field(default_factory=list)  # 性格池
    personality_template: Optional[str] = None  # 性格模板
    appearance_pool: List[str] = Field(default_factory=list)  # 外貌池
    appearance_hints: Optional[str] = None  # 外貌提示
    speech_pattern: Optional[str] = None  # 说话方式
    occupation: Optional[str] = None  # 职业
    default_map: Optional[str] = None  # 默认地图
    # 向后兼容
    template_id: Optional[str] = None  # 旧字段名
    location_id: Optional[str] = None  # 旧字段名

    def get_name(self) -> str:
        """获取一个随机名称"""
        import random
        if self.name_pool:
            return random.choice(self.name_pool)
        return self.name_pattern or self.id

    def get_appearance(self) -> str:
        """获取一个随机外貌描述"""
        import random
        if self.appearance_pool:
            return random.choice(self.appearance_pool)
        return self.appearance_hints or "普通的外貌"

    def get_personality(self) -> str:
        """获取一个随机性格描述"""
        import random
        if self.personality_pool:
            return random.choice(self.personality_pool)
        return self.personality_template or "友好但有些拘谨"
