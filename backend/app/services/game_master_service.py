"""
Game Master Service - Phase 6 核心编排服务

这是整个游戏循环的核心编排器，负责：
1. 管理游戏会话的完整生命周期
2. 处理玩家输入并决定响应类型（GM叙述/NPC对话/战斗）
3. 协调GM Pro（叙述者）和NPC Pro（角色扮演）
4. 整合战斗系统并将结果转换为事件
5. 管理场景切换和记忆预加载

游戏循环状态机：
    IDLE → SCENE → DIALOGUE/COMBAT → SCENE → ...

    - IDLE: 等待开始
    - SCENE: 场景叙述阶段（GM Pro描述环境、NPC动作等）
    - DIALOGUE: 与NPC对话阶段（NPC Pro扮演角色）
    - COMBAT: 战斗阶段（战斗引擎处理）
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from app.config import settings
from app.combat.combat_engine import CombatEngine
from app.models.event import (
    Event,
    EventContent,
    EventType,
    GMEventIngestRequest,
    NaturalEventIngestRequest,
)
from app.models.game import (
    CombatContext,
    CombatResolveRequest,
    CombatStartRequest,
    GameSessionState,
    SceneState,
)
from app.models.pro import CharacterProfile, ChatMessage, SceneContext
from app.services.flash_service import FlashService
from app.services.game_loop_service import GameLoopService
from app.services.gm_flash_service import GMFlashService
from app.services.graph_store import GraphStore
from app.services.pro_service import ProService
# NPC Instance Pool 集成
from app.services.instance_manager import InstanceManager
from app.services.memory_graphizer import MemoryGraphizer
from app.services.flash_pro_bridge import FlashProBridge


class GamePhase(str, Enum):
    """游戏阶段"""
    IDLE = "idle"
    SCENE = "scene"           # 场景叙述
    DIALOGUE = "dialogue"     # NPC对话
    COMBAT = "combat"         # 战斗中
    ENDED = "ended"           # 会话结束


class InputType(str, Enum):
    """玩家输入类型"""
    NARRATION = "narration"       # 叙述/行动描述 -> GM处理
    DIALOGUE = "dialogue"         # 对话 -> NPC处理
    COMBAT_ACTION = "combat"      # 战斗行动 -> 战斗引擎
    SYSTEM = "system"             # 系统命令


@dataclass
class GameContext:
    """游戏上下文（内存中的运行时状态）"""
    world_id: str
    session_id: str
    phase: GamePhase = GamePhase.IDLE
    current_scene: Optional[SceneState] = None
    current_npc: Optional[str] = None  # 当前对话的NPC ID
    conversation_history: List[ChatMessage] = field(default_factory=list)
    game_day: int = 1
    known_characters: List[str] = field(default_factory=list)
    character_locations: Dict[str, str] = field(default_factory=dict)


class GameMasterService:
    """
    游戏大师服务 - 核心编排器

    整合所有子服务，提供统一的游戏循环接口。
    """

    def __init__(
        self,
        graph_store: Optional[GraphStore] = None,
        game_loop: Optional[GameLoopService] = None,
        gm_service: Optional[GMFlashService] = None,
        pro_service: Optional[ProService] = None,
        flash_service: Optional[FlashService] = None,
        combat_engine: Optional[CombatEngine] = None,
        instance_manager: Optional[InstanceManager] = None,
    ) -> None:
        self.graph_store = graph_store or GraphStore()
        self.flash_service = flash_service or FlashService(self.graph_store)
        self.gm_service = gm_service or GMFlashService(self.graph_store, self.flash_service)
        self.pro_service = pro_service or ProService(self.graph_store, self.flash_service)
        self.game_loop = game_loop or GameLoopService(gm_service=self.gm_service)
        self.combat_engine = combat_engine or CombatEngine()

        # NPC 实例池管理器（双层认知系统）
        self.instance_manager = instance_manager or InstanceManager(
            max_instances=settings.instance_pool_max_instances,
            context_window_size=settings.instance_pool_context_window_size,
            graphize_threshold=settings.instance_pool_graphize_threshold,
            keep_recent_tokens=settings.instance_pool_keep_recent_tokens,
            graph_store=self.graph_store,
        )
        # 记忆图谱化器
        self.memory_graphizer = MemoryGraphizer(graph_store=self.graph_store)
        # Flash-Pro 通信桥接
        self.flash_pro_bridge = FlashProBridge(
            flash_service=self.flash_service,
            graph_store=self.graph_store,
        )

        # 运行时上下文缓存
        self._contexts: Dict[str, GameContext] = {}

        # Gemini 客户端（用于GM叙述）
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model = settings.gemini_main_model

    # ============================================
    # 会话管理
    # ============================================

    async def start_session(
        self,
        world_id: str,
        session_id: Optional[str] = None,
        participants: Optional[List[str]] = None,
        known_characters: Optional[List[str]] = None,
        character_locations: Optional[Dict[str, str]] = None,
    ) -> GameContext:
        """
        开始新的游戏会话

        Args:
            world_id: 世界ID
            session_id: 可选的会话ID
            participants: 玩家列表
            known_characters: 世界中的已知角色列表
            character_locations: 角色当前位置映射

        Returns:
            GameContext: 游戏上下文
        """
        from app.models.game import CreateSessionRequest

        request = CreateSessionRequest(
            session_id=session_id,
            participants=participants or [],
        )

        response = await self.game_loop.create_session(world_id, request)
        session = response.session

        # 创建运行时上下文
        context = GameContext(
            world_id=world_id,
            session_id=session.session_id,
            phase=GamePhase.IDLE,
            known_characters=known_characters or [],
            character_locations=character_locations or {},
        )

        self._contexts[f"{world_id}:{session.session_id}"] = context

        return context

    def get_context(self, world_id: str, session_id: str) -> Optional[GameContext]:
        """获取游戏上下文"""
        return self._contexts.get(f"{world_id}:{session_id}")

    async def get_session(self, world_id: str, session_id: str) -> Optional[GameSessionState]:
        """获取会话状态"""
        return await self.game_loop.get_session(world_id, session_id)

    # ============================================
    # 场景管理
    # ============================================

    async def enter_scene(
        self,
        world_id: str,
        session_id: str,
        scene: SceneState,
        generate_description: bool = True,
    ) -> Dict[str, Any]:
        """
        进入新场景

        这是场景切换的核心方法：
        1. 更新会话的当前场景
        2. 为在场的NPC预加载相关记忆
        3. 生成场景描述（GM叙述）

        Args:
            world_id: 世界ID
            session_id: 会话ID
            scene: 场景状态
            generate_description: 是否生成场景描述

        Returns:
            {
                "scene": SceneState,
                "description": "GM生成的场景描述",
                "npc_memories": {"npc_id": "预加载的记忆"}
            }
        """
        context = self.get_context(world_id, session_id)
        if not context:
            raise ValueError(f"Session not found: {session_id}")

        # 1. 更新场景
        from app.models.game import UpdateSceneRequest
        await self.game_loop.update_scene(world_id, session_id, UpdateSceneRequest(scene=scene))

        context.current_scene = scene
        context.phase = GamePhase.SCENE
        context.current_npc = None
        context.conversation_history = []

        # 2. 更新角色位置
        for char_id in scene.participants:
            if scene.location:
                context.character_locations[char_id] = scene.location

        # 3. 为在场NPC预加载记忆
        npc_memories = {}
        for npc_id in scene.participants:
            if npc_id == "player":
                continue
            try:
                memory = await self._preload_npc_memory(world_id, npc_id, scene)
                if memory:
                    npc_memories[npc_id] = memory
            except Exception as e:
                npc_memories[npc_id] = f"(记忆加载失败: {e})"

        # 4. 生成场景描述
        description = ""
        if generate_description:
            description = await self._generate_scene_description(context, scene, npc_memories)

        # 5. 记录场景变化事件
        await self._record_scene_change_event(context, scene)

        return {
            "scene": scene,
            "description": description,
            "npc_memories": npc_memories,
        }

    async def _preload_npc_memory(
        self,
        world_id: str,
        npc_id: str,
        scene: SceneState,
    ) -> Optional[str]:
        """为NPC预加载与场景相关的记忆"""
        from app.models.flash import NaturalRecallRequest

        # 构建查询：与场景、在场人物相关的记忆
        query_parts = []
        if scene.location:
            query_parts.append(f"关于{scene.location}的记忆")
        if scene.participants:
            other_chars = [c for c in scene.participants if c != npc_id]
            if other_chars:
                query_parts.append(f"关于{', '.join(other_chars)}的记忆")

        if not query_parts:
            return None

        query = "；".join(query_parts)

        try:
            result = await self.flash_service.recall_memory_natural(
                world_id=world_id,
                character_id=npc_id,
                request=NaturalRecallRequest(
                    query=query,
                    translate=True,
                    include_subgraph=False,
                ),
            )
            return result.translated_memory
        except Exception:
            return None

    async def _generate_scene_description(
        self,
        context: GameContext,
        scene: SceneState,
        npc_memories: Dict[str, str],
    ) -> str:
        """生成GM风格的场景描述"""
        # 构建NPC状态描述
        npc_states = []
        for npc_id, memory in npc_memories.items():
            profile = await self.pro_service.get_profile(context.world_id, npc_id)
            state = await self.graph_store.get_character_state(context.world_id, npc_id) or {}
            npc_states.append({
                "id": npc_id,
                "name": profile.name or npc_id,
                "occupation": profile.occupation,
                "mood": state.get("mood", "normal"),
                "recent_memory": memory[:200] if memory else None,
            })

        prompt = f"""# 角色: TRPG游戏主持人 (GM)

