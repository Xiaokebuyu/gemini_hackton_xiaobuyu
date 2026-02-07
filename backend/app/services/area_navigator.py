"""
Area Navigator - 区域导航系统

提供基于地图数据的导航功能：
- 当前位置查询
- 相邻区域获取
- 路径计算（A*算法）
- 旅行可行性检查
- 子地点导航（Module 2）
"""
import json
import heapq
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from google.cloud import firestore

from app.config import settings


logger = logging.getLogger(__name__)


class InteractionType(str, Enum):
    """子地点交互类型"""
    VISIT = "visit"       # 普通访问
    SHOP = "shop"         # 商店
    QUEST = "quest"       # 任务
    REST = "rest"         # 休息
    CRAFT = "craft"       # 制作
    WORSHIP = "worship"   # 祭拜


class ConnectionType(str, Enum):
    """连接类型"""
    TRAVEL = "travel"        # 普通旅行
    GATE = "gate"            # 门/入口
    TELEPORT = "teleport"    # 传送
    SECRET = "secret"        # 隐藏通道


@dataclass
class SubLocation:
    """
    地图内的子地点

    将 key_features 升级为可导航的子地点，支持：
    - 进入/离开子地点
    - 子地点内的NPC交互
    - 子地点特有的可用操作
    """
    id: str
    name: str
    description: str = ""
    parent_map_id: str = ""
    atmosphere: Optional[str] = None
    interaction_type: InteractionType = InteractionType.VISIT
    available_actions: List[str] = field(default_factory=list)
    resident_npcs: List[str] = field(default_factory=list)
    passerby_spawn_rate: float = 0.3
    travel_time_minutes: int = 0  # 同地图内暂不消耗时间

    @classmethod
    def from_dict(cls, data: Dict[str, Any], parent_map_id: str = "") -> "SubLocation":
        interaction_type = data.get("interaction_type", "visit")
        try:
            interaction_type = InteractionType(interaction_type)
        except ValueError:
            interaction_type = InteractionType.VISIT

        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            parent_map_id=parent_map_id,
            atmosphere=data.get("atmosphere"),
            interaction_type=interaction_type,
            available_actions=data.get("available_actions", []),
            resident_npcs=data.get("resident_npcs", []),
            passerby_spawn_rate=data.get("passerby_spawn_rate", 0.3),
            travel_time_minutes=data.get("travel_time_minutes", 0),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "parent_map_id": self.parent_map_id,
            "atmosphere": self.atmosphere,
            "interaction_type": self.interaction_type.value,
            "available_actions": self.available_actions,
            "resident_npcs": self.resident_npcs,
            "passerby_spawn_rate": self.passerby_spawn_rate,
            "travel_time_minutes": self.travel_time_minutes,
        }


@dataclass
class MapConnection:
    """地图连接"""
    target_map_id: str
    connection_type: ConnectionType = ConnectionType.TRAVEL
    travel_time: str = "30分钟"
    requirements: Optional[Dict[str, Any]] = None
    description: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MapConnection":
        connection_type_raw = str(data.get("connection_type", "travel")).strip().lower()
        if connection_type_raw == "walk":
            connection_type_raw = "travel"
        return cls(
            target_map_id=data["target_map_id"],
            connection_type=ConnectionType(connection_type_raw),
            travel_time=data.get("travel_time", "30分钟"),
            requirements=data.get("requirements"),
            description=data.get("description"),
        )


