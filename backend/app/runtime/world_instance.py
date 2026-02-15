"""WorldInstance — 世界级静态数据注册表（Phase 1 实现）。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from google.cloud import firestore

from app.config import settings
from app.runtime.models.world_constants import WorldConstants
from app.runtime.models.area_state import AreaDefinition, SubLocationDef, AreaConnection

logger = logging.getLogger(__name__)


class WorldInstance:
    """世界静态数据的一次性加载容器。

    所有数据在 initialize() 时并行从 Firestore 批量加载，
    之后为只读访问。
    """

    def __init__(self, world_id: str) -> None:
        self.world_id = world_id
        self.world_constants: Optional[WorldConstants] = None
        self.character_registry: Dict[str, Dict[str, Any]] = {}
        self.area_registry: Dict[str, AreaDefinition] = {}
        self.monster_registry: Dict[str, Dict[str, Any]] = {}
        self.item_registry: Dict[str, Dict[str, Any]] = {}
        self.skill_registry: Dict[str, Dict[str, Any]] = {}
        self.chapter_registry: Dict[str, Any] = {}
        self.mainline_registry: Dict[str, Any] = {}
        self._initialized: bool = False

    async def initialize(self) -> None:
        """并行 Firestore 批量加载所有注册表。"""
        if self._initialized:
            logger.debug("WorldInstance '%s' 已初始化，跳过", self.world_id)
            return

        t0 = time.monotonic()
        db = firestore.Client(database=settings.firestore_database)
        world_ref = db.collection("worlds").document(self.world_id)

        # 并行加载所有注册表
        results = await asyncio.gather(
            self._load_world_constants(world_ref),
            self._load_characters(world_ref),
            self._load_areas(world_ref),
            self._load_chapters(world_ref),
            self._load_mainlines(world_ref),
            self._load_combat_entities(world_ref, "monsters"),
            self._load_combat_entities(world_ref, "skills"),
            self._load_combat_entities(world_ref, "items"),
            return_exceptions=True,
        )

        # 处理结果
        labels = [
            "world_constants", "characters", "areas",
            "chapters", "mainlines", "monsters", "skills", "items",
        ]
        for label, result in zip(labels, results):
            if isinstance(result, Exception):
                logger.warning(
                    "WorldInstance '%s' 加载 %s 失败: %s",
                    self.world_id, label, result,
                )

        self._initialized = True
        elapsed = time.monotonic() - t0
        logger.info(
            "WorldInstance '%s' 初始化完成 (%.2fs): "
            "%d characters, %d areas, %d chapters, %d mainlines, "
            "%d monsters, %d items, %d skills",
            self.world_id, elapsed,
            len(self.character_registry),
            len(self.area_registry),
            len(self.chapter_registry),
            len(self.mainline_registry),
            len(self.monster_registry),
            len(self.item_registry),
            len(self.skill_registry),
        )

    # ---- Firestore 加载方法 ----

    async def _load_world_constants(
        self, world_ref: firestore.DocumentReference
    ) -> None:
        """加载 worlds/{wid}/meta/info 单文档。"""
        doc = world_ref.collection("meta").document("info").get()
        if not doc.exists:
            logger.warning(
                "WorldInstance '%s': meta/info 文档不存在", self.world_id
            )
            return
        data = doc.to_dict() or {}
        data.setdefault("world_id", self.world_id)
        self.world_constants = WorldConstants(**data)

    async def _load_characters(
        self, world_ref: firestore.DocumentReference
    ) -> None:
        """加载 worlds/{wid}/characters/ 集合遍历。"""
        chars_ref = world_ref.collection("characters")
        for doc in chars_ref.stream():
            data = doc.to_dict()
            if not data:
                continue
            data["id"] = doc.id
            self.character_registry[doc.id] = data

    async def _load_areas(
        self, world_ref: firestore.DocumentReference
    ) -> None:
        """加载 worlds/{wid}/maps/ 集合（含 info/data 子文档）。"""
        maps_ref = world_ref.collection("maps")
        for map_doc in maps_ref.stream():
            info_doc = (
                map_doc.reference.collection("info").document("data").get()
            )
            if not info_doc.exists:
                continue
            info = info_doc.to_dict() or {}
            area_id = map_doc.id
            self.area_registry[area_id] = self._parse_area_definition(
                area_id, info
            )

    async def _load_chapters(
        self, world_ref: firestore.DocumentReference
    ) -> None:
        """加载 worlds/{wid}/chapters/ 集合遍历。"""
        chapters_ref = world_ref.collection("chapters")
        for doc in chapters_ref.stream():
            data = doc.to_dict()
            if not data:
                continue
            data["id"] = doc.id
            self.chapter_registry[doc.id] = data

    async def _load_mainlines(
        self, world_ref: firestore.DocumentReference
    ) -> None:
        """加载 worlds/{wid}/mainlines/ 集合遍历。"""
        mainlines_ref = world_ref.collection("mainlines")
        for doc in mainlines_ref.stream():
            data = doc.to_dict()
            if not data:
                continue
            data["id"] = doc.id
            self.mainline_registry[doc.id] = data

    async def _load_combat_entities(
        self, world_ref: firestore.DocumentReference, entity_type: str
    ) -> None:
        """加载 worlds/{wid}/combat_entities/{type} 文档中的 entries 数组。"""
        doc = (
            world_ref.collection("combat_entities")
            .document(entity_type)
            .get()
        )
        if not doc.exists:
            return
        data = doc.to_dict() or {}
        entries = data.get("entries", [])
        if not isinstance(entries, list):
            return

        registry = {
            "monsters": self.monster_registry,
            "skills": self.skill_registry,
            "items": self.item_registry,
        }.get(entity_type)
        if registry is None:
            return

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_id = entry.get("id") or entry.get("name", "")
            if entry_id:
                registry[str(entry_id)] = entry

    # ---- 数据解析辅助 ----

    @staticmethod
    def _parse_area_definition(area_id: str, info: Dict[str, Any]) -> AreaDefinition:
        """将 Firestore 地图 info 数据转为 AreaDefinition。"""
        sub_locations = []
        for sl_data in info.get("sub_locations", []):
            if not isinstance(sl_data, dict):
                continue
            sub_locations.append(SubLocationDef(
                id=sl_data.get("id", ""),
                name=sl_data.get("name", ""),
                description=sl_data.get("description", ""),
                interaction_type=sl_data.get("interaction_type", "visit"),
                resident_npcs=sl_data.get("resident_npcs", []),
                requirements=sl_data.get("requirements", {}),
                available_actions=sl_data.get("available_actions", []),
                passerby_spawn_rate=sl_data.get("passerby_spawn_rate", 0.0),
                metadata={
                    k: v for k, v in sl_data.items()
                    if k not in (
                        "id", "name", "description", "interaction_type",
                        "resident_npcs", "requirements",
                        "available_actions", "passerby_spawn_rate",
                    )
                },
            ))

        connections = []
        for conn_data in info.get("connections", []):
            if not isinstance(conn_data, dict):
                continue
            connections.append(AreaConnection(
                target_area_id=conn_data.get("target_map_id", ""),
                connection_type=conn_data.get("connection_type", "travel"),
                travel_time=conn_data.get("travel_time", "30 minutes"),
                requirements=conn_data.get("requirements", {}),
                description=conn_data.get("description", ""),
            ))

        # danger_level: Firestore 存储为字符串 (low/medium/high/extreme)
        # AreaDefinition 期望 int，做简单映射
        danger_raw = info.get("danger_level", "low")
        danger_map = {"low": 1, "medium": 2, "high": 3, "extreme": 4}
        if isinstance(danger_raw, str):
            danger_level = danger_map.get(danger_raw.lower(), 1)
        else:
            try:
                danger_level = int(danger_raw)
            except (TypeError, ValueError):
                danger_level = 1

        return AreaDefinition(
            area_id=area_id,
            name=info.get("name", ""),
            description=info.get("description", ""),
            danger_level=danger_level,
            area_type=info.get("area_type", "settlement"),
            tags=info.get("tags", []),
            key_features=info.get("key_features", []),
            available_actions=info.get("available_actions", []),
            sub_locations=sub_locations,
            connections=connections,
            resident_npcs=info.get("resident_npcs", []),
            ambient_description=info.get("atmosphere", ""),
            region=info.get("region", ""),
            metadata={
                k: v for k, v in info.items()
                if k not in (
                    "id", "name", "description", "danger_level", "area_type",
                    "tags", "sub_locations", "connections", "resident_npcs",
                    "atmosphere", "key_features", "available_actions", "region",
                )
            },
        )

    # ---- 查询方法 ----

    def get_characters_in_area(self, area_id: str) -> List[Dict[str, Any]]:
        """按角色的 default_map / current_map 过滤区域内角色。

        角色数据中 profile.metadata.default_map 或 state.current_map
        匹配 area_id 时视为在该区域内。
        """
        result = []
        for char_id, char_data in self.character_registry.items():
            # 检查 profile.metadata.default_map
            profile = char_data.get("profile", {})
            metadata = profile.get("metadata", {}) if isinstance(profile, dict) else {}
            default_map = metadata.get("default_map", "") if isinstance(metadata, dict) else ""
            # 检查 state.current_map
            state = char_data.get("state", {})
            current_map = state.get("current_map", "") if isinstance(state, dict) else ""

            if default_map == area_id or current_map == area_id:
                result.append(char_data)
        return result

    def get_characters_at_sublocation(
        self, area_id: str, sub_id: str
    ) -> List[Dict[str, Any]]:
        """按子地点定义的 resident_npcs 过滤角色。"""
        area_def = self.area_registry.get(area_id)
        if not area_def:
            return []

        sub_loc = area_def.get_sub_location(sub_id)
        if not sub_loc or not sub_loc.resident_npcs:
            return []

        resident_set = set(sub_loc.resident_npcs)
        return [
            char_data
            for char_id, char_data in self.character_registry.items()
            if char_id in resident_set
        ]

    def get_monsters_for_danger(self, danger_level: int) -> List[Dict[str, Any]]:
        """按 challenge_rating 过滤怪物。

        challenge_rating 可能是字符串（如 "白瓷"、"黄金"）或数值，
        采用简单映射进行匹配。
        """
        # 危险等级 → challenge_rating 映射
        danger_to_ratings = {
            1: {"白瓷", "porcelain", "low", "1"},
            2: {"黑曜石", "obsidian", "钢铁", "steel", "medium", "2"},
            3: {"青铜", "bronze", "白银", "silver", "high", "3"},
            4: {"黄金", "gold", "白金", "platinum", "extreme", "4"},
        }
        allowed_ratings = set()
        for level in range(1, danger_level + 1):
            allowed_ratings.update(danger_to_ratings.get(level, set()))

        result = []
        for monster_data in self.monster_registry.values():
            cr = str(monster_data.get("challenge_rating", "")).strip().lower()
            if cr in allowed_ratings:
                result.append(monster_data)
        return result

    def get_skills_for_classes(self, classes: List[str]) -> List[Dict[str, Any]]:
        """按 source 过滤技能。"""
        if not classes:
            return list(self.skill_registry.values())

        classes_lower = {c.lower() for c in classes}
        result = []
        for skill_data in self.skill_registry.values():
            source = str(skill_data.get("source", "")).strip().lower()
            # source 字段可能是 "warrior"、"priest" 等职业标识
            if source in classes_lower:
                result.append(skill_data)
            # 也检查 class/classes 字段（兼容不同数据格式）
            elif str(skill_data.get("class", "")).strip().lower() in classes_lower:
                result.append(skill_data)
            elif any(
                c.lower() in classes_lower
                for c in skill_data.get("classes", [])
                if isinstance(c, str)
            ):
                result.append(skill_data)
        return result

    def get_area_definition(self, area_id: str) -> Optional[AreaDefinition]:
        """获取区域定义。"""
        return self.area_registry.get(area_id)

    def get_character(self, character_id: str) -> Optional[Dict[str, Any]]:
        """获取角色数据。"""
        return self.character_registry.get(character_id)
