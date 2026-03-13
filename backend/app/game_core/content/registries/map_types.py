"""MapRegistry 使用的子结构体类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.game_core.content.registries.shared_types import LootTableDef


@dataclass(slots=True)
class EncounterEntry:
    """单条遭遇表条目，描述一组随机遭遇的构成与触发条件。"""

    id: str = ""                                   # 跟踪 key，load 时自动补 "{area_id}_{idx}" 若为空
    monster_ids: list[str] = field(default_factory=list)  # → MonsterRegistry
    weight: float = 1.0                            # 加权随机选择权重
    min_danger: float = 0.0                        # 触发所需最低危险度
    description: str = ""
    map_category: str | None = None                # battle_maps category hint / fallback override


@dataclass(slots=True)
class CheckPath:
    """单条检定路径，代表完成交互的一种方式（OR 逻辑）。"""

    skill: str = ""                          # perception / investigation / athletics / thieves_tools
    dc: int = 10                             # 检定 DC
    label: str = ""                          # 前端显示文本 "撬锁" / "强行破开"
    fail_consequence: str | None = None      # 失败后果描述，None = 无后果


@dataclass(slots=True)
class TrapData:
    """陷阱数据，附加在 ContainerData 上。"""

    detect_dc: int = 15                      # 被动感知检测 DC
    disarm_dc: int = 15                      # 盗贼工具拆除 DC
    damage: str = "1d6"                      # 伤害骰表达式
    damage_type: str = "piercing"            # fire / poison / piercing / necrotic
    effect: str | None = None               # 附加效果描述 "中毒 3 回合"，None = 仅伤害


@dataclass(slots=True)
class ContainerData:
    """容器专属数据，仅 InteractableTemplate.type="container" 时有值。"""

    container_type: str = "chest"            # chest / corpse / barrel / crate / bookshelf / stash
    locked: bool = False                     # locked=True → 自动生成 thieves_tools 检定
    breakable: bool = False                  # breakable=True → 自动追加 athletics 检定
    trap: TrapData | None = None
    loot: LootTableDef = field(default_factory=LootTableDef)


@dataclass(slots=True)
class InteractableTemplate:
    """可交互物模板（容器、机关、谜题等）。"""

    id: str = ""
    name: str = ""
    description: str = ""
    type: str = "inspect"                    # inspect / use / loot / puzzle / container
    visibility_dc: int | None = None         # 被动感知 DC，None = 始终可见
    checks: list[CheckPath] = field(default_factory=list)   # 多条检定路径（OR 逻辑）
    reward: dict[str, Any] | None = None    # {type: "quest_hook"/"item"/"info"/"sub_location", id: "..."}
    one_time: bool = False                   # True = 交互一次后标记已用
    tags: list[str] = field(default_factory=list)
    functional: dict[str, Any] | None = None  # {type: "board_browse"/"donation", params?: {...}}
    container_data: ContainerData | None = None  # 仅 type="container" 时有值


@dataclass(slots=True)
class HostileGroup:
    """敌对群组定义，描述一组同类型怪物。"""

    monster_ids: list[str] = field(default_factory=list)  # → MonsterRegistry
    count: str = "1"                         # 骰子表达式 "3" / "1d4+1"
    role: str = "guard"                      # guard / patrol / boss / ambush


@dataclass(slots=True)
class HostileConfig:
    """敌对区域配置，描述整个区域的敌对状态。"""

    hostile_groups: list[HostileGroup] = field(default_factory=list)
    stealth_dc: int = 12                     # DC = 最高敌方被动感知
    alert_state: str = "unaware"             # unaware / patrolling / alert（alert → DC+5）
    blocking: bool = True                    # True = 必须击败才能离开
    ambient_description: str = ""            # 环境描述供 GM 叙述


@dataclass(slots=True)
class HostileTemplate:
    """敌对子区域模板（如"地下城入口守卫区"）。"""

    id: str = ""
    name: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    hostile_config: HostileConfig = field(default_factory=HostileConfig)
    interactables: list[InteractableTemplate] = field(default_factory=list)
    refresh_delay_ticks: int = 12            # 清理后多少 tick 刷新（12≈半天）
    min_danger: float = 0.0                  # 区域 danger_level >= 此值才生成


@dataclass(slots=True)
class Discovery:
    """主动探索可发现的隐藏信息或子地点。"""

    id: str = ""
    name: str = ""
    check_type: str = "perception"           # perception / investigation / nature
    dc: int = 15
    reward: dict[str, Any] = field(default_factory=dict)  # {type: "sub_location"/"item"/"quest_hook", id: "..."}
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SubAreaClusterConfig:
    """动态子位置集群配置，控制区域内动态子地点的生成上限和偏好。"""

    max_dynamic: int = 5                     # 动态子位置上限
    max_permanent_dynamic: int = 2           # 永久动态子位置上限
    fill_tags: list[str] = field(default_factory=list)    # 偏好 tag
    fill_density: str = "normal"             # sparse / normal / dense


@dataclass(slots=True)
class RoomTemplate:
    """房间静态模板（公会大厅内的具体区域等）。"""

    id: str = ""
    name: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    discoverable: bool = False               # True = 需要主动发现才可进入
    discovery_dc: int = 0                    # 0 = 询问即可知道；>0 = 需要检定
    resident_npcs: list[str] = field(default_factory=list)  # character_id 列表
    interactables: list[InteractableTemplate] = field(default_factory=list)


@dataclass(slots=True)
class SubLocationTemplate:
    """子地点静态模板（酒馆、商店、副本入口等）。"""

    id: str = ""
    name: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    type: str = "visit"                      # visit / shop / quest / rest / worship / dungeon / discovery / camp
    available_hours: tuple[int, int] | None = None  # (开门, 关门) 小时，None = 24h
    resident_npcs: list[str] = field(default_factory=list)  # character_id 列表
    interactables: list[InteractableTemplate] = field(default_factory=list)
    hostile_config: HostileConfig | None = None
    rooms: dict[str, RoomTemplate] = field(default_factory=dict)   # room_id → RoomTemplate
    default_room: str = ""                   # 进入此子地点时玩家自动放置的房间 ID
