"""
NPC Instance Manager.

实例池管理器，负责：
- 懒加载 NPC 实例（首次交互时创建）
- LRU 淘汰策略（内存不足时清理不活跃实例）
- 持久化状态（实例销毁前保存到 Firestore）
"""
import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from app.models.npc_instance import (
    NPCConfig,
    NPCInstanceInfo,
    NPCInstanceState,
)
from app.models.character_profile import CharacterProfile
from app.services.context_window import ContextWindow

if TYPE_CHECKING:
    from app.services.flash_service import FlashService
    from app.services.graph_store import GraphStore
    from app.services.memory_graph import MemoryGraph

logger = logging.getLogger(__name__)


@dataclass
class NPCInstance:
    """
    单个 NPC 的完整认知实例

    双层认知系统：
    - 实时上下文层（工作记忆）：ContextWindow 管理 200K 上下文
    - Flash 层（潜意识）：FlashService + MemoryGraph 管理长期记忆
    """

    npc_id: str
    world_id: str

    # 实时上下文层（工作记忆）
    context_window: ContextWindow

    # Flash 层（潜意识）- 延迟初始化
    _flash_service: Optional["FlashService"] = None
    _memory_graph: Optional["MemoryGraph"] = None

    # 配置
    config: Optional[NPCConfig] = None
    profile: Optional[CharacterProfile] = None

    # 状态
    state: NPCInstanceState = field(default_factory=lambda: NPCInstanceState(
        npc_id="", world_id=""
    ))

    # 回调（用于获取依赖）
    _get_flash_service: Optional[Callable] = None
    _get_memory_graph: Optional[Callable] = None

    def __post_init__(self):
        """初始化后设置状态"""
        self.state.npc_id = self.npc_id
        self.state.world_id = self.world_id

    @property
    def flash_service(self) -> "FlashService":
        """懒加载 Flash 服务"""
        if self._flash_service is None:
            if self._get_flash_service:
                self._flash_service = self._get_flash_service()
            else:
                from app.services.flash_service import FlashService
                self._flash_service = FlashService()
        return self._flash_service

    @property
    def memory_graph(self) -> "MemoryGraph":
        """懒加载记忆图谱"""
        if self._memory_graph is None:
            if self._get_memory_graph:
                self._memory_graph = self._get_memory_graph()
            else:
                from app.services.memory_graph import MemoryGraph
                self._memory_graph = MemoryGraph()
        return self._memory_graph

    @property
    def is_active(self) -> bool:
        """是否活跃"""
        return self.state.is_active

    @property
    def last_access(self) -> datetime:
        """最后访问时间"""
        return self.state.last_access

    def touch(self) -> None:
        """更新最后访问时间"""
        self.state.last_access = datetime.now()

    def get_info(self) -> NPCInstanceInfo:
        """获取实例信息"""
        return NPCInstanceInfo(
            npc_id=self.npc_id,
            world_id=self.world_id,
            name=self.config.name if self.config else self.npc_id,
            is_active=self.state.is_active,
            last_access=self.state.last_access,
            context_tokens=self.context_window.current_tokens,
            context_usage_ratio=self.context_window.usage_ratio,
            graphize_count=self.state.graphize_count,
        )

    async def persist(self, graph_store: "GraphStore") -> None:
        """
        持久化实例状态

        Args:
            graph_store: 图谱存储服务
        """
        # 保存状态到 Firestore
        state_data = {
            "npc_id": self.npc_id,
            "is_active": self.state.is_active,
            "last_access": self.state.last_access.isoformat(),
            "conversation_turn_count": self.state.conversation_turn_count,
            "total_tokens_used": self.state.total_tokens_used,
            "graphize_count": self.state.graphize_count,
            "last_persist": datetime.now().isoformat(),
        }

        await graph_store.update_character_state(
            self.world_id,
            self.npc_id,
            {"instance_state": state_data},
        )

        self.state.is_dirty = False
        self.state.last_persist = datetime.now()