@dataclass
class MapArea:
    """地图区域"""
    id: str
    name: str
    description: str = ""
    atmosphere: str = ""
    danger_level: str = "low"  # low, medium, high, extreme
    region: str = ""
    connections: List[MapConnection] = field(default_factory=list)
    available_actions: List[str] = field(default_factory=list)
    key_features: List[str] = field(default_factory=list)
    resident_npcs: List[str] = field(default_factory=list)
    sub_locations: Dict[str, SubLocation] = field(default_factory=dict)  # 新增：子地点

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MapArea":
        connections = [
            MapConnection.from_dict(c) for c in data.get("connections", [])
        ]

        # 解析子地点
        sub_locations = {}
        for sl_data in data.get("sub_locations", []):
            sl = SubLocation.from_dict(sl_data, parent_map_id=data["id"])
            sub_locations[sl.id] = sl

        # 向后兼容：如果没有 sub_locations 但有 key_features，自动生成子地点
        if not sub_locations and data.get("key_features"):
            for i, feature in enumerate(data["key_features"]):
                # 生成标准化的ID
                sl_id = f"feature_{i}"
                sub_locations[sl_id] = SubLocation(
                    id=sl_id,
                    name=feature,
                    description=f"{feature}",
                    parent_map_id=data["id"],
                    interaction_type=InteractionType.VISIT,
                )

        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            atmosphere=data.get("atmosphere", ""),
            danger_level=data.get("danger_level", "low"),
            region=data.get("region", ""),
            connections=connections,
            available_actions=data.get("available_actions", []),
            key_features=data.get("key_features", []),
            resident_npcs=data.get("resident_npcs", []),
            sub_locations=sub_locations,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "atmosphere": self.atmosphere,
            "danger_level": self.danger_level,
            "region": self.region,
            "connections": [
                {
                    "target_map_id": c.target_map_id,
                    "connection_type": c.connection_type.value,
                    "travel_time": c.travel_time,
                }
                for c in self.connections
            ],
            "available_actions": self.available_actions,
            "key_features": self.key_features,
            "sub_locations": [sl.to_dict() for sl in self.sub_locations.values()],
        }


@dataclass
class TravelResult:
    """旅行结果"""
    success: bool
    path: List[str] = field(default_factory=list)
    total_time_minutes: int = 0
    segments: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "path": self.path,
            "total_time_minutes": self.total_time_minutes,
            "segments": self.segments,
            "error": self.error,
        }


