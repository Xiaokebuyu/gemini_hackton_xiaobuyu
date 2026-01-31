"""
角色数据加载器

将角色资料加载到 Firestore。
"""
import json
from pathlib import Path
from typing import Dict, Any, List, Optional

from google.cloud import firestore

from app.config import settings
from app.services.graph_store import GraphStore
from app.tools.worldbook_graphizer.models import CharactersData, CharacterInfo, NPCTier
from app.models.passerby import PasserbySpawnConfig, PasserbyTemplate


class CharacterLoader:
    """角色数据加载器"""

    def __init__(self, firestore_client: Optional[firestore.Client] = None):
        """
        初始化加载器

        Args:
            firestore_client: Firestore 客户端（可选）
        """
        self.db = firestore_client or firestore.Client(
            database=settings.firestore_database
        )
        self.graph_store = GraphStore(firestore_client=self.db)

    def _get_map_npcs_ref(
        self,
        world_id: str,
        map_id: str,
    ) -> firestore.DocumentReference:
        """获取地图 NPC 配置文档引用"""
        return (
            self.db.collection("worlds")
            .document(world_id)
            .collection("maps")
            .document(map_id)
            .collection("npcs")
            .document("assignments")
        )

    async def load_characters(
        self,
        world_id: str,
        characters_data: CharactersData,
        dry_run: bool = False,
        verbose: bool = True,
    ) -> Dict[str, Any]:
        """
        加载所有角色数据到 Firestore

        Args:
            world_id: 世界 ID
            characters_data: 角色数据
            dry_run: 是否只模拟执行
            verbose: 是否输出详细信息

        Returns:
            加载结果统计
        """
        stats = {
            "main_loaded": 0,
            "secondary_loaded": 0,
            "passerby_templates_collected": 0,
            "passerby_pools_initialized": 0,
            "map_assignments_loaded": 0,
            "errors": [],
        }

        # 收集 PASSERBY 模板，按地点分组
        passerby_templates_by_map: Dict[str, List[PasserbyTemplate]] = {}

        # 加载主要和次要角色的 Profile，收集 PASSERBY 模板
        for char in characters_data.characters:
            try:
                if char.tier == NPCTier.PASSERBY:
                    # 收集为模板，不跳过
                    template = PasserbyTemplate(
                        id=char.id,
                        name_pool=[char.name] if char.name else [],
                        appearance_pool=[char.appearance] if char.appearance else [],
                        personality_pool=[char.personality] if char.personality else [],
                        occupation=char.occupation,
                        default_map=char.default_map,
                    )
                    map_key = char.default_map or "default"
                    if map_key not in passerby_templates_by_map:
                        passerby_templates_by_map[map_key] = []
                    passerby_templates_by_map[map_key].append(template)
                    stats["passerby_templates_collected"] += 1

                    if verbose:
                        print(f"  Collected passerby template: {char.id} for map {map_key}")
                    continue

                if verbose:
                    print(f"  Loading character: {char.id} ({char.name}) - {char.tier.value}")

                if not dry_run:
                    await self._load_character_profile(world_id, char)

                if char.tier == NPCTier.MAIN:
                    stats["main_loaded"] += 1
                else:
                    stats["secondary_loaded"] += 1

            except Exception as e:
                error_msg = f"Failed to load character {char.id}: {str(e)}"
                stats["errors"].append(error_msg)
                if verbose:
                    print(f"    ERROR: {error_msg}")

        # 加载地图 NPC 分配
        for map_id, assignments in characters_data.map_assignments.items():
            try:
                if verbose:
                    main_count = len(assignments.get("main", []))
                    secondary_count = len(assignments.get("secondary", []))
                    print(f"  Loading map assignments: {map_id}")
                    print(f"    - Main: {main_count}, Secondary: {secondary_count}")

                if not dry_run:
                    await self._load_map_assignments(world_id, map_id, assignments)

                stats["map_assignments_loaded"] += 1

            except Exception as e:
                error_msg = f"Failed to load map assignments for {map_id}: {str(e)}"
                stats["errors"].append(error_msg)
                if verbose:
                    print(f"    ERROR: {error_msg}")

        # Module 4: 初始化路人池
        for map_id, templates in passerby_templates_by_map.items():
            try:
                template_ids = [t.id for t in templates]
                if verbose:
                    print(f"  Initializing passerby pool for map: {map_id}")
                    print(f"    - Templates: {template_ids}")

                if not dry_run:
                    await self._init_passerby_pool(world_id, map_id, templates)

                stats["passerby_pools_initialized"] += 1

            except Exception as e:
                error_msg = f"Failed to init passerby pool for {map_id}: {str(e)}"
                stats["errors"].append(error_msg)
                if verbose:
                    print(f"    ERROR: {error_msg}")

        return stats

    async def _load_character_profile(
        self,
        world_id: str,
        char: CharacterInfo,
    ) -> None:
        """加载单个角色的 Profile"""
        # 构建 CharacterProfile 格式
        profile = {
            "name": char.name,
            "occupation": char.occupation,
            "age": char.age,
            "personality": char.personality,
            "speech_pattern": char.speech_pattern,
            "example_dialogue": char.example_dialogue,
            "metadata": {
                "appearance": char.appearance,
                "backstory": char.backstory,
                "aliases": char.aliases,
                "importance": char.importance,
                "tags": char.tags,
                "default_map": char.default_map,
                "tier": char.tier.value,
                "relationships": char.relationships,
            }
        }

        # 使用 GraphStore 的接口保存
        await self.graph_store.set_character_profile(
            world_id=world_id,
            character_id=char.id,
            profile=profile,
            merge=True,
        )

        # 初始化角色状态
        initial_state = {
            "current_map": char.default_map,
            "status": "active",
        }
        await self.graph_store.update_character_state(
            world_id=world_id,
            character_id=char.id,
            updates=initial_state,
        )

    async def _load_map_assignments(
        self,
        world_id: str,
        map_id: str,
        assignments: Dict[str, List[str]],
    ) -> None:
        """加载地图的 NPC 分配"""
        npcs_ref = self._get_map_npcs_ref(world_id, map_id)

        assignments_data = {
            "main": assignments.get("main", []),
            "secondary": assignments.get("secondary", []),
            "passerby_templates": assignments.get("passerby_templates", []),
        }

        npcs_ref.set(assignments_data, merge=True)

    async def _init_passerby_pool(
        self,
        world_id: str,
        map_id: str,
        templates: List[PasserbyTemplate],
    ) -> None:
        """初始化地图的路人池"""
        # 构建路人池配置
        config = PasserbySpawnConfig(
            max_concurrent=5,
            spawn_interval_minutes=30,
            despawn_after_minutes=60,
            templates=[t.id for t in templates],
        )

        # 存储模板数据
        templates_data = {}
        for t in templates:
            templates_data[t.id] = {
                "id": t.id,
                "name_pool": t.name_pool,
                "appearance_pool": t.appearance_pool,
                "personality_pool": t.personality_pool,
                "occupation": t.occupation,
            }

        # 保存到 Firestore
        pool_ref = (
            self.db.collection("worlds")
            .document(world_id)
            .collection("maps")
            .document(map_id)
            .collection("passerby_pool")
            .document("config")
        )

        pool_ref.set({
            "config": config.model_dump(),
            "templates": templates_data,
            "active_instances": {},
            "sentiment": 0.0,
        }, merge=True)

    async def load_profiles_from_file(
        self,
        world_id: str,
        profiles_path: Path,
        dry_run: bool = False,
        verbose: bool = True,
    ) -> Dict[str, Any]:
        """
        从 character_profiles.json 文件加载角色

        Args:
            world_id: 世界 ID
            profiles_path: character_profiles.json 文件路径
            dry_run: 是否只模拟执行
            verbose: 是否输出详细信息

        Returns:
            加载结果统计
        """
        if not profiles_path.exists():
            raise FileNotFoundError(f"Profiles file not found: {profiles_path}")

        profiles = json.loads(profiles_path.read_text(encoding="utf-8"))

        stats = {
            "profiles_loaded": 0,
            "errors": [],
        }

        for char_id, profile in profiles.items():
            try:
                if verbose:
                    print(f"  Loading profile: {char_id} ({profile.get('name', 'Unknown')})")

                if not dry_run:
                    await self.graph_store.set_character_profile(
                        world_id=world_id,
                        character_id=char_id,
                        profile=profile,
                        merge=True,
                    )

                    # 初始化状态
                    metadata = profile.get("metadata", {})
                    initial_state = {
                        "current_map": metadata.get("default_map"),
                        "status": "active",
                    }
                    await self.graph_store.update_character_state(
                        world_id=world_id,
                        character_id=char_id,
                        updates=initial_state,
                    )

                stats["profiles_loaded"] += 1

            except Exception as e:
                error_msg = f"Failed to load profile {char_id}: {str(e)}"
                stats["errors"].append(error_msg)
                if verbose:
                    print(f"    ERROR: {error_msg}")

        return stats

    async def load_single_character(
        self,
        world_id: str,
        char_id: str,
        characters_data: CharactersData,
        dry_run: bool = False,
        verbose: bool = True,
    ) -> bool:
        """
        加载单个角色

        Args:
            world_id: 世界 ID
            char_id: 角色 ID
            characters_data: 角色数据
            dry_run: 是否只模拟执行
            verbose: 是否输出详细信息

        Returns:
            是否成功
        """
        # 查找角色
        char = None
        for c in characters_data.characters:
            if c.id == char_id:
                char = c
                break

        if not char:
            if verbose:
                print(f"  Character not found: {char_id}")
            return False

        if char.tier == NPCTier.PASSERBY:
            if verbose:
                print(f"  Skipping passerby character: {char_id}")
            return False

        if verbose:
            print(f"  Loading character: {char.id} ({char.name})")

        if not dry_run:
            await self._load_character_profile(world_id, char)

        return True

    async def get_character_profile(
        self,
        world_id: str,
        character_id: str,
    ) -> Optional[Dict[str, Any]]:
        """获取角色 Profile"""
        return await self.graph_store.get_character_profile(world_id, character_id)

    async def list_characters(self, world_id: str) -> List[str]:
        """列出所有角色 ID"""
        chars_ref = (
            self.db.collection("worlds")
            .document(world_id)
            .collection("characters")
        )
        return [doc.id for doc in chars_ref.stream()]