class InstanceManager:
    """
    NPC 实例池管理器

    功能：
    1. 懒加载 NPC 实例（首次交互时创建）
    2. LRU 淘汰策略（内存不足时清理不活跃实例）
    3. 持久化状态（实例销毁前保存到 Firestore）
    """

    def __init__(
        self,
        max_instances: int = 20,
        evict_after: timedelta = timedelta(minutes=30),
        context_window_size: int = 200_000,
        graphize_threshold: float = 0.8,
        keep_recent_tokens: int = 50_000,
        graph_store: Optional["GraphStore"] = None,
    ):
        """
        初始化实例管理器

        Args:
            max_instances: 最大同时活跃实例数
            evict_after: 不活跃多久后可淘汰
            context_window_size: 上下文窗口大小（tokens）
            graphize_threshold: 图谱化阈值
            keep_recent_tokens: 图谱化后保留的 token 数
            graph_store: 图谱存储服务（可选）
        """
        self.max_instances = max_instances
        self.evict_after = evict_after
        self.context_window_size = context_window_size
        self.graphize_threshold = graphize_threshold
        self.keep_recent_tokens = keep_recent_tokens

        # 实例存储（使用 OrderedDict 保持 LRU 顺序）
        self._instances: OrderedDict[str, NPCInstance] = OrderedDict()

        # 依赖
        self._graph_store = graph_store

        # 锁（防止并发创建同一实例）
        self._locks: Dict[str, asyncio.Lock] = {}

        # 统计
        self._total_created: int = 0
        self._total_evicted: int = 0

    @property
    def graph_store(self) -> "GraphStore":
        """懒加载 GraphStore"""
        if self._graph_store is None:
            from app.services.graph_store import GraphStore
            self._graph_store = GraphStore()
        return self._graph_store

    def _make_key(self, world_id: str, npc_id: str) -> str:
        """生成实例键"""
        return f"{world_id}:{npc_id}"

    def _get_lock(self, key: str) -> asyncio.Lock:
        """获取或创建锁"""
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    # ==================== 实例获取与创建 ====================

    async def get_or_create(
        self,
        npc_id: str,
        world_id: str,
        config: Optional[NPCConfig] = None,
        preload_memory: bool = True,
    ) -> NPCInstance:
        """
        获取或创建 NPC 实例（懒加载）

        Args:
            npc_id: NPC ID
            world_id: 世界 ID
            config: NPC 配置（可选）
            preload_memory: 是否预加载记忆

        Returns:
            NPCInstance 实例
        """
        key = self._make_key(world_id, npc_id)

        # 如果已存在，直接返回（更新 LRU）
        if key in self._instances:
            self._touch(key)
            return self._instances[key]

        # 使用锁防止并发创建
        lock = self._get_lock(key)
        async with lock:
            # 双重检查
            if key in self._instances:
                self._touch(key)
                return self._instances[key]

            # 检查是否需要淘汰
            if len(self._instances) >= self.max_instances:
                await self._evict_lru()

            # 创建新实例
            instance = await self._create_instance(
                npc_id, world_id, config, preload_memory
            )
            self._instances[key] = instance
            self._total_created += 1

            return instance

    async def _create_instance(
        self,
        npc_id: str,
        world_id: str,
        config: Optional[NPCConfig] = None,
        preload_memory: bool = True,
    ) -> NPCInstance:
        """
        创建新的 NPC 实例

        Args:
            npc_id: NPC ID
            world_id: 世界 ID
            config: NPC 配置（可选）
            preload_memory: 是否预加载记忆

        Returns:
            新创建的 NPCInstance
        """
        # 创建上下文窗口
        context_window = ContextWindow(
            npc_id=npc_id,
            world_id=world_id,
            max_tokens=self.context_window_size,
            graphize_threshold=self.graphize_threshold,
            keep_recent_tokens=self.keep_recent_tokens,
        )

        # 获取或创建配置
        if config is None:
            config = await self._load_npc_config(world_id, npc_id)

        # 获取角色 profile
        profile = await self._load_profile(world_id, npc_id)

        # 设置系统提示词
        system_prompt = self._build_system_prompt(config, profile)
        context_window.set_system_prompt(system_prompt)

        # 创建实例
        instance = NPCInstance(
            npc_id=npc_id,
            world_id=world_id,
            context_window=context_window,
            config=config,
            profile=profile,
            _get_flash_service=lambda: self._create_flash_service(world_id, npc_id),
        )

        # 恢复之前的状态（如果存在）
        await self._restore_instance_state(instance)

        return instance

    async def _load_npc_config(self, world_id: str, npc_id: str) -> NPCConfig:
        """加载 NPC 配置"""
        # 从 profile 中获取配置信息
        profile_data = await self.graph_store.get_character_profile(world_id, npc_id)

        if profile_data:
            return NPCConfig(
                npc_id=npc_id,
                name=profile_data.get("name", npc_id),
                occupation=profile_data.get("occupation"),
                age=profile_data.get("age"),
                personality=profile_data.get("personality", ""),
                speech_pattern=profile_data.get("speech_pattern", ""),
                example_dialogue=profile_data.get("example_dialogue"),
                system_prompt=profile_data.get("system_prompt"),
                metadata=profile_data.get("metadata", {}),
            )

        # 默认配置
        return NPCConfig(npc_id=npc_id, name=npc_id)

    async def _load_profile(self, world_id: str, npc_id: str) -> CharacterProfile:
        """加载角色 Profile"""
        profile_data = await self.graph_store.get_character_profile(world_id, npc_id)
        if profile_data:
            return CharacterProfile(**profile_data)
        return CharacterProfile(name=npc_id)

    def _build_system_prompt(
        self,
        config: NPCConfig,
        profile: CharacterProfile,
    ) -> str:
        """构建系统提示词"""
        # 如果有自定义的系统提示词，优先使用
        if config.system_prompt:
            return config.system_prompt

        if profile.system_prompt:
            return profile.system_prompt

        # 构建默认的系统提示词
        parts = [f"你是 {config.name}。"]

        if config.occupation:
            parts.append(f"职业：{config.occupation}。")

        if config.personality:
            parts.append(f"性格：{config.personality}。")

        if config.speech_pattern:
            parts.append(f"说话风格：{config.speech_pattern}。")

        if config.example_dialogue:
            parts.append(f"\n示例对话：\n{config.example_dialogue}")

        parts.append("\n请以第一人称回应，保持角色一致性。")

        return "\n".join(parts)

    def _create_flash_service(self, world_id: str, npc_id: str) -> "FlashService":
        """创建 Flash 服务"""
        from app.services.flash_service import FlashService
        return FlashService(graph_store=self.graph_store)

    async def _restore_instance_state(self, instance: NPCInstance) -> None:
        """恢复实例之前的状态"""
        state_data = await self.graph_store.get_character_state(
            instance.world_id, instance.npc_id
        )

        if state_data and "instance_state" in state_data:
            saved = state_data["instance_state"]
            instance.state.conversation_turn_count = saved.get("conversation_turn_count", 0)
            instance.state.total_tokens_used = saved.get("total_tokens_used", 0)
            instance.state.graphize_count = saved.get("graphize_count", 0)

    # ==================== LRU 淘汰 ====================

    def _touch(self, key: str) -> None:
        """更新 LRU 顺序"""
        if key in self._instances:
            # 移到末尾（最近使用）
            self._instances.move_to_end(key)
            self._instances[key].touch()

    async def _evict_lru(self) -> None:
        """淘汰最久未使用的实例"""
        if not self._instances:
            return

        # 找到最久未使用且超过淘汰时间的实例
        now = datetime.now()
        evict_key = None

        for key, instance in self._instances.items():
            if now - instance.last_access > self.evict_after:
                evict_key = key
                break

        # 如果没有超时的，淘汰最旧的
        if evict_key is None:
            evict_key = next(iter(self._instances))

        await self._evict_instance(evict_key)

    async def _evict_instance(self, key: str) -> None:
        """淘汰指定实例（含对话图谱化）"""
        instance = self._instances.pop(key, None)
        if instance:
            # 图谱化剩余对话
            if instance.context_window.message_count > 0:
                try:
                    graphized = await self._graphize_messages(
                        instance=instance,
                        current_scene=None,
                        game_day=1,
                        force_all=True,
                    )
                    if graphized and graphized.get("success"):
                        logger.info(
                            "[InstanceManager] 淘汰图谱化 %s: nodes=%d edges=%d",
                            key,
                            graphized.get("nodes_added", 0),
                            graphized.get("edges_added", 0),
                        )
                except Exception as e:
                    logger.error("[InstanceManager] 淘汰图谱化失败 %s: %s", key, e, exc_info=True)
            # 持久化状态
            await instance.persist(self.graph_store)
            self._total_evicted += 1

    async def maybe_graphize_instance(
        self,
        world_id: str,
        npc_id: str,
        current_scene: Optional[str] = None,
        game_day: int = 1,
    ) -> Optional[Dict[str, Any]]:
        """达到阈值时实时图谱化实例上下文。"""
        key = self._make_key(world_id, npc_id)
        lock = self._get_lock(key)
        async with lock:
            instance = self._instances.get(key)
            if not instance:
                return None
            return await self._graphize_messages(
                instance=instance,
                current_scene=current_scene,
                game_day=game_day,
                force_all=False,
            )

    async def _graphize_messages(
        self,
        instance: NPCInstance,
        current_scene: Optional[str],
        game_day: int,
        force_all: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """执行实例图谱化并回收已图谱化消息。"""
        from app.models.context_window import GraphizeRequest
        from app.services.memory_graphizer import MemoryGraphizer

        if force_all:
            messages = [
                msg
                for msg in instance.context_window.get_all_messages()
                if not msg.is_graphized
            ]
            request = GraphizeRequest(
                npc_id=instance.npc_id,
                world_id=instance.world_id,
                messages=messages,
                current_scene=current_scene,
                game_day=game_day,
            )
        else:
            trigger = instance.context_window.check_graphize_trigger()
            if not trigger.should_graphize:
                return None
            request = instance.context_window.get_graphize_request(
                current_scene=current_scene,
                game_day=game_day,
            )

        if not request.messages:
            return None

        graphizer = MemoryGraphizer(graph_store=self.graph_store)
        result = await graphizer.graphize(request)
        if not result.success:
            logger.error(
                "[InstanceManager] 实时图谱化失败 %s:%s: %s",
                instance.world_id,
                instance.npc_id,
                result.error,
            )
            return {
                "success": False,
                "error": result.error,
                "nodes_added": 0,
                "edges_added": 0,
                "messages_removed": 0,
                "tokens_freed": 0,
            }

        message_ids = [msg.id for msg in request.messages]
        instance.context_window.mark_messages_graphized(message_ids)
        remove_result = instance.context_window.remove_graphized_messages()
        instance.state.graphize_count += 1
        instance.state.is_dirty = True
        return {
            "success": True,
            "nodes_added": result.nodes_added,
            "edges_added": result.edges_added,
            "messages_removed": remove_result.removed_count,
            "tokens_freed": remove_result.tokens_freed,
            "usage_ratio": remove_result.usage_ratio,
        }

    # ==================== 实例管理 ====================

    def get(self, world_id: str, npc_id: str) -> Optional[NPCInstance]:
        """
        获取实例（不创建）

        Args:
            world_id: 世界 ID
            npc_id: NPC ID

        Returns:
            NPCInstance 或 None
        """
        key = self._make_key(world_id, npc_id)
        instance = self._instances.get(key)
        if instance:
            self._touch(key)
        return instance

    def has(self, world_id: str, npc_id: str) -> bool:
        """检查实例是否存在"""
        key = self._make_key(world_id, npc_id)
        return key in self._instances

    async def remove(self, world_id: str, npc_id: str, persist: bool = True) -> bool:
        """
        移除实例

        Args:
            world_id: 世界 ID
            npc_id: NPC ID
            persist: 是否持久化状态

        Returns:
            是否成功移除
        """
        key = self._make_key(world_id, npc_id)
        instance = self._instances.pop(key, None)

        if instance:
            if persist:
                await instance.persist(self.graph_store)
            return True

        return False

    def list_instances(self, world_id: Optional[str] = None) -> List[NPCInstanceInfo]:
        """
        列出所有实例

        Args:
            world_id: 可选的世界 ID 过滤

        Returns:
            实例信息列表
        """
        instances = []
        for key, instance in self._instances.items():
            if world_id is None or instance.world_id == world_id:
                instances.append(instance.get_info())
        return instances

    async def persist_all(self) -> int:
        """
        持久化所有实例

        Returns:
            持久化的实例数
        """
        count = 0
        for instance in self._instances.values():
            if instance.state.is_dirty:
                await instance.persist(self.graph_store)
                count += 1
        return count

    async def clear(self, persist: bool = True) -> int:
        """
        清除所有实例

        Args:
            persist: 是否持久化状态

        Returns:
            清除的实例数
        """
        count = len(self._instances)

        if persist:
            for instance in self._instances.values():
                await instance.persist(self.graph_store)

        self._instances.clear()
        return count

    # ==================== 统计信息 ====================

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "active_instances": len(self._instances),
            "max_instances": self.max_instances,
            "total_created": self._total_created,
            "total_evicted": self._total_evicted,
            "instances": [inst.get_info().model_dump() for inst in self._instances.values()],
        }

    def __len__(self) -> int:
        return len(self._instances)

    def __repr__(self) -> str:
        return (
            f"InstanceManager(active={len(self._instances)}, "
            f"max={self.max_instances}, "
            f"created={self._total_created}, "
            f"evicted={self._total_evicted})"
        )