你是一个经验丰富的桌游主持人。请为玩家描述当前场景。

## 场景信息
- 地点: {scene.location or '未知地点'}
- 描述: {scene.description}
- 氛围: {scene.atmosphere or '普通'}

## 在场的NPC
{self._format_npc_states(npc_states)}

## 输出要求

请用沉浸式的第二人称视角描述场景：
1. 描述环境和氛围
2. 介绍在场的NPC及其当前状态
3. 暗示可能的互动选项
4. 保持简洁（2-4段）

不要使用任何元信息或OOC（角色外）描述。直接开始叙述。"""

        response = self.client.models.generate_content(
            model=self.model,
            contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
        )

        return self._extract_text(response)

    def _format_npc_states(self, npc_states: List[Dict]) -> str:
        """格式化NPC状态供prompt使用"""
        if not npc_states:
            return "（没有NPC在场）"

        lines = []
        for npc in npc_states:
            line = f"- {npc['name']}"
            if npc.get('occupation'):
                line += f"（{npc['occupation']}）"
            if npc.get('mood') and npc['mood'] != 'normal':
                line += f"，看起来{npc['mood']}"
            if npc.get('recent_memory'):
                line += f"\n  最近记忆: {npc['recent_memory']}"
            lines.append(line)

        return "\n".join(lines)

    async def _record_scene_change_event(
        self,
        context: GameContext,
        scene: SceneState,
    ) -> None:
        """记录场景变化事件到GM图谱"""
        event = Event(
            type=EventType.SCENE_CHANGE,
            game_day=context.game_day,
            location=scene.location,
            participants=scene.participants,
            content=EventContent(
                raw=f"场景切换到{scene.location or '新地点'}",
                structured={"scene_id": scene.scene_id, "description": scene.description},
            ),
        )

        request = GMEventIngestRequest(
            event=event,
            distribute=True,
            known_characters=context.known_characters,
            character_locations=context.character_locations,
        )

        await self.gm_service.ingest_event(context.world_id, request)

    # ============================================
    # 玩家输入处理
    # ============================================

    async def process_player_input(
        self,
        world_id: str,
        session_id: str,
        player_input: str,
        input_type: Optional[InputType] = None,
    ) -> Dict[str, Any]:
        """
        处理玩家输入 - 游戏循环的核心入口

        这是玩家与游戏交互的主要接口。根据输入类型和当前状态，
        决定如何处理：
        - 叙述/行动 → GM处理并生成结果
        - 对话 → NPC Pro处理
        - 战斗指令 → 战斗引擎处理

        Args:
            world_id: 世界ID
            session_id: 会话ID
            player_input: 玩家输入
            input_type: 输入类型（可选，会自动推断）

        Returns:
            {
                "type": "narration/dialogue/combat/error",
                "response": "响应内容",
                "speaker": "响应者（GM/NPC名字）",
                "event_recorded": bool,
                "state_changes": {...}
            }
        """
        context = self.get_context(world_id, session_id)
        if not context:
            return {"type": "error", "response": "会话不存在，请先创建会话"}

        if context.phase == GamePhase.IDLE:
            return {"type": "error", "response": "请先进入一个场景"}

        # 推断输入类型
        if input_type is None:
            input_type = await self._classify_input(player_input, context)

        # 根据类型处理
        if input_type == InputType.SYSTEM:
            return await self._handle_system_command(context, player_input)

        elif input_type == InputType.COMBAT_ACTION and context.phase == GamePhase.COMBAT:
            return await self._handle_combat_action(context, player_input)

        elif input_type == InputType.DIALOGUE and context.current_npc:
            return await self._handle_dialogue(context, player_input)

        else:
            # 默认作为叙述处理
            return await self._handle_narration(context, player_input)

    async def _classify_input(self, player_input: str, context: GameContext) -> InputType:
        """分类玩家输入"""
        input_lower = player_input.lower().strip()

        # 系统命令
        if input_lower.startswith("/") or input_lower.startswith("!"):
            return InputType.SYSTEM

        # 战斗阶段的输入
        if context.phase == GamePhase.COMBAT:
            return InputType.COMBAT_ACTION

        # 正在与NPC对话时
        if context.current_npc and context.phase == GamePhase.DIALOGUE:
            return InputType.DIALOGUE

        # 检查是否是对话意图（包含引号或明显的对话词）
        dialogue_indicators = ['"', '"', '"', '说', '问', '告诉', '回答', '对话']
        if any(ind in player_input for ind in dialogue_indicators):
            return InputType.DIALOGUE

        return InputType.NARRATION

    async def _handle_system_command(
        self,
        context: GameContext,
        command: str,
    ) -> Dict[str, Any]:
        """处理系统命令"""
        cmd = command.lstrip("/!").lower().split()[0]

        if cmd in ("help", "帮助"):
            return {
                "type": "system",
                "response": """可用命令：