class AreaNavigator:
    """
    区域导航器

    提供地图导航和路径计算功能。
    """

    # 危险等级对应的遭遇概率
    DANGER_ENCOUNTER_RATES = {
        "low": 0.05,
        "medium": 0.15,
        "high": 0.30,
        "extreme": 0.50,
    }

    def __init__(self, world_id: str, maps_data: Optional[Dict[str, Any]] = None):
        """
        初始化导航器

        Args:
            world_id: 世界ID
            maps_data: 地图数据（可选，不提供则从文件加载）
        """
        self.world_id = world_id
        self.maps: Dict[str, MapArea] = {}
        self._adjacency: Dict[str, List[str]] = {}

        if maps_data:
            self._load_maps(maps_data)
        else:
            self._load_maps_from_file()

    def _load_maps_from_file(self) -> None:
        """从文件加载地图数据"""
        project_root = Path(__file__).resolve().parents[2]
        candidate_paths = [
            Path(f"data/{self.world_id}/structured/maps.json"),
            project_root / "data" / self.world_id / "structured" / "maps.json",
        ]

        # 去重后按顺序尝试（优先 cwd 相对路径）
        seen = set()
        unique_paths = []
        for p in candidate_paths:
            key = str(p.resolve()) if p.exists() else str(p)
            if key in seen:
                continue
            seen.add(key)
            unique_paths.append(p)

        for maps_path in unique_paths:
            if not maps_path.exists():
                continue
            try:
                with open(maps_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._load_maps(data)
                break
            except Exception as exc:
                logger.warning("加载本地地图文件失败(%s)，将继续尝试其他来源: %s", maps_path, exc)

        # 本地文件不存在/加载失败/内容为空时，回退 Firestore
        if not self.maps:
            self._load_maps_from_firestore()

    def _load_maps_from_firestore(self) -> None:
        """从 Firestore 加载地图数据（fallback）。"""
        try:
            db = firestore.Client(database=settings.firestore_database)
            maps_ref = (
                db.collection("worlds")
                .document(self.world_id)
                .collection("maps")
            )

            maps_data: List[Dict[str, Any]] = []
            for map_doc in maps_ref.stream():
                info_doc = map_doc.reference.collection("info").document("data").get()
                if not info_doc.exists:
                    continue

                info = info_doc.to_dict() or {}
                if not isinstance(info, dict):
                    continue

                # Firestore 的 map_id 在文档 ID 上，运行时需要补回到数据体。
                map_payload = {"id": map_doc.id, **info}
                maps_data.append(map_payload)

            if maps_data:
                self._load_maps({"maps": maps_data})
        except Exception as exc:
            logger.warning("Firestore 地图回退加载失败: %s", exc)

    def _load_maps(self, data: Dict[str, Any]) -> None:
        """加载地图数据"""
        maps_list = data.get("maps", [])
        for map_data in maps_list:
            area = MapArea.from_dict(map_data)
            self.maps[area.id] = area

        # 构建邻接表
        self._build_adjacency()

    def _build_adjacency(self) -> None:
        """构建邻接关系"""
        self._adjacency = {map_id: [] for map_id in self.maps}

        for map_id, area in self.maps.items():
            for conn in area.connections:
                if conn.target_map_id in self.maps:
                    self._adjacency[map_id].append(conn.target_map_id)

    def get_area(self, area_id: str) -> Optional[MapArea]:
        """获取区域信息"""
        return self.maps.get(area_id)

    def get_adjacent_areas(self, area_id: str) -> List[MapConnection]:
        """获取相邻区域及其连接信息"""
        area = self.maps.get(area_id)
        if not area:
            return []
        return area.connections

    def get_available_destinations(self, area_id: str) -> List[Dict[str, Any]]:
        """
        获取可前往的目的地列表

        Args:
            area_id: 当前区域ID

        Returns:
            可用目的地列表，包含名称、描述、旅行时间等
        """
        connections = self.get_adjacent_areas(area_id)
        destinations = []

        for conn in connections:
            target_area = self.maps.get(conn.target_map_id)
            if target_area:
                destinations.append({
                    "id": conn.target_map_id,
                    "name": target_area.name,
                    "description": target_area.description[:100] if target_area.description else "",
                    "travel_time": conn.travel_time,
                    "connection_type": conn.connection_type.value,
                    "danger_level": target_area.danger_level,
                    "requirements": conn.requirements,
                })

        return destinations

    def can_travel(
        self,
        from_id: str,
        to_id: str,
        player_state: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        """
        检查是否可以旅行

        Args:
            from_id: 起点区域ID
            to_id: 终点区域ID
            player_state: 玩家状态（用于检查需求）

        Returns:
            (可以旅行, 原因/描述)
        """
        from_area = self.maps.get(from_id)
        to_area = self.maps.get(to_id)

        if not from_area:
            return False, f"未知的起点位置: {from_id}"

        if not to_area:
            return False, f"未知的目的地: {to_id}"

        # 检查直接连接
        for conn in from_area.connections:
            if conn.target_map_id == to_id:
                # 检查需求
                if conn.requirements and player_state:
                    req_check = self._check_requirements(conn.requirements, player_state)
                    if not req_check[0]:
                        return req_check
                return True, f"可以前往{to_area.name}，{conn.travel_time}"

        # 检查是否可以通过路径到达
        path = self.find_path(from_id, to_id)
        if path:
            return True, f"可以通过 {' -> '.join(path)} 到达"

        return False, f"无法从 {from_area.name} 前往 {to_area.name}"

    def _check_requirements(
        self,
        requirements: Dict[str, Any],
        player_state: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """检查旅行需求"""
        # 检查等级需求
        if "min_level" in requirements:
            player_level = player_state.get("level", 1)
            if player_level < requirements["min_level"]:
                return False, f"需要等级 {requirements['min_level']} 以上"

        # 检查物品需求
        if "required_items" in requirements:
            player_items = player_state.get("items", [])
            for item in requirements["required_items"]:
                if item not in player_items:
                    return False, f"需要物品: {item}"

        # 检查任务需求
        if "required_quests" in requirements:
            completed_quests = player_state.get("completed_quests", [])
            for quest in requirements["required_quests"]:
                if quest not in completed_quests:
                    return False, f"需要先完成任务: {quest}"

        return True, "满足所有需求"

    def find_path(self, from_id: str, to_id: str) -> List[str]:
        """
        使用A*算法寻找路径

        Args:
            from_id: 起点
            to_id: 终点

        Returns:
            路径列表（包含起点和终点），如果没有路径返回空列表
        """
        if from_id not in self.maps or to_id not in self.maps:
            return []

        if from_id == to_id:
            return [from_id]

        # A* 算法
        open_set = [(0, from_id)]  # (f_score, node_id)
        came_from: Dict[str, str] = {}
        g_score: Dict[str, float] = {from_id: 0}
        f_score: Dict[str, float] = {from_id: self._heuristic(from_id, to_id)}

        while open_set:
            _, current = heapq.heappop(open_set)

            if current == to_id:
                # 重建路径
                path = [current]
                while current in came_from:
                    current = came_from[current]
                    path.append(current)
                return list(reversed(path))

            for neighbor in self._adjacency.get(current, []):
                tentative_g = g_score[current] + self._edge_cost(current, neighbor)

                if neighbor not in g_score or tentative_g < g_score[neighbor]:
                    came_from[neighbor] = current
                    g_score[neighbor] = tentative_g
                    f_score[neighbor] = tentative_g + self._heuristic(neighbor, to_id)
                    heapq.heappush(open_set, (f_score[neighbor], neighbor))

        return []  # 没有找到路径

    def _heuristic(self, from_id: str, to_id: str) -> float:
        """启发式函数（简化版，返回固定值）"""
        # 由于没有真实坐标，使用简单的启发式
        return 1.0

    def _edge_cost(self, from_id: str, to_id: str) -> float:
        """边的代价（基于旅行时间）"""
        from_area = self.maps.get(from_id)
        if not from_area:
            return float("inf")

        for conn in from_area.connections:
            if conn.target_map_id == to_id:
                # 解析旅行时间为分钟
                return self._parse_travel_time(conn.travel_time)

        return float("inf")

    def _parse_travel_time(self, travel_time: str) -> float:
        """解析旅行时间字符串为分钟"""
        time_str = travel_time.lower()

        if "分钟" in time_str or "minutes" in time_str:
            try:
                return float("".join(filter(str.isdigit, time_str))) or 30
            except ValueError:
                return 30
        elif "小时" in time_str or "hour" in time_str:
            try:
                hours = float("".join(filter(str.isdigit, time_str))) or 1
                return hours * 60
            except ValueError:
                return 60
        elif "半天" in time_str or "half day" in time_str:
            return 360  # 6小时
        elif "一天" in time_str or "day" in time_str:
            return 720  # 12小时
        else:
            return 30  # 默认30分钟

    def calculate_travel(
        self,
        from_id: str,
        to_id: str,
        player_state: Optional[Dict[str, Any]] = None,
    ) -> TravelResult:
        """
        计算完整的旅行信息

        Args:
            from_id: 起点
            to_id: 终点
            player_state: 玩家状态

        Returns:
            TravelResult 包含路径、时间、分段信息
        """
        # 检查是否可以旅行
        can_travel, reason = self.can_travel(from_id, to_id, player_state)
        if not can_travel:
            return TravelResult(success=False, error=reason)

        # 找到路径
        path = self.find_path(from_id, to_id)
        if not path:
            return TravelResult(success=False, error="找不到可行路径")

        # 计算每段行程
        segments = []
        total_time = 0

        for i in range(len(path) - 1):
            from_area = self.maps[path[i]]
            to_area = self.maps[path[i + 1]]

            # 找到连接信息
            travel_time_str = "30分钟"
            for conn in from_area.connections:
                if conn.target_map_id == path[i + 1]:
                    travel_time_str = conn.travel_time
                    break

            time_minutes = int(self._parse_travel_time(travel_time_str))
            total_time += time_minutes

            segments.append({
                "from_id": path[i],
                "from_name": from_area.name,
                "to_id": path[i + 1],
                "to_name": to_area.name,
                "travel_time": travel_time_str,
                "time_minutes": time_minutes,
                "danger_level": to_area.danger_level,
                "encounter_rate": self.DANGER_ENCOUNTER_RATES.get(to_area.danger_level, 0.1),
            })

        return TravelResult(
            success=True,
            path=path,
            total_time_minutes=total_time,
            segments=segments,
        )

    def get_all_areas(self) -> List[Dict[str, Any]]:
        """获取所有区域信息"""
        return [
            {
                "id": area.id,
                "name": area.name,
                "region": area.region,
                "danger_level": area.danger_level,
            }
            for area in self.maps.values()
        ]

    def resolve_location_name(self, name: str) -> Optional[str]:
        """
        解析位置名称到ID（保持向后兼容）

        Args:
            name: 位置名称或ID

        Returns:
            区域ID，如果找不到返回None
        """
        # 直接匹配ID
        if name in self.maps:
            return name

        # 匹配名称
        name_lower = name.lower()
        for area_id, area in self.maps.items():
            if area.name.lower() == name_lower:
                return area_id
            # 部分匹配
            if name_lower in area.name.lower() or area.name.lower() in name_lower:
                return area_id

        return None

    def resolve_location(self, name: str) -> Tuple[Optional[str], Optional[str]]:
        """
        解析位置名称，返回 (map_id, sub_location_id)

        Args:
            name: 位置名称或ID

        Returns:
            (map_id, None) - 匹配到地图
            (map_id, sub_loc_id) - 匹配到子地点
            (None, None) - 未匹配
        """
        # 1. 先尝试匹配地图名
        map_id = self.resolve_location_name(name)
        if map_id:
            return (map_id, None)

        # 2. 在所有地图的子地点中搜索
        name_lower = name.lower()
        for mid, area in self.maps.items():
            for sl_id, sl in area.sub_locations.items():
                # 精确匹配ID
                if name_lower == sl_id.lower():
                    return (mid, sl_id)
                # 精确匹配名称
                if name_lower == sl.name.lower():
                    return (mid, sl_id)
                # 部分匹配名称
                if name_lower in sl.name.lower() or sl.name.lower() in name_lower:
                    return (mid, sl_id)

        return (None, None)

    def get_sub_locations(self, map_id: str) -> List[SubLocation]:
        """
        获取地图内所有子地点

        Args:
            map_id: 地图ID

        Returns:
            子地点列表
        """
        area = self.maps.get(map_id)
        if not area:
            return []
        return list(area.sub_locations.values())

    def get_sub_location(self, map_id: str, sub_loc_id: str) -> Optional[SubLocation]:
        """
        获取特定子地点

        Args:
            map_id: 地图ID
            sub_loc_id: 子地点ID

        Returns:
            子地点对象，不存在返回None
        """
        area = self.maps.get(map_id)
        if not area:
            return None
        return area.sub_locations.get(sub_loc_id)

    def get_sub_location_info(self, map_id: str, sub_loc_id: str) -> Optional[Dict[str, Any]]:
        """
        获取子地点详细信息

        Args:
            map_id: 地图ID
            sub_loc_id: 子地点ID

        Returns:
            子地点信息字典
        """
        sub_loc = self.get_sub_location(map_id, sub_loc_id)
        if not sub_loc:
            return None

        return {
            "id": sub_loc.id,
            "name": sub_loc.name,
            "description": sub_loc.description,
            "atmosphere": sub_loc.atmosphere,
            "interaction_type": sub_loc.interaction_type.value,
            "available_actions": sub_loc.available_actions,
            "resident_npcs": sub_loc.resident_npcs,
            "passerby_spawn_rate": sub_loc.passerby_spawn_rate,
        }

    def get_available_sub_locations(self, map_id: str) -> List[Dict[str, Any]]:
        """
        获取可前往的子地点列表

        Args:
            map_id: 当前地图ID

        Returns:
            子地点信息列表
        """
        area = self.maps.get(map_id)
        if not area:
            return []

        return [
            {
                "id": sl.id,
                "name": sl.name,
                "type": sl.interaction_type.value,
                "description": sl.description[:100] if sl.description else "",
            }
            for sl in area.sub_locations.values()
        ]
