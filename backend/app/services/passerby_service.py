"""
Passerby Service - 路人NPC管理服务

路人NPC特点：
- PASSERBY级别，按地点聚合存储
- 共享地点级短记忆（passerby_pool 内）
- 运行时生成，非持久化角色资料
- 强制使用FAST层AI响应

Firestore结构：
    worlds/{world_id}/maps/{map_id}/passerby_pool/config
"""
import random
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from google.cloud import firestore

from app.config import settings
from app.models.passerby import (
    LocationPasserbyPool,
    PasserbyInstance,
    PasserbySpawnConfig,
    PasserbyTemplate,
    SharedMemoryContribution,
)
from app.services.tiered_ai_service import AITier, TieredAIService
from app.tools.worldbook_graphizer.models import NPCTier


class PasserbyService:
    """
    路人NPC管理服务

    职责：
    - 路人生成与管理
    - 路人对话处理（强制FAST层）
    - 共享记忆贡献
    - 路人池状态持久化
    """

    # 默认名称池
    DEFAULT_NAMES = [
        "旅人", "商人", "农夫", "流浪者", "朝圣者",
        "新手冒险者", "老练的旅行者", "疲惫的行商",
        "好奇的村民", "热心的居民",
    ]

    # 默认外貌描述
    DEFAULT_APPEARANCES = [
        "穿着普通的旅行服装",
        "身着朴素的衣物",
        "看起来风尘仆仆",
        "衣着整洁但简单",
        "带着一个大背包",
    ]

    # 默认性格片段
    DEFAULT_PERSONALITIES = [
        "友好但有些拘谨",
        "热情健谈",
        "沉默寡言，但眼神友善",
        "谨慎而礼貌",
        "随和开朗",
    ]

    def __init__(
        self,
        tiered_ai: Optional[TieredAIService] = None,
        firestore_client: Optional[firestore.Client] = None,
    ):
        """
        初始化路人服务

        Args:
            tiered_ai: 三层AI服务
            firestore_client: Firestore客户端
        """
        self._tiered_ai = tiered_ai or TieredAIService()
        self._db = firestore_client or firestore.Client(database=settings.firestore_database)

        # 内存缓存
        self._pools: Dict[str, LocationPasserbyPool] = {}
        self._templates: Dict[str, Dict[str, PasserbyTemplate]] = {}  # key: world_id:map_id, value: {template_id: template}

    async def get_or_spawn_passerby(
        self,
        world_id: str,
        map_id: str,
        sub_location_id: Optional[str] = None,
        spawn_hint: Optional[str] = None,
    ) -> PasserbyInstance:
        """
        获取或生成路人

        Args:
            world_id: 世界ID
            map_id: 地图ID
            sub_location_id: 子地点ID（可选）
            spawn_hint: 生成提示（可选）

        Returns:
            路人实例
        """
        pool = await self._get_pool(world_id, map_id)

        # 检查是否有可复用的实例（在同一位置）
        location_key = sub_location_id or map_id
        for inst in pool.active_instances.values():
            inst_location = inst.sub_location_id or inst.map_id
            if inst_location == location_key:
                return inst

        # 检查是否达到上限
        if len(pool.active_instances) >= pool.config.max_concurrent:
            # 淘汰最旧的（按生成时间）
            oldest = min(
                pool.active_instances.values(),
                key=lambda x: x.spawn_time,
            )
            await self.despawn_passerby(world_id, map_id, oldest.instance_id)

        # 生成新路人
        return await self._spawn_passerby(world_id, map_id, sub_location_id, pool)

    async def despawn_passerby(
        self,
        world_id: str,
        map_id: str,
        instance_id: str,
        persist_memory: bool = True,
    ) -> None:
        """
        移除路人，可选合并记忆

        Args:
            world_id: 世界ID
            map_id: 地图ID
            instance_id: 实例ID
            persist_memory: 是否保留交互记忆
        """
        pool = await self._get_pool(world_id, map_id)
        instance = pool.active_instances.pop(instance_id, None)

        if instance and persist_memory and instance.interaction_count > 0:
            # 将交互贡献到共享记忆
            await self._contribute_to_shared_memory(
                world_id,
                map_id,
                f"{instance.name}与冒险者有过{instance.interaction_count}次对话",
                instance.sub_location_id,
            )

        # 持久化池状态
        await self._save_pool(world_id, map_id, pool)

    async def handle_passerby_dialogue(
        self,
        world_id: str,
        map_id: str,
        instance_id: str,
        player_message: str,
    ) -> Dict[str, Any]:
        """
        处理路人对话（强制使用FAST层）

        Args:
            world_id: 世界ID
            map_id: 地图ID
            instance_id: 实例ID
            player_message: 玩家消息

        Returns:
            对话响应
        """
        pool = await self._get_pool(world_id, map_id)
        instance = pool.active_instances.get(instance_id)

        if not instance:
            return {"error": "路人不存在", "success": False}

        # 获取共享记忆作为上下文（支持子地点级别）
        shared_memory = await self._get_shared_memory(world_id, map_id, instance.sub_location_id)

        # 使用FAST层响应
        npc_profile = {
            "name": instance.name,
            "personality": instance.personality_snippet,
            "appearance": instance.appearance,
            "speech_pattern": "简短、随意",
            "occupation": "路人",
            "shared_context": shared_memory,
        }

        response = await self._tiered_ai.respond(
            world_id=world_id,
            npc_id=instance_id,
            npc_tier=NPCTier.PASSERBY,
            query=player_message,
            location_id=map_id,
            npc_profile=npc_profile,
            force_tier=AITier.FAST,
            sub_location_id=instance.sub_location_id,  # 传递路人所在子地点
        )

        # 更新交互计数
        instance.interaction_count += 1
        instance.last_interaction = datetime.now()
        await self._save_pool(world_id, map_id, pool)

        return {
            "success": True,
            "response": response.content,
            "speaker": instance.name,
            "tier_used": response.tier_used.value,
            "latency_ms": response.latency_ms,
            "cache_hit": response.cache_hit,
        }

    async def get_active_passersby(
        self,
        world_id: str,
        map_id: str,
        sub_location_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取当前位置的活跃路人列表

        Args:
            world_id: 世界ID
            map_id: 地图ID
            sub_location_id: 子地点ID（可选）

        Returns:
            路人信息列表
        """
        pool = await self._get_pool(world_id, map_id)

        location_key = sub_location_id or map_id
        result = []

        for inst in pool.active_instances.values():
            inst_location = inst.sub_location_id or inst.map_id
            if inst_location == location_key:
                result.append({
                    "instance_id": inst.instance_id,
                    "name": inst.name,
                    "appearance": inst.appearance,
                    "mood": inst.mood,
                    "interaction_count": inst.interaction_count,
                })

        return result

    # ==================== 内部方法 ====================

    async def _get_pool(self, world_id: str, map_id: str) -> LocationPasserbyPool:
        """获取或创建地点池"""
        key = f"{world_id}:{map_id}"
        if key not in self._pools:
            self._pools[key] = await self._load_pool(world_id, map_id)
        return self._pools[key]

    async def _load_pool(self, world_id: str, map_id: str) -> LocationPasserbyPool:
        """从Firestore加载路人池"""
        doc_ref = (
            self._db.collection("worlds")
            .document(world_id)
            .collection("maps")
            .document(map_id)
            .collection("passerby_pool")
            .document("config")
        )

        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict()
            config_data = data.get("config", {})
            instances_data = data.get("active_instances", {})
            templates_data = data.get("templates", {})
            shared_memories_data = data.get("shared_memories", [])

            # 加载模板到缓存
            key = f"{world_id}:{map_id}"
            self._templates[key] = {}
            for t_id, t_data in templates_data.items():
                try:
                    self._templates[key][t_id] = PasserbyTemplate(**t_data)
                except Exception:
                    pass  # 忽略无效模板

            shared_memories = []
            for memory_data in shared_memories_data:
                try:
                    shared_memories.append(SharedMemoryContribution(**memory_data))
                except Exception:
                    continue

            return LocationPasserbyPool(
                map_id=map_id,
                config=PasserbySpawnConfig(**config_data) if config_data else PasserbySpawnConfig(),
                active_instances={
                    k: PasserbyInstance(**v)
                    for k, v in instances_data.items()
                },
                sentiment=data.get("sentiment", 0.0),
                shared_memories=shared_memories,
            )

        # 默认池
        return LocationPasserbyPool(
            map_id=map_id,
            config=PasserbySpawnConfig(),
        )

    async def _save_pool(self, world_id: str, map_id: str, pool: LocationPasserbyPool) -> None:
        """保存路人池到Firestore"""
        doc_ref = (
            self._db.collection("worlds")
            .document(world_id)
            .collection("maps")
            .document(map_id)
            .collection("passerby_pool")
            .document("config")
        )

        doc_ref.set({
            "config": pool.config.model_dump(),
            "active_instances": {
                k: v.model_dump() for k, v in pool.active_instances.items()
            },
            "sentiment": pool.sentiment,
            "shared_memories": [m.model_dump() for m in pool.shared_memories[-100:]],
            "updated_at": datetime.now(),
        }, merge=True)

    async def _spawn_passerby(
        self,
        world_id: str,
        map_id: str,
        sub_location_id: Optional[str],
        pool: LocationPasserbyPool,
    ) -> PasserbyInstance:
        """生成新路人"""
        # 从模板随机选择（如果有）
        template_id = "generic"
        if pool.config.templates:
            template_id = random.choice(pool.config.templates)

        # 尝试使用已加载的模板
        key = f"{world_id}:{map_id}"
        template = self._templates.get(key, {}).get(template_id)

        if template:
            # 使用模板的方法生成属性
            name = template.get_name()
            appearance = template.get_appearance()
            personality = template.get_personality()
        else:
            # 使用默认生成方法
            name = self._generate_name(template_id)
            appearance = self._generate_appearance(template_id)
            personality = self._generate_personality(template_id)

        instance = PasserbyInstance(
            instance_id=str(uuid.uuid4()),
            template_id=template_id,
            map_id=map_id,
            sub_location_id=sub_location_id,
            name=name,
            appearance=appearance,
            personality_snippet=personality,
            spawn_time=datetime.now(),
        )

        pool.active_instances[instance.instance_id] = instance
        await self._save_pool(world_id, map_id, pool)

        return instance

    async def _contribute_to_shared_memory(
        self,
        world_id: str,
        map_id: str,
        content: str,
        sub_location_id: Optional[str] = None,
    ) -> None:
        """贡献到路人池共享记忆。"""
        pool = await self._get_pool(world_id, map_id)
        pool.shared_memories.append(
            SharedMemoryContribution(
                contributor_type="passerby",
                content=content,
                sub_location_id=sub_location_id,
            )
        )
        if len(pool.shared_memories) > 100:
            pool.shared_memories = pool.shared_memories[-100:]
        await self._save_pool(world_id, map_id, pool)

    async def _get_shared_memory(
        self,
        world_id: str,
        map_id: str,
        sub_location_id: Optional[str] = None,
    ) -> str:
        """
        获取共享记忆摘要

        优先获取子地点级记忆，然后补充地图级记忆

        Args:
            world_id: 世界ID
            map_id: 地图ID
            sub_location_id: 子地点ID（可选）

        Returns:
            共享记忆文本
        """
        try:
            pool = await self._get_pool(world_id, map_id)
            contents = []
            recent_memories = list(reversed(pool.shared_memories))

            # 1. 先取当前子地点记忆（最多 3 条）
            if sub_location_id:
                for memory in recent_memories:
                    if memory.sub_location_id != sub_location_id:
                        continue
                    if memory.content in contents:
                        continue
                    contents.append(memory.content)
                    if len(contents) >= 3:
                        break

            # 2. 再补充地图级通用记忆，整体最多 5 条
            if len(contents) < 5:
                for memory in recent_memories:
                    if memory.sub_location_id not in (None, ""):
                        continue
                    if memory.content in contents:
                        continue
                    contents.append(memory.content)
                    if len(contents) >= 5:
                        break

            return "\n".join(contents)

        except Exception:
            return ""

    def _generate_name(self, template_id: str) -> str:
        """生成路人名称"""
        return random.choice(self.DEFAULT_NAMES)

    def _generate_appearance(self, template_id: str) -> str:
        """生成路人外貌"""
        return random.choice(self.DEFAULT_APPEARANCES)

    def _generate_personality(self, template_id: str) -> str:
        """生成路人性格"""
        return random.choice(self.DEFAULT_PERSONALITIES)

    # ==================== 管理方法 ====================

    async def cleanup_inactive(
        self,
        world_id: str,
        map_id: str,
        inactive_minutes: int = 60,
    ) -> int:
        """
        清理不活跃的路人

        Args:
            world_id: 世界ID
            map_id: 地图ID
            inactive_minutes: 不活跃时间（分钟）

        Returns:
            清理的数量
        """
        pool = await self._get_pool(world_id, map_id)
        cutoff = datetime.now() - timedelta(minutes=inactive_minutes)

        to_remove = []
        for inst_id, inst in pool.active_instances.items():
            last_active = inst.last_interaction or inst.spawn_time
            if last_active < cutoff:
                to_remove.append(inst_id)

        for inst_id in to_remove:
            await self.despawn_passerby(world_id, map_id, inst_id, persist_memory=True)

        return len(to_remove)

    async def update_pool_config(
        self,
        world_id: str,
        map_id: str,
        config: PasserbySpawnConfig,
    ) -> None:
        """更新路人池配置"""
        pool = await self._get_pool(world_id, map_id)
        pool.config = config
        await self._save_pool(world_id, map_id, pool)