/look - 查看当前场景
/talk <npc_id> - 与NPC开始对话
/leave - 离开当前对话
/status - 查看状态
/recall <内容> - 让当前NPC回忆某事
/combat - 查看战斗状态
""",
                "speaker": "系统",
            }

        elif cmd == "look":
            if context.current_scene:
                return {
                    "type": "system",
                    "response": f"当前位置: {context.current_scene.location or '未知'}\n{context.current_scene.description}",
                    "speaker": "系统",
                }
            return {"type": "system", "response": "当前没有场景", "speaker": "系统"}

        elif cmd == "talk":
            parts = command.split(maxsplit=1)
            if len(parts) < 2:
                return {"type": "system", "response": "用法: /talk <npc_id>", "speaker": "系统"}
            npc_id = parts[1].strip()
            return await self.start_dialogue(context.world_id, context.session_id, npc_id)

        elif cmd == "leave":
            return await self.end_dialogue(context.world_id, context.session_id)

        elif cmd == "status":
            return {
                "type": "system",
                "response": f"阶段: {context.phase.value}\n当前NPC: {context.current_npc or '无'}\n游戏日: {context.game_day}",
                "speaker": "系统",
            }

        return {"type": "system", "response": f"未知命令: {cmd}", "speaker": "系统"}

    async def _handle_narration(
        self,
        context: GameContext,
        player_action: str,
    ) -> Dict[str, Any]:
        """处理叙述/行动"""
        # 1. GM处理玩家行动并生成叙述
        response = await self._generate_gm_response(context, player_action)

        # 2. 记录事件
        event_recorded = False
        try:
            await self._record_player_action_event(context, player_action, response)
            event_recorded = True
        except Exception:
            pass

        return {
            "type": "narration",
            "response": response,
            "speaker": "GM",
            "event_recorded": event_recorded,
        }

    async def _generate_gm_response(
        self,
        context: GameContext,
        player_action: str,
    ) -> str:
        """生成GM对玩家行动的响应"""
        scene = context.current_scene
        scene_desc = ""
        if scene:
            scene_desc = f"""
