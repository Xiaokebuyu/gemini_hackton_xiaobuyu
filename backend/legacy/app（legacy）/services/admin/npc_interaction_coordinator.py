"""NPCInteractionCoordinator — NPC 直接交互 + 私聊管线。

从 PipelineOrchestrator 提取，与 CombatCoordinator / NarrativeCoordinator 同级。

两条入口：
  - process_interact_stream: NPC→GM观察→队友旁观→对话选项→持久化
  - process_private_chat_stream: NPC(InstanceManager 双层认知)→对话选项→持久化
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
from typing import Any, Dict, List, Optional

from app.config import settings
from app.exceptions import LLMServiceError
from app.runtime.game_runtime import GameRuntime
from app.runtime.session_runtime import SessionRuntime
from app.services.admin._graphize_helper import maybe_graphize_session_history

logger = logging.getLogger(__name__)


class NPCInteractionCoordinator:
    """NPC 直接交互流 — interact + private_chat。"""

    def __init__(
        self,
        llm_service: Any = None,
        instance_manager: Any = None,
        teammate_response_service: Any = None,
        state_manager: Any = None,
        party_service: Any = None,
        narrative_service: Any = None,
        session_history_manager: Any = None,
        session_store: Any = None,
        **_kwargs: Any,
    ) -> None:
        self.llm_service = llm_service
        self.instance_manager = instance_manager
        self.teammate_response_service = teammate_response_service
        self.state_manager = state_manager
        self.party_service = party_service
        self.narrative_service = narrative_service
        self.session_history_manager = session_history_manager
        self.session_store = session_store

    # =================================================================
    # NPC /interact 交互流
    # =================================================================

    async def process_interact_stream(
        self,
        world_id: str,
        session_id: str,
        npc_id: str,
        player_input: str,
    ):
        """NPC 直接交互 SSE 流。

        流程: Setup → NPC 回复 → GM 观察(可 PASS) → 队友观察 → 对话选项 → 持久化
        """
        # ── A: Setup ──
        rt = await GameRuntime.get_instance()
        world = await rt.get_world(world_id)
        session = SessionRuntime(
            world_id=world_id,
            session_id=session_id,
            world=world,
            state_manager=self.state_manager,
            party_service=self.party_service,
            narrative_service=self.narrative_service,
            session_history_manager=self.session_history_manager,

            session_store=self.session_store,

        )
        await session.restore()

        if not session.player:
            yield {"type": "error", "error": "请先创建角色后再开始冒险。"}
            return

        wg = getattr(session, "world_graph", None)
        if not wg:
            yield {"type": "error", "error": "世界图谱不可用。"}
            return

        npc_node = wg.get_node(npc_id)
        if not npc_node:
            yield {"type": "error", "error": f"NPC '{npc_id}' 不存在。"}
            return

        npc_name = npc_node.name or npc_id

        # SceneBus: contact + 写入玩家发言
        if session.scene_bus:
            from app.world.scene import BusEntry, BusEntryType
            session.scene_bus.contact(npc_id)
            session.scene_bus.publish(BusEntry(
                actor="player",
                actor_name="player",
                type=BusEntryType.SPEECH,
                content=player_input,
            ))

        yield {"type": "interact_start", "npc_id": npc_id, "npc_name": npc_name}

        # ── B1: NPC Agentic Response ──
        try:
            from app.agentic.agentic_executor import AgenticExecutor
            from app.agentic.immersive_tools import AgenticContext

            npc_traits = set(npc_node.properties.get("traits", []))
            event_queue: asyncio.Queue = asyncio.Queue()

            from app.services.world_api import WorldAPI
            npc_api = WorldAPI(session, role="npc", agent_id=npc_id)

            npc_ctx = AgenticContext(
                session=session,
                agent_id=npc_id,
                role="npc",
                scene_bus=session.scene_bus,
                world_id=world_id,
                chapter_id=getattr(session, "chapter_id", ""),
                area_id=getattr(session, "area_id", ""),
                location_id=getattr(session, "sub_location", ""),
                world_graph=getattr(session, "world_graph", None),
                api=npc_api,
            )

            npc_system_prompt = self._build_npc_system_prompt(npc_node, session)
            npc_model, npc_thinking = self._select_npc_model(npc_node)

            bus_summary = ""
            if session.scene_bus:
                bus_summary = session.scene_bus.get_round_summary(viewer_id=npc_id) or ""

            npc_user_prompt = bus_summary or f"玩家对你说：{player_input}"

            executor = AgenticExecutor(self.llm_service)
            npc_result = await executor.run(
                ctx=npc_ctx,
                system_prompt=npc_system_prompt,
                user_prompt=npc_user_prompt,
                traits=npc_traits,
                event_queue=event_queue,
                model_override=npc_model,
                thinking_level=npc_thinking,
            )

            # Drain NPC tool events
            while not event_queue.empty():
                evt = event_queue.get_nowait()
                evt["character_id"] = npc_id
                yield evt

            npc_response_text = npc_result.narration or ""
            yield {
                "type": "npc_response",
                "npc_id": npc_id,
                "npc_name": npc_name,
                "text": npc_response_text,
            }

            # 写入总线
            if session.scene_bus and npc_response_text:
                from app.world.scene import BusEntry, BusEntryType
                session.scene_bus.publish(BusEntry(
                    actor=npc_id,
                    actor_name=npc_name,
                    type=BusEntryType.SPEECH,
                    content=npc_response_text,
                ))
        except (LLMServiceError, asyncio.TimeoutError) as exc:
            logger.error("[interact] NPC response failed: %s", exc, exc_info=True)
            npc_response_text = ""
            yield {"type": "error", "error": f"NPC 回复失败: {exc}"}

        # ── B2: GM Observer (可 [PASS]) ──
        try:
            from app.agentic.agentic_executor import AgenticExecutor
            from app.agentic.immersive_tools import AgenticContext
            from app.services.world_api import WorldAPI

            gm_obs_api = WorldAPI(session, role="gm", agent_id="gm")
            gm_ctx = AgenticContext(
                session=session,
                agent_id="gm",
                role="gm",
                scene_bus=session.scene_bus,
                world_id=world_id,
                chapter_id=getattr(session, "chapter_id", ""),
                area_id=getattr(session, "area_id", ""),
                location_id=getattr(session, "sub_location", ""),
                world_graph=getattr(session, "world_graph", None),
                api=gm_obs_api,
            )
            gm_prompt = (
                f"玩家正在与{npc_name}对话。作为 GM，观察这次交互。\n"
                f"如果场景需要环境描述、气氛渲染或重要事件提示，请简短叙述。\n"
                f"如果不需要介入，直接输出 [PASS]。\n\n"
            )
            if session.scene_bus:
                gm_bus = session.scene_bus.get_round_summary() or ""
                if gm_bus:
                    gm_prompt += f"场景总线:\n{gm_bus}"

            gm_executor = AgenticExecutor(self.llm_service)
            gm_result = await gm_executor.run(
                ctx=gm_ctx,
                system_prompt="你是游戏 GM。简洁观察，必要时渲染氛围。不必要时输出[PASS]。",
                user_prompt=gm_prompt,
                model_override=settings.admin_agentic_model,
                thinking_level=settings.admin_flash_thinking_level,
            )
            gm_narration = gm_result.narration or ""
            if gm_narration:
                yield {"type": "gm_observation", "text": gm_narration}
        except (LLMServiceError, asyncio.TimeoutError) as exc:
            logger.warning("[interact] GM observer failed: %s", exc)
            gm_narration = ""

        # ── B3: Teammate Observer ──
        teammate_responses: List[Dict[str, Any]] = []
        party = session.party
        if party and party.get_active_members():
            context_dict = {"_runtime_session": session}
            if session.scene_bus:
                bus_for_tm = session.scene_bus.get_round_summary(
                    exclude_actors={"player", "gm"},
                ) or ""
                if bus_for_tm:
                    context_dict["scene_bus_summary"] = bus_for_tm
            try:
                async for tm_event in self.teammate_response_service.process_round_stream(
                    party=party,
                    player_input=player_input,
                    gm_response=gm_narration or npc_response_text,
                    context=context_dict,
                ):
                    yield tm_event
                    if tm_event.get("type") == "teammate_end" and tm_event.get("response"):
                        teammate_responses.append({
                            "character_id": tm_event["character_id"],
                            "name": tm_event["name"],
                            "response": tm_event["response"],
                        })
            except (LLMServiceError, asyncio.TimeoutError) as exc:
                logger.warning("[interact] teammate observer failed: %s", exc)

        # ── C: Dialogue Options ──
        dialogue_options = await self._generate_dialogue_options(
            npc_name, npc_node, player_input, npc_response_text, session,
        )
        yield {
            "type": "dialogue_options",
            "options": [opt.model_dump() for opt in dialogue_options],
        }

        # ── D: Persist ──
        if session.history:
            session.history.record_round(
                player_input=player_input,
                gm_response=gm_narration or "",
                metadata={"source": "interact", "npc_id": npc_id},
            )
            session.history.record_npc_response(
                character_id=npc_id,
                name=npc_name,
                dialogue=npc_response_text,
            )
            for t in teammate_responses:
                session.history.record_teammate_response(
                    character_id=t["character_id"],
                    name=t["name"],
                    response=t["response"],
                )

        # 叙事计数
        if session.narrative:
            npc_interactions = getattr(session.narrative, "npc_interactions", None)
            if isinstance(npc_interactions, dict):
                npc_interactions[npc_id] = npc_interactions.get(npc_id, 0) + 1
            session.mark_narrative_dirty()

        await maybe_graphize_session_history(session, source="interact")

        # SceneBus clear
        if session.scene_bus:
            session.scene_bus.clear()

        await session.persist()

        yield {
            "type": "complete",
            "npc_id": npc_id,
            "npc_name": npc_name,
            "npc_response": npc_response_text,
            "gm_observation": gm_narration,
            "teammate_responses": teammate_responses,
        }

    # =================================================================
    # 私聊 /private-chat 交互流
    # =================================================================

    async def process_private_chat_stream(
        self,
        world_id: str,
        session_id: str,
        npc_id: str,
        player_input: str,
    ):
        """私聊 SSE 流 — 完整 Pipeline，GM/队友观察跳过。

        与 process_interact_stream 对齐，但跳过 GM 观察和队友旁观（私密模式）。
        使用 InstanceManager 双层认知（上下文窗口 + 记忆图谱化）。
        """
        # ── A: Setup ──
        rt = await GameRuntime.get_instance()
        world = await rt.get_world(world_id)
        session = SessionRuntime(
            world_id=world_id,
            session_id=session_id,
            world=world,
            state_manager=self.state_manager,
            party_service=self.party_service,
            narrative_service=self.narrative_service,
            session_history_manager=self.session_history_manager,

            session_store=self.session_store,

        )
        await session.restore()

        if not session.player:
            yield {"type": "error", "error": "请先创建角色后再开始冒险。"}
            return

        wg = getattr(session, "world_graph", None)
        if not wg:
            yield {"type": "error", "error": "世界图谱不可用。"}
            return

        npc_node = wg.get_node(npc_id)
        if not npc_node:
            yield {"type": "error", "error": f"NPC '{npc_id}' 不存在。"}
            return

        npc_name = npc_node.name or npc_id

        # InstanceManager 双层认知
        if not self.instance_manager:
            yield {"type": "error", "error": "InstanceManager 不可用。"}
            return

        instance = await self.instance_manager.get_or_create(
            npc_id,
            world_id,
            world_graph=wg,
            session_id=session_id,
        )
        instance.context_window.add_message("user", player_input)

        # SceneBus: contact + 写入玩家发言
        if session.scene_bus:
            from app.world.scene import BusEntry, BusEntryType
            session.scene_bus.contact(npc_id)
            session.scene_bus.publish(BusEntry(
                actor="player",
                actor_name="player",
                type=BusEntryType.SPEECH,
                content=player_input,
            ))

        yield {"type": "interact_start", "npc_id": npc_id, "npc_name": npc_name}

        # ── B: NPC Agentic Response ──
        npc_response_text = ""
        try:
            from app.agentic.agentic_executor import AgenticExecutor
            from app.agentic.immersive_tools import AgenticContext

            npc_traits = set(npc_node.properties.get("traits", []))
            event_queue: asyncio.Queue = asyncio.Queue()

            from app.services.world_api import WorldAPI
            npc_api = WorldAPI(session, role="npc", agent_id=npc_id)

            npc_ctx = AgenticContext(
                session=session,
                agent_id=npc_id,
                role="npc",
                scene_bus=session.scene_bus,
                world_id=world_id,
                chapter_id=getattr(session, "chapter_id", ""),
                area_id=getattr(session, "area_id", ""),
                location_id=getattr(session, "sub_location", ""),
                world_graph=getattr(session, "world_graph", None),
                api=npc_api,
            )

            # 系统提示来自 InstanceManager（保留双层认知 + 记忆注入）
            npc_system_prompt = instance.context_window.get_system_prompt()
            npc_model, npc_thinking = self._select_npc_model(npc_node)

            # 构建 user_prompt：最近对话历史
            character_name = instance.config.name if instance.config else npc_name
            recent_messages = instance.context_window.get_all_messages()
            conversation_lines = []
            for msg in recent_messages[-20:]:
                role_label = "玩家" if msg.role == "user" else character_name
                conversation_lines.append(f"{role_label}: {msg.content}")
            npc_user_prompt = "\n".join(conversation_lines)

            executor = AgenticExecutor(self.llm_service)
            npc_result = await executor.run(
                ctx=npc_ctx,
                system_prompt=npc_system_prompt,
                user_prompt=npc_user_prompt,
                traits=npc_traits,
                event_queue=event_queue,
                model_override=npc_model,
                thinking_level=npc_thinking,
            )

            # Drain NPC tool events
            while not event_queue.empty():
                evt = event_queue.get_nowait()
                evt["character_id"] = npc_id
                yield evt

            npc_response_text = npc_result.narration or ""
            yield {
                "type": "npc_response",
                "npc_id": npc_id,
                "npc_name": npc_name,
                "text": npc_response_text,
            }

            # 写回 InstanceManager 上下文 + SceneBus
            instance.context_window.add_message("assistant", npc_response_text)
            if session.scene_bus and npc_response_text:
                from app.world.scene import BusEntry, BusEntryType
                session.scene_bus.publish(BusEntry(
                    actor=npc_id,
                    actor_name=npc_name,
                    type=BusEntryType.SPEECH,
                    content=npc_response_text,
                ))
        except (LLMServiceError, asyncio.TimeoutError) as exc:
            logger.error("[private_chat] NPC response failed: %s", exc, exc_info=True)
            yield {"type": "error", "error": f"NPC 回复失败: {exc}"}

        # ── C: 后处理（私密模式 — 跳过 GM/队友） ──

        # C1: 对话选项
        dialogue_options = await self._generate_dialogue_options(
            npc_name, npc_node, player_input, npc_response_text, session,
        )
        yield {
            "type": "dialogue_options",
            "options": [opt.model_dump() for opt in dialogue_options],
        }

        # C2: 历史记录
        if session.history:
            session.history.record_round(
                player_input=player_input,
                gm_response="",
                metadata={"source": "private_chat", "npc_id": npc_id},
            )
            session.history.record_npc_response(
                character_id=npc_id,
                name=npc_name,
                dialogue=npc_response_text,
            )

        # C3: 叙事计数
        if session.narrative:
            npc_interactions = getattr(session.narrative, "npc_interactions", None)
            if isinstance(npc_interactions, dict):
                npc_interactions[npc_id] = npc_interactions.get(npc_id, 0) + 1
            session.mark_narrative_dirty()

        await maybe_graphize_session_history(session, source="private_chat")

        # C4: SceneBus clear
        if session.scene_bus:
            session.scene_bus.clear()

        # C5: InstanceManager 图谱化检查
        try:
            await self.instance_manager.maybe_graphize_instance(
                world_id,
                npc_id,
                world_graph=session.world_graph,
                session_id=session_id,
            )
        except (LLMServiceError, KeyError) as exc:
            logger.debug("[private_chat] instance graphize check failed: %s", exc)

        # C6: 统一持久化
        await session.persist()

        yield {
            "type": "complete",
            "npc_id": npc_id,
            "npc_name": npc_name,
            "npc_response": npc_response_text,
            "gm_observation": "",
            "teammate_responses": [],
        }

    # =================================================================
    # NPC 辅助方法
    # =================================================================

    def _build_npc_system_prompt(self, npc_node: Any, session: Any) -> str:
        """从 WorldGraph 节点属性构建 NPC 系统提示。"""
        props = npc_node.properties
        if props.get("system_prompt"):
            return props["system_prompt"]

        parts = [f"你是{npc_node.name}。"]
        if props.get("occupation"):
            parts.append(f"职业：{props['occupation']}。")
        if props.get("personality"):
            parts.append(f"性格：{props['personality']}。")
        if props.get("speech_pattern"):
            parts.append(f"说话风格：{props['speech_pattern']}。")
        if props.get("background"):
            parts.append(f"背景：{props['background'][:200]}。")
        if props.get("example_dialogue"):
            parts.append(f"\n示例对话：\n{props['example_dialogue']}")

        # 好感度上下文
        disps = npc_node.state.get("dispositions", {}).get("player", {})
        if disps:
            a, t = disps.get("approval", 0), disps.get("trust", 0)
            if a or t:
                parts.append(f"\n你对冒险者的态度：好感{a:+d}，信任{t:+d}。")

        parts.append("\n以第一人称回应，保持角色一致性。直接输出对话。")
        return "\n".join(parts)

    @staticmethod
    def _select_npc_model(npc_node: Any):
        """根据 NPC 层级选择模型。"""
        tier = npc_node.properties.get("tier", "secondary")
        is_essential = npc_node.state.get("is_essential", False)
        cfg = settings.npc_tier_config
        if tier == "main" or is_essential:
            return cfg.main_model, cfg.main_thinking
        return cfg.secondary_model, cfg.secondary_thinking

    async def _generate_dialogue_options(
        self,
        npc_name: str,
        npc_node: Any,
        player_input: str,
        npc_response: str,
        session: Any,
    ):
        """生成 4 个对话选项。"""
        from app.models.admin_protocol import DialogueOption

        prompt = (
            f"你在与{npc_name}对话。\n你刚说：{player_input}\n{npc_name}回复：{npc_response}\n\n"
            "生成4个简短对话选项（15字以内），覆盖不同态度。\n"
            '[{"text":"...","intent":"...","tone":"curious"},...]'
        )
        try:
            raw = await self.llm_service.generate_simple(
                prompt,
                model_override=settings.npc_tier_config.passerby_model,
            )
            stripped = getattr(self.llm_service, "_strip_code_block", lambda x: x)(raw or "[]")
            parsed = _json.loads(stripped)
            if isinstance(parsed, list):
                return [
                    DialogueOption(
                        text=o["text"],
                        intent=o.get("intent", ""),
                        tone=o.get("tone", "neutral"),
                    )
                    for o in parsed[:4]
                    if o.get("text")
                ]
        except (LLMServiceError, KeyError, ValueError) as exc:
            logger.warning("[interact] dialogue options failed: %s", exc)

        return [
            DialogueOption(text="继续询问", intent="continue", tone="curious"),
            DialogueOption(text="表示感谢", intent="thank", tone="friendly"),
            DialogueOption(text="告辞离开", intent="leave", tone="neutral"),
            DialogueOption(text="追问细节", intent="dig_deeper", tone="curious"),
        ]