当前场景:
- 地点: {scene.location or '未知'}
- 描述: {scene.description}
- 在场人物: {', '.join(scene.participants) if scene.participants else '无'}
"""

        prompt = f"""# 角色: TRPG游戏主持人 (GM)

你是一个经验丰富的桌游主持人。请对玩家的行动做出回应。
{scene_desc}

## 玩家行动
{player_action}

## 输出要求

1. 用第二人称描述行动的结果
2. 如果行动涉及技能检定，假设检定成功（或根据情境判断）
3. 描述环境/NPC的反应
4. 推进故事发展
5. 保持简洁（1-3段）

如果玩家试图与NPC对话，简短回应后提示玩家可以直接和NPC交谈。

直接开始叙述，不要任何OOC内容。"""

        response = self.client.models.generate_content(
            model=self.model,
            contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
        )

        return self._extract_text(response)

    async def _record_player_action_event(
        self,
        context: GameContext,
        player_action: str,
        gm_response: str,
    ) -> None:
        """记录玩家行动事件"""
        event = Event(
            type=EventType.ACTION,
            game_day=context.game_day,
            location=context.current_scene.location if context.current_scene else None,
            participants=["player"],
            witnesses=context.current_scene.participants if context.current_scene else [],
            content=EventContent(
                raw=f"玩家行动: {player_action}\n结果: {gm_response[:200]}",
                structured={"action": player_action},
            ),
        )

        request = GMEventIngestRequest(
            event=event,
            distribute=True,
            known_characters=context.known_characters,
            character_locations=context.character_locations,
        )

        await self.gm_service.ingest_event(context.world_id, request)

    # ============================================
    # NPC对话
    # ============================================

    async def start_dialogue(
        self,
        world_id: str,
        session_id: str,
        npc_id: str,
    ) -> Dict[str, Any]:
        """
        开始与NPC对话

        使用 NPC 实例池的双层认知系统：
        - Pro 层：200K 上下文窗口管理当前对话
        - Flash 层：图谱检索提供长期记忆

        Args:
            world_id: 世界ID
            session_id: 会话ID
            npc_id: NPC ID

        Returns:
            NPC的开场白
        """
        context = self.get_context(world_id, session_id)
        if not context:
            return {"type": "error", "response": "会话不存在"}

        # 检查NPC是否在场
        if context.current_scene and npc_id not in context.current_scene.participants:
            return {"type": "error", "response": f"{npc_id}不在当前场景"}

        # 获取或创建 NPC 实例（双层认知系统）
        npc_instance = await self.instance_manager.get_or_create(npc_id, world_id)

        # 设置对话状态
        context.current_npc = npc_id
        context.phase = GamePhase.DIALOGUE
        context.conversation_history = []

        # 获取NPC资料（从实例或重新加载）
        profile = npc_instance.profile or await self.pro_service.get_profile(world_id, npc_id)

        # 预加载场景相关记忆到 Pro 层
        scene = context.current_scene
        if scene:
            try:
                memory_injection = await self.flash_pro_bridge.preload_scene_memory(
                    world_id=world_id,
                    npc_id=npc_id,
                    scene_description=scene.description or scene.location or "",
                    other_characters=[c for c in scene.participants if c != npc_id and c != "player"],
                    npc_profile=profile,
                )
                # 将预加载的记忆添加到上下文窗口（作为系统消息）
                if memory_injection.text and memory_injection.confidence > 0.3:
                    npc_instance.context_window.add_message(
                        "system",
                        f"[场景记忆] {memory_injection.text}",
                        metadata={"type": "preload_memory", "source_nodes": memory_injection.source_nodes},
                    )
            except Exception as e:
                # 记忆预加载失败不影响对话
                pass

        # 生成开场白
        greeting = await self._generate_npc_greeting(context, profile)

        # 将开场白添加到 NPC 实例的上下文窗口
        npc_instance.context_window.add_message("assistant", greeting)
        npc_instance.state.conversation_turn_count = 1
        npc_instance.state.is_dirty = True

        return {
            "type": "dialogue",
            "response": greeting,
            "speaker": profile.name or npc_id,
            "npc_id": npc_id,
            "instance_info": {
                "context_tokens": npc_instance.context_window.current_tokens,
                "context_usage": f"{npc_instance.context_window.usage_ratio:.1%}",
            },
        }

    async def _generate_npc_greeting(
        self,
        context: GameContext,
        profile: CharacterProfile,
    ) -> str:
        """生成NPC开场白"""
        scene = context.current_scene
        scene_context = SceneContext(
            description=scene.description if scene else "",
            location=scene.location if scene else None,
            present_characters=scene.participants if scene else [],
        )

        # 使用Pro服务生成开场白
        result = await self.pro_service.chat(
            world_id=context.world_id,
            character_id=context.current_npc,
            request=type('ChatRequest', (), {
                'message': "（玩家走过来准备和你交谈）",
                'scene': scene_context,
                'conversation_history': [],
                'injected_memory': None,
            })(),
        )

        return result.response

    async def _handle_dialogue(
        self,
        context: GameContext,
        player_message: str,
    ) -> Dict[str, Any]:
        """
        处理与NPC的对话

        使用双层认知系统：
        1. 将消息添加到 NPC 实例的 200K 上下文窗口
        2. 检测是否需要图谱化（窗口满载）
        3. 如需要，触发图谱化流程
        4. 生成 NPC 回复
        """
        if not context.current_npc:
            return {"type": "error", "response": "当前没有对话对象"}

        # 获取 NPC 实例
        npc_instance = self.instance_manager.get(context.world_id, context.current_npc)
        if not npc_instance:
            # 如果实例不存在（可能被 LRU 淘汰），重新创建
            npc_instance = await self.instance_manager.get_or_create(
                context.current_npc, context.world_id
            )

        # 获取NPC资料
        profile = npc_instance.profile or await self.pro_service.get_profile(
            context.world_id, context.current_npc
        )

        # 1. 将玩家消息添加到上下文窗口
        add_result = npc_instance.context_window.add_message("user", player_message)

        # 2. 检查是否需要图谱化
        graphize_triggered = False
        graphize_result = None
        if add_result.should_graphize:
            graphize_result = await self._trigger_graphize(context, npc_instance)
            graphize_triggered = True

        # 3. 构建场景上下文
        scene = context.current_scene
        scene_context = SceneContext(
            description=scene.description if scene else "",
            location=scene.location if scene else None,
            present_characters=scene.participants if scene else [],
        )

        # 4. 调用Pro服务获取NPC回复（使用上下文窗口中的历史）
        from app.models.pro import ChatRequest, ChatMessage

        # 从上下文窗口构建对话历史
        window_messages = npc_instance.context_window.get_all_messages()
        conversation_history = [
            ChatMessage(role=m.role, content=m.content)
            for m in window_messages
            if m.role in ("user", "assistant")
        ]

        request = ChatRequest(
            message=player_message,
            scene=scene_context,
            conversation_history=conversation_history[-20:],  # 保持合理长度
        )

        result = await self.pro_service.chat(
            world_id=context.world_id,
            character_id=context.current_npc,
            request=request,
        )

        # 5. 将 NPC 回复添加到上下文窗口
        npc_instance.context_window.add_message("assistant", result.response)
        npc_instance.state.conversation_turn_count += 1
        npc_instance.state.is_dirty = True

        # 6. 同步到 GameContext 的对话历史（兼容旧逻辑）
        context.conversation_history.append(ChatMessage(role="user", content=player_message))
        context.conversation_history.append(ChatMessage(role="assistant", content=result.response))
        if len(context.conversation_history) > 20:
            context.conversation_history = context.conversation_history[-20:]

        # 7. 记录对话事件
        await self._record_dialogue_event(context, player_message, result.response)

        # 8. 构建响应
        response_data = {
            "type": "dialogue",
            "response": result.response,
            "speaker": profile.name or context.current_npc,
            "npc_id": context.current_npc,
            "tool_called": result.tool_called,
            "instance_info": {
                "context_tokens": npc_instance.context_window.current_tokens,
                "context_usage": f"{npc_instance.context_window.usage_ratio:.1%}",
                "turn_count": npc_instance.state.conversation_turn_count,
            },
        }

        if result.recalled_memory:
            response_data["recalled_memory"] = result.recalled_memory

        if graphize_triggered:
            response_data["graphize_triggered"] = True
            response_data["graphize_result"] = {
                "messages_processed": graphize_result.messages_processed if graphize_result else 0,
                "nodes_added": graphize_result.nodes_added if graphize_result else 0,
            }

        return response_data

    async def _trigger_graphize(
        self,
        context: GameContext,
        npc_instance,
    ):
        """
        触发图谱化流程

        当 NPC 实例的上下文窗口达到阈值时：
        1. 提取需要图谱化的旧消息
        2. 使用 MemoryGraphizer 转换为图谱节点/边
        3. 标记并移除已图谱化的消息
        """
        from app.models.context_window import GraphizeRequest

        # 1. 获取图谱化请求
        graphize_request = npc_instance.context_window.get_graphize_request(
            current_scene=context.current_scene.location if context.current_scene else None,
            game_day=context.game_day,
        )

        if not graphize_request.messages:
            return None

        # 2. 执行图谱化
        result = await self.memory_graphizer.graphize(
            request=graphize_request,
            npc_profile=npc_instance.profile,
        )

        if result.success:
            # 3. 标记并移除已图谱化的消息
            message_ids = [m.id for m in graphize_request.messages]
            npc_instance.context_window.mark_messages_graphized(message_ids)
            npc_instance.context_window.remove_graphized_messages()
            npc_instance.state.graphize_count += 1

        return result

    async def _record_dialogue_event(
        self,
        context: GameContext,
        player_message: str,
        npc_response: str,
    ) -> None:
        """记录对话事件"""
        event = Event(
            type=EventType.DIALOGUE,
            game_day=context.game_day,
            location=context.current_scene.location if context.current_scene else None,
            participants=["player", context.current_npc],
            content=EventContent(
                raw=f"玩家对{context.current_npc}说: {player_message}\n{context.current_npc}回答: {npc_response[:100]}",
            ),
        )

        request = GMEventIngestRequest(
            event=event,
            distribute=True,
            recipients=["player", context.current_npc],
            known_characters=context.known_characters,
        )

        await self.gm_service.ingest_event(context.world_id, request)

    async def end_dialogue(
        self,
        world_id: str,
        session_id: str,
    ) -> Dict[str, Any]:
        """
        结束当前对话

        NPC 实例保留在池中（不销毁），以便下次对话时恢复上下文。
        如果实例有未保存的更改，标记为脏数据（稍后持久化）。
        """
        context = self.get_context(world_id, session_id)
        if not context:
            return {"type": "error", "response": "会话不存在"}

        if not context.current_npc:
            return {"type": "system", "response": "当前没有对话"}

        npc_id = context.current_npc

        # 获取 NPC 实例信息
        npc_instance = self.instance_manager.get(world_id, npc_id)
        instance_stats = None
        if npc_instance:
            instance_stats = {
                "context_tokens": npc_instance.context_window.current_tokens,
                "context_usage": f"{npc_instance.context_window.usage_ratio:.1%}",
                "turn_count": npc_instance.state.conversation_turn_count,
                "graphize_count": npc_instance.state.graphize_count,
            }
            # 标记为不活跃（但保留在池中）
            npc_instance.state.is_active = False

        # 清理 GameContext 状态
        context.current_npc = None
        context.phase = GamePhase.SCENE
        context.conversation_history = []

        response = {
            "type": "system",
            "response": f"你结束了与{npc_id}的对话。",
            "speaker": "系统",
        }

        if instance_stats:
            response["instance_stats"] = instance_stats

        return response

    # ============================================
    # 战斗系统
    # ============================================

    async def trigger_combat(
        self,
        world_id: str,
        session_id: str,
        enemies: List[Dict],
        player_state: Dict,
        combat_description: str = "",
        environment: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        触发战斗

        Args:
            world_id: 世界ID
            session_id: 会话ID
            enemies: 敌人列表
            player_state: 玩家状态
            combat_description: 战斗描述
            environment: 环境配置

        Returns:
            战斗初始化信息
        """
        context = self.get_context(world_id, session_id)
        if not context:
            return {"type": "error", "response": "会话不存在"}

        # 构建战斗上下文
        combat_context = CombatContext(
            location=context.current_scene.location if context.current_scene else None,
            participants=["player"],
            witnesses=context.current_scene.participants if context.current_scene else [],
            known_characters=context.known_characters,
            character_locations=context.character_locations,
        )

        # 启动战斗
        request = CombatStartRequest(
            player_state=player_state,
            enemies=enemies,
            environment=environment or {},
            combat_context=combat_context,
        )

        response = await self.game_loop.start_combat(world_id, session_id, request)

        # 更新状态
        context.phase = GamePhase.COMBAT

        # 生成战斗开始叙述
        narration = await self._generate_combat_narration(
            "start",
            combat_description,
            enemies,
        )

        return {
            "type": "combat",
            "phase": "start",
            "combat_id": response.combat_id,
            "narration": narration,
            "combat_state": response.combat_state,
            "available_actions": self.combat_engine.get_available_actions(response.combat_id),
        }

    async def execute_combat_action(
        self,
        world_id: str,
        session_id: str,
        action_id: str,
    ) -> Dict[str, Any]:
        """
        执行战斗行动

        Args:
            world_id: 世界ID
            session_id: 会话ID
            action_id: 行动ID

        Returns:
            行动结果
        """
        context = self.get_context(world_id, session_id)
        if not context or context.phase != GamePhase.COMBAT:
            return {"type": "error", "response": "当前不在战斗中"}

        session_state = await self.get_session(world_id, session_id)
        if not session_state or not session_state.active_combat_id:
            return {"type": "error", "response": "没有活跃的战斗"}

        combat_id = session_state.active_combat_id

        # 执行行动
        result = self.combat_engine.execute_action(combat_id, action_id)

        # 检查战斗是否结束
        combat_session = self.combat_engine.get_combat_state(combat_id)
        if combat_session and combat_session.state.value == "ended":
            return await self._handle_combat_end(context, combat_id)

        # 生成行动叙述
        narration = self._format_action_result(result)

        return {
            "type": "combat",
            "phase": "action",
            "action_result": result.to_dict(),
            "narration": narration,
            "available_actions": self.combat_engine.get_available_actions(combat_id),
        }

    async def _handle_combat_end(
        self,
        context: GameContext,
        combat_id: str,
    ) -> Dict[str, Any]:
        """处理战斗结束"""
        # 获取战斗结果
        result = self.combat_engine.get_combat_result(combat_id)

        # 解决战斗并记录事件
        resolve_request = CombatResolveRequest(
            combat_id=combat_id,
            use_engine=True,
            dispatch=True,
        )

        await self.game_loop.resolve_combat(
            context.world_id,
            context.session_id,
            resolve_request,
        )

        # 更新状态
        context.phase = GamePhase.SCENE

        # 生成战斗结束叙述
        narration = await self._generate_combat_narration(
            "end",
            result.summary,
            [],
            result.to_dict(),
        )

        return {
            "type": "combat",
            "phase": "end",
            "result": result.to_dict(),
            "narration": narration,
        }

    async def _handle_combat_action(
        self,
        context: GameContext,
        player_input: str,
    ) -> Dict[str, Any]:
        """处理战斗中的玩家输入"""
        session_state = await self.get_session(context.world_id, context.session_id)
        if not session_state or not session_state.active_combat_id:
            return {"type": "error", "response": "没有活跃的战斗"}

        combat_id = session_state.active_combat_id
        available_actions = self.combat_engine.get_available_actions(combat_id)

        # 尝试匹配行动
        action_id = self._match_combat_action(player_input, available_actions)

        if not action_id:
            return {
                "type": "combat",
                "phase": "input",
                "response": f"无法识别的行动。可用行动：\n" + "\n".join(
                    f"- {a.display_name}: {a.description}" for a in available_actions
                ),
                "available_actions": available_actions,
            }

        return await self.execute_combat_action(
            context.world_id,
            context.session_id,
            action_id,
        )

    def _match_combat_action(
        self,
        player_input: str,
        available_actions: List,
    ) -> Optional[str]:
        """匹配玩家输入到战斗行动"""
        input_lower = player_input.lower()

        for action in available_actions:
            # 精确匹配action_id
            if action.action_id.lower() == input_lower:
                return action.action_id
            # 匹配显示名称
            if action.display_name.lower() in input_lower:
                return action.action_id
            # 关键词匹配
            if action.action_type.value in input_lower:
                return action.action_id

        return None

    async def _generate_combat_narration(
        self,
        phase: str,
        description: str,
        enemies: List[Dict],
        result: Optional[Dict] = None,
    ) -> str:
        """生成战斗叙述"""
        if phase == "start":
            enemy_desc = ", ".join(e.get("type", "敌人") for e in enemies)
            prompt = f"""# 角色: TRPG游戏主持人

用简短、紧张的语气描述战斗开始：
- 敌人: {enemy_desc}
- 情境: {description}

用第二人称，1-2句话。直接叙述，不要OOC。"""

        elif phase == "end":
            prompt = f"""# 角色: TRPG游戏主持人

用简短的语气描述战斗结束：
- 结果: {description}
- 详情: {result if result else ''}

用第二人称，1-2句话。直接叙述，不要OOC。"""

        else:
            return description

        response = self.client.models.generate_content(
            model=self.model,
            contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
        )

        return self._extract_text(response)

    def _format_action_result(self, result) -> str:
        """格式化行动结果为叙述"""
        messages = result.messages if hasattr(result, 'messages') else []
        return "\n".join(messages)

    # ============================================
    # 辅助方法
    # ============================================

    def _extract_text(self, response) -> str:
        """从Gemini响应中提取文本"""
        text = ""
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if hasattr(part, 'text') and part.text:
                    if not (hasattr(part, 'thought') and part.thought):
                        text += part.text
        return text.strip()

    async def advance_day(
        self,
        world_id: str,
        session_id: str,
    ) -> Dict[str, Any]:
        """推进游戏日"""
        context = self.get_context(world_id, session_id)
        if not context:
            return {"type": "error", "response": "会话不存在"}

        context.game_day += 1

        return {
            "type": "system",
            "response": f"新的一天开始了。现在是第 {context.game_day} 天。",
            "game_day": context.game_day,
        }
